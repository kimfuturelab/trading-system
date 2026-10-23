"""3211 standalone equity owner regression; synthetic broker receipts only."""
from scalp3211_capital_owner_v1 import capture_3211_capital_owner

def main():
    basic = dict(alias="test",expected_suffix="3211",status="PASS",
                 estimated_assets=285345,orderable_cash=285345,
                 cash_misoo=0,errors=[],positions=[])
    good = capture_3211_capital_owner(basic)
    assert good["status"] == "BROKER_CAPITAL_VERIFIED_ONLY", good
    assert good["estimated_assets_krw"] == 285345
    assert good["risk_reference_krw_1p5pct"] == 4280
    assert good["max_order_qty"] == 1
    assert good["leverage"] == 1
    assert good["order_allowed"] is False
    for changed in (
        {"alias":"day"}, {"expected_suffix":"4311"},
        {"status":"ERROR"}, {"errors":["API_FAILED"]},
        {"estimated_assets":-1}, {"orderable_cash":999999},
        {"cash_misoo":1}, {"positions":[{"symbol":"005930","qty_total":1}]}
    ):
        report = capture_3211_capital_owner({**basic,**changed})
        assert report["status"] == "BLOCK", (changed,report)
        assert report["order_allowed"] is False
    print("[PASS] 3211 isolated broker capital / 1-share SOR route / no leverage / fail closed")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
