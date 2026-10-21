"""3211 Stage4 approval -> original trade_plan_link_v1 SQLite plan.

No independent price model, new spreadsheet, or new broker order engine.
Existing Stage4 cells MUST explicitly select the 3211 test account.
Never infer 3211 approval from DAY/SWING rows.
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal, InvalidOperation
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scalp3211_capital_owner_v1 import capture_3211_capital_owner
from scalp3211_exit_owner_v1 import approved_exit_contract

HOME=Path.home()
ROOT=HOME/"trading-system-stage3"
KST=ZoneInfo("Asia/Seoul")


def positive_int(value):
    try:
        # Reject decimals, truncated numbers, boolean and string currencies
        if type(value) is bool or value is None:
            return None
        digits=str(value).replace(",","").replace("₩","").strip()
        exact=Decimal(digits)
        if not exact.is_finite() or exact!=exact.to_integral_value():
            return None
        number=int(exact)
        return number if number>0 else None
    except (TypeError, ValueError, InvalidOperation):
        return None


def review_stage4_for_3211(*, facts:dict, approval:list, order:list,
                           broker_capital:dict, trade_date:str,
                           operation_mode:str|None)->dict:
    """Pure validation. Never fabricates an entry, stop, target or approval."""
    failures=[]
    if facts.get("daily_sheet") != trade_date+"_빠른계산":
        failures.append("TRADE_DATE_SHEET_MISMATCH")
    if facts.get("quick_account_alias")!="test":
        failures.append("3211_ACCOUNT_NOT_SELECTED_IN_ORIGINAL_STAGE4")
    if facts.get("symbol_code")!="005930":
        failures.append("SAMSUNG_NOT_SELECTED")
    if (facts.get("handoff_status")!="READY" or facts.get("sync_status")!="READY"
            or facts.get("entry_input_status")!="READY"):
        failures.append("STAGE4_NOT_READY")
    timestamp=str(facts.get("input_at") or "")
    try:
        dt=datetime.fromisoformat(timestamp.replace("Z","+00:00"))
        if dt.tzinfo is None or dt.astimezone(KST).date().isoformat()!=trade_date:
            failures.append("STAGE4_INPUT_NOT_CURRENT_SESSION")
    except ValueError:
        failures.append("STAGE4_INPUT_TIME_INVALID")
    if facts.get("stage_decision") not in ("진입가능","축소진입"):
        failures.append("ORIGINAL_STAGE4_NOT_APPROVED")
    if facts.get("stage_decision")!=facts.get("quick_stage_decision"):
        failures.append("ORIGINAL_STAGE4_DECISION_NOT_MATCHED")
    if not str(facts.get("final_r") or "").strip():
        failures.append("ORIGINAL_FINAL_R_MISSING")
    if operation_mode not in ("생활비모드","복리모드"):
        failures.append("ORIGINAL_OPERATION_MODE_UNVERIFIED")
    price=positive_int(facts.get("entry_price"))
    stop=positive_int(facts.get("stop_price"))
    qty=positive_int(facts.get("qty"))
    invest=positive_int(facts.get("invest_amount"))
    loss=positive_int(facts.get("quick_expected_loss"))
    if (not all((price,stop,qty,invest,loss))
            or qty!=1 or not 0<stop<price
            or invest!=price or loss!=price-stop):
        failures.append("ONE_SHARE_STAGE4_PRICE_STOP_RISK_MISMATCH")
    handoff_id=str(facts.get("handoff_id") or "").strip()
    if not handoff_id or len(handoff_id)>120:
        failures.append("ORIGINAL_STAGE4_HANDOFF_ID_MISSING")
    if broker_capital.get("status")!="BROKER_CAPITAL_VERIFIED_ONLY" or broker_capital.get("reason"):
        failures.append("3211_BROKER_OWNER_NOT_VERIFIED")
    if (broker_capital.get("account_alias")!="test"
            or broker_capital.get("account_suffix")!="3211"
            or broker_capital.get("symbol")!="005930"
            or broker_capital.get("max_order_qty")!=1):
        failures.append("BROKER_ACCOUNT_SCOPE_MISMATCH")
    cash=broker_capital.get("orderable_cash_krw")
    limit=broker_capital.get("risk_reference_krw_1p5pct")
    if (price is None or loss is None
            or type(cash) is not int or type(limit) is not int
            or price>cash or loss>limit):
        failures.append("3211_CAPITAL_OR_STOP_RISK_LIMIT")
    row={"trade_date":trade_date,"account_alias":"test","symbol":"005930",
         "planned_qty":1,"planned_entry_price":price,"stop_price":stop,
         "expected_loss":loss,"stage_decision":facts.get("stage_decision"),
         "handoff_id":handoff_id}
    contract=approved_exit_contract(
        plan=row,trade_date=trade_date,
        quick_approval_row=approval,quick_order_row=order)
    if contract is None:
        failures.append("APPROVED_STAGE4_EXIT_CONTRACT_MISSING")
    if failures:
        return {"status":"BLOCK","reasons":failures,"plan":None,"exit":None}
    plan={
        "handoff_id":handoff_id,"created_at":timestamp,"trade_date":trade_date,
        "symbol":"005930","stock_name":"삼성전자","account_alias":"test",
        "planned_entry_price":price,"stop_price":stop,
        "planned_qty":1,"planned_invest_amount":invest,
        "expected_loss":loss,"final_r":str(facts["final_r"]),
        "stage_decision":facts["stage_decision"],
        "operation_mode":operation_mode,
    }
    return {"status":"APPROVED_SOURCE_ONLY","reasons":[],
            "plan":plan,"exit":contract}


def persist_approved_3211_stage4(*, today:str, broker_capital:dict|None=None)->dict:
    """Read authenticated owners and save one original Stage4 plan, never BUY."""
    for folder in (ROOT/"collector", ROOT/"fill-shadow-v1",
                   HOME/"trading-system/collector"):
        if str(folder) not in sys.path:
            sys.path.insert(0,str(folder))
    from stage4_p047_handoff import (
        session, read_result, get_values, current_operation_mode, STAGE4_ID
    )
    from trade_plan_link_v1 import save_plan, get_plan, DB
    if broker_capital is None:
        from account_snapshot_readonly import collect
        broker_capital=capture_3211_capital_owner(collect("test"))
    s=session()
    daily=today+"_빠른계산"
    facts=read_result(s,daily)
    # P047.read_result does not include the original DAILY_SHEET key.
    # Establish its identity from the authenticated ENTRY_연동 map, not
    # from the caller or an assumed current trading day.
    from stage4_p047_handoff import entry_map
    origin=entry_map(get_values(s, STAGE4_ID, "ENTRY_연동", "A1:C50"))
    facts["daily_sheet"]=str(origin.get("DAILY_SHEET") or "")
    approval=get_values(s,STAGE4_ID,daily,"A19:AC19")
    order=get_values(s,STAGE4_ID,daily,"A25:AC25")
    decision=review_stage4_for_3211(
        facts=facts,approval=approval[0] if approval else [],
        order=order[0] if order else [],
        broker_capital=broker_capital,trade_date=today,
        operation_mode=current_operation_mode())
    if decision["status"]!="APPROVED_SOURCE_ONLY":
        return {"status":"WAIT_APPROVAL","reasons":decision["reasons"],
                "orders_sent":False}
    plan=decision["plan"]
    import sqlite3
    with sqlite3.connect(f"file:{DB}?mode=ro",uri=True,timeout=5) as c:
        existing=get_plan(c,plan["handoff_id"])
    if existing is not None:
        for key in ("trade_date","account_alias","symbol","planned_qty",
                    "planned_entry_price","stop_price","expected_loss","final_r",
                    "stage_decision"):
            if str(existing.get(key))!=str(plan.get(key)):
                return {"status":"BLOCK_CONFLICTING_STAGE4_ID","orders_sent":False}
        return {"status":"ALREADY_PERSISTED","handoff_id":plan["handoff_id"],
                "orders_sent":False}
    saved=save_plan(plan)
    if any(str(saved.get(key))!=str(plan.get(key))
           for key in ("trade_date","account_alias","symbol","planned_qty",
                       "planned_entry_price","stop_price","expected_loss","final_r")):
        raise RuntimeError("3211_STAGE4_READBACK_MISMATCH")
    return {"status":"STAGE4_PLAN_PERSISTED","handoff_id":plan["handoff_id"],
            "orders_sent":False}
