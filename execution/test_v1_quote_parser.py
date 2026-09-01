#!/usr/bin/env python3
from __future__ import annotations

from quote_reader import QuotePriceError, extract_last_price


def expect_error(payload: object) -> None:
    try:
        extract_last_price(payload)
    except QuotePriceError:
        return
    raise AssertionError(f"expected QuotePriceError for payload={payload!r}")


def main() -> int:
    print("===== V1 QUOTE PARSER FIXTURE TEST =====")
    print("[SAFE] fixture-only: no Kiwoom API and no broker write")

    assert extract_last_price({"현재가": "+000259250", "return_code": 0}) == 259250
    print("[PASS] Korean 현재가 + sign/zero-padding -> 259250")

    assert extract_last_price({"cur_prc": "-000259250", "return_code": 0}) == 259250
    print("[PASS] cur_prc - sign/zero-padding -> absolute price 259250")

    assert extract_last_price({"wrapper": {"현재가": "259250"}}) == 259250
    print("[PASS] nested payload current price")

    expect_error({"종목명": "삼성전자", "return_code": 0})
    print("[PASS] missing current-price field fails closed")

    expect_error({"현재가": "259250", "cur_prc": "259500"})
    print("[PASS] conflicting current-price fields fail closed")

    expect_error({"현재가": "0"})
    print("[PASS] zero/invalid price fails closed")

    print("[PASS] V1 quote parser fixture suite complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
