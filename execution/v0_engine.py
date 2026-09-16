#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from broker_cli import BrokerCliError, KiwoomCliBroker
from capacity import buy_capacity_errors, capacity_summary
from safety import (
    ExecutionConfig,
    SYMBOL_RE,
    assert_live_write_allowed,
    load_env_file,
    mask_account,
    validate_order_request,
)
from state_store import DuplicateIntentError, StateStore

DEFAULT_AUTH_ENV = Path.home() / "api-read-v2.env"
DEFAULT_EXEC_ENV = Path.home() / "trading-execution.env"


def load_runtime(auth_env: Path, exec_env: Path) -> ExecutionConfig:
    load_env_file(auth_env, required=True)
    load_env_file(exec_env, required=False)
    return ExecutionConfig.from_env()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def parse_intish(value: Any) -> int | None:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def normalize_symbol(value: Any) -> str:
    text = str(value or "").strip().upper()
    if len(text) == 7 and text.startswith("A") and text[1:].isdigit():
        return text[1:]
    return text


def _top_level_rows(payload: Any) -> list[dict[str, Any]]:
    """Collect list-of-dict rows from a named Kiwoom JSON payload.

    Different account/order APIs use different top-level Korean table names,
    so V0 intentionally parses table values rather than hard-coding only one.
    Scalar summary fields are ignored.
    """
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    rows: list[dict[str, Any]] = []
    for value in payload.values():
        if isinstance(value, list):
            rows.extend(row for row in value if isinstance(row, dict))
    return rows


def _row_order_no(row: dict[str, Any]) -> str:
    return str(row.get("주문번호") or row.get("ord_no") or "").strip()


def _row_symbol(row: dict[str, Any]) -> str:
    return normalize_symbol(
        row.get("종목번호")
        or row.get("종목코드")
        or row.get("stk_cd")
        or row.get("code")
    )


def _row_filled_qty(row: dict[str, Any]) -> int:
    for key in ("체결수량", "체결량", "cntr_qty"):
        value = parse_intish(row.get(key))
        if value is not None:
            return max(0, value)
    return 0


def _row_order_qty(row: dict[str, Any]) -> int:
    for key in ("주문수량", "ord_qty"):
        value = parse_intish(row.get(key))
        if value is not None:
            return max(0, value)
    return 0


def _row_unfilled_qty(row: dict[str, Any]) -> int | None:
    for key in ("미체결수량", "미체결량", "oso_qty"):
        value = parse_intish(row.get(key))
        if value is not None:
            return max(0, value)
    return None


def broker_account_mask(snapshot: dict[str, Any]) -> str:
    account = _dict(snapshot.get("account"))
    return str(account.get("계좌번호") or account.get("acnt_no") or "").strip()


def account_mask_matches(configured_full: str, broker_masked: str) -> bool:
    configured_full = str(configured_full or "").strip()
    broker_masked = str(broker_masked or "").strip()
    if not configured_full or len(configured_full) < 4 or len(broker_masked) < 4:
        return False
    return configured_full[-4:] == broker_masked[-4:]


def holdings_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    holdings = _dict(snapshot.get("holdings"))
    rows = holdings.get("계좌평가잔고개별합산")
    if isinstance(rows, list):
        return [row for row in rows if isinstance(row, dict)]
    return _top_level_rows(holdings)


def holding_qty(snapshot: dict[str, Any], symbol: str) -> int:
    symbol = normalize_symbol(symbol)
    total = 0
    for row in holdings_rows(snapshot):
        if _row_symbol(row) != symbol:
            continue
        for key in ("보유수량", "rmnd_qty", "보유량"):
            qty = parse_intish(row.get(key))
            if qty is not None:
                total += max(0, qty)
                break
    return total


def orderable_cash(snapshot: dict[str, Any]) -> int | None:
    """Reference-only legacy cash field. Never use as the BUY capacity gate."""
    return parse_intish(_dict(snapshot.get("cash")).get("주문가능금액"))


def open_order_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return _top_level_rows(snapshot.get("open_orders"))


def fill_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return _top_level_rows(snapshot.get("fills"))


def order_status_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    return _top_level_rows(snapshot.get("order_fill_status"))


def snapshot_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    cash = _dict(snapshot.get("cash"))
    orders = _dict(snapshot.get("order_fill_status"))
    return {
        "broker_account": broker_account_mask(snapshot) or "(unknown)",
        "orderable_cash_reference_only": parse_intish(cash.get("주문가능금액")),
        "estimated_deposit_reference_only": parse_intish(cash.get("예수금")),
        "holding_count": len(holdings_rows(snapshot)),
        "broker_open_order_count": len(open_order_rows(snapshot)),
        "broker_fill_row_count": len(fill_rows(snapshot)),
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


def sync_open_intents(
    store: StateStore,
    snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    """Reconcile local non-terminal intents against Kiwoom broker truth.

    Rules:
    - Full broker fill => local order intent becomes FILLED (terminal order state).
    - Open/unfilled broker order => OPEN or PARTIAL and remains blocking.
    - No broker order number on a local intent => never guess; remains blocking.
    - Submitted order not yet visible => remains blocking; never blind resend.
    """
    transitions: list[dict[str, Any]] = []
    fills = fill_rows(snapshot)
    status_rows = order_status_rows(snapshot)
    opens = open_order_rows(snapshot)

    for intent in store.open_intents():
        intent_id = str(intent.get("intent_id") or "")
        order_no = str(intent.get("broker_order_no") or "").strip()
        requested_qty = int(intent.get("qty") or 0)
        old_status = str(intent.get("status") or "")

        if not order_no:
            transitions.append(
                {
                    "intent_id": intent_id,
                    "from": old_status,
                    "to": old_status,
                    "reason": "NO_BROKER_ORDER_NO_REQUIRES_REVIEW",
                }
            )
            continue

        matched_fills = [row for row in fills if _row_order_no(row) == order_no]
        matched_status = [row for row in status_rows if _row_order_no(row) == order_no]
        matched_open = [row for row in opens if _row_order_no(row) == order_no]

        # Prefer ka10076 fill rows. If unavailable, fall back to the account
        # order/fill status row already proven in the 2026-09-01 live test.
        source_rows = matched_fills if matched_fills else matched_status
        filled_qty = sum(_row_filled_qty(row) for row in source_rows)
        if matched_status and not matched_fills:
            filled_qty = sum(_row_filled_qty(row) for row in matched_status)

        if requested_qty > 0 and filled_qty >= requested_qty:
            store.update_intent(
                intent_id,
                status="FILLED",
                broker_order_no=order_no,
                last_error=None,
                raw_response={
                    "matched_fill_rows": matched_fills,
                    "matched_status_rows": matched_status,
                    "filled_qty": filled_qty,
                },
            )
            transitions.append(
                {
                    "intent_id": intent_id,
                    "from": old_status,
                    "to": "FILLED",
                    "broker_order_no": order_no,
                    "filled_qty": filled_qty,
                }
            )
            continue

        if matched_open:
            unfilled_values = [_row_unfilled_qty(row) for row in matched_open]
            unfilled_values = [v for v in unfilled_values if v is not None]
            new_status = "PARTIAL" if filled_qty > 0 else "OPEN"
            store.update_intent(
                intent_id,
                status=new_status,
                broker_order_no=order_no,
                raw_response={
                    "matched_open_rows": matched_open,
                    "matched_fill_rows": matched_fills,
                    "filled_qty": filled_qty,
                    "unfilled_qty": max(unfilled_values) if unfilled_values else None,
                },
            )
            transitions.append(
                {
                    "intent_id": intent_id,
                    "from": old_status,
                    "to": new_status,
                    "broker_order_no": order_no,
                    "filled_qty": filled_qty,
                }
            )
            continue

        new_status = "AWAITING_BROKER_VISIBILITY"
        store.update_intent(
            intent_id,
            status=new_status,
            broker_order_no=order_no,
            last_error="order not found in current open/fill snapshot; DO NOT RESEND",
        )
        transitions.append(
            {
                "intent_id": intent_id,
                "from": old_status,
                "to": new_status,
                "broker_order_no": order_no,
                "filled_qty": filled_qty,
            }
        )

    return transitions


def reconciliation_report(
    cfg: ExecutionConfig,
    store: StateStore,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    masked = broker_account_mask(snapshot)
    open_intents = store.open_intents()
    broker_opens = open_order_rows(snapshot)
    reasons: list[str] = []

    if not cfg.allowed_account:
        reasons.append("ALLOWED_ACCOUNT_NOT_SET")
    elif not account_mask_matches(cfg.allowed_account, masked):
        reasons.append("BROKER_ACCOUNT_SUFFIX_MISMATCH")

    if broker_opens:
        reasons.append("BROKER_OPEN_ORDERS_PRESENT")

    if open_intents:
        reasons.append("LOCAL_OPEN_INTENTS_REQUIRE_REVIEW")

    return {
        "reconciled_for_new_write": not reasons,
        "block_reasons": reasons,
        "broker_account": masked or "(unknown)",
        "configured_account": mask_account(cfg.allowed_account),
        "broker_open_order_count": len(broker_opens),
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


def semantic_order_errors(
    snapshot: dict[str, Any],
    *,
    side: str,
    symbol: str,
    qty: int,
) -> list[str]:
    """V0 position-aware last-mile checks before a live write.

    BUY capacity is intentionally NOT checked here. The sole capacity truth is
    Kiwoom `orders chance` -> 최대주문가능금액, checked immediately before BUY.
    """
    reasons: list[str] = []
    held = holding_qty(snapshot, symbol)
    side = side.upper()

    if side == "BUY" and held > 0:
        reasons.append("V0_BUY_WOULD_STACK_EXISTING_POSITION")
    if side == "SELL" and held < qty:
        reasons.append("V0_SELL_QTY_EXCEEDS_HOLDING")
    return reasons


def cmd_read_state(args: argparse.Namespace) -> int:
    cfg = load_runtime(Path(args.auth_env), Path(args.exec_env))
    broker = KiwoomCliBroker(mode=args.mode)
    store = StateStore(cfg.state_db)
    snapshot = read_and_save_snapshot(broker, store)
    transitions = sync_open_intents(store, snapshot)

    print("===== V0 PYTHON BROKER READ =====")
    print(json.dumps(snapshot_summary(snapshot), ensure_ascii=False, indent=2))
    print("\n===== INTENT SYNC =====")
    print(json.dumps(transitions, ensure_ascii=False, indent=2))
    print("\n===== RECONCILIATION =====")
    print(json.dumps(reconciliation_report(cfg, store, snapshot), ensure_ascii=False, indent=2))
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    cfg = load_runtime(Path(args.auth_env), Path(args.exec_env))
    broker = KiwoomCliBroker(mode=args.mode)
    store = StateStore(cfg.state_db)
    snapshot = read_and_save_snapshot(broker, store)
    transitions = sync_open_intents(store, snapshot)
    report = reconciliation_report(cfg, store, snapshot)

    print("===== V0 RECONCILE =====")
    print(json.dumps(snapshot_summary(snapshot), ensure_ascii=False, indent=2))
    print("===== INTENT SYNC =====")
    print(json.dumps(transitions, ensure_ascii=False, indent=2))
    print("===== GATE =====")
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


def _resolve_chance_price(args: argparse.Namespace) -> int | None:
    if args.chance_price is not None:
        return int(args.chance_price)
    if args.order_type == "limit" and args.price:
        return parse_intish(args.price)
    return None


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

    # READ/sync/reconcile immediately before every live write.
    snapshot = read_and_save_snapshot(broker, store)
    transitions = sync_open_intents(store, snapshot)
    if transitions:
        print("===== PRE-WRITE INTENT SYNC =====")
        print(json.dumps(transitions, ensure_ascii=False, indent=2))

    report = reconciliation_report(cfg, store, snapshot)
    if not report["reconciled_for_new_write"]:
        print("[BLOCKED] reconciliation: " + ", ".join(report["block_reasons"]))
        return 2

    semantic_reasons = semantic_order_errors(
        snapshot,
        side=side,
        symbol=symbol,
        qty=args.qty,
    )
    if semantic_reasons:
        print("[BLOCKED] position gate: " + ", ".join(semantic_reasons))
        return 2

    # BUY capacity truth: Kiwoom symbol-specific `orders chance` only.
    if side == "BUY":
        chance_price = _resolve_chance_price(args)
        if chance_price is None or chance_price <= 0:
            print(
                "[BLOCKED] BUY requires --chance-price for market orders "
                "(limit orders may reuse --price)."
            )
            return 2
        chance = broker.order_chance(
            symbol=symbol,
            side="buy",
            price=chance_price,
        )
        cap_errors = buy_capacity_errors(
            chance.payload,
            qty=args.qty,
            reference_price=chance_price,
        )
        cap = capacity_summary(chance.payload)
        cap["reference_price"] = chance_price
        cap["qty"] = args.qty
        cap["required_notional"] = chance_price * args.qty
        print("===== PRE-WRITE MAX ORDERABLE CAPACITY =====")
        print(json.dumps(cap, ensure_ascii=False, indent=2))
        if cap_errors:
            print("[BLOCKED] max-orderable gate: " + ", ".join(cap_errors))
            return 2

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
        store.update_intent(
            intent_id,
            status="UNKNOWN_ACK",
            last_error=f"timeout after {exc.timeout}s; DO NOT RESEND; reconcile broker first",
        )
        print("[UNKNOWN] broker write timed out. DO NOT RESEND. Run reconcile/read-state first.")
        return 3
    except Exception as exc:
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
    print("[IMPORTANT] SUBMITTED != FILLED. Run `v0_engine.py reconcile` until FILLED before another write.")
    return 0


def cmd_intents(args: argparse.Namespace) -> int:
    cfg = load_runtime(Path(args.auth_env), Path(args.exec_env))
    store = StateStore(cfg.state_db)
    print(json.dumps(store.list_intents(), ensure_ascii=False, indent=2, default=str))
    return 0


def add_order_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--side", required=True, choices=["buy", "sell"])
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--qty", type=int, default=1)
    parser.add_argument("--exchange", choices=["KRX", "NXT", "SOR"], default="KRX")
    parser.add_argument("--order-type", choices=["market", "limit"], default="market")
    parser.add_argument("--price", default=None)
    parser.add_argument(
        "--chance-price",
        type=int,
        default=None,
        help="BUY capacity simulation reference price. Required for market BUY; limit BUY may reuse --price.",
    )


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
