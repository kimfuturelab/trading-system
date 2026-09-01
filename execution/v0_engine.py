#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from broker_cli import BrokerCliError, KiwoomCliBroker
from safety import (
    ExecutionConfig,
    SYMBOL_RE,
    assert_live_write_allowed,
    load_env_file,
    mask_account,
    validate_order_request,
)
from state_store import DuplicateIntentError, StateStore

KST = ZoneInfo("Asia/Seoul")
DEFAULT_AUTH_ENV = Path.home() / "api-read-v2.env"
DEFAULT_EXEC_ENV = Path.home() / "trading-execution.env"


def load_runtime(auth_env: Path, exec_env: Path) -> ExecutionConfig:
    load_env_file(auth_env, required=True)
    load_env_file(exec_env, required=False)
    return ExecutionConfig.from_env()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def broker_account_mask(snapshot: dict[str, Any]) -> str:
    account = _dict(snapshot.get("account"))
    return str(account.get("계좌번호") or account.get("acnt_no") or "").strip()


def account_mask_matches(configured_full: str, broker_masked: str) -> bool:
    """Compare the configured account with CLI's redacted account safely.

    `kiwoomcli` intentionally redacts account numbers.  V0 therefore verifies
    the visible suffix while keeping the full allowed account only in the
    private server env file.
    """
    configured_full = str(configured_full or "").strip()
    broker_masked = str(broker_masked or "").strip()
    if not configured_full or len(configured_full) < 4 or len(broker_masked) < 4:
        return False
    return configured_full[-4:] == broker_masked[-4:]


def parse_intish(value: Any) -> int | None:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def snapshot_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    cash = _dict(snapshot.get("cash"))
    holdings = _dict(snapshot.get("holdings"))
    orders = _dict(snapshot.get("order_fill_status"))
    rows = holdings.get("계좌평가잔고개별합산")
    if not isinstance(rows, list):
        rows = []
    return {
        "broker_account": broker_account_mask(snapshot) or "(unknown)",
        "orderable_cash": parse_intish(cash.get("주문가능금액")),
        "estimated_deposit": parse_intish(cash.get("예수금")),
        "holding_count": len(rows),
        "order_status_return_code": orders.get("return_code"),
        "order_status_return_msg": orders.get("return_msg"),
    }


def read_and_save_snapshot(
    broker: KiwoomCliBroker,
    store: StateStore,
) -> dict[str, Any]:
    snapshot = broker.read_snapshot()
    store.save_snapshot("BROKER_FULL", snapshot)
    return snapshot


def reconciliation_report(
    cfg: ExecutionConfig,
    store: StateStore,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    masked = broker_account_mask(snapshot)
    open_intents = store.open_intents()
    reasons: list[str] = []

    if not cfg.allowed_account:
        reasons.append("ALLOWED_ACCOUNT_NOT_SET")
    elif not account_mask_matches(cfg.allowed_account, masked):
        reasons.append("BROKER_ACCOUNT_SUFFIX_MISMATCH")

    if open_intents:
        # V0 does not guess.  Once there is an unfinished local intent we need
        # broker-order/fill mapping before allowing another write.
        reasons.append("LOCAL_OPEN_INTENTS_REQUIRE_REVIEW")

    return {
        "reconciled_for_new_write": not reasons,
        "block_reasons": reasons,
        "broker_account": masked or "(unknown)",
        "configured_account": mask_account(cfg.allowed_account),
        "local_open_intent_count": len(open_intents),
        "local_open_intents": [
            {
                "intent_id": row.get("intent_id"),
                "symbol": row.get("symbol"),
                "side": row.get("side"),
                "qty": row.get("qty"),
                "status": row.get("status"),
                "broker_order_no": row.get("broker_order_no"),
            }
            for row in open_intents
        ],
    }


def preview_shape_errors(cfg: ExecutionConfig, *, symbol: str, qty: int) -> list[str]:
    reasons: list[str] = []
    symbol = symbol.strip().upper()
    if not SYMBOL_RE.fullmatch(symbol):
        reasons.append("INVALID_SYMBOL_FORMAT")
    if qty <= 0:
        reasons.append("QTY_MUST_BE_POSITIVE")
    if qty > cfg.max_order_qty:
        reasons.append("QTY_EXCEEDS_V0_LIMIT")
    if cfg.allowed_symbols and symbol not in cfg.allowed_symbols:
        reasons.append("SYMBOL_NOT_ALLOWED")
    return reasons


def cmd_read_state(args: argparse.Namespace) -> int:
    cfg = load_runtime(Path(args.auth_env), Path(args.exec_env))
    broker = KiwoomCliBroker(mode=args.mode)
    store = StateStore(cfg.state_db)
    snapshot = read_and_save_snapshot(broker, store)

    print("===== V0 PYTHON BROKER READ =====")
    print(json.dumps(snapshot_summary(snapshot), ensure_ascii=False, indent=2))
    print("\n===== RECONCILIATION =====")
    print(json.dumps(reconciliation_report(cfg, store, snapshot), ensure_ascii=False, indent=2))
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    cfg = load_runtime(Path(args.auth_env), Path(args.exec_env))
    broker = KiwoomCliBroker(mode=args.mode)
    store = StateStore(cfg.state_db)
    snapshot = read_and_save_snapshot(broker, store)
    report = reconciliation_report(cfg, store, snapshot)

    print("===== V0 RECONCILE =====")
    print(json.dumps(snapshot_summary(snapshot), ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["reconciled_for_new_write"]:
        print("[PASS] broker/local baseline reconciled for a new write.")
        return 0
    print("[BLOCKED] reconciliation gate not satisfied.")
    return 2


def cmd_preview_order(args: argparse.Namespace) -> int:
    cfg = load_runtime(Path(args.auth_env), Path(args.exec_env))
    reasons = preview_shape_errors(cfg, symbol=args.symbol, qty=args.qty)
    if reasons:
        print("[BLOCKED] " + ", ".join(reasons))
        return 2

    broker = KiwoomCliBroker(mode=args.mode)
    result = broker.preview_order(
        side=args.side,
        symbol=args.symbol.upper(),
        qty=args.qty,
        exchange=args.exchange,
        order_type=args.order_type,
        price=args.price,
    )
    print("===== V0 ORDER PREVIEW — NOT SENT =====")
    if not cfg.allowed_symbols:
        print("[WARN] ALLOWED_SYMBOLS is empty; preview only. Live gate will still block.")
    print(result.text)
    return 0


def _extract_order_no(payload: Any) -> str | None:
    body = _dict(payload)
    value = body.get("주문번호") or body.get("ord_no")
    text = str(value or "").strip()
    return text or None


def cmd_live_order(args: argparse.Namespace) -> int:
    if not args.i_understand_this_sends_a_live_order:
        print("[BLOCKED] explicit CLI acknowledgement flag is required.")
        return 2

    cfg = load_runtime(Path(args.auth_env), Path(args.exec_env))
    symbol = args.symbol.strip().upper()
    side = args.side.strip().upper()
    intent_id = args.intent_id.strip()

    reasons = validate_order_request(
        cfg,
        account=cfg.allowed_account,
        symbol=symbol,
        qty=args.qty,
        side=side,
        intent_id=intent_id,
    )
    if reasons:
        print("[BLOCKED] Safety Gate: " + ", ".join(reasons))
        return 2

    broker = KiwoomCliBroker(mode=args.mode)
    store = StateStore(cfg.state_db)

    # READ/reconcile immediately before every live write.
    snapshot = read_and_save_snapshot(broker, store)
    report = reconciliation_report(cfg, store, snapshot)
    if not report["reconciled_for_new_write"]:
        print("[BLOCKED] reconciliation: " + ", ".join(report["block_reasons"]))
        return 2

    # Independent final write guard.
    assert_live_write_allowed(cfg)

    # Persist intent BEFORE broker write.
    try:
        store.reserve_intent(
            intent_id=intent_id,
            account_masked=mask_account(cfg.allowed_account),
            symbol=symbol,
            side=side,
            qty=args.qty,
            requested_price=args.price,
            mode=cfg.auto_mode,
        )
    except DuplicateIntentError as exc:
        print(f"[BLOCKED] {exc}")
        return 2

    try:
        result = broker.submit_order(
            side=side.lower(),
            symbol=symbol,
            qty=args.qty,
            exchange=args.exchange,
            order_type=args.order_type,
            price=args.price,
            confirm_live_write=True,
        )
    except subprocess.TimeoutExpired as exc:
        # Critical rule: result is UNKNOWN, never blind retry.
        store.update_intent(
            intent_id,
            status="UNKNOWN_ACK",
            last_error=f"timeout after {exc.timeout}s; DO NOT RESEND; reconcile broker first",
        )
        print("[UNKNOWN] broker write timed out. DO NOT RESEND. Run reconcile/read-state first.")
        return 3
    except Exception as exc:
        # For a non-timeout CLI/API failure we still preserve the intent and
        # require a human/broker reconciliation before deciding terminal state.
        store.update_intent(
            intent_id,
            status="SUBMIT_ERROR_REVIEW",
            last_error=f"{type(exc).__name__}: {exc}",
        )
        print(f"[REVIEW] submit failed: {type(exc).__name__}: {exc}")
        print("Do not reuse the same intent_id or blindly resend.")
        return 3

    order_no = _extract_order_no(result.payload)
    store.update_intent(
        intent_id,
        status="SUBMITTED",
        broker_order_no=order_no,
        raw_response=result.payload,
    )

    print("===== LIVE ORDER SUBMITTED =====")
    print(json.dumps(result.payload, ensure_ascii=False, indent=2))
    print("[IMPORTANT] SUBMITTED != FILLED. Run read-state/reconcile and verify broker holdings/fills.")
    return 0


def cmd_intents(args: argparse.Namespace) -> int:
    cfg = load_runtime(Path(args.auth_env), Path(args.exec_env))
    store = StateStore(cfg.state_db)
    print(json.dumps(store.open_intents(), ensure_ascii=False, indent=2, default=str))
    return 0


def add_order_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--side", required=True, choices=["buy", "sell"])
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--qty", type=int, default=1)
    parser.add_argument("--exchange", choices=["KRX", "NXT", "SOR"], default="KRX")
    parser.add_argument("--order-type", choices=["market", "limit"], default="market")
    parser.add_argument("--price", default=None)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="V0 Python execution engine")
    p.add_argument("--auth-env", default=str(DEFAULT_AUTH_ENV))
    p.add_argument("--exec-env", default=str(DEFAULT_EXEC_ENV))
    p.add_argument("--mode", choices=["real", "demo"], default="real")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("read-state")
    sub.add_parser("reconcile")
    sub.add_parser("intents")

    preview = sub.add_parser("preview-order")
    add_order_args(preview)

    live = sub.add_parser("live-order")
    add_order_args(live)
    live.add_argument("--intent-id", required=True)
    live.add_argument(
        "--i-understand-this-sends-a-live-order",
        action="store_true",
        help="Additional explicit acknowledgement required in addition to env safety gates.",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()
    handlers = {
        "read-state": cmd_read_state,
        "reconcile": cmd_reconcile,
        "preview-order": cmd_preview_order,
        "live-order": cmd_live_order,
        "intents": cmd_intents,
    }
    try:
        return handlers[args.command](args)
    except BrokerCliError as exc:
        print(f"[BROKER ERROR] {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("[STOP] 사용자 중지")
        return 130
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
