#!/usr/bin/env python3
from __future__ import annotations

from quote_reader import exchange_query_code, extract_last_price


def main() -> int:
    print("===== V1 NXT QUOTE CONTRACT FIXTURE =====")
    print("[SAFE] parser/contract only; no Kiwoom network and no order")

    assert exchange_query_code("005930", "KRX") == "005930"
    assert exchange_query_code("005930", "NXT") == "005930_NX"
    assert extract_last_price({"cur_prc": "+00260500"}) == 260500
    assert extract_last_price({"현재가": "-00260000"}) == 260000

    print("[PASS] KRX code=005930 / NXT code=005930_NX contract")
    print("[PASS] ka10001 cur_prc/current-price parser")
    print("[SAFE] no network or broker write occurred")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
