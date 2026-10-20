#!/usr/bin/env python3
"""SOR 3211 EXIT fixture: synthetic Kiwoom only; NEVER sends broker orders."""
from __future__ import annotations
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from exit_plan import ExitPlan, ExitPlanStore
from exit_write_journal import ExitWriteJournal
from quote_reader import QuoteSnapshot, exchange_query_code
from state_store import StateStore
from test_v1_live_exit_once_fixture import FakeBroker, armed_cfg, disarmed_cfg
from v1_live_exit_once import run_live_exit_once

KST = ZoneInfo("Asia/Seoul")

class SorBroker(FakeBroker):
    def __init__(self):
        super().__init__(exchange="SOR")
    # SOR is an execution route, not a valid account holdings exchange enum.
    def holdings(self, *, basis: str = "total", exchange: str = "KRX"):
        assert exchange == "KRX"
        return self._fake_holdings_result()
    def _fake_holdings_result(self):
        from test_v1_live_exit_once_fixture import FakeResult
        return FakeResult(payload=self._holdings(self.post_qty))

def due(store, plan_id):
    now = datetime.now(KST)
    store.reserve_plan(ExitPlan(
        plan_id=plan_id, symbol="005930", qty=1,
        entry_price=260000, stop_price=200000,
        take_profit_price=400000, timecut_at=now-timedelta(seconds=1)
    ))

def quote(_broker, *, symbol, exchange="SOR"):
    assert (symbol,exchange) == ("005930","SOR")
    return QuoteSnapshot(symbol=symbol, last_price=260000,
        captured_at=datetime.now(KST), payload={"cur_prc":"+260000"},
        exchange="SOR", query_code=exchange_query_code(symbol,exchange))

def main():
    print("[SAFE] Synthetic SOR EXIT fixture. No real broker or orders.")
    assert exchange_query_code("005930","SOR") == "005930_AL"
    with tempfile.TemporaryDirectory(prefix="sor3211-test-") as td:
        root = Path(td)
        store = ExitPlanStore(root/"v1.sqlite3")
        v0 = StateStore(root/"v0.sqlite3")
        journal = ExitWriteJournal(root/"write.sqlite3")
        due(store, "SOR-3211-DISARM")
        broker = SorBroker()
        try:
            run_live_exit_once(cfg=disarmed_cfg(root/"v0.sqlite3"),
                plan_id="SOR-3211-DISARM",v1=store,v0=v0,journal=journal,
                broker=broker,quote_reader=quote,exchange="SOR")
            raise AssertionError("DISARM unexpectedly passed")
        except RuntimeError as exc:
            assert "실주문 차단" in str(exc)
        assert not broker.submit_calls
        due(store, "SOR-3211-DUE")
        broker2 = SorBroker()
        result = run_live_exit_once(cfg=armed_cfg(root/"v0.sqlite3"),
            plan_id="SOR-3211-DUE",v1=store,v0=v0,journal=journal,
            broker=broker2,quote_reader=quote,exchange="SOR")
        assert result["action"] == "SELL_SUBMITTED"
        assert result["order_route"]["policy"] == "SOR_AGGRESSIVE_LIMIT_2TICKS"
        assert (result["order_route"]["order_type"],result["order_route"]["price"]) == ("limit","259000")
        assert result["quote"]["query_code"] == "005930_AL"
        assert store.get_plan("SOR-3211-DUE")["status"] == "CLOSED"
        assert len(broker2.submit_calls) == 1
        try:
            run_live_exit_once(cfg=armed_cfg(root/"v0.sqlite3"),
                plan_id="SOR-3211-DUE",v1=store,v0=v0,
                journal=journal,broker=broker2,quote_reader=quote,exchange="SOR")
            raise AssertionError("duplicate SELL unexpectedly passed")
        except RuntimeError as exc:
            assert "unsupported plan status=CLOSED" in str(exc)
        assert len(broker2.submit_calls) == 1
    print("[PASS] SOR 3211 STOP/TIMECUT limit SELL, reconciled CLOSED, duplicate blocked")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
