"""3211 GOLD->Stage4 source proof tests: no live orders or sheet writes."""
from datetime import datetime
from zoneinfo import ZoneInfo

from scalp3211_gold_signal_sheet_v1 import decision_fields,sheet_updates
from scalp3211_isolated_stage4_bridge_v1 import approve_isolated
KST=ZoneInfo("Asia/Seoul")
NOW=datetime(2026,10,21,12,30,tzinfo=KST)


def valid_packet():
    return {"trade_date":"2026-10-21","captured_at":NOW.isoformat(),
            "stages":{
              "stage0":{"status":"OK","value":{"next_operation_mode":"생활비모드"}},
              "stage1":{"status":"OK","source_at":"2026-10-21",
                "value":{"regime_code":"GREEN","allowed_strategy":"돌파",
                         "max_hold_stage":"ALLOWED"}},
              "stage2":{"status":"OK","source_at":"2026-10-21 12:25:00","value":{}},
              "rejuscha":{"status":"OK","value":{
                "action_status":"READY","executable_actions":["돌파"]}},
              "entry":{"status":"OK","value":{
                "action_status":"READY_CANDIDATE","target_symbol":"005930",
                "final_r":"0.5R"}},
              "stage4":{"status":"OK","value":{
                "account_alias":"test","account_suffix":"3211",
                "symbol_code":"005930","action_status":"READY",
                "final_approval":"주문 가능","stage_decision":"진입가능",
                "handoff_id":"3211-20261021-1","handoff_status":"READY",
                "sync_status":"READY","entry_price":200000,
                "stop_price":198000,"target1_price":202000,
                "qty":1,"invest_amount":200000,"expected_loss":2000,
                "final_r":"0.5R","approved_method":"돌파",
                "input_at":"2026-10-21T12:25:00+09:00"}}}}
def test():
    p=valid_packet()
    result=decision_fields(p,now=NOW)
    assert result["status"]=="READY",result
    values=[[] for _ in range(21)]
    mapping={3:"2026-10-21",4:"3211",5:"005930",
             6:"285,345",7:"285,345",8:"4,280",
             9:"2026-10-21 12:29:00",
             10:"BROKER_CAPITAL_VERIFIED_ONLY",11:"3211-20261021-1",
             12:"돌파",13:"200000",14:"198000",15:"202000",
             16:"1",17:"2000",18:"200000",19:"0.5R",
             20:"2026-10-21T12:25:00+09:00",21:"생활비모드",
             22:"READY",23:"주문 가능"}
    for k,v in mapping.items():
        values[k-3]=[v]
    owner={"owner":"SCALP3211_ISOLATED_BROKER_CAPITAL_V1",
           "status":"BROKER_CAPITAL_VERIFIED_ONLY","reason":[],
           "account_alias":"test","account_suffix":"3211",
           "symbol":"005930","max_order_qty":1,
           "estimated_assets_krw":285345,"orderable_cash_krw":285345,
           "risk_reference_krw_1p5pct":4280}
    approved=approve_isolated(packet=p,owner=owner,sheet_values=values,now=NOW)
    assert approved["status"]=="APPROVED_SOURCE_ONLY",approved
    assert approved["plan"]["planned_entry_price"]==200000
    assert approved["contract"]["target1_price"]==202000
    p["stages"]["stage4"]["value"]["account_alias"]="day"
    blocked=approve_isolated(packet=p,owner=owner,sheet_values=values,now=NOW)
    assert blocked["status"]!="APPROVED_SOURCE_ONLY"
    assert all(x["sheet"]=="3211_독립계산" for x in sheet_updates(
        decision_fields(p,now=NOW)))
    print("3211_SOURCE_ONLY_APPROVAL_AND_DAY_REJECTION_PASS")


if __name__=="__main__":
    test()
