#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from exit_write_journal import ExitWriteJournal
from safety import ExecutionConfig
from v1_live_sell_adapter import execute_live_sell_once

KST = ZoneInfo("Asia/Seoul")


@dataclass
class FakeResult:
    payload: object


class SpyBroker:
    def __init__(self, journal: ExitWriteJournal, *, mode: str = "success") -> None:
        self.journal = journal
        self.mode = mode
        self.submit_calls: list[dict] = []

    def submit_order(self, **kwargs):
        self.submit_calls.append(dict(kwargs))
        intent_id = "V1-LIVE-FIXTURE-001-EXIT"
        if self.mode.startswith("timeout"):
            intent_id = "V1-LIVE-FIXTURE-TIMEOUT-EXIT"
        elif self.mode.startswith("error"):
            intent_id = "V1-LIVE-FIXTURE-ERROR-EXIT"
        elif self.mode.startswith("missing"):
            intent_id = "V1-LIVE-FIXTURE-MISSING-EXIT"

        row = self.journal.get(intent_id)
        assert row is not None
        assert row["state"] == "WRITE_RESERVED"

        if self.mode == "timeout":
            raise subprocess.TimeoutExpired(cmd="kiwoomcli sell", timeout=25)
        if self.mode == "error":
            raise RuntimeError("simulated broker exception")
        if self.mode == "missing":
            return FakeResult(payload={"return_code": 0, "return_msg": "accepted?"})
        return FakeResult(
            payload={
                "return_code": 0,
                "return_msg": "매도주문이 완료되었습니다.",
                "주문번호": "0099999",
            }
        )


def cfg(*, armed: bool) -> ExecutionConfig:
    return ExecutionConfig(
        auto_mode="LIVE_SMALL" if armed else "OFF",
        kill_switch=not armed,
        allow_live_orders=armed,
        daily_arm_date=datetime.now(KST).strftime("%Y-%m-%d") if armed else "",
        allowed_account="1234563211",
        allowed_symbols=frozenset({"005930"}),
        max_order_qty=1,
        state_db=Path("/tmp/not-used-v0.sqlite3"),
        auth_env=Path("/tmp/not-used-auth.env"),
    )


def snapshot(qty: int) -> dict:
    rows = []
    if qty > 0:
        rows.append({"종목번호": "A005930", "보유수량": str(qty)})
    return {
        "account": {"계좌번호": "******3211"},
        "holdings": {"계좌평가잔고개별합산": rows},
        "open_orders": {"미체결": []},
    }


def plan(plan_id: str) -> dict:
    return {
        "plan_id": plan_id,
        "symbol": "005930",
        "qty": 1,
        "remaining_qty": 1,
        "status": "EXIT_TRIGGERED",
        "trigger_reason": "TIMECUT",
        "exit_intent_id": f"{plan_id}-EXIT",
    }


def main() -> int:
    print("===== V1 LIVE SELL ADAPTER SAFETY FIXTURE =====")
    print("[SAFE] fixture-only: fake broker; no Kiwoom API and no real order")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        journal = ExitWriteJournal(root / "write-journal.sqlite3")
        submitted: list[tuple[str, str]] = []

        def mark_submitted(plan_id: str, *, broker_order_no: str):
            submitted.append((plan_id, broker_order_no))

        # 1) Current DISARM-style config must block before write reservation.
        disarmed_plan = plan("V1-LIVE-FIXTURE-DISARMED")
        broker = SpyBroker(journal)
        try:
            execute_live_sell_once(
                cfg=cfg(armed=False),
                plan_row=disarmed_plan,
                snapshot=snapshot(1),
                local_open_intent_count=0,
                broker=broker,
                journal=journal,
                mark_exit_submitted=mark_submitted,
            )
        except RuntimeError as exc:
            assert "LIVE_SELL_BLOCKED" in str(exc)
        else:
            raise AssertionError("DISARMED config must fail closed")
        assert journal.get(disarmed_plan["exit_intent_id"]) is None
        assert broker.submit_calls == []
        print("[PASS] DISARMED config blocks before journal reservation/broker write")

        # 2) Exact holding + armed fixture -> reserve before broker call -> ACKED once.
        ok_plan = plan("V1-LIVE-FIXTURE-001")
        broker = SpyBroker(journal, mode="success")
        result = execute_live_sell_once(
            cfg=cfg(armed=True),
            plan_row=ok_plan,
            snapshot=snapshot(1),
            local_open_intent_count=0,
            broker=broker,
            journal=journal,
            mark_exit_submitted=mark_submitted,
        )
        assert result.broker_order_no == "0099999"
        assert len(broker.submit_calls) == 1
        assert broker.submit_calls[0]["confirm_live_write"] is True
        row = journal.get(ok_plan["exit_intent_id"])
        assert row is not None and row["state"] == "ACKED"
        assert row["broker_order_no"] == "0099999"
        assert submitted[-1] == (ok_plan["plan_id"], "0099999")
        print("[PASS] WRITE_RESERVED exists before fake broker write; success -> ACKED")

        # Duplicate logical SELL must never call broker again.
        try:
            execute_live_sell_once(
                cfg=cfg(armed=True),
                plan_row=ok_plan,
                snapshot=snapshot(1),
                local_open_intent_count=0,
                broker=broker,
                journal=journal,
                mark_exit_submitted=mark_submitted,
            )
        except RuntimeError as exc:
            assert "LIVE_SELL_DUPLICATE_BLOCKED" in str(exc)
        else:
            raise AssertionError("duplicate write must be blocked")
        assert len(broker.submit_calls) == 1
        print("[PASS] ACKED journal blocks duplicate SELL; broker call count stays 1")

        # 3) Timeout after broker call -> UNKNOWN_ACK and no blind resend.
        timeout_plan = plan("V1-LIVE-FIXTURE-TIMEOUT")
        timeout_broker = SpyBroker(journal, mode="timeout")
        try:
            execute_live_sell_once(
                cfg=cfg(armed=True),
                plan_row=timeout_plan,
                snapshot=snapshot(1),
                local_open_intent_count=0,
                broker=timeout_broker,
                journal=journal,
                mark_exit_submitted=mark_submitted,
            )
        except RuntimeError as exc:
            assert "LIVE_SELL_UNKNOWN_ACK" in str(exc)
        else:
            raise AssertionError("timeout must become UNKNOWN_ACK")
        row = journal.get(timeout_plan["exit_intent_id"])
        assert row is not None and row["state"] == "UNKNOWN_ACK"
        assert len(timeout_broker.submit_calls) == 1
        try:
            execute_live_sell_once(
                cfg=cfg(armed=True),
                plan_row=timeout_plan,
                snapshot=snapshot(1),
                local_open_intent_count=0,
                broker=timeout_broker,
                journal=journal,
                mark_exit_submitted=mark_submitted,
            )
        except RuntimeError as exc:
            assert "LIVE_SELL_DUPLICATE_BLOCKED" in str(exc)
        else:
            raise AssertionError("UNKNOWN_ACK retry must be blocked")
        assert len(timeout_broker.submit_calls) == 1
        print("[PASS] timeout -> UNKNOWN_ACK; second SELL is blocked with no resend")

        # 4) Other broker exception -> REVIEW_REQUIRED and no retry.
        error_plan = plan("V1-LIVE-FIXTURE-ERROR")
        error_broker = SpyBroker(journal, mode="error")
        try:
            execute_live_sell_once(
                cfg=cfg(armed=True),
                plan_row=error_plan,
                snapshot=snapshot(1),
                local_open_intent_count=0,
                broker=error_broker,
                journal=journal,
                mark_exit_submitted=mark_submitted,
            )
        except RuntimeError as exc:
            assert "LIVE_SELL_REVIEW_REQUIRED" in str(exc)
        else:
            raise AssertionError("broker exception must require review")
        row = journal.get(error_plan["exit_intent_id"])
        assert row is not None and row["state"] == "REVIEW_REQUIRED"
        assert len(error_broker.submit_calls) == 1
        print("[PASS] broker exception -> REVIEW_REQUIRED; no automatic retry")

        # 5) Success response without order number is ambiguous.
        missing_plan = plan("V1-LIVE-FIXTURE-MISSING")
        missing_broker = SpyBroker(journal, mode="missing")
        try:
            execute_live_sell_once(
                cfg=cfg(armed=True),
                plan_row=missing_plan,
                snapshot=snapshot(1),
                local_open_intent_count=0,
                broker=missing_broker,
                journal=journal,
                mark_exit_submitted=mark_submitted,
            )
        except RuntimeError as exc:
            assert "LIVE_SELL_UNKNOWN_ACK" in str(exc)
        else:
            raise AssertionError("missing order number must fail closed")
        row = journal.get(missing_plan["exit_intent_id"])
        assert row is not None and row["state"] == "UNKNOWN_ACK"
        print("[PASS] response without order number -> UNKNOWN_ACK")

        # 6) Simulated crash after WRITE_RESERVED but before broker call.
        crash_plan = plan("V1-LIVE-FIXTURE-CRASH")
        journal.reserve_once(
            exit_intent_id=crash_plan["exit_intent_id"],
            plan_id=crash_plan["plan_id"],
            symbol="005930",
            qty=1,
        )
        crash_broker = SpyBroker(journal)
        try:
            execute_live_sell_once(
                cfg=cfg(armed=True),
                plan_row=crash_plan,
                snapshot=snapshot(1),
                local_open_intent_count=0,
                broker=crash_broker,
                journal=journal,
                mark_exit_submitted=mark_submitted,
            )
        except RuntimeError as exc:
            assert "LIVE_SELL_DUPLICATE_BLOCKED" in str(exc)
        else:
            raise AssertionError("restart after WRITE_RESERVED must block")
        assert crash_broker.submit_calls == []
        print("[PASS] restart with orphan WRITE_RESERVED blocks broker write")

        # 7) Holding=0 blocks before journal/broker write.
        flat_plan = plan("V1-LIVE-FIXTURE-FLAT")
        flat_broker = SpyBroker(journal)
        try:
            execute_live_sell_once(
                cfg=cfg(armed=True),
                plan_row=flat_plan,
                snapshot=snapshot(0),
                local_open_intent_count=0,
                broker=flat_broker,
                journal=journal,
                mark_exit_submitted=mark_submitted,
            )
        except RuntimeError as exc:
            assert "NO_BROKER_HOLDING_TO_EXIT" in str(exc)
        else:
            raise AssertionError("flat holding must fail closed")
        assert journal.get(flat_plan["exit_intent_id"]) is None
        assert flat_broker.submit_calls == []
        print("[PASS] holding=0 blocks before write journal and broker method")

    print("[PASS] V1 live SELL adapter safety fixture complete")
    print("[SAFE] no network or real order call occurred")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
