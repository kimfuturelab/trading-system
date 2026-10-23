"""Mirror verified GOLD Stage4 evidence to 3211_독립계산 (display only).

The original GOLD decision packet remains authoritative. Never derive a 3211
approval from DAY/SWING, a strategy list, a simulated quote, or a sheet value.
No broker orders, new trading rules or DAY/SWING writes.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo
from scalp3211_switch_v1 import read_entry_control, MODULES

KST=ZoneInfo("Asia/Seoul")
HOME=Path.home()
PACKET=HOME/"trading-system-stage3/runtime/decision_packet_v1_latest.json"
SHEET="3211_독립계산"


def _integer(value):
    # The original GOLD producer's num() serializes every numeric value as
    # float (e.g. 1.0 share). Accept only mathematically exact positive
    # integers; never truncate or round a fractional quantity or price.
    if value is None or isinstance(value, bool):
        return None
    try:
        d = Decimal(str(value).replace(",", "").replace("₩", "").strip())
        if d.is_finite() and d > 0 and d == d.to_integral_value():
            return int(d)
    except (InvalidOperation, ValueError, TypeError):
        pass
    return None


def stage0_independent_of_disabled_stage5(stage0: dict, *, today: str) -> bool:
    """An OFF weekly module cannot by itself block an otherwise verified 0-gate.

    This never approves an incomplete self-check, missing account verification,
    old source date, or an explicit 0-gate block. The shared GOLD producer
    remains unchanged; this applies only to the isolated 3211 viewer.
    """
    if stage0.get("status") == "OK":
        return True
    reasons = {x for x in str(stage0.get("reason") or "").split("|") if x}
    value = stage0.get("value") or {}
    return (
        reasons == {"STAGE5_NOT_OK"}
        and stage0.get("source_at") == today
        and value.get("self_gate") == "통과"
        and value.get("mode_gate") == "통과"
        and value.get("capital_gate") == "통과"
        and value.get("account_gate") == "통과"
        and value.get("start_decision") == "허용"
        and value.get("active_account") not in (None, "", "대기")
        and value.get("next_operation_mode") in ("생활비모드", "복리모드")
    )


def decision_fields(packet:dict, *, now:datetime, modules:dict|None=None)->dict:
    switches = read_entry_control()["modules"] if modules is None else {
        k: modules.get(k, "ON") for k in MODULES}
    problems=[]
    warnings=[]
    def require(condition,reason,module=None):
        if not condition:
            (warnings if module and switches[module]=="OFF" else problems).append(reason)
    today=now.astimezone(KST).date().isoformat()
    trade_date=packet.get("trade_date")
    require(trade_date==today,"GOLD_SESSION_DATE_MISMATCH")
    try:
        created=datetime.fromisoformat(packet["captured_at"])
        require(created.tzinfo is not None and created.utcoffset() is not None,
                "GOLD_CAPTURE_TIME_NOT_AWARE")
        if created.tzinfo:
            elapsed=(now.astimezone(KST)-created.astimezone(KST)).total_seconds()
            require(0<=elapsed<=180,"GOLD_PACKET_STALE")
    except (ValueError,TypeError,KeyError):
        require(False,"GOLD_CAPTURE_TIME_MISSING")
    stages=packet.get("stages") or {}
    for stage in ("stage0","stage1","stage2","entry","stage4"):
        original = stages.get(stage) or {}
        verified = original.get("status") == "OK"
        if stage == "stage0" and not verified and switches["stage5"] == "OFF":
            verified = stage0_independent_of_disabled_stage5(original, today=today)
            if verified:
                warnings.append("STAGE5_DISABLED_STAGE0_OTHER_GATES_VERIFIED")
        require(verified,stage.upper()+"_NOT_OK",stage)
    for axis in ("material","supply","chart"):
        value=((stages.get("rejuscha") or {}).get("value") or {}).get(axis) or {}
        if "status" in value:
            require(value["status"]=="OK",axis.upper()+"_NOT_OK",axis)
    if (stages.get("rejuscha") or {}).get("status")!="OK":
        require(False,"REJUSCHA_NOT_OK",
                "material" if any(switches[x]=="OFF" for x in ("material","supply","chart")) else None)
    require((stages.get("stage1") or {}).get("source_at")==today,
            "REGIME_SOURCE_NOT_CURRENT_SESSION","stage1")
    require(str((stages.get("stage2") or {}).get("source_at") or "").startswith(today),
            "RISK_STAGE2_SOURCE_NOT_CURRENT_SESSION","stage2")
    r=(stages.get("stage1") or {}).get("value") or {}
    v=(stages.get("stage4") or {}).get("value") or {}
    entry=(stages.get("entry") or {}).get("value") or {}
    supply=(stages.get("rejuscha") or {}).get("value") or {}
    require(v.get("account_alias")=="test" and
            str(v.get("account_suffix") or "")=="3211",
            "GOLD_3211_ACCOUNT_PROOF_MISSING")
    require(v.get("symbol_code")=="005930" and
            entry.get("target_symbol")=="005930",
            "GOLD_SAMSUNG_SYMBOL_PROOF_MISSING",
            "entry" if v.get("symbol_code")=="005930" else None)
    require(v.get("action_status")=="READY" and
            v.get("final_approval") in ("주문 가능","매수 승인"),
            "STAGE4_FINAL_APPROVAL_MISSING","stage4")
    require(v.get("stage_decision") in ("진입가능","축소진입"),
            "STAGE4_ORIGINAL_DECISION_MISSING","stage4")
    require(entry.get("action_status")=="READY_CANDIDATE",
            "GOLD_ENTRY_BLOCKED","entry")
    require(supply.get("action_status")=="READY",
            "GOLD_REJUSCHA_BLOCKED",
            "material" if any(switches[x]=="OFF" for x in ("material","supply","chart")) else None)
    require(v.get("handoff_id") and v.get("handoff_status")=="READY"
            and v.get("sync_status")=="READY",
            "STAGE4_REAL_HANDOFF_MISSING")
    price=_integer(v.get("entry_price"))
    stop=_integer(v.get("stop_price"))
    target=_integer(v.get("target1_price"))
    qty=_integer(v.get("qty"))
    require(bool(price and stop and target and stop<price<target
                 and qty==1 and _integer(v.get("invest_amount"))==price
                 and _integer(v.get("expected_loss"))==price-stop),
            "STAGE4_PRICE_STOP_TARGET_OR_LOSS_INVALID")
    require(entry.get("final_r")==v.get("final_r") and
            isinstance(v.get("final_r"),str) and bool(v.get("final_r")),
            "STAGE4_R_PROVENANCE_MISMATCH","stage2")
    method=v.get("approved_method")
    require(isinstance(method,str) and bool(method) and
            method in (supply.get("executable_actions") or []) and
            method in str(r.get("allowed_strategy") or ""),
            "APPROVED_METHOD_NOT_IN_ORIGINAL_INTERSECTION",
            "entry" if switches["entry"]=="OFF" else
            "stage4" if switches["stage4"]=="OFF" else
            "stage1" if switches["stage1"]=="OFF" else
            "material" if any(switches[x]=="OFF" for x in ("material","supply","chart")) else None)
    if r.get("max_hold_stage")=="EXIT_ONLY":
        require(entry.get("confirmed_special_event") is True and
                entry.get("special_event_method")==method,
                "SPECIAL_EVENT_NOT_CONFIRMED","stage1")
    timestamp=v.get("input_at")
    try:
        at=datetime.fromisoformat(str(timestamp).replace("Z","+00:00"))
        require(at.tzinfo is not None and
                at.astimezone(KST).date().isoformat()==today,
                "STAGE4_INPUT_TIME_NOT_CURRENT")
    except (ValueError,TypeError):
        require(False,"STAGE4_INPUT_TIME_INVALID")
    operation=((stages.get("stage0") or {}).get("value") or {}).get("next_operation_mode")
    require(operation in ("생활비모드","복리모드"),
            "OPERATION_MODE_MISSING","stage0")
    if problems:
        return {"status":"PENDING","reasons":problems,"warnings":warnings,"values":None}
    return {"status":"READY","reasons":[],"warnings":warnings,"values":{
        "handoff_id":str(v["handoff_id"]),"method":method,
        "entry":price,"stop":stop,"target1":target,
        "final_r":v["final_r"],"input_at":str(timestamp),
        "mode":operation,"trade_date":today}}


def sheet_updates(result:dict)->list[dict]:
    """Clear stale approved signals immediately when original GOLD blocks."""
    values=result.get("values") or {}
    cells=[
        values.get("handoff_id",""),values.get("method",""),
        values.get("entry",""),values.get("stop",""),values.get("target1","")
    ]
    tail=[values.get("final_r",""),values.get("input_at",""),
          values.get("mode",""),result["status"]]
    memo=("경고·사용자 OFF: "+",".join(result.get("warnings",[])[:5])
          if result["status"]=="READY" and result.get("warnings")
          else "GOLD 승인 원본 확인" if result["status"]=="READY" else
          "승인 대기: "+",".join(result.get("reasons",[])[:5]))
    return [
        {"sheet":SHEET,"range":"B11:B15","values":[[x] for x in cells]},
        {"sheet":SHEET,"range":"B19:B22","values":[[x] for x in tail]},
        {"sheet":SHEET,"range":"B24","values":[[memo]]},
    ]


def sync_once()->dict:
    sys.path.insert(0,str(HOME/"trading-system-stage3/collector"))
    from stage4_p047_handoff import session,get_values,update_values,STAGE4_ID
    try:
        p=json.loads(PACKET.read_text(encoding="utf-8"))
        result=decision_fields(p,now=datetime.now(KST))
    except (OSError,ValueError,TypeError,KeyError):
        # A missing/corrupt packet must clear a previous READY approval,
        # never leave an apparently valid BUY candidate on the sheet.
        result={"status":"PENDING",
                "reasons":["GOLD_PACKET_UNAVAILABLE_OR_INVALID"],
                "values":None}
    s=session()
    identity=get_values(s,STAGE4_ID,SHEET,"B4:B5")
    if identity!=[["3211"],["005930"]]:
        raise RuntimeError("3211_SHEET_IDENTITY_NOT_MATCHED")
    # Only B11:B15, B19:B22 and B24 are written. All other cells are owned
    # by Kiwoom capital sync or formulas. This process cannot approve an order.
    update_values(s,STAGE4_ID,sheet_updates(result))
    return {"status":result["status"],"reasons":result["reasons"],
            "account":"3211","broker_write":False,"orders_sent":False}


if __name__=="__main__":
    try:
        print(json.dumps(sync_once(),ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"status":"ERROR_FAIL_CLOSED",
                          "type":type(exc).__name__,"orders_sent":False},
                         ensure_ascii=False))
        raise SystemExit(2)
