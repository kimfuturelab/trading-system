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
from scalp3211_switch_v1 import (entry_switch_on, read_entry_control, MODULES,
                                  module_modes_for_capital, trial_authorized,
                                  entry_session_open)
from scalp3211_isolated_stage4_bridge_v1 import persist_live as persist_approved_3211_stage4
from scalp3211_fill_exit_bridge_v1 import register_existing_v1_plan
from scalp3211_user_trial_v1 import (
    build_user_trial,save_user_trial,trial_contract,PREFIX, SOURCE)
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


def current_entry_is_allowed(packet: dict, plan: dict, modules:dict|None=None) -> bool:
    switches = read_entry_control()["modules"] if modules is None else {
        k:modules.get(k,"ON") for k in MODULES}
    def on(name): return switches[name]=="ON"
    stages=packet.get("stages") or {}
    for name in ("stage1","stage2","entry","stage4"):
        if on(name) and (stages.get(name) or {}).get("status")!="OK":
            return False
    for axis in ("material","supply","chart"):
        axis_value=(((stages.get("rejuscha") or {}).get("value") or {})
                    .get(axis) or {})
        if on(axis) and "status" in axis_value and axis_value["status"]!="OK":
            return False
    if all(on(x) for x in ("material","supply","chart")) and (
        stages.get("rejuscha") or {}).get("status")!="OK":
        return False
    regime=stages.get("stage1") or {}
    entry=(stages.get("entry") or {}).get("value") or {}
    s4=(stages.get("stage4") or {}).get("value") or {}
    score=(stages.get("rejuscha") or {}).get("value") or {}
    selfgate=((stages.get("stage0") or {}).get("value") or {}).get("self_gate")
    if on("stage0") and selfgate not in ("통과","통과(감속)"):
        return False
    if ((on("stage1") and regime.get("source_at")!=packet["trade_date"])
            or (on("entry") and entry.get("action_status")!="READY_CANDIDATE")
            or (all(on(x) for x in ("material","supply","chart"))
                and score.get("action_status")!="READY")
            or (on("stage4") and s4.get("action_status")!="READY")
            or (on("entry") and entry.get("target_symbol")!=SYMBOL)
            or s4.get("handoff_id")!=plan.get("handoff_id")
            or s4.get("symbol_code")!=SYMBOL
            or s4.get("entry_price")!=plan.get("planned_entry_price")
            or s4.get("stop_price")!=plan.get("stop_price")
            or s4.get("qty")!=1
            or s4.get("final_r")!=plan.get("final_r")):
        return False
    if on("stage4") and plan.get("stage_decision") not in ("진입가능","축소진입"):
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
        if on("entry") and on("stage4"):
            return False
        original_method=""
    method=original_method.strip()
    if on("entry") and not method:
        return False
    if (method and all(on(x) for x in ("material","supply","chart"))
            and method not in (score.get("executable_actions") or [])):
        return False
    if (on("stage1") and method and
            method not in str((regime.get("value") or {}).get("allowed_strategy") or "")):
        return False
    # RED allows only a confirmed special-event entry. A mere item in the
    # allowed-method list is not evidence that the event actually occurred.
    if on("stage1") and (regime.get("value") or {}).get("max_hold_stage") == "EXIT_ONLY":
        confirmed = (
            entry.get("confirmed_special_event") is True
            and entry.get("special_event_method") == method
        )
        if not confirmed:
            return False
    return True


def authenticated_exit_spec(plan: dict, today: str) -> dict | None:
    # The 3211 exit owner must be the same isolated sheet + original GOLD
    # proof used to persist the 3211 Stage4 plan, never DAY's quickcalc.
    from scalp3211_isolated_stage4_bridge_v1 import inspect_live
    if str(plan.get("handoff_id") or "").startswith(PREFIX):
        if not trial_authorized(today):
            return None
        return trial_contract(plan)
    original=inspect_live(today=today)
    if original.get("status")!="APPROVED_SOURCE_ONLY":
        return None
    actual=original["plan"]
    for field in ("handoff_id","account_alias","symbol","trade_date",
                  "planned_entry_price","stop_price","planned_qty",
                  "final_r","stage_decision"):
        if str(actual.get(field))!=str(plan.get(field)):
            return None
    return original["contract"]


def stored_contract_path(handoff_id: str) -> Path:
    from hashlib import sha256
    return CONTRACT_DIR / (sha256(handoff_id.encode()).hexdigest()[:24]+".json")


def approved_contract_for_cycle(plan: dict, today: str, *, allow_new: bool) -> dict | None:
    path=stored_contract_path(str(plan["handoff_id"]))
    if path.exists():
        contract=json.loads(path.read_text(encoding="utf-8"))
        if (contract.get("handoff_id") != plan["handoff_id"] or
                contract.get("trade_date") != today or
                contract.get("source") not in ("STAGE4_APPROVED",SOURCE)):
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


def protective_watch_runtime_ready() -> bool:
    """Do not BUY while the separately scheduled V1 SELL watcher is missing.

    Logs are only a liveness receipt, not evidence that the exit E2E passed.
    The original V1 safety checks remain mandatory at any actual SELL.
    """
    import subprocess
    try:
        cron=subprocess.run(["crontab","-l"],capture_output=True,text=True,timeout=4)
        if cron.returncode != 0 or "SCALP3211_EXIT_WATCH" not in cron.stdout:
            return False
        log=HOME/"trading-system/runtime/scalp3211_exit_watch.log"
        with log.open("rb") as stream:
            stream.seek(max(0, log.stat().st_size-18000))
            lines=stream.read().decode("utf-8",errors="replace").splitlines()
        receipts=[]
        for line in lines:
            if not line.startswith("{"):
                continue
            try:
                item=json.loads(line)
            except json.JSONDecodeError:
                continue
            if "at_kst" in item and "decision" in item:
                receipts.append(item)
        if not receipts:
            return False
        last=receipts[-1]
        captured=datetime.fromisoformat(last["at_kst"]).astimezone(KST)
        seconds=(datetime.now(KST)-captured).total_seconds()
        return (
            0 <= seconds <= 180
            and last.get("account_suffix")=="3211"
            and last.get("symbol")==SYMBOL
            and last.get("capital_owner_status")=="BROKER_CAPITAL_VERIFIED_ONLY"
            and (last.get("decision") or {}).get("status")=="IDLE_FLAT"
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


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
    # A just-filled BUY is visible in the broker holdings BEFORE its V1 exit
    # plan is attached. Do not orphan-halt before authenticating that fill:
    # permit recovery via the existing durable BUY intent + actual fill +
    # immutable pre-BUY approved exit contract, never via holdings alone.
    orphan_holding = (
        capital.get("status") == "BLOCK"
        and "EXISTING_HOLDING_REQUIRES_MANAGEMENT" in (capital.get("reason") or [])
        and not any(x.get("status") not in ("CLOSED", "CANCELLED")
                    for x in ExitPlanStore(V1_DB).list_plans())
    )
    orphan_halt = {"state":"HALT_UNPROTECTED_3211_HOLDING",
                   "reason":"BROKER_HAS_SHARE_WITHOUT_AUTHENTICATED_BUY_AND_V1_EXIT_PLAN"}
    plans=stage4_plans(today)
    if not plans and entry_switch_on():
        if trial_authorized(today):
            # The user's one-off 3211 current-price instruction supplies the
            # entry decision. This is never masqueraded as GOLD approval.
            from quote_reader import read_quote_snapshot
            quote=read_quote_snapshot(KiwoomCliBroker(mode="real"),
                                      symbol=SYMBOL,exchange="SOR")
            trial=build_user_trial(quote=quote,capital=capital,
                                   today=today,authorized=True)
            save_user_trial(trial)
            plans=stage4_plans(today)
        else:
            source=persist_approved_3211_stage4(today=today,broker_capital=capital)
            if source.get("status") in ("PLAN_PERSISTED","ALREADY_PERSISTED"):
                plans=stage4_plans(today)
    if len(plans)!=1:
        return orphan_halt if orphan_holding else {
            "state":"WAIT_STAGE4_ONE_APPROVED_PLAN","count":len(plans)}
    p=plans[0]
    id_buy=intent_id_from_stage4(p["handoff_id"])
    broker=KiwoomCliBroker(mode="real")
    snapshot=broker.read_snapshot()
    state=StateStore(cfg.state_db)
    sync_open_intents(state,snapshot)
    intent=state.get_intent(id_buy)
    if intent:
        if intent["status"]!="FILLED":
            return orphan_halt if orphan_holding else {
                "state":"WAIT_BROKER_FILL_NO_RESEND","status":intent["status"]}
        fill=verified_first_fill(
            handoff_id=p["handoff_id"],intents=state.list_intents(),
            snapshot=snapshot,trade_date=today)
        if not fill:
            return orphan_halt if orphan_holding else {
                "state":"WAIT_AUTHENTICATED_FIRST_FILL"}
        contract=approved_contract_for_cycle(p,today,allow_new=False)
        if not contract:
            return {"state":"HALT_FILL_NEEDS_APPROVED_EXIT_CONTRACT"}
        result=register_existing_v1_plan(
            fill=fill,approved_exit=contract,store=ExitPlanStore(V1_DB))
        return {"state":"FIRST_FILL_PROTECTED","result":result}
    if orphan_holding:
        return orphan_halt
    if not execute:
        return {"state":"SHADOW_ONLY_NO_NEW_ORDER"}
    # ON/OFF is a separate system switch; never suppress the already-started
    # fill recovery above, nor the protective V1 SELL watcher.
    if not entry_switch_on():
        return {"state":"ENTRY_OFF_WAITING_FOR_SYSTEM_ON"}
    if p.get("status") != "OPEN":
        return {"state":"WAIT_STAGE4_PLAN_OPEN_STATUS"}
    t = datetime.now(KST)
    if not entry_session_open(t):
        return {"state":"WAIT_SOR_SESSION_AND_SAFE_FLAT_BY_CLOSE"}
    if capital.get("status")!="BROKER_CAPITAL_VERIFIED_ONLY":
        return {"state":"WAIT_FLAT_3211_CAPITAL"}
    user_one_time=str(p.get("handoff_id") or "").startswith(PREFIX)
    if user_one_time and not trial_authorized(today):
        return {"state":"WAIT_USER_TRIAL_SESSION_AUTHORIZATION"}
    if user_one_time:
        if (capital.get("estimated_assets_krw",0)>100_000_000 or
                p.get("trade_date")!=today):
            return {"state":"WAIT_USER_TRIAL_OWNER_OR_DATE"}
        # A current SOR quote was authenticated when creating this immutable
        # one-time limit plan. Subsequent market changes must NOT reprice,
        # invalidate, or endlessly block that previously chosen LIMIT price.
        # The broker's cash, pending-order and one-intent checks remain below.
    else:
        packet=original_packet(today)
    if not user_one_time and not current_entry_is_allowed(
            packet,p,modules=module_modes_for_capital(capital)):
        return {"state":"WAIT_GOLD_AND_STAGE4_APPROVAL",
                "entry_action":((packet.get("stages") or {}).get("entry") or {}).get("value",{}).get("action_status"),
                "stage4_action":((packet.get("stages") or {}).get("stage4") or {}).get("value",{}).get("action_status"),
                "method_approved":bool((
                    ((packet.get("stages") or {}).get("stage4") or {}).get("value") or {}
                ).get("approved_method"))}
    price=p["planned_entry_price"]
    loss=p["expected_loss"]
    if (price>capital["orderable_cash_krw"] or
            (not user_one_time and
             loss>capital["risk_reference_krw_1p5pct"])):
        return {"state":"WAIT_3211_CASH_AND_R"}
    if open_order_rows(snapshot) or state.open_intents():
        return {"state":"WAIT_EXISTING_BROKER_OR_LOCAL_ORDER"}
    if any(x["status"] not in ("CLOSED","CANCELLED")
           for x in ExitPlanStore(V1_DB).list_plans()):
        return {"state":"WAIT_EXISTING_PROTECTIVE_PLAN"}
    contract=approved_contract_for_cycle(p,today,allow_new=True)
    if contract is None:
        return {"state":"WAIT_APPROVED_TARGET_AND_24BAR_EXIT"}
    if not protective_watch_runtime_ready():
        return {"state":"WAIT_EXISTING_V1_PROTECTIVE_WATCHER"}
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
