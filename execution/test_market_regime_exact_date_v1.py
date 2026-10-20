"""No network and no brokerage writes. Regression for tomorrow's prepublished row."""
from market_regime_exact_date_v1 import select_market_regime

def row(when, label, allowed_time, strategy, code):
    result = [""] * 40
    result[0], result[2], result[3], result[4], result[39] = (
        when, label, allowed_time, strategy, code)
    return result

def main():
    future = row("2026-10-21", "초약세", "장중예외", "사이드카·서킷", "RED")
    today = row("2026-10-20", "초약세", "장중예외", "사이드카·서킷", "RED")
    past = row("2026-10-19", "약세", "종배", "종가매매", "RED+")
    result = select_market_regime([future, today, past], "2026-10-20")
    assert result["status"] == "OK", result
    assert result["source_at"] == "2026-10-20"
    assert result["value"]["regime_code"] == "RED"
    assert result["value"]["max_hold"] == "기존 보유 청산만"
    assert select_market_regime([future], "2026-10-20")["status"] == "PENDING"
    assert select_market_regime([today,today], "2026-10-20")["status"] == "DATA_CONFLICT"
    assert select_market_regime([["bad"]], "2026-10-20")["status"] == "PENDING"
    assert select_market_regime([future,today], "2026-10-99")["status"] == "BLOCK"
    assert select_market_regime([future,today], "2026-10-21")["source_at"] == "2026-10-21"
    print("[PASS] exact session date selection / future date exclusion / duplicate and missing fail closed")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
