#!/usr/bin/env python3
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from exit_plan import (
    DuplicateExitPlanError,
    ExitPlan,
    ExitPlanStore,
    evaluate_exit_trigger,
    pre_exit_gate_errors,
)

KST = ZoneInfo("Asia/Seoul")
BASE_NOW = datetime(2026, 9, 1, 14, 30, tzinfo=KST)


def make_plan(plan_id: str, *, qty: int = 1, minutes: int = 30) -> ExitPlan:
    return ExitPlan(
        plan_id=plan_id,
        symbol="005930",
        qty=qty,
        entry_price=100_000,
        stop_price=98_000,
        take_profit_price=103_000,
        timecut_at=BASE_NOW + timedelta(minutes=minutes),
    )


def main() -> int:
    print("===== V1 EXIT ENGINE FIXTURE TEST =====")
    print("[SAFE] fixture-only: no Kiwoom API, no broker write, no --confirm")

    with tempfile.TemporaryDirectory(prefix="v1-exit-") as tmp:
        db = Path(tmp) / "exit.sqlite3"
        store = ExitPlanStore(db)

        bad = ExitPlan(
            plan_id="BAD-REL",
            symbol="005930",
            qty=1,
            entry_price=100_000,
            stop_price=101_000,
            take_profit_price=103_000,
            timecut_at=BASE_NOW + timedelta(minutes=30),
        )
        try:
            store.reserve_plan(bad)
            raise AssertionError("invalid stop relationship was accepted")
        except ValueError as exc:
            assert "STOP_MUST_BE_BELOW_ENTRY_FOR_LONG" in str(exc)
        print("[PASS] invalid EXIT_PLAN fails closed")

        p_stop = make_plan("V1-STOP-001")
        row = store.reserve_plan(p_stop)
        assert row["status"] == "EXIT_ARMED"
        decision = evaluate_exit_trigger(
            p_stop,
            last_price=97_900,
            now=BASE_NOW,
        )
        assert decision is not None and decision.reason == "STOP"
        created, triggered = store.trigger_once(p_stop.plan_id, decision)
        assert created is True
        assert triggered["status"] == "EXIT_TRIGGERED"
        assert triggered["trigger_reason"] == "STOP"
        first_intent = triggered["exit_intent_id"]
        created2, triggered2 = store.trigger_once(p_stop.plan_id, decision)
        assert created2 is False
        assert triggered2["exit_intent_id"] == first_intent
        events = store.list_events(p_stop.plan_id)
        assert [e["event_type"] for e in events].count("TRIGGER_RESERVED") == 1
        print("[PASS] STOP trigger is atomic; duplicate EXIT trigger blocked")

        assert pre_exit_gate_errors(
            triggered,
            broker_holding_qty=1,
            broker_open_order_count=0,
            local_open_intent_count=0,
        ) == []
        mismatch = pre_exit_gate_errors(
            triggered,
            broker_holding_qty=2,
            broker_open_order_count=0,
            local_open_intent_count=0,
        )
        assert any(x.startswith("BROKER_HOLDING_MISMATCH") for x in mismatch)
        blocked = pre_exit_gate_errors(
            triggered,
            broker_holding_qty=1,
            broker_open_order_count=1,
            local_open_intent_count=1,
        )
        assert "BROKER_OPEN_ORDERS_PRESENT" in blocked
        assert "LOCAL_OPEN_INTENTS_PRESENT" in blocked
        print("[PASS] pre-exit gate requires exact holding and clean broker/local state")

        store2 = ExitPlanStore(db)
        reloaded = store2.get_plan(p_stop.plan_id)
        assert reloaded is not None
        assert reloaded["status"] == "EXIT_TRIGGERED"
        assert reloaded["trigger_reason"] == "STOP"
        assert reloaded["exit_intent_id"] == first_intent
        created3, reloaded2 = store2.trigger_once(p_stop.plan_id, decision)
        assert created3 is False
        assert reloaded2["exit_intent_id"] == first_intent
        print("[PASS] restart recovery preserves trigger/exit_intent; no blind duplicate")

        p_tp = make_plan("V1-TP-001")
        store2.reserve_plan(p_tp)
        d_tp = evaluate_exit_trigger(
            p_tp,
            last_price=103_100,
            now=BASE_NOW,
        )
        assert d_tp is not None and d_tp.reason == "TAKE_PROFIT"
        c_tp, r_tp = store2.trigger_once(p_tp.plan_id, d_tp)
        assert c_tp and r_tp["trigger_reason"] == "TAKE_PROFIT"
        print("[PASS] TAKE_PROFIT trigger")

        p_time = make_plan("V1-TIME-001", minutes=10)
        store2.reserve_plan(p_time)
        d_time = evaluate_exit_trigger(
            p_time,
            last_price=100_500,
            now=BASE_NOW + timedelta(minutes=11),
        )
        assert d_time is not None and d_time.reason == "TIMECUT"
        c_time, r_time = store2.trigger_once(p_time.plan_id, d_time)
        assert c_time and r_time["trigger_reason"] == "TIMECUT"
        print("[PASS] TIMECUT trigger")

        p_none = make_plan("V1-NONE-001")
        store2.reserve_plan(p_none)
        no_trigger = evaluate_exit_trigger(
            p_none,
            last_price=100_500,
            now=BASE_NOW,
        )
        assert no_trigger is None
        assert store2.get_plan(p_none.plan_id)["status"] == "EXIT_ARMED"
        print("[PASS] no trigger inside valid price/time band")

        try:
            store2.reserve_plan(p_none)
            raise AssertionError("duplicate plan_id was accepted")
        except DuplicateExitPlanError:
            pass
        print("[PASS] duplicate plan_id blocked")

        p_partial = make_plan("V1-PARTIAL-001", qty=10)
        store2.reserve_plan(p_partial)
        d_partial = evaluate_exit_trigger(
            p_partial,
            last_price=97_500,
            now=BASE_NOW,
        )
        assert d_partial is not None
        _, partial_triggered = store2.trigger_once(p_partial.plan_id, d_partial)
        assert pre_exit_gate_errors(
            partial_triggered,
            broker_holding_qty=10,
            broker_open_order_count=0,
            local_open_intent_count=0,
        ) == []
        submitted = store2.mark_exit_submitted(
            p_partial.plan_id,
            broker_order_no="FIXTURE-SELL-001",
        )
        assert submitted["status"] == "EXIT_SUBMITTED"
        partial = store2.reconcile_holding(
            p_partial.plan_id,
            broker_holding_qty=6,
        )
        assert partial["status"] == "PARTIAL_EXIT"
        assert partial["remaining_qty"] == 6
        assert partial["exit_intent_id"] == submitted["exit_intent_id"]
        print("[PASS] partial exit persists remaining_qty=6 without automatic resend")

        closed = store2.reconcile_holding(
            p_partial.plan_id,
            broker_holding_qty=0,
        )
        assert closed["status"] == "CLOSED"
        assert closed["remaining_qty"] == 0
        print("[PASS] broker holding=0 -> CLOSED")

        p_halt = make_plan("V1-HALT-001", qty=2)
        store2.reserve_plan(p_halt)
        halted = store2.reconcile_holding(
            p_halt.plan_id,
            broker_holding_qty=3,
        )
        assert halted["status"] == "HALTED"
        assert "BROKER_HOLDING_GREATER_THAN_REMAINING" in halted["last_error"]
        print("[PASS] unexpected extra holding -> HALTED")

    print("[PASS] V1 EXIT fixture suite complete")
    print("[SAFE] no network or order call occurred")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
