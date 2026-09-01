#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from broker_cli import BrokerCliError, KiwoomCliBroker
from exit_plan import ExitPlan, ExitPlanStore, evaluate_exit_trigger, parse_iso_datetime
from exit_write_journal import ExitWriteJournal
from exit_write_reconcile import reconcile_exit_write
from nxt_order_policy import aggressive_limit_price
from quote_reader import QuoteSnapshot, read_quote_snapshot
from safety import ExecutionConfig, assert_live_write_allowed, load_env_file
from state_store import StateStore
from v0_engine import snapshot_summary, sync_open_intents
from v1_live_sell_adapter import execute_live_sell_once

DEFAULT_AUTH_ENV = Path.home() / "api-read-v2.env"
DEFAULT_EXEC_ENV = Path.home() / "trading-execution.env"
DEFAULT_V1_DB = Path.home() / ".trading-execution-v1.sqlite3"
DEFAULT_WRITE_DB = Path.home() / ".trading-execution-v1-write.sqlite3"


def load_runtime(auth_env: Path, exec_env: Path) -> ExecutionConfig:
    load_env_file(auth_env, required=True)
    load_env_file(exec_env, required=False)
    return ExecutionConfig.from_env()


def _plan_from_row(row: dict[str, Any]) -> ExitPlan:
    return ExitPlan(
        plan_id=str(row["plan_id"]), symbol=str(row["symbol"]), qty=int(row["qty"]),
        entry_price=int(row["entry_price"]), stop_price=int(row["stop_price"]),
        take_profit_price=int(row["take_profit_price"]),
        timecut_at=parse_iso_datetime(str(row["timecut_at"])),
    )


def _pre_snapshot(broker: Any, exchange: str) -> dict[str, Any]:
    exchange = exchange.upper()
    if exchange == "KRX":
        return broker.read_snapshot()
    return {
        "account": broker.account_list().payload,
        "cash": broker.cash(basis="estimated").payload,
        "holdings": broker.holdings(basis="total", exchange=exchange).payload,
        "order_fill_status": broker.order_fill_status().payload,
        "open_orders": broker.list_open_orders().payload,
        "fills": broker.list_fills().payload,
    }


def _post_snapshot(broker: Any, exchange: str) -> dict[str, Any]:
    return {
        "holdings": broker.holdings(basis="total", exchange=exchange).payload,
        "open_orders": broker.list_open_orders().payload,
    }


def run_live_exit_once(
    *, cfg: ExecutionConfig, plan_id: str, v1: ExitPlanStore, v0: StateStore,
    journal: ExitWriteJournal, broker: Any,
    quote_reader: Callable[..., QuoteSnapshot] = read_quote_snapshot,
    exchange: str = "KRX",
) -> dict[str, Any]:
    """Evaluate one EXIT_PLAN and, if fully gated, SELL once on KRX or NXT.

    KRX uses the already validated market route. NXT has no generic market route
    in this execution path, so V1 derives a bounded aggressive LIMIT SELL from a
    fresh NXT quote. Lack of immediate fill never causes an automatic resend.
    """
    exchange = str(exchange or "").strip().upper()
    if exchange not in {"KRX", "NXT"}:
        raise RuntimeError("LIVE_EXIT_BLOCKED: exchange must be KRX|NXT")

    assert_live_write_allowed(cfg)
    row = v1.get_plan(plan_id)
    if row is None:
        raise RuntimeError(f"LIVE_EXIT_BLOCKED: plan_id 없음: {plan_id}")

    status = str(row.get("status") or "")
    quote: QuoteSnapshot | None = None
    trigger_reserved_now = False

    if status in {"EXIT_ARMED", "POSITION"}:
        quote = quote_reader(
            broker,
            symbol=str(row.get("symbol") or ""),
            exchange=exchange,
        )
        decision = evaluate_exit_trigger(
            _plan_from_row(row), last_price=int(quote.last_price), now=quote.captured_at,
        )
        if decision is None:
            return {
                "action": "NO_TRIGGER", "plan_id": plan_id, "exchange": exchange,
                "last_price": int(quote.last_price),
                "captured_at": quote.captured_at.isoformat(timespec="seconds"),
                "broker_write": False,
            }
        trigger_reserved_now, row = v1.trigger_once(plan_id, decision)
    elif status == "EXIT_TRIGGERED":
        pass
    else:
        raise RuntimeError(f"LIVE_EXIT_BLOCKED: unsupported plan status={status}")

    # NXT needs a priced route. Always refresh the NXT quote at the actual write
    # boundary, even if the trigger had been reserved earlier.
    sell_order_type = "market"
    sell_price: str | None = None
    if exchange == "NXT":
        quote = quote_reader(
            broker,
            symbol=str(row.get("symbol") or ""),
            exchange="NXT",
        )
        sell_order_type = "limit"
        sell_price = str(aggressive_limit_price(int(quote.last_price), side="SELL", ticks=2))

    snapshot = _pre_snapshot(broker, exchange)
    sync_open_intents(v0, snapshot)
    local_open_count = len(v0.open_intents())

    result = execute_live_sell_once(
        cfg=cfg, plan_row=row, snapshot=snapshot,
        local_open_intent_count=local_open_count, broker=broker, journal=journal,
        mark_exit_submitted=v1.mark_exit_submitted, exchange=exchange,
        order_type=sell_order_type, price=sell_price,
    )

    fresh_plan = v1.get_plan(plan_id)
    if fresh_plan is None:
        raise RuntimeError("LIVE_EXIT_RECONCILE_BLOCKED: plan disappeared")
    post = _post_snapshot(broker, exchange)
    journal_row = journal.get(result.exit_intent_id)
    recon = reconcile_exit_write(
        plan_row=fresh_plan, snapshot=post, journal_row=journal_row,
        mark_exit_submitted=v1.mark_exit_submitted,
        reconcile_holding=v1.reconcile_holding,
    )
    final_plan = v1.get_plan(plan_id)

    return {
        "action": "SELL_SUBMITTED", "exchange": exchange,
        "trigger_reserved_now": trigger_reserved_now,
        "quote": None if quote is None else {
            "last_price": int(quote.last_price),
            "captured_at": quote.captured_at.isoformat(timespec="seconds"),
            "exchange": getattr(quote, "exchange", exchange),
            "query_code": getattr(quote, "query_code", ""),
        },
        "order_route": {
            "order_type": sell_order_type,
            "price": sell_price,
            "policy": "KRX_MARKET" if exchange == "KRX" else "NXT_AGGRESSIVE_LIMIT_2TICKS",
        },
        "pre_write_broker": snapshot_summary(snapshot),
        "local_open_intent_count": local_open_count,
        "sell": asdict(result), "journal": journal_row,
        "post_reconcile": asdict(recon), "final_plan": final_plan,
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="V1 one-shot live EXIT: exchange quote -> gated SELL once -> reconcile"
    )
    p.add_argument("--plan-id", required=True)
    p.add_argument("--exchange", choices=["KRX", "NXT"], default="KRX")
    p.add_argument("--auth-env", default=str(DEFAULT_AUTH_ENV))
    p.add_argument("--exec-env", default=str(DEFAULT_EXEC_ENV))
    p.add_argument("--v1-db", default=str(DEFAULT_V1_DB))
    p.add_argument("--write-db", default=str(DEFAULT_WRITE_DB))
    p.add_argument("--mode", choices=["real", "demo"], default="real")
    p.add_argument("--i-understand-this-sends-a-live-sell", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    if not args.i_understand_this_sends_a_live_sell:
        print("[BLOCKED] explicit live SELL acknowledgement flag is required.")
        return 2

    cfg = load_runtime(Path(args.auth_env), Path(args.exec_env))
    broker = KiwoomCliBroker(mode=args.mode)
    v1 = ExitPlanStore(Path(args.v1_db))
    v0 = StateStore(cfg.state_db)
    journal = ExitWriteJournal(Path(args.write_db))

    print("===== V1 ONE-SHOT LIVE EXIT =====")
    print(f"[DANGER] This command can send exactly one real {args.exchange} SELL when every gate passes.")
    try:
        report = run_live_exit_once(
            cfg=cfg, plan_id=args.plan_id, v1=v1, v0=v0,
            journal=journal, broker=broker, exchange=args.exchange,
        )
    except (BrokerCliError, RuntimeError, ValueError) as exc:
        print(f"[BLOCKED/REVIEW] {type(exc).__name__}: {exc}")
        return 2

    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    if report.get("action") == "NO_TRIGGER":
        print("[NO_TRIGGER] No broker write was sent.")
        return 0
    final = report.get("final_plan") or {}
    if final.get("status") == "CLOSED":
        print("[PASS] SELL acknowledged and broker holding is flat. V1 live exit CLOSED.")
    else:
        print("[PENDING] SELL acknowledged; broker truth is not flat yet. DO NOT RESEND. Reconcile only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
