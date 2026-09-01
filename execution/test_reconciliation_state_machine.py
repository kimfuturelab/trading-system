#!/usr/bin/env python3
from __future__ import annotations

import tempfile
from pathlib import Path

from state_store import StateStore
from v0_engine import sync_open_intents


def manual_fill_snapshot() -> dict:
    """Fixture derived from the successful 2026-09-01 Samsung Electronics live roundtrip.

    No API call and no order is sent by this test.
    """
    return {
        "fills": {
            "체결": [
                {
                    "주문번호": "0013510",
                    "종목코드": "005930",
                    "주문수량": "0000000001",
                    "체결량": "0000000001",
                    "체결가": "0000259250",
                }
            ],
            "return_code": 0,
        },
        "order_fill_status": {
            "계좌별주문체결현황배열": [
                {
                    "주문번호": "0013510",
                    "종목번호": "A005930",
                    "주문수량": "0000000001",
                    "체결수량": "0000000001",
                    "체결단가": "0000259250",
                }
            ],
            "return_code": 0,
        },
        "open_orders": {"미체결": [], "return_code": 0},
        "holdings": {"계좌평가잔고개별합산": []},
    }


def open_order_snapshot() -> dict:
    return {
        "fills": {"체결": [], "return_code": 0},
        "order_fill_status": {"계좌별주문체결현황배열": [], "return_code": 0},
        "open_orders": {
            "미체결": [
                {
                    "주문번호": "0099999",
                    "종목코드": "005930",
                    "주문수량": "0000000001",
                    "미체결수량": "0000000001",
                }
            ],
            "return_code": 0,
        },
        "holdings": {"계좌평가잔고개별합산": []},
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="v0-reconcile-test-") as tmp:
        store = StateStore(Path(tmp) / "state.sqlite3")

        store.reserve_intent(
            intent_id="TEST-FILLED-001",
            account_masked="******3211",
            symbol="005930",
            side="BUY",
            qty=1,
            requested_price=None,
            mode="LIVE_SMALL",
        )
        store.update_intent(
            "TEST-FILLED-001",
            status="SUBMITTED",
            broker_order_no="0013510",
        )
        transitions = sync_open_intents(store, manual_fill_snapshot())
        row = store.get_intent("TEST-FILLED-001")
        assert row is not None
        assert row["status"] == "FILLED", row
        assert store.open_intents() == [], store.open_intents()
        print("[PASS] SUBMITTED -> FILLED from broker fill; intent becomes terminal")
        print(transitions)

        store.reserve_intent(
            intent_id="TEST-OPEN-001",
            account_masked="******3211",
            symbol="005930",
            side="BUY",
            qty=1,
            requested_price=None,
            mode="LIVE_SMALL",
        )
        store.update_intent(
            "TEST-OPEN-001",
            status="SUBMITTED",
            broker_order_no="0099999",
        )
        transitions = sync_open_intents(store, open_order_snapshot())
        row = store.get_intent("TEST-OPEN-001")
        assert row is not None
        assert row["status"] == "OPEN", row
        assert len(store.open_intents()) == 1, store.open_intents()
        print("[PASS] SUBMITTED -> OPEN when broker reports unfilled order; remains blocking")
        print(transitions)

    print("[SAFE] fixture-only test completed; no Kiwoom API and no order call occurred")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
