"""3211 protective SELL audit: read-only visibility of unfilled/partial exits.

Never opens broker sessions, creates/cancels/amends orders or writes EXIT_PLAN.
This report does NOT prove a position is flat; broker holding is authoritative.
"""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

KST=ZoneInfo("Asia/Seoul")
EXIT_DB=Path.home()/".trading-execution-3211-v1.sqlite3"
WRITE_DB=Path.home()/".trading-execution-3211-v1-write.sqlite3"
SYMBOL="005930"


def _moment(value):
    try:
        dt=datetime.fromisoformat(str(value))
        return dt.astimezone(KST) if dt.tzinfo is not None else None
    except (TypeError,ValueError):
        return None


def classify_exit(*, plan:dict, submitted_at:str|None,
                  now:datetime)->dict:
    """Conservative broker-independent status; no automatic resend decision."""
    status=str(plan.get("status") or "")
    pid=str(plan.get("plan_id") or "")
    result={"plan_id":pid,"state":status,"remaining_qty":plan.get("remaining_qty"),
            "broker_write":False,"automatic_resend":False,
            "requires_review":False,"reasons":[]}
    if not pid.startswith("SCALP3211-") or plan.get("symbol")!=SYMBOL or plan.get("qty")!=1:
        result.update(requires_review=True,reasons=["OUT_OF_SCOPE_PLAN"])
        return result
    current=now.astimezone(KST)
    if status in ("CLOSED","CANCELLED"):
        result["reasons"]=["BROKER_POSITION_MUST_STILL_BE_CONFIRMED"]
        return result
    if status=="HALTED":
        result.update(requires_review=True,reasons=["EXIT_HALTED_OR_ACCOUNT_MISMATCH"])
    elif status=="PARTIAL_EXIT":
        result.update(requires_review=True,reasons=["PARTIAL_FILL_NO_AUTO_RESEND"])
    elif status=="EXIT_TRIGGERED":
        result.update(requires_review=True,reasons=["EXIT_TRIGGER_RESERVED_WRITE_STATE_MUST_BE_RECONCILED"])
    elif status=="EXIT_SUBMITTED":
        sent=_moment(submitted_at)
        if sent is None:
            result.update(requires_review=True,reasons=["EXIT_SUBMITTED_TIME_UNKNOWN"])
        elif current-sent>timedelta(minutes=2):
            result.update(requires_review=True,reasons=["UNFILLED_OR_UNRECONCILED_SELL_OVER_2_MIN"])
        else:
            result["reasons"]=["SELL_SUBMITTED_PENDING_BROKER_FILL"]
    elif status in ("EXIT_ARMED","POSITION"):
        end=_moment(plan.get("timecut_at"))
        if end is None or current>=end:
            result.update(requires_review=True,reasons=["EXIT_TIME_CUTOFF_UNEXECUTED_OR_UNKNOWN"])
        else:
            result["reasons"]=["EXIT_MONITOR_ACTIVE"]
    else:
        result.update(requires_review=True,reasons=["UNKNOWN_EXIT_STATE"])
    return result


def _read_db(path:Path,query:str,args:tuple=())->list[dict]:
    with sqlite3.connect(f"file:{path}?mode=ro",uri=True,timeout=3) as con:
        con.row_factory=sqlite3.Row
        return [dict(row) for row in con.execute(query,args).fetchall()]


def audit_once(*,now:datetime|None=None,db:Path=EXIT_DB)->dict:
    at=now or datetime.now(KST)
    if not db.exists():
        return {"at_kst":at.isoformat(timespec="seconds"),
                "status":"NO_LOCAL_EXIT_DB","broker_truth":"NOT_CHECKED",
                "requires_review":False,"plans":[],"broker_write":False}
    try:
        plans=_read_db(db,"SELECT * FROM exit_plans WHERE plan_id LIKE 'SCALP3211-%'")
        submits=_read_db(db,
            "SELECT plan_id, MAX(created_at) AS at FROM exit_events "
            "WHERE event_type='EXIT_SUBMITTED' GROUP BY plan_id")
    except (sqlite3.Error,OSError) as exc:
        return {"at_kst":at.isoformat(timespec="seconds"),
                "status":"AUDIT_DB_UNAVAILABLE","requires_review":True,
                "reason":type(exc).__name__,"broker_truth":"NOT_CHECKED",
                "broker_write":False}
    stamps={str(x["plan_id"]):x["at"] for x in submits}
    reports=[classify_exit(plan=p,submitted_at=stamps.get(p["plan_id"]),now=at)
             for p in plans]
    attention=any(p["requires_review"] for p in reports)
    return {"at_kst":at.isoformat(timespec="seconds"),
            "status":"REVIEW_REQUIRED" if attention else "NO_LOCAL_EXCEPTION",
            "requires_review":attention,"plans":reports,
            "broker_truth":"NOT_CHECKED","broker_write":False,
            "automatic_resend":False}


if __name__=="__main__":
    print(json.dumps(audit_once(),ensure_ascii=False,default=str))
