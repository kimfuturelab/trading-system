#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import dataclass

from broker_cli import KiwoomCliBroker
from sell_handoff import prepare_sell_handoff
from sell_preview import preview_sell_handoff


@dataclass
class FakeResult:
    text: str
    payload: object | None = None


class SpyBroker:
    def __init__(self) -> None:
        self.preview_calls: list[dict] = []
        self.submit_calls: list[dict] = []

    def preview_order(self, **kwargs):
        self.preview_calls.append(dict(kwargs))
        return FakeResult(text="PREVIEW ONLY")

    def submit_order(self, **kwargs):
        self.submit_calls.append(dict(kwargs))
        raise AssertionError("submit_order must never be called in Shadow preview")


def holdings_snapshot(qty: int) -> dict:
    rows = []
    if qty > 0:
        rows.append({"종목번호": "A005930", "보유수량": str(qty)})
    return {
        "holdings": {"계좌평가잔고개별합산": rows},
        "open_orders": {"미체결": []},
    }


def triggered_plan(remaining_qty: int = 1) -> dict:
    return {
        "plan_id": "V1-PREVIEW-FIXTURE-001",
        "symbol": "005930",
        "qty": remaining_qty,
        "remaining_qty": remaining_qty,
        "status": "EXIT_TRIGGERED",
        "trigger_reason": "TIMECUT",
        "exit_intent_id": "V1-PREVIEW-FIXTURE-001-EXIT",
    }


def main() -> int:
    print("===== V1 INTEGRATED SHADOW SELL PREVIEW FIXTURE =====")
    print("[SAFE] fixture-only: no Kiwoom API, no broker write, no --confirm")

    plan = triggered_plan(1)
    snapshot = holdings_snapshot(1)
    handoff = prepare_sell_handoff(plan, snapshot, local_open_intent_count=0)
    broker = SpyBroker()
    result = preview_sell_handoff(broker, handoff)

    assert result.text == "PREVIEW ONLY"
    assert len(broker.preview_calls) == 1
    assert len(broker.submit_calls) == 0
    call = broker.preview_calls[0]
    assert call == {
        "side": "sell",
        "symbol": "005930",
        "qty": 1,
        "exchange": "KRX",
        "order_type": "market",
        "price": None,
    }
    print("[PASS] EXIT_TRIGGERED + exact holding=1 -> one broker PREVIEW call")
    print("[PASS] submit_order call count = 0")

    # Verify the real Kiwoom CLI preview argument builder omits --confirm.
    args = KiwoomCliBroker._order_args(
        side="sell",
        exchange="KRX",
        symbol="005930",
        qty=1,
        order_type="market",
        price=None,
        mode="real",
        confirm=False,
    )
    assert "--confirm" not in args
    assert args[:3] == ["domestic", "orders", "sell"]
    print("[PASS] Kiwoom SELL preview args contain no --confirm")

    # Flat broker truth must block before preview.
    flat_broker = SpyBroker()
    try:
        flat_handoff = prepare_sell_handoff(
            triggered_plan(1), holdings_snapshot(0), local_open_intent_count=0
        )
        preview_sell_handoff(flat_broker, flat_handoff)
    except RuntimeError as exc:
        assert "NO_BROKER_HOLDING_TO_EXIT" in str(exc)
    else:
        raise AssertionError("flat broker state must fail closed")
    assert len(flat_broker.preview_calls) == 0
    assert len(flat_broker.submit_calls) == 0
    print("[PASS] holding=0 -> blocked before PREVIEW; no broker method called")

    print("[PASS] V1 integrated Shadow SELL preview fixture complete")
    print("[SAFE] no network or order call occurred")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
