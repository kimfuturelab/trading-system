#!/usr/bin/env python3
"""3211 single-share GOLD -> existing V0 SOR BUY -> real fill -> V1 EXIT.

The only broker order path is the pre-existing v0_engine.cmd_live_order, which
still enforces user allowlist, daily arm, kill switch, broker capacity,
reconciliation and durable duplicate-intent protection.
An approved source-proven exit contract must be persisted BEFORE BUY.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from broker_cli import KiwoomCliBroker
from exit_plan import ExitPlanStore
from safety import ExecutionConfig, load_env_file
from scalp3211_capital_owner_v1 import capture_3211_capital_owner
from scalp3211_broker_fill_v1 import intent_id_from_stage4, verified_first_fill
from scalp3211_exit_owner_v1 import approved_exit_contract
from scalp3211_fill_exit_bridge_v1 import register_existing_v1_plan
from state_store import StateStore
from v0_engine import cmd_live_order, open_order_rows, sync_open_intents

KST = ZoneInfo("Asia/Seoul")
HOME = Path.home()
STAGE3 = HOME / "trading-system-stage3"
PLAN_DB = STAGE3 / "runtime/fill-shadow/trade_events.sqlite3"
PACKET = STAGE3 / "runtime/decision_packet_v1_latest.json"
V1_DB = HOME / ".trading-execution-3211-v1.sqlite3"
CONTRACT_DIR = HOME / "trading-system/runtime/3211-approved"
SYMBOL = "005930"
ACCOUNT = "test"


def _today() -> str:
    return datetime.now(KST).date().isoformat()


def stage4_plans(today: str) -> list[dict]:
    if not PLAN_DB.is_file():
        return []
    with sqlite3.connect(f"file:{PLAN_DB}?mode=ro",uri=True,timeout=5) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM stage4_trade_plans WHERE trade_date=? "
            "AND account_alias=? AND symbol=?",
            (today, ACCOUNT, SYMBOL)).fetchall()
    return [dict(row) for row in rows]


def original_packet(today: str) -> dict:
    p = json.loads(PACKET.read_text(encoding="utf-8"))
    captured = datetime.fromisoformat(p["captured_at"])
    if (p.get("trade_date") != today or captured.tzinfo is None
            or (datetime.now(KST)-captured.astimezone(KST)).total_seconds()>180):
        raise RuntimeError("GOLD_DECISION_PACKET_NOT_FRESH")
    return p


def current_entry_is_allowed(packet: dict, plan: dict) -> bool:
    stages=packet.get("stages") or {}
    for name in ("stage1","stage2","rejuscha","entry","stage4"):
        if (stages.get(name) or {}).get("status")!="OK":
            return False
    regime=stages["stage1"]
    entry=stages["entry"]["value"]
    s4=stages["stage4"]["value"]
    score=(stages["rejuscha"].get("value") or {})
    selfgate=((stages.get("stage0") or {}).get("value") or {}).get("self_gate")
    if selfgate not in ("통과","통과(감속)"):
        return False
    if (regime.get("source_at")!=packet["trade_date"]
            or entry.get("action_status")!="READY_CANDIDATE"
            or score.get("action_status")!="READY"
            or s4.get("action_status")!="READY"
            or entry.get("target_symbol")!=SYMBOL
            or s4.get("handoff_id")!=plan.get("handoff_id")
            or s4.get("symbol_code")!=SYMBOL
            or s4.get("entry_price")!=plan.get("planned_entry_price")
            or s4.get("stop_price")!=plan.get("stop_price")
            or s4.get("qty")!=1
            or s4.get("final_r")!=plan.get("final_r")):
        return False
    if plan.get("stage_decision") not in ("진입가능","축소진입"):
        return False
    if (plan.get("planned_qty")!=1 or
            plan.get("planned_invest_amount")!=plan.get("planned_entry_price") or
            plan.get("expected_loss") !=
            plan.get("planned_entry_price")-plan.get("stop_price")):
        return False
    # The original Stage4 SQLite schema has no approved_method column.
    # Use only a method explicitly selected by an authenticated GOLD owner.
    # Never infer a strategy from the regime's list of eligible strategies.
    original_method = (
        s4.get("approved_method") or
        entry.get("approved_method") or
        score.get("approved_method")
    )
    if not isinstance(original_method, str):
        return False
    method=original_method.strip()
    if (not method or method not in (score.get("executable_actions") or [])
            or method not in str((regime.get("value") or {}).get("allowed_strategy") or "")):
        return False
    # RED allows only a confirmed special-event entry. A mere item in the
    # allowed-method list is not evidence that the event actually occurred.
    if (regime.get("value") or {}).get("max_hold_stage") == "EXIT_ONLY":
        confirmed = (
            entry.get("confirmed_special_event") is True
            and entry.get("special_event_method") == method
        )
        if not confirmed:
            return False
    return True


def authenticated_exit_spec(plan: dict, today: str) -> dict | None:
    sys.path.insert(0,str(STAGE3/"collector"))
    from stage4_p047_handoff import session, get_values, STAGE4_ID
    s=session()
    rows=get_values(s,STAGE4_ID,f"{today}_빠른계산","A19:AC25")
    if len(rows)<7:
        return None
    return approved_exit_contract(
        plan=plan,trade_date=today,
        quick_approval_row=rows[0],quick_order_row=rows[6])


def stored_contract_path(handoff_id: str) -> Path:
    from hashlib import sha256
    return CONTRACT_DIR / (sha256(handoff_id.encode()).hexdigest()[:24]+".json")


def approved_contract_for_cycle(plan: dict, today: str, *, allow_new: bool) -> dict | None:
    path=stored_contract_path(str(plan["handoff_id"]))
    if path.exists():
        contract=json.loads(path.read_text(encoding="utf-8"))
        if (contract.get("handoff_id") != plan["handoff_id"] or
                contract.get("trade_date") != today or
                contract.get("source") != "STAGE4_APPROVED"):
            raise RuntimeError("IMMUTABLE_EXIT_CONTRACT_MISMATCH")
        return contract
    if not allow_new:
        return None
    approved=authenticated_exit_spec(plan,today)
    if approved is None:
        return None
    CONTRACT_DIR.mkdir(parents=True,exist_ok=True)
    # O_EXCL makes the pre-buy protection contract immutable on retries.
    with path.open("x",encoding="utf-8") as stream:
        json.dump(approved,stream,ensure_ascii=False,sort_keys=True)
    return approved


def once(*, execute: bool) -> dict:
    today=_today()
    load_env_file(HOME/"api-read-v2.env",required=True)
    load_env_file(HOME/"trading-execution.env",required=True)
    cfg=ExecutionConfig.from_env()
    if (not cfg.allowed_account.endswith("3211") or
            cfg.allowed_symbols!=frozenset({SYMBOL}) or cfg.max_order_qty!=1):
        return {"state":"HALT","reason":"3211_ONLY_SCOPE"}
    sys.path.insert(0,str(HOME/"trading-system/collector"))
    from account_snapshot_readonly import collect
    capital=capture_3211_capital_owner(collect("test"))
    if capital.get("status") == "BLOCK" and "EXISTING_HOLDING_REQUIRES_MANAGEMENT" in (capital.get("reason") or []):
        if not any(x.get("status") not in ("CLOSED","CANCELLED")
                   for x in ExitPlanStore(V1_DB).list_plans()):
            return {"state":"HALT_UNPROTECTED_3211_HOLDING",
                    "reason":"BROKER_HAS_SHARE_WITHOUT_V1_EXIT_PLAN"}
    plans=stage4_plans(today)
    if len(plans)!=1:
        return {"state":"WAIT_STAGE4_ONE_APPROVED_PLAN","count":len(plans)}
    p=plans[0]
    id_buy=intent_id_from_stage4(p["handoff_id"])
    broker=KiwoomCliBroker(mode="real")
    snapshot=broker.read_snapshot()
    state=StateStore(cfg.state_db)
    sync_open_intents(state,snapshot)
    intent=state.get_intent(id_buy)
    if intent:
        if intent["status"]!="FILLED":
            return {"state":"WAIT_BROKER_FILL_NO_RESEND","status":intent["status"]}
        fill=verified_first_fill(
            handoff_id=p["handoff_id"],intents=state.list_intents(),
            snapshot=snapshot,trade_date=today)
        if not fill:
            return {"state":"WAIT_AUTHENTICATED_FIRST_FILL"}
        contract=approved_contract_for_cycle(p,today,allow_new=False)
        if not contract:
            return {"state":"HALT_FILL_NEEDS_APPROVED_EXIT_CONTRACT"}
        result=register_existing_v1_plan(
            fill=fill,approved_exit=contract,store=ExitPlanStore(V1_DB))
        return {"state":"FIRST_FILL_PROTECTED","result":result}
    if not execute:
        return {"state":"SHADOW_ONLY_NO_NEW_ORDER"}
    if p.get("status") != "OPEN":
        return {"state":"WAIT_STAGE4_PLAN_OPEN_STATUS"}
    t = datetime.now(KST)
    if t.weekday() >= 5 or (t.hour * 100 + t.minute) < 800 or (t.hour * 100 + t.minute) >= 1955:
        return {"state":"WAIT_SOR_SESSION_AND_SAFE_FLAT_BY_CLOSE"}
    if capital.get("status")!="BROKER_CAPITAL_VERIFIED_ONLY":
        return {"state":"WAIT_FLAT_3211_CAPITAL"}
    packet=original_packet(today)
    if not current_entry_is_allowed(packet,p):
        return {"state":"WAIT_GOLD_AND_STAGE4_APPROVAL",
                "entry_action":((packet.get("stages") or {}).get("entry") or {}).get("value",{}).get("action_status"),
                "stage4_action":((packet.get("stages") or {}).get("stage4") or {}).get("value",{}).get("action_status"),
                "method_approved":bool((
                    ((packet.get("stages") or {}).get("stage4") or {}).get("value") or {}
                ).get("approved_method"))}
    price=p["planned_entry_price"]
    loss=p["expected_loss"]
    if (price>capital["orderable_cash_krw"] or
            loss>capital["risk_reference_krw_1p5pct"]):
        return {"state":"WAIT_3211_CASH_AND_R"}
    if open_order_rows(snapshot) or state.open_intents():
        return {"state":"WAIT_EXISTING_BROKER_OR_LOCAL_ORDER"}
    if any(x["status"] not in ("CLOSED","CANCELLED")
           for x in ExitPlanStore(V1_DB).list_plans()):
        return {"state":"WAIT_EXISTING_PROTECTIVE_PLAN"}
    contract=approved_contract_for_cycle(p,today,allow_new=True)
    if contract is None:
        return {"state":"WAIT_APPROVED_TARGET_AND_24BAR_EXIT"}
    if cfg.kill_switch or not cfg.allow_live_orders or cfg.daily_arm_date!=today:
        return {"state":"WAIT_LIVE_KILL_SWITCH_AND_DAILY_ARM"}
    args=argparse.Namespace(
        auth_env=str(HOME/"api-read-v2.env"),
        exec_env=str(HOME/"trading-execution.env"),mode="real",
        side="buy",symbol=SYMBOL,qty=1,exchange="SOR",order_type="limit",
        price=str(price),chance_price=price,intent_id=id_buy,
        i_understand_this_sends_a_live_order=True)
    rc=cmd_live_order(args)  # original V0 durable-intent broker engine only
    post=once(execute=False) if rc==0 else None
    return {"state":"V0_BUY_COMMAND_RETURNED","exit_code":rc,"intent_id":id_buy,
            "post_submit_fill_and_exit":post}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--execute",action="store_true")
    a=p.parse_args()
    try:
        out=once(execute=a.execute)
    except Exception as e:
        out={"state":"ERROR_FAIL_CLOSED","error":f"{type(e).__name__}: {e}"}
    print(json.dumps(out,ensure_ascii=False,default=str))
    return 0 if out.get("state") not in ("ERROR_FAIL_CLOSED","HALT") else 2


if __name__=="__main__":
    raise SystemExit(main())
