#!/usr/bin/env python3
from __future__ import annotations

import tempfile
from pathlib import Path

from safety import ExecutionConfig
from state_store import DuplicateIntentError, StateStore
from v0_engine import (
    holding_qty,
    reconciliation_report,
    semantic_order_errors,
    sync_open_intents,
)

SYMBOL = "005930"
ACCOUNT_FULL = "00003211"
ACCOUNT_MASKED = "******3211"
BUY_ORDER_NO = "FBUY001"
SELL_ORDER_NO = "FSELL01"


def make_cfg(db_path: Path) -> ExecutionConfig:
    return ExecutionConfig(
        auto_mode="PAPER",
        kill_switch=True,
        allow_live_orders=False,
        daily_arm_date="",
        allowed_account=ACCOUNT_FULL,
        allowed_symbols=frozenset({SYMBOL}),
        max_order_qty=1,
        state_db=db_path,
        auth_env=Path("/dev/null"),
    )


def snapshot(*, holding: int, fills: list[dict], opens: list[dict] | None = None) -> dict:
    holding_rows = []
    if holding:
        holding_rows.append(
            {
                "종목번호": f"A{SYMBOL}",
                "종목명": "삼성전자",
                "보유수량": f"{holding:010d}",
                "매매가능수량": f"{holding:010d}",
            }
        )
    return {
        "account": {
            "계좌번호": ACCOUNT_MASKED,
            "return_code": 0,
        },
        "cash": {
            "주문가능금액": "000000000500000",
            "예수금": "000000000500000",
            "return_code": 0,
        },
        "holdings": {
            "계좌평가잔고개별합산": holding_rows,
            "return_code": 0,
        },
        "open_orders": {
            "미체결": list(opens or []),
            "return_code": 0,
        },
        "fills": {
            "체결": fills,
            "return_code": 0,
        },
        "order_fill_status": {
            "계좌별주문체결현황배열": fills,
            "return_code": 0,
            "return_msg": "fixture",
        },
    }


def fill_row(order_no: str, *, side: str, qty: int = 1, price: int = 259000) -> dict:
    return {
        "주문번호": order_no,
        "종목번호": f"A{SYMBOL}",
        "종목명": "삼성전자",
        "주문유형구분": "현금매수" if side == "BUY" else "현금매도",
        "주문수량": f"{qty:010d}",
        "체결수량": f"{qty:010d}",
        "체결단가": f"{price:010d}",
    }


def assert_gate_passes(cfg: ExecutionConfig, store: StateStore, snap: dict, label: str) -> None:
    report = reconciliation_report(cfg, store, snap)
    assert report["reconciled_for_new_write"], (label, report)
    assert report["broker_open_order_count"] == 0, (label, report)
    assert report["local_open_intent_count"] == 0, (label, report)


def main() -> int:
    print("===== V0 FULL ROUNDTRIP FIXTURE TEST =====")
    print("[SAFE] fixture-only: no Kiwoom API, no broker write, no --confirm")

    with tempfile.TemporaryDirectory(prefix="trading-v0-roundtrip-") as tmp:
        db_path = Path(tmp) / "state.sqlite3"
        cfg = make_cfg(db_path)
        store = StateStore(db_path)

        # 0) Flat baseline must reconcile.
        flat0 = snapshot(holding=0, fills=[])
        assert holding_qty(flat0, SYMBOL) == 0
        assert_gate_passes(cfg, store, flat0, "baseline")
        print("[PASS] baseline: holding=0, open_orders=0, open_intents=0")

        # 1) BUY intent is persisted before the hypothetical broker write.
        buy_intent = "FIXTURE-BUY-001"
        store.reserve_intent(
            intent_id=buy_intent,
            account_masked=ACCOUNT_MASKED,
            symbol=SYMBOL,
            side="BUY",
            qty=1,
            requested_price=None,
            mode="PAPER",
        )
        store.update_intent(
            buy_intent,
            status="SUBMITTED",
            broker_order_no=BUY_ORDER_NO,
            raw_response={"주문번호": BUY_ORDER_NO},
        )
        assert len(store.open_intents()) == 1
        print("[PASS] BUY intent persisted before fill")

        # 2) Broker reports full BUY fill and holding becomes 1.
        buy_fill = fill_row(BUY_ORDER_NO, side="BUY", price=259250)
        after_buy = snapshot(holding=1, fills=[buy_fill])
        transitions = sync_open_intents(store, after_buy)
        assert any(t.get("to") == "FILLED" for t in transitions), transitions
        assert holding_qty(after_buy, SYMBOL) == 1
        assert not store.open_intents(), store.open_intents()
        assert_gate_passes(cfg, store, after_buy, "after_buy_fill")
        assert semantic_order_errors(after_buy, side="SELL", symbol=SYMBOL, qty=1) == []
        assert "V0_BUY_WOULD_STACK_EXISTING_POSITION" in semantic_order_errors(
            after_buy, side="BUY", symbol=SYMBOL, qty=1
        )
        print("[PASS] BUY SUBMITTED -> FILLED; holding=1; duplicate stacking BUY blocked")

        # 3) Restart simulation: reopen the same DB and verify no phantom open intent.
        store = StateStore(db_path)
        assert not store.open_intents(), store.open_intents()
        print("[PASS] restart recovery: FILLED BUY remains terminal after DB reopen")

        # 4) SELL intent, then broker full fill and holding returns to 0.
        sell_intent = "FIXTURE-SELL-001"
        store.reserve_intent(
            intent_id=sell_intent,
            account_masked=ACCOUNT_MASKED,
            symbol=SYMBOL,
            side="SELL",
            qty=1,
            requested_price=None,
            mode="PAPER",
        )
        store.update_intent(
            sell_intent,
            status="SUBMITTED",
            broker_order_no=SELL_ORDER_NO,
            raw_response={"주문번호": SELL_ORDER_NO},
        )
        assert len(store.open_intents()) == 1

        sell_fill = fill_row(SELL_ORDER_NO, side="SELL", price=259000)
        after_sell = snapshot(holding=0, fills=[buy_fill, sell_fill])
        transitions = sync_open_intents(store, after_sell)
        assert any(
            t.get("intent_id") == sell_intent and t.get("to") == "FILLED"
            for t in transitions
        ), transitions
        assert holding_qty(after_sell, SYMBOL) == 0
        assert not store.open_intents(), store.open_intents()
        assert_gate_passes(cfg, store, after_sell, "after_sell_fill")
        print("[PASS] SELL SUBMITTED -> FILLED; final holding=0; gate clean")

        # 5) Duplicate intent must remain impossible even after terminal fill/restart.
        try:
            store.reserve_intent(
                intent_id=buy_intent,
                account_masked=ACCOUNT_MASKED,
                symbol=SYMBOL,
                side="BUY",
                qty=1,
                requested_price=None,
                mode="PAPER",
            )
        except DuplicateIntentError:
            print("[PASS] duplicate intent_id still blocked after completed roundtrip")
        else:
            raise AssertionError("duplicate intent_id was unexpectedly accepted")

        all_intents = store.list_intents()
        statuses = {row["intent_id"]: row["status"] for row in all_intents}
        assert statuses[buy_intent] == "FILLED", statuses
        assert statuses[sell_intent] == "FILLED", statuses

    print("[PASS] FULL FIXTURE ROUNDTRIP: BUY -> FILL -> holding 1 -> restart -> SELL -> FILL -> holding 0")
    print("[SAFE] no network or order call occurred")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
