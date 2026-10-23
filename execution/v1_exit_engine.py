#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from exit_plan import (
    DuplicateExitPlanError,
    ExitPlan,
    ExitPlanStore,
    evaluate_exit_trigger,
    parse_iso_datetime,
)

KST = ZoneInfo("Asia/Seoul")
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


def cmd_create(args: argparse.Namespace) -> int:
    store = ExitPlanStore(Path(args.db))
    plan = ExitPlan(
        plan_id=args.plan_id,
        symbol=args.symbol,
        qty=args.qty,
        entry_price=args.entry_price,
        stop_price=args.stop_price,
        take_profit_price=args.take_profit_price,
        timecut_at=parse_iso_datetime(args.timecut_at),
    )
    try:
        row = store.reserve_plan(plan)
    except DuplicateExitPlanError as exc:
        print(f"[BLOCKED] {exc}")
        return 2
    print("===== V1 EXIT PLAN CREATED — NO BROKER CALL =====")
    print(json.dumps(row, ensure_ascii=False, indent=2))
    print("[SAFE] EXIT_PLAN only. No Kiwoom API and no order sent.")
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    store = ExitPlanStore(Path(args.db))
    row = store.get_plan(args.plan_id)
    if row is None:
        print(f"[BLOCKED] plan_id 없음: {args.plan_id}")
        return 2
    plan = _plan_from_row(row)
    current = parse_iso_datetime(args.now) if args.now else datetime.now(KST)
    decision = evaluate_exit_trigger(
        plan,
        last_price=args.last_price,
        now=current,
    )
    print("===== V1 EXIT EVALUATION — NO BROKER CALL =====")
    if decision is None:
        print("[NO_TRIGGER] STOP/TAKE_PROFIT/TIMECUT 모두 미충족")
        print(json.dumps(store.get_plan(args.plan_id), ensure_ascii=False, indent=2))
        return 0

    created, updated = store.trigger_once(args.plan_id, decision)
    print(
        json.dumps(
            {
                "trigger_reserved_now": created,
                "reason": decision.reason,
                "price": decision.price,
                "triggered_at": decision.triggered_at.isoformat(timespec="seconds"),
                "plan": updated,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if created:
        print("[TRIGGERED] EXIT intent reserved once. Still NO broker order.")
    else:
        print("[DUPLICATE_BLOCKED] existing trigger/intent preserved. NO broker order.")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    store = ExitPlanStore(Path(args.db))
    row = store.get_plan(args.plan_id)
    if row is None:
        print(f"[BLOCKED] plan_id 없음: {args.plan_id}")
        return 2
    print(json.dumps(row, ensure_ascii=False, indent=2))
    return 0


def cmd_events(args: argparse.Namespace) -> int:
    store = ExitPlanStore(Path(args.db))
    print(json.dumps(store.list_events(args.plan_id), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="V1 exit-plan state machine (no broker write in this version)"
    )
    p.add_argument("--db", default=str(DEFAULT_DB))
    sub = p.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create-plan")
    create.add_argument("--plan-id", required=True)
    create.add_argument("--symbol", required=True)
    create.add_argument("--qty", type=int, required=True)
    create.add_argument("--entry-price", type=int, required=True)
    create.add_argument("--stop-price", type=int, required=True)
    create.add_argument("--take-profit-price", type=int, required=True)
    create.add_argument("--timecut-at", required=True)

    evaluate = sub.add_parser("evaluate")
    evaluate.add_argument("--plan-id", required=True)
    evaluate.add_argument("--last-price", type=int, required=True)
    evaluate.add_argument(
        "--now",
        default=None,
        help="Timezone-aware ISO datetime. Omit to use current KST.",
    )

    show = sub.add_parser("show")
    show.add_argument("--plan-id", required=True)

    events = sub.add_parser("events")
    events.add_argument("--plan-id", required=True)
    return p


def main() -> int:
    args = build_parser().parse_args()
    handlers = {
        "create-plan": cmd_create,
        "evaluate": cmd_evaluate,
        "show": cmd_show,
        "events": cmd_events,
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
