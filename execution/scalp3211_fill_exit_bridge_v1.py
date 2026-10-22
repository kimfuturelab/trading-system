"""Attach a confirmed 3211 first BUY fill to the existing V1 exit watchdog.

Uses the existing fill-shadow SQLite, Stage4 trade plan and 3211 V1 DB.
NEVER creates a new stop, target or fill value and NEVER sends a broker order.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from exit_plan import ExitPlan, ExitPlanStore

KST = ZoneInfo("Asia/Seoul")
ROOT = Path.home() / "trading-system-stage3/runtime"
FILLS_DB = ROOT / "fill-shadow/trade_events.sqlite3"
EXIT_DB = Path.home() / ".trading-execution-3211-v1.sqlite3"


def read_matched_first_fill(*, handoff_id: str, db: Path = FILLS_DB) -> dict | None:
    """Require one live broker-sourced buy LOT matched to exactly one approved plan."""
    if not db.exists():
        return None
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5) as c:
        c.row_factory = sqlite3.Row
        plans = c.execute(
            "SELECT * FROM stage4_trade_plans WHERE handoff_id=? AND "
            "account_alias='test' AND symbol='005930' AND planned_qty=1 "
            "AND stage_decision IN ('진입가능','축소진입')",
            (handoff_id,),
        ).fetchall()
        if len(plans) != 1:
            return None
        rows = c.execute(
            "SELECT f.*,l.stage4_handoff_id,l.lot_type,l.qty_remaining "
            "FROM fills f INNER JOIN lots l ON "
            "l.source_ref=('FILL:' || f.account_alias || ':' || "
            "coalesce(f.order_no,'') || ':' || coalesce(f.fill_no,'')) "
            "WHERE f.account_alias='test' AND f.symbol='005930' AND "
            "f.side='BUY' AND l.stage4_handoff_id=? AND l.lot_type='LIVE_FILL' "
            "ORDER BY f.first_seen_at",
            (handoff_id,),
        ).fetchall()
    if len(rows) != 1:
        return None
    fill = dict(rows[0])
    if (int(fill.get("fill_qty") or 0) != 1
            or int(fill.get("qty_remaining") or 0) != 1
            or int(fill.get("fill_price") or 0) <= 0
            or not fill.get("order_no") or not fill.get("fill_no")
            or not fill.get("first_seen_at")):
        return None
    try:
        moment = datetime.fromisoformat(str(fill["first_seen_at"]).replace("Z", "+00:00"))
        if moment.tzinfo is None:
            return None
    except ValueError:
        return None
    return {
        "handoff_id": handoff_id, "order_no": str(fill["order_no"]),
        "fill_no": str(fill["fill_no"]), "price": int(fill["fill_price"]),
        "qty": 1, "first_fill_at": moment.astimezone(KST).isoformat(timespec="seconds"),
        "broker_source": str(fill.get("source") or ""),
    }


def register_existing_v1_plan(
    *, fill: dict, approved_exit: dict, store: ExitPlanStore | None = None
) -> dict:
    """Idempotent local V1 registration; requires approved exit price contract."""
    from hashlib import sha256

    handoff_id = str(fill.get("handoff_id") or "")
    if not handoff_id or handoff_id != approved_exit.get("handoff_id"):
        raise ValueError("EXIT_OWNER_HANDOFF_MISMATCH")
    if approved_exit.get("source") not in (
            "STAGE4_APPROVED","USER_DIRECT_3211_ONE_TIME"):
        raise ValueError("APPROVED_STRATEGY_SOURCE_REQUIRED")
    if approved_exit.get("timecut_policy") != "GOLD_DAY_24_COMPLETE_5M_BARS":
        raise ValueError("24_CONFIRMED_BAR_POLICY_REQUIRED")
    entry = int(fill["price"])
    stop = approved_exit.get("stop_price")
    target = approved_exit.get("target1_price")
    if type(stop) is not int or type(target) is not int or not (0 < stop < entry < target):
        raise ValueError("APPROVED_STOP_OR_TAKE_PROFIT_MISSING")
    risk = approved_exit.get("max_loss_krw")
    if type(risk) is not int or (entry-stop) > risk:
        raise ValueError("ACTUAL_FIRST_FILL_RISK_EXCEEDED")
    # The absolute deadline is only a final same-day fail-safe. The GOLD 24
    # completed-bar timecut is evaluated by the continuous V1 watcher.
    entry_at = datetime.fromisoformat(fill["first_fill_at"]).astimezone(KST)
    end_at = entry_at.replace(hour=19, minute=55, second=0, microsecond=0)
    if end_at <= entry_at:
        raise ValueError("MARKET_CLOSE_SAFETY_DEADLINE_PASSED")
    digest = sha256((handoff_id+"|"+fill["order_no"]+"|"+fill["fill_no"]).encode()).hexdigest()[:18]
    plan = ExitPlan("SCALP3211-"+digest, "005930", 1, entry, stop, target, end_at)
    v1 = store or ExitPlanStore(EXIT_DB)
    existing = v1.get_plan(plan.plan_id)
    if existing is not None:
        if (int(existing["entry_price"]) != entry or int(existing["stop_price"]) != stop
                or int(existing["take_profit_price"]) != target
                or str(existing["timecut_at"]) !=
                    end_at.isoformat(timespec="seconds")):
            raise ValueError("EXISTING_PROTECTIVE_PLAN_CONFLICT")
        row = existing
    else:
        row = v1.reserve_plan(plan)
    # A restart between reserve_plan and the fill event must not silently
    # suppress the 24-bar exit. Restore the matching event idempotently.
    events = [e for e in v1.list_events(plan.plan_id)
              if e.get("event_type") == "FIRST_REAL_BUY_FILL"]
    payload = {"first_fill_at":fill["first_fill_at"],
               "handoff_id":handoff_id, "order_no":fill["order_no"],
               "fill_no":fill["fill_no"],"qty":1,"source":fill["broker_source"]}
    if events:
        if len(events) != 1 or json.loads(events[0]["payload_json"]) != payload:
            raise ValueError("FIRST_FILL_EVENT_CONFLICT")
    else:
        with v1.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            # Read while holding SQLite's writer lock, preventing two workers
            # from appending the same first-fill record concurrently.
            already = con.execute(
                "SELECT payload_json FROM exit_events "
                "WHERE plan_id=? AND event_type='FIRST_REAL_BUY_FILL'",
                (plan.plan_id,),
            ).fetchall()
            if len(already) > 1 or (already and json.loads(already[0][0]) != payload):
                raise ValueError("FIRST_FILL_EVENT_CONFLICT")
            if not already:
                v1._event(con,plan_id=plan.plan_id,
                          event_type="FIRST_REAL_BUY_FILL",payload=payload)
    return {"status":"ALREADY_ARMED" if existing else "V1_EXIT_ARMED",
            "plan_id":row["plan_id"],
            "timecut_policy":"GOLD_DAY_24_COMPLETE_5M_BARS"}
