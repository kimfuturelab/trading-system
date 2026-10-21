"""3211 original Stage4 source-only regression: no Google or Kiwoom writes."""
from scalp3211_stage4_store_v1 import review_stage4_for_3211


def run():
    trade_date="2026-10-21"
    facts=dict(daily_sheet=trade_date+"_빠른계산",
               quick_account_alias="test",symbol_code="005930",
               handoff_status="READY",sync_status="READY",
               entry_input_status="READY",
               input_at="2026-10-21T11:05:00+09:00",
               stage_decision="진입가능",quick_stage_decision="진입가능",
               final_r="0.50R",entry_price="200000",stop_price=198000,
               qty="1.0",invest_amount="200,000",quick_expected_loss=2000,
               handoff_id="TEST-3211-001")
    owner=dict(status="BROKER_CAPITAL_VERIFIED_ONLY",reason=[],
               account_alias="test",account_suffix="3211",symbol="005930",
               max_order_qty=1,orderable_cash_krw=285345,
               risk_reference_krw_1p5pct=4280)
    approval=[""]*29
    approval[27]="주문 가능"
    order=[""]*29
    order[1]="3211 시험계좌"
    order[2]="삼성전자"
    order[3]="신규매수"
    order[6],order[7],order[9],order[10]=200000,1,198000,202000
    def check(facts=facts,owner=owner,approval=approval,mode="생활비모드"):
        return review_stage4_for_3211(
            facts=facts,broker_capital=owner,approval=approval,
            order=order,trade_date=trade_date,operation_mode=mode)
    good=check()
    assert good["status"]=="APPROVED_SOURCE_ONLY",good
    assert good["plan"]["account_alias"]=="test"
    assert good["plan"]["planned_qty"]==1
    assert good["plan"]["expected_loss"]==2000
    assert good["exit"]["target1_price"]==202000
    assert check(facts={**facts,"quick_account_alias":"day"})["status"]=="BLOCK"
    assert check(owner={**owner,"orderable_cash_krw":None})["status"]=="BLOCK"
    assert check(owner={**owner,"risk_reference_krw_1p5pct":1500})["status"]=="BLOCK"
    assert check(mode=None)["status"]=="BLOCK"
    assert check(facts={**facts,"quick_expected_loss":"100"})["status"]=="BLOCK"
    approval[27]="주문 금지"
    assert check()["status"]=="BLOCK"
    print("[PASS] original Stage4 account=3211, risk, approval and mode consistency")


if __name__=="__main__":
    run()
