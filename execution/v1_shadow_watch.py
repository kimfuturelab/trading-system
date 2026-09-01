#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from broker_cli import KiwoomCliBroker
from exit_plan import ExitPlan, ExitPlanStore, evaluate_exit_trigger, parse_iso_datetime
from quote_reader import read_quote_snapshot

DEFAULT_DB = Path.home() / ".trading-execution-v1.sqlite3"


def _plan_from_row(row: dict) -> ExitPlan:
    return ExitPlan(
        plan_id=str(row["plan_id"]),
        symbol=str(row["symbol"]),
        qty=int(row["qty"]),
        entry_price=int(row["entry_price"]),
        stop_price=int(row["stop_price"]),
        take_profit_price=int(row["take_profit_price"]),
        timecut_at=parse_iso_datetime(str(row["timecut_at"])),
    )


def cmd_quote(args: argparse.Namespace) -> int:
    broker = KiwoomCliBroker(mode=args.mode)
    quote = read_quote_snapshot(broker, symbol=args.symbol)
    print("===== V1 LIVE QUOTE READ — SHADOW / NO ORDER =====")
    print(
        json.dumps(
            {
                "symbol": quote.symbol,
                "last_price": quote.last_price,
                "captured_at": quote.captured_at.isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print("[SAFE] Kiwoom READ only. No order command and no --confirm.")
    return 0


def cmd_check_plan(args: argparse.Namespace) -> int:
    store = ExitPlanStore(Path(args.db))
    row = store.get_plan(args.plan_id)
    if row is None:
        print(f"[BLOCKED] plan_id 없음: {args.plan_id}")
        return 2

    status = str(row.get("status") or "")
    if status not in {"EXIT_ARMED", "POSITION"}:
        print(f"[BLOCKED] Shadow 평가 허용 상태가 아님: {status}")
        print(json.dumps(row, ensure_ascii=False, indent=2))
        return 2

    plan = _plan_from_row(row)
    broker = KiwoomCliBroker(mode=args.mode)
    quote = read_quote_snapshot(broker, symbol=plan.symbol)
    decision = evaluate_exit_trigger(
        plan,
        last_price=quote.last_price,
        now=quote.captured_at,
    )

    print("===== V1 EXIT SHADOW CHECK — LIVE PRICE / NO ORDER =====")
    print(
        json.dumps(
            {
                "plan_id": plan.plan_id,
                "symbol": plan.symbol,
                "qty": plan.qty,
                "last_price": quote.last_price,
                "stop_price": plan.stop_price,
                "take_profit_price": plan.take_profit_price,
                "timecut_at": plan.timecut_at.isoformat(timespec="seconds"),
                "checked_at": quote.captured_at.isoformat(timespec="seconds"),
                "would_trigger": decision.reason if decision else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if decision is None:
        print("[NO_TRIGGER] live price/time is inside the valid EXIT band.")
        print("[SAFE] Shadow only. No broker SELL and no DB trigger mutation.")
        return 0

    if not args.reserve_shadow_trigger:
        print(
            f"[WOULD_TRIGGER] {decision.reason} at {decision.price}. "
            "Shadow only; trigger NOT reserved and no broker SELL."
        )
        return 0

    created, updated = store.trigger_once(plan.plan_id, decision)
    print(
        json.dumps(
            {
                "shadow_trigger_reserved_now": created,
                "reason": decision.reason,
                "price": decision.price,
                "updated_plan": updated,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if created:
        print("[SHADOW_TRIGGER_RESERVED] DB intent reserved once. Still NO broker SELL.")
    else:
        print("[DUPLICATE_BLOCKED] prior trigger preserved. Still NO broker SELL.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="V1 live-price Shadow evaluator. READ only; no broker SELL path."
    )
    p.add_argument("--mode", choices=["real", "demo"], default="real")
    p.add_argument("--db", default=str(DEFAULT_DB))
    sub = p.add_subparsers(dest="command", required=True)

    quote = sub.add_parser("quote")
    quote.add_argument("--symbol", required=True)

    check = sub.add_parser("check-plan")
    check.add_argument("--plan-id", required=True)
    check.add_argument(
        "--reserve-shadow-trigger",
        action="store_true",
        help="Persist the trigger exactly once in the V1 DB. Still sends no broker order.",
    )
    return p


def main() -> int:
    args = build_parser().parse_args()
    handlers = {
        "quote": cmd_quote,
        "check-plan": cmd_check_plan,
    }
    try:
        return handlers[args.command](args)
    except KeyboardInterrupt:
        print("[STOP] 사용자 중지")
        return 130
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
