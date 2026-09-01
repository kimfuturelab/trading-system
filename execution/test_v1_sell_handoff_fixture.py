#!/usr/bin/env python3
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from exit_plan import ExitPlan, ExitPlanStore, ExitTrigger, pre_exit_gate_errors
from sell_handoff import prepare_sell_handoff
from v0_engine import holding_qty, open_order_rows

KST = ZoneInfo("Asia/Seoul")


def snapshot(*, symbol: str = "005930", holding: int = 1, open_orders: int = 0) -> dict:
    holding_rows = []
    if holding > 0:
        holding_rows.append(
            {
                "종목번호": f"A{symbol}",
                "보유수량": f"{holding:010d}",
            }
        )
    open_rows = [
        {
            "주문번호": f"TESTOPEN{i + 1:03d}",
            "종목코드": symbol,
            "미체결수량": "1",
        }
        for i in range(open_orders)
    ]
    return {
        "holdings": {"계좌평가잔고개별합산": holding_rows},
        "open_orders": {"미체결": open_rows},
    }


def make_triggered(store: ExitPlanStore, plan_id: str, qty: int) -> dict:
    now = datetime.now(KST)
    plan = ExitPlan(
        plan_id=plan_id,
        symbol="005930",
        qty=qty,
        entry_price=260000,
        stop_price=250000,
        take_profit_price=280000,
        timecut_at=now + timedelta(minutes=30),
    )
    store.reserve_plan(plan)
    created, row = store.trigger_once(
        plan_id,
        ExitTrigger("STOP", 249000, now),
    )
    assert created is True
    assert row["status"] == "EXIT_TRIGGERED"
    return row


def main() -> int:
    print("===== V1 SELL HANDOFF FIXTURE TEST =====")
    print("[SAFE] fixture-only: no Kiwoom API, no broker write, no --confirm")

    with tempfile.TemporaryDirectory(prefix="v1-sell-handoff-") as td:
        db = Path(td) / "v1.sqlite3"
        store = ExitPlanStore(db)

        row = make_triggered(store, "TEST-HANDOFF-001", 1)
        snap = snapshot(holding=1, open_orders=0)
        assert holding_qty(snap, "005930") == 1
        assert len(open_order_rows(snap)) == 0
        handoff = prepare_sell_handoff(row, snap, local_open_intent_count=0)
        assert handoff.side == "SELL"
        assert handoff.symbol == "005930"
        assert handoff.qty == 1
        assert handoff.exit_intent_id == "TEST-HANDOFF-001-EXIT"
        print("[PASS] exact holding=1 + clean state -> one SELL handoff prepared")

        submitted = store.mark_exit_submitted(
            "TEST-HANDOFF-001",
            broker_order_no="TESTSELL001",
        )
        assert submitted["status"] == "EXIT_SUBMITTED"
        assert submitted["broker_order_no"] == "TESTSELL001"

        restarted = ExitPlanStore(db)
        recovered = restarted.get_plan("TEST-HANDOFF-001")
        assert recovered is not None
        assert recovered["status"] == "EXIT_SUBMITTED"
        assert recovered["broker_order_no"] == "TESTSELL001"
        duplicate_blocked = False
        try:
            restarted.mark_exit_submitted(
                "TEST-HANDOFF-001",
                broker_order_no="TESTSELL002",
            )
        except RuntimeError:
            duplicate_blocked = True
        assert duplicate_blocked
        print("[PASS] restart preserves EXIT_SUBMITTED/order_no; duplicate SELL submit transition blocked")

        closed = restarted.reconcile_holding(
            "TEST-HANDOFF-001",
            broker_holding_qty=0,
        )
        assert closed["status"] == "CLOSED"
        assert closed["remaining_qty"] == 0
        print("[PASS] broker holding=0 -> CLOSED")

        row2 = make_triggered(store, "TEST-GATES-001", 1)
        mismatch = pre_exit_gate_errors(
            row2,
            broker_holding_qty=2,
            broker_open_order_count=0,
            local_open_intent_count=0,
        )
        assert "BROKER_HOLDING_MISMATCH(2!=1)" in mismatch
        open_block = pre_exit_gate_errors(
            row2,
            broker_holding_qty=1,
            broker_open_order_count=1,
            local_open_intent_count=0,
        )
        assert "BROKER_OPEN_ORDERS_PRESENT" in open_block
        local_block = pre_exit_gate_errors(
            row2,
            broker_holding_qty=1,
            broker_open_order_count=0,
            local_open_intent_count=1,
        )
        assert "LOCAL_OPEN_INTENTS_PRESENT" in local_block
        print("[PASS] holding mismatch / broker open order / local open intent all fail closed")

        row3 = make_triggered(store, "TEST-PARTIAL-001", 10)
        partial_handoff = prepare_sell_handoff(
            row3,
            snapshot(holding=10),
            local_open_intent_count=0,
        )
        assert partial_handoff.qty == 10
        store.mark_exit_submitted(
            "TEST-PARTIAL-001",
            broker_order_no="TESTSELL010",
        )
        partial = store.reconcile_holding(
            "TEST-PARTIAL-001",
            broker_holding_qty=6,
        )
        assert partial["status"] == "PARTIAL_EXIT"
        assert partial["remaining_qty"] == 6

        partial_restarted = ExitPlanStore(db).get_plan("TEST-PARTIAL-001")
        assert partial_restarted is not None
        assert partial_restarted["status"] == "PARTIAL_EXIT"
        assert partial_restarted["remaining_qty"] == 6

        blocked_after_partial = False
        try:
            prepare_sell_handoff(
                partial_restarted,
                snapshot(holding=6),
                local_open_intent_count=0,
            )
        except RuntimeError as exc:
            blocked_after_partial = "EXIT_PLAN_NOT_TRIGGERED" in str(exc)
        assert blocked_after_partial
        print("[PASS] partial exit persists remaining=6 and does not auto-create a second SELL handoff")

        partial_closed = store.reconcile_holding(
            "TEST-PARTIAL-001",
            broker_holding_qty=0,
        )
        assert partial_closed["status"] == "CLOSED"
        assert partial_closed["remaining_qty"] == 0
        print("[PASS] partial path later broker holding=0 -> CLOSED")

    print("[PASS] V1 SELL handoff fixture suite complete")
    print("[SAFE] no network or order call occurred")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
