#!/usr/bin/env python3
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from exit_plan import ExitPlan, ExitPlanStore
from exit_write_journal import ExitWriteJournal
from quote_reader import QuoteSnapshot
from safety import ExecutionConfig
from state_store import StateStore
from v1_live_exit_once import run_live_exit_once

KST = ZoneInfo("Asia/Seoul")


@dataclass
class FakeResult:
    payload: object


class FakeBroker:
    def __init__(self, *, expected_exchange: str = "KRX") -> None:
        self.expected_exchange = expected_exchange
        self.submit_calls: list[dict] = []
        self.read_snapshot_calls = 0
        self.pre_qty = 1
        self.post_qty = 0

    @staticmethod
    def _holdings(qty: int) -> dict:
        rows = []
        if qty > 0:
            rows.append({"종목번호": "A005930", "보유수량": str(qty)})
        return {"계좌평가잔고개별합산": rows}

    def read_snapshot(self):
        self.read_snapshot_calls += 1
        return {
            "account": {"계좌번호": "******3211"},
            "cash": {"주문가능금액": "1000000", "예수금": "1000000"},
            "holdings": self._holdings(self.pre_qty),
            "order_fill_status": {"return_code": 0, "return_msg": "ok"},
            "open_orders": {"미체결": []},
            "fills": {"체결": []},
        }

    def account_list(self):
        return FakeResult(payload={"계좌번호": "******3211"})

    def cash(self, *, basis: str = "estimated"):
        return FakeResult(payload={"주문가능금액": "1000000", "예수금": "1000000"})

    def order_fill_status(self):
        return FakeResult(payload={"return_code": 0, "return_msg": "ok"})

    def list_fills(self):
        return FakeResult(payload={"체결": []})

    def submit_order(self, **kwargs):
        self.submit_calls.append(dict(kwargs))
        assert kwargs["side"] == "sell"
        assert kwargs["symbol"] == "005930"
        assert kwargs["qty"] == 1
        assert kwargs["exchange"] == self.expected_exchange
        assert kwargs["order_type"] == "market"
        assert kwargs["confirm_live_write"] is True
        return FakeResult(payload={"return_code": 0, "주문번호": "0088888"})

    def holdings(self, *, basis: str = "total", exchange: str = "KRX"):
        assert exchange == self.expected_exchange
        # First NXT holdings read is pre-write; later one is post-write.
        if self.expected_exchange == "NXT" and not self.submit_calls:
            return FakeResult(payload=self._holdings(self.pre_qty))
        return FakeResult(payload=self._holdings(self.post_qty))

    def list_open_orders(self):
        return FakeResult(payload={"미체결": []})


def armed_cfg(v0_db: Path) -> ExecutionConfig:
    return ExecutionConfig(
        auto_mode="LIVE_SMALL", kill_switch=False, allow_live_orders=True,
        daily_arm_date=datetime.now(KST).strftime("%Y-%m-%d"),
        allowed_account="1234563211", allowed_symbols=frozenset({"005930"}),
        max_order_qty=1, state_db=v0_db, auth_env=Path("/tmp/not-used-auth.env"),
    )


def disarmed_cfg(v0_db: Path) -> ExecutionConfig:
    return ExecutionConfig(
        auto_mode="OFF", kill_switch=True, allow_live_orders=False,
        daily_arm_date="", allowed_account="1234563211",
        allowed_symbols=frozenset({"005930"}), max_order_qty=1,
        state_db=v0_db, auth_env=Path("/tmp/not-used-auth.env"),
    )


def reserve_due_plan(store: ExitPlanStore, plan_id: str) -> None:
    now = datetime.now(KST)
    store.reserve_plan(ExitPlan(
        plan_id=plan_id, symbol="005930", qty=1, entry_price=260000,
        stop_price=200000, take_profit_price=400000,
        timecut_at=now - timedelta(seconds=5),
    ))


def fake_quote(_broker, *, symbol: str, exchange: str = "KRX") -> QuoteSnapshot:
    return QuoteSnapshot(
        symbol=symbol, last_price=260000, captured_at=datetime.now(KST),
        payload={"현재가": "+00260000"}, exchange=exchange,
        query_code=symbol if exchange == "KRX" else f"{symbol}_NX",
    )


def main() -> int:
    print("===== V1 ONE-SHOT LIVE EXIT INTEGRATION FIXTURE =====")
    print("[SAFE] fake broker only; no Kiwoom API and no real order")

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        v1 = ExitPlanStore(root / "v1.sqlite3")
        v0 = StateStore(root / "v0.sqlite3")
        journal = ExitWriteJournal(root / "write.sqlite3")

        reserve_due_plan(v1, "V1-LIVE-ONCE-DISARM")
        broker = FakeBroker()
        try:
            run_live_exit_once(cfg=disarmed_cfg(root / "v0.sqlite3"), plan_id="V1-LIVE-ONCE-DISARM", v1=v1, v0=v0, journal=journal, broker=broker, quote_reader=fake_quote)
        except RuntimeError as exc:
            assert "실주문 차단" in str(exc)
        else:
            raise AssertionError("DISARM must block")
        assert broker.submit_calls == []
        print("[PASS] DISARM blocks before trigger mutation and broker write")

        plan_id = "V1-LIVE-ONCE-001"
        reserve_due_plan(v1, plan_id)
        broker = FakeBroker(expected_exchange="KRX")
        report = run_live_exit_once(cfg=armed_cfg(root / "v0.sqlite3"), plan_id=plan_id, v1=v1, v0=v0, journal=journal, broker=broker, quote_reader=fake_quote, exchange="KRX")
        assert report["action"] == "SELL_SUBMITTED"
        assert report["exchange"] == "KRX"
        assert len(broker.submit_calls) == 1
        assert v1.get_plan(plan_id)["status"] == "CLOSED"
        print("[PASS] KRX quote -> TIMECUT -> one SELL -> broker flat -> CLOSED")

        try:
            run_live_exit_once(cfg=armed_cfg(root / "v0.sqlite3"), plan_id=plan_id, v1=v1, v0=v0, journal=journal, broker=broker, quote_reader=fake_quote)
        except RuntimeError as exc:
            assert "unsupported plan status=CLOSED" in str(exc)
        else:
            raise AssertionError("CLOSED plan must block rerun")
        assert len(broker.submit_calls) == 1
        print("[PASS] CLOSED/restart path blocks duplicate SELL")

        no_trigger_id = "V1-LIVE-ONCE-NOTRIGGER"
        now = datetime.now(KST)
        v1.reserve_plan(ExitPlan(plan_id=no_trigger_id, symbol="005930", qty=1, entry_price=260000, stop_price=200000, take_profit_price=400000, timecut_at=now + timedelta(minutes=30)))
        broker2 = FakeBroker()
        report2 = run_live_exit_once(cfg=armed_cfg(root / "v0.sqlite3"), plan_id=no_trigger_id, v1=v1, v0=v0, journal=journal, broker=broker2, quote_reader=fake_quote)
        assert report2["action"] == "NO_TRIGGER" and broker2.submit_calls == []
        print("[PASS] NO_TRIGGER returns without broker write or write journal")

        nxt_id = "V1-LIVE-ONCE-NXT-001"
        reserve_due_plan(v1, nxt_id)
        nxt_broker = FakeBroker(expected_exchange="NXT")
        nxt_report = run_live_exit_once(cfg=armed_cfg(root / "v0.sqlite3"), plan_id=nxt_id, v1=v1, v0=v0, journal=journal, broker=nxt_broker, quote_reader=fake_quote, exchange="NXT")
        assert nxt_report["exchange"] == "NXT"
        assert nxt_report["quote"]["query_code"] == "005930_NX"
        assert nxt_broker.submit_calls[0]["exchange"] == "NXT"
        assert v1.get_plan(nxt_id)["status"] == "CLOSED"
        print("[PASS] NXT quote code 005930_NX -> NXT holding gate -> one NXT SELL -> CLOSED")

    print("[PASS] V1 KRX/NXT one-shot live exit integration fixture complete")
    print("[SAFE] no network or real order call occurred")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
