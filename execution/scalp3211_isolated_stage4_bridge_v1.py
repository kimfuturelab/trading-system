"""One authoritative 3211 handoff: verified GOLD -> isolated Stage4 sheet -> existing plan DB.

Sheet is only a mirrored display/readback; never an independent approval.
Neither this module nor its imports submits Kiwoom orders.
"""
from __future__ import annotations
import sqlite3
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from scalp3211_gold_signal_sheet_v1 import decision_fields, SHEET, KST
from scalp3211_switch_v1 import module_modes_for_capital
from scalp3211_capital_owner_v1 import capture_3211_capital_owner

HOME=Path.home()
ROOT=HOME/"trading-system-stage3"
PACKET=ROOT/"runtime/decision_packet_v1_latest.json"


def exact_int(value):
    try:
        if value is None or type(value) is bool:
            return None
        v=Decimal(str(value).replace(",","").replace("₩","").strip())
        if not v.is_finite() or v!=v.to_integral_value():
            return None
        return int(v)
    except (InvalidOperation,ValueError,TypeError):
        return None


def approve_isolated(*, packet:dict, owner:dict, sheet_values:list,
                     now:datetime)->dict:
    """Match every sensitive sheet field to BOTH original GOLD and 3211 broker."""
    result=decision_fields(packet,now=now,
                           modules=module_modes_for_capital(owner))
    reasons=list(result["reasons"])
    warnings=list(result.get("warnings",[]))
    modes=module_modes_for_capital(owner)
    if result["status"]!="READY":
        return {"status":"WAIT_ORIGINAL_GOLD","reasons":reasons}
    data=result["values"]
    # A one-column read from B3:B23. No missing rows allowed in approved state.
    def get(row):
        i=row-3
        if i<0 or i>=len(sheet_values) or not sheet_values[i]:
            return None
        return sheet_values[i][0]
    def same(value,expected,why,module=None):
        if value!=expected:
            (warnings if module and modes[module]=="OFF" else reasons).append(why)
    same(str(get(3)),data["trade_date"],"TRADE_DATE_SHEET_MISMATCH")
    same(str(get(4)),"3211","ISOLATED_ACCOUNT_NOT_3211")
    same(str(get(5)),"005930","ISOLATED_SYMBOL_NOT_SAMSUNG")
    expected_owner=(owner.get("status")=="BROKER_CAPITAL_VERIFIED_ONLY"
                    and not owner.get("reason")
                    and owner.get("account_alias")=="test"
                    and owner.get("account_suffix")=="3211"
                    and owner.get("symbol")=="005930"
                    and owner.get("max_order_qty")==1)
    if not expected_owner:
        reasons.append("3211_BROKER_OWNER_NOT_VERIFIED")
    cash=owner.get("orderable_cash_krw")
    equity=owner.get("estimated_assets_krw")
    risk=owner.get("risk_reference_krw_1p5pct")
    for row,expected,name in ((6,equity,"EQUITY"),(7,cash,"CASH"),(8,risk,"RISK")):
        if type(expected) is not int or exact_int(get(row))!=expected:
            reasons.append("3211_BROKER_"+name+"_SHEET_MISMATCH")
    same(str(get(10)),"BROKER_CAPITAL_VERIFIED_ONLY",
         "BROKER_SHEET_SOURCE_NOT_FRESH")
    try:
        stamp=datetime.fromisoformat(str(get(9)).replace(" ","T"))
        if stamp.tzinfo is None:
            stamp=stamp.replace(tzinfo=KST)
        elapsed=(now.astimezone(KST)-stamp.astimezone(KST)).total_seconds()
        if not (0<=elapsed<420):
            reasons.append("3211_BROKER_RECEIPT_STALE")
    except (ValueError,TypeError):
        reasons.append("3211_BROKER_RECEIPT_TIME_MISSING")
    for row,key in ((11,"handoff_id"),(12,"method"),(19,"final_r"),
                    (20,"input_at"),(21,"mode")):
        if str(get(row) or "")!=str(data[key]):
            reasons.append("GOLD_SHEET_"+key.upper()+"_MISMATCH")
    for row,key in ((13,"entry"),(14,"stop"),(15,"target1")):
        if exact_int(get(row))!=data[key]:
            reasons.append("GOLD_SHEET_"+key.upper()+"_MISMATCH")
    price,stop,target=data["entry"],data["stop"],data["target1"]
    if (exact_int(get(16))!=1 or exact_int(get(17))!=price-stop or
            exact_int(get(18))!=price):
        reasons.append("ISOLATED_STAGE4_QUANTITY_LOSS_OR_COST_MISMATCH")
    same(str(get(22)),"READY","GOLD_STAGE4_INTERSECTION_NOT_READY","stage4")
    same(str(get(23)),"주문 가능","ISOLATED_FINAL_APPROVAL_NOT_GRANTED","stage4")
    if (type(cash) is not int or type(risk) is not int or
            price>cash or price-stop>risk or price-stop<=0):
        reasons.append("3211_INDEPENDENT_CAPITAL_OR_RISK_LIMIT")
    if reasons:
        return {"status":"WAIT_ORIGINAL_OR_SHEET", "reasons":reasons}
    handoff=data["handoff_id"]
    plan={"handoff_id":handoff,"created_at":data["input_at"],
          "trade_date":data["trade_date"],"symbol":"005930",
          "stock_name":"삼성전자","account_alias":"test",
          "planned_entry_price":price,"stop_price":stop,
          "planned_qty":1,"planned_invest_amount":price,
          "expected_loss":price-stop,"final_r":data["final_r"],
          "stage_decision":"진입가능","operation_mode":data["mode"]}
    # The ORIGINAL Stage4 decision must be used, including reduction.
    plan["stage_decision"]=packet["stages"]["stage4"]["value"]["stage_decision"]
    contract={"source":"STAGE4_APPROVED","handoff_id":handoff,
              "trade_date":data["trade_date"],"stop_price":stop,
              "target1_price":target,"max_loss_krw":price-stop,
              "timecut_policy":"GOLD_DAY_24_COMPLETE_5M_BARS",
              "account_alias":"test","symbol":"005930","execution_qty":1}
    return {"status":"APPROVED_SOURCE_ONLY", "reasons":[],
            "warnings":warnings,"plan":plan,"contract":contract}


def inspect_live(*, today:str, owner:dict|None=None)->dict:
    import json
    for p in (ROOT/"collector",ROOT/"fill-shadow-v1",
              HOME/"trading-system/collector"):
        if str(p) not in sys.path:
            sys.path.insert(0,str(p))
    from stage4_p047_handoff import session,get_values,STAGE4_ID
    from account_snapshot_readonly import collect
    if owner is None:
        owner=capture_3211_capital_owner(collect("test"))
    try:
        packet=json.loads(PACKET.read_text(encoding="utf-8"))
    except (OSError,ValueError):
        return {"status":"WAIT_ORIGINAL_GOLD","reasons":["DECISION_PACKET_UNAVAILABLE"]}
    s=session()
    values=get_values(s,STAGE4_ID,SHEET,"B3:B23")
    now=datetime.now(KST)
    if today!=now.date().isoformat():
        return {"status":"WAIT_TRADE_DATE","reasons":["TODAY_NOT_MATCHED"]}
    return approve_isolated(packet=packet,owner=owner,
                            sheet_values=values,now=now)


def persist_live(*, today:str, broker_capital:dict|None=None)->dict:
    result=inspect_live(today=today,owner=broker_capital)
    if result["status"]!="APPROVED_SOURCE_ONLY":
        return {"status":"WAIT_APPROVAL","reasons":result.get("reasons",[]),
                "orders_sent":False}
    from trade_plan_link_v1 import DB,get_plan,save_plan
    plan=result["plan"]
    with sqlite3.connect(f"file:{DB}?mode=ro",uri=True,timeout=5) as c:
        existing=get_plan(c,plan["handoff_id"])
    compare=("trade_date","account_alias","symbol","planned_qty",
             "planned_entry_price","stop_price","expected_loss",
             "final_r","stage_decision","operation_mode")
    if existing:
        if any(str(existing.get(k))!=str(plan.get(k)) for k in compare):
            return {"status":"BLOCK_CONFLICTING_HANDOFF_ID","orders_sent":False}
        return {"status":"ALREADY_PERSISTED","handoff_id":plan["handoff_id"],
                "orders_sent":False}
    saved=save_plan(plan)
    if any(str(saved.get(k))!=str(plan.get(k)) for k in compare):
        raise RuntimeError("ISOLATED_3211_PLAN_READBACK_MISMATCH")
    return {"status":"PLAN_PERSISTED","handoff_id":plan["handoff_id"],
            "orders_sent":False}
