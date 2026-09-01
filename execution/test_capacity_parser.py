#!/usr/bin/env python3
from __future__ import annotations

from capacity import buy_capacity_errors, capacity_summary, maximum_orderable_amount


FIXTURE = {
    "증거금20%주문가능금액": "000000499167",
    "증거금20%주문가능수량": "0000000000",
    "증거금30%주문가능금액": "000000499167",
    "증거금30%주문가능수량": "0000000000",
    "증거금40%주문가능금액": "000000499167",
    "증거금40%주문가능수량": "0000000000",
    "증거금50%주문가능금액": "000000499167",
    "증거금50%주문가능수량": "0000000000",
    "증거금60%주문가능금액": "000000499167",
    "증거금60%주문가능수량": "0000000000",
    "증거금감면60%주문가능금": "000000499167",
    "증거금감면60%주문가능수": "0000000000",
    "증거금100%주문가능금액": "000000499167",
    "증거금100%주문가능수량": "0000000000",
    "전일재사용가능금액": "000000000000",
    "금일재사용가능금액": "000000258405",
    "예수금": "000000500011",
    "주문가능현금": "000000240761",
    "return_code": 0,
}


def main() -> int:
    maximum = maximum_orderable_amount(FIXTURE)
    assert maximum == 499_167, maximum

    summary = capacity_summary(FIXTURE)
    assert summary["maximum_orderable_amount"] == 499_167
    assert summary["orderable_cash_reference_only"] == 240_761
    assert summary["today_reusable_amount_reference_only"] == 258_405
    assert summary["deposit_reference_only"] == 500_011

    assert buy_capacity_errors(FIXTURE, qty=1, reference_price=259_000) == []
    assert buy_capacity_errors(FIXTURE, qty=2, reference_price=259_000) == [
        "MAX_ORDERABLE_AMOUNT_INSUFFICIENT(499167<518000)"
    ]

    direct = {"최대주문가능금액": "000000777777", "주문가능현금": "1"}
    assert maximum_orderable_amount(direct) == 777_777

    assert maximum_orderable_amount({"주문가능현금": "999999"}) is None

    print("===== V0 CAPACITY POLICY FIXTURE TEST =====")
    print("[PASS] maximum_orderable_amount=499167")
    print("[PASS] 주문가능현금=240761 is reference-only and does not block")
    print("[PASS] 1 x 259000 passes; 2 x 259000 blocks")
    print("[PASS] missing trusted max field fails closed")
    print("[SAFE] fixture-only; no Kiwoom API and no order call occurred")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
