#!/usr/bin/env python3
"""3211 Samsung-only continuous V1 protective exit poller.

Reuses the existing V1 exit plan / Kiwoom quote / safety / intent journal.
Run once per scheduler tick, never loops forever in-process. Default is
READ-ONLY. A separate external verified LIVE/DAILY ARM remains compulsory.
Cannot BUY. Never touches accounts 4311 or 5311.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from broker_cli import KiwoomCliBroker
from exit_plan import ExitPlanStore
from exit_write_journal import ExitWriteJournal
from safety import ExecutionConfig, load_env_file
from state_store import StateStore
from v1_live_exit_once import run_live_exit_once
from scalp3211_bar_horizon_v1 import completed_24_bar_timecut
from exit_plan import ExitTrigger, evaluate_exit_trigger
from quote_reader import read_quote_snapshot
from v1_live_exit_once import _plan_from_row
from scalp3211_capital_owner_v1 import capture_3211_capital_owner

KST = ZoneInfo("Asia/Seoul")
HOME = Path.home()
V1_DB = HOME / ".trading-execution-3211-v1.sqlite3"
WRITE_DB = HOME / ".trading-execution-3211-v1-write.sqlite3"
ACCOUNT_SUFFIX, SYMBOL = "3211", "005930"


def session_close_due(at: datetime) -> bool:
    """3211 same-day flat safety: submit a first approved EXIT before 20:00.

    This does not promise a fill. Any existing unfilled EXIT is reconciled,
    never resent, and the original global live-write gates remain binding.
    """
    at = at.astimezone(KST)
    hhmm = at.hour * 100 + at.minute
    return at.weekday() < 5 and 1955 <= hhmm < 2000


def evaluate_watch(
    *,
    capital: dict, account_config: ExecutionConfig,
    plans: list[dict], execute: bool,
    active_window: bool,
) -> dict:
    """Fail closed on account mismatch, orphan holdings and plan ambiguity."""
    reasons = []
    if not str(account_config.allowed_account).endswith(ACCOUNT_SUFFIX):
        reasons.append("EXECUTION_ACCOUNT_NOT_3211")
    if account_config.allowed_symbols != frozenset({SYMBOL}):
        reasons.append("EXECUTION_SYMBOL_SCOPE_MISMATCH")
    if account_config.max_order_qty != 1:
        reasons.append("EXECUTION_QTY_SCOPE_MISMATCH")
    owner_errors = set(capital.get("reason") or [])
    holds = "EXISTING_HOLDING_REQUIRES_MANAGEMENT" in owner_errors
    if (capital.get("status") != "BROKER_CAPITAL_VERIFIED_ONLY"
            and not (holds and owner_errors == {"EXISTING_HOLDING_REQUIRES_MANAGEMENT"})):
        reasons.append("CAPITAL_OWNER_NOT_VALIDATED")
    valid_plans = [
        p for p in plans if
        str(p.get("plan_id") or "").startswith("SCALP3211-") and
        str(p.get("symbol") or "") == SYMBOL and
        int(p.get("qty") or 0) == 1
    ]
    others = [p for p in plans if p not in valid_plans and
              str(p.get("status") or "") not in {"CLOSED","HALTED"}]
    active = [p for p in valid_plans if str(p.get("status")) not in {"CLOSED","HALTED"}]
    if others:
        reasons.append("UNRECOGNIZED_ACTIVE_EXIT_PLAN")
    if len(active) > 1:
        reasons.append("MULTIPLE_ACTIVE_PLANS")
    if holds and not active:
        reasons.append("UNMANAGED_POSITION_BLOCK_NEW_BUY")
    if active and not holds and str(active[0].get("status") or "") not in {"EXIT_SUBMITTED", "PARTIAL_EXIT"}:
        reasons.append("PLAN_WITHOUT_CONFIRMED_POSITION")
    if not active_window and execute:
        reasons.append("OUTSIDE_0800_TO_2000")
    if reasons:
        return {"status":"HALT", "reasons":reasons, "broker_write":False}
    if not active:
        return {"status":"IDLE_FLAT","reasons":[], "broker_write":False}
    p = active[0]
    status = str(p.get("status") or "")
    if status in {"EXIT_SUBMITTED", "PARTIAL_EXIT"}:
        # An existing SELL may still be open or partly executed. Reconcile broker
        # holdings/order state only; never submit a second SELL on this path.
        return {"status":"RECONCILE_ONLY","plan_id":p["plan_id"],
                "reasons":["EXISTING_SELL_ORDER_NO_RESEND"],"broker_write":False}
    if status not in {"EXIT_ARMED","POSITION","EXIT_TRIGGERED"}:
        return {"status":"HALT","plan_id":p["plan_id"],
                "reasons":["EXIT_PLAN_STATUS_UNSUPPORTED"],"broker_write":False}
    return {"status":"RUN_LIVE_EXIT_ONCE" if execute else "READY_SHADOW",
            "plan_id":p["plan_id"],"reasons":[],"broker_write":False}


def main() -> int:
    ap = argparse.ArgumentParser(description="3211 SOR SELL watch (default shadow only)")
    ap.add_argument("--auth-env",default=str(HOME/"api-read-v2.env"))
    ap.add_argument("--exec-env",default=str(HOME/"trading-execution.env"))
    ap.add_argument("--execute",action="store_true",
                    help="explicit live SELL requested; V0/V1 DAILY ARM still enforced")
    args=ap.parse_args()
    load_env_file(Path(args.auth_env),required=True)
    load_env_file(Path(args.exec_env),required=True)
    cfg=ExecutionConfig.from_env()

    # Actual account proof must come from the same dedicated 3211 collector.
    import sys
    collector=str(HOME/"trading-system"/"collector")
    if collector not in sys.path:sys.path.insert(0,collector)
    from account_snapshot_readonly import collect
    capital=capture_3211_capital_owner(collect("test"))
    if capital.get("status") not in {"BROKER_CAPITAL_VERIFIED_ONLY","BLOCK"}:
        raise RuntimeError("UNEXPECTED_ACCOUNT_OWNER_RESULT")

    v1=ExitPlanStore(V1_DB)
    plans=v1.list_plans()
    at=datetime.now(KST)
    hm=at.hour*100+at.minute
    active_window=at.weekday()<5 and 800<=hm<2000
    decision=evaluate_watch(capital=capital,account_config=cfg,
                            plans=plans,execute=args.execute,
                            active_window=active_window)
    if decision["status"]=="RECONCILE_ONLY":
        # Read broker truth, update local plan; NEVER transmit or retry SELL.
        from exit_write_reconcile import reconcile_exit_write
        from dataclasses import asdict
        broker=KiwoomCliBroker(mode="real")
        plan=v1.get_plan(decision["plan_id"])
        journal=ExitWriteJournal(WRITE_DB)
        last_id=str(plan.get("exit_intent_id") or "")
        snapshot={
            "holdings":broker.holdings(basis="total",exchange="KRX").payload,
            "open_orders":broker.list_open_orders().payload,
        }
        result=reconcile_exit_write(
            plan_row=plan,snapshot=snapshot,
            journal_row=journal.get(last_id) if last_id else None,
            mark_exit_submitted=v1.mark_exit_submitted,
            reconcile_holding=v1.reconcile_holding,
        )
        decision={"status":"RECONCILED_NO_RESEND","plan_id":plan["plan_id"],
                  "result":asdict(result),"final_plan":v1.get_plan(plan["plan_id"]),
                  "broker_write":False}
    if decision["status"]=="RUN_LIVE_EXIT_ONCE":
        if cfg.kill_switch or not cfg.allow_live_orders or cfg.daily_arm_date!=at.date().isoformat():
            decision={"status":"HALT","reasons":["LIVE_ARM_OR_KILL_SWITCH_NOT_READY"],"broker_write":False}
        else:
            broker=KiwoomCliBroker(mode="real")
            # First actual BUY fill is persisted as a V1 event. The same
            # completed 5m-bar SQLite used by the existing DAY engine decides
            # the 24-bar timecut, not a wall-clock approximation.
            events=v1.list_events(decision["plan_id"])
            anchors=[]
            for event in events:
                if event.get("event_type")=="FIRST_REAL_BUY_FILL":
                    payload=json.loads(event.get("payload_json") or "{}")
                    if payload.get("first_fill_at"):
                        anchors.append(payload["first_fill_at"])
            if len(set(anchors))==1:
                first_fill_at=anchors[0]
                if session_close_due(at) or completed_24_bar_timecut(first_fill_at):
                    row=v1.get_plan(decision["plan_id"])
                    if row and row.get("status") in ("EXIT_ARMED","POSITION"):
                        quote=read_quote_snapshot(broker,symbol=SYMBOL,exchange="SOR")
                        normal=evaluate_exit_trigger(
                            _plan_from_row(row),last_price=int(quote.last_price),
                            now=quote.captured_at)
                        if normal is None:
                            v1.trigger_once(
                                decision["plan_id"],
                                ExitTrigger("TIMECUT",int(quote.last_price),quote.captured_at))
            elif len(anchors)>1:
                raise RuntimeError("AMBIGUOUS_FIRST_REAL_FILL_ANCHORS")
            data=run_live_exit_once(
                cfg=cfg,plan_id=decision["plan_id"],v1=v1,
                v0=StateStore(cfg.state_db),journal=ExitWriteJournal(WRITE_DB),
                broker=broker,exchange="SOR")
            decision={"status":"EXIT_EVALUATED", "plan_id":decision["plan_id"],
                      "result":data,"broker_write":data.get("action")=="SELL_SUBMITTED"}
    print(json.dumps({"at_kst":at.isoformat(timespec="seconds"),
                      "account_suffix":ACCOUNT_SUFFIX,"symbol":SYMBOL,
                      "mode":"LIVE_REQUESTED" if args.execute else "SHADOW",
                      "capital_owner_status":capital.get("status"),
                      "decision":decision},ensure_ascii=False,default=str))
    return 2 if decision["status"]=="HALT" else 0


if __name__=="__main__":
    raise SystemExit(main())
