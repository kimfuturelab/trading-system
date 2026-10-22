"""Single-day, single-share 3211 user-authorized current-price trading bridge.

Never grants the general GOLD Stage4 approval or initiates Kiwoom orders.
Order authority: a recorded explicit USER_DIRECT_3211 request dated TODAY,
broker-proven account 3211 <=100m and unmodified V0 live safeguards.
No DAY/SWING access. 3% hard stop, 3% living-mode profit target and 24
confirmed five-minute bars; target derives from the canonical master policy.
"""
from __future__ import annotations
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST=ZoneInfo("Asia/Seoul")
ROOT=Path.home()/"trading-system-stage3"
DB=ROOT/"runtime/fill-shadow/trade_events.sqlite3"
PREFIX="USER3211-SAMSUNG-1SH-"
SOURCE="USER_DIRECT_3211_ONE_TIME"
CAPITAL_CEILING=100_000_000

def tick(price:int)->int:
    if price<2000:return 1
    if price<5000:return 5
    if price<20000:return 10
    if price<50000:return 50
    if price<200000:return 100
    if price<500000:return 500
    return 1000

def floor_tick(value:int)->int:
    unit=tick(value)
    return value//unit*unit

def ceil_tick(value:int)->int:
    unit=tick(value)
    return ((value+unit-1)//unit)*unit

def build_user_trial(*,quote,capital:dict,today:str,authorized:bool)->dict:
    if not authorized:raise ValueError("USER_TRIAL_NOT_AUTHORIZED")
    if (capital.get("status")!="BROKER_CAPITAL_VERIFIED_ONLY"
            or capital.get("account_alias")!="test"
            or capital.get("account_suffix")!="3211"
            or capital.get("symbol")!="005930"
            or capital.get("max_order_qty")!=1):
        raise ValueError("3211_BROKER_OWNER_INVALID")
    assets=capital.get("estimated_assets_krw")
    cash=capital.get("orderable_cash_krw")
    if (type(assets) is not int or not (0<assets<=CAPITAL_CEILING)
            or type(cash) is not int):
        raise ValueError("USER_TRIAL_CAPITAL_NOT_BELOW_100M")
    if quote.symbol!="005930" or quote.exchange!="SOR":
        raise ValueError("USER_TRIAL_SOR_SAMSUNG_QUOTE_REQUIRED")
    at=quote.captured_at.astimezone(KST)
    elapsed=(datetime.now(KST)-at).total_seconds()
    if at.date().isoformat()!=today or not (0<=elapsed<=120):
        raise ValueError("USER_TRIAL_QUOTE_STALE")
    price=quote.last_price
    if type(price) is not int or price<=0 or cash<price:
        raise ValueError("USER_TRIAL_PRICE_NOT_ORDERABLE")
    stop=floor_tick(price*97//100)
    target=ceil_tick((price*103+99)//100)
    if not (0<stop<price<target):
        raise ValueError("USER_TRIAL_STOP_TARGET_INVALID")
    return {
        "handoff_id":PREFIX+today, "created_at":at.isoformat(timespec="seconds"),
        "trade_date":today, "symbol":"005930","stock_name":"삼성전자",
        "account_alias":"test", "planned_entry_price":price,"stop_price":stop,
        "planned_qty":1,"planned_invest_amount":price,"expected_loss":price-stop,
        "final_r":"USER_1SH_WARN_ONLY","stage_decision":"진입가능",
        "operation_mode":"생활비모드","status":"OPEN",
        "target1_price":target,
        "risk_warning":price-stop>capital.get("risk_reference_krw_1p5pct",0),
        "source":SOURCE,
    }

def trial_contract(plan:dict)->dict:
    if (not str(plan.get("handoff_id") or "").startswith(PREFIX)
            or plan.get("trade_date")!=str(plan["handoff_id"])[len(PREFIX):]
            or plan.get("account_alias")!="test"
            or plan.get("symbol")!="005930"
            or int(plan.get("planned_qty") or 0)!=1
            or plan.get("operation_mode")!="생활비모드"):
        raise ValueError("USER_TRIAL_CONTRACT_SCOPE_MISMATCH")
    price=int(plan["planned_entry_price"])
    stop=int(plan["stop_price"])
    if stop!=floor_tick(price*97//100):
        raise ValueError("USER_TRIAL_STOP_MISMATCH")
    target=ceil_tick((price*103+99)//100)
    return {
      "source":SOURCE,"handoff_id":plan["handoff_id"],
      "trade_date":plan["trade_date"],"stop_price":stop,
      "target1_price":target,"max_loss_krw":price-stop,
      "timecut_policy":"GOLD_DAY_24_COMPLETE_5M_BARS",
      "account_alias":"test","symbol":"005930","execution_qty":1,
    }

def save_user_trial(plan:dict)->dict:
    if not DB.is_file():
        raise ValueError("EXISTING_STAGE4_PLAN_DB_MISSING")
    # No multiple user plans, no changing a plan once it exists.
    with sqlite3.connect(f"file:{DB}?mode=ro",uri=True,timeout=5) as con:
        con.row_factory=sqlite3.Row
        existing=con.execute(
          "SELECT * FROM stage4_trade_plans WHERE trade_date=? AND account_alias='test' AND symbol='005930'",
          (plan["trade_date"],)).fetchall()
    if existing:
        raise ValueError("3211_TODAY_ALREADY_HAS_PLAN_NO_RESEND")
    sys.path.insert(0,str(ROOT/"fill-shadow-v1"))
    from trade_plan_link_v1 import save_plan
    saved=save_plan(plan)
    for k in ("handoff_id","trade_date","account_alias","symbol",
              "planned_entry_price","stop_price","planned_qty",
              "expected_loss","operation_mode"):
        if str(saved.get(k))!=str(plan[k]):
            raise ValueError("USER_TRIAL_PLAN_READBACK_MISMATCH:"+k)
    return saved

def preview_live()->dict:
    from pathlib import Path
    from safety import load_env_file
    from broker_cli import KiwoomCliBroker
    from quote_reader import read_quote_snapshot
    from scalp3211_capital_owner_v1 import capture_3211_capital_owner
    from scalp3211_switch_v1 import trial_authorized,read_entry_control
    load_env_file(Path.home()/"api-read-v2.env",required=True)
    sys.path.insert(0,str(Path.home()/"trading-system/collector"))
    from account_snapshot_readonly import collect
    now=datetime.now(KST)
    today=now.date().isoformat()
    capital=capture_3211_capital_owner(collect("test"))
    quote=read_quote_snapshot(KiwoomCliBroker(mode="real"),symbol="005930",exchange="SOR")
    try:
        trial=build_user_trial(quote=quote,capital=capital,today=today,authorized=True)
        return {"status":"USER_3211_ORDER_PREVIEW_ONLY",
                "trade_date":today,"quote_at":quote.captured_at.isoformat(),
                "capital_krw":capital["estimated_assets_krw"],
                "orderable_cash_krw":capital["orderable_cash_krw"],
                "buy_switch":read_entry_control()["state"],
                "trial_authorized_today":trial_authorized(today),
                "plan":trial,"contract":trial_contract(trial),
                "broker_write":False}
    except (ValueError,TypeError,KeyError) as exc:
        return {"status":"NOT_PREVIEWABLE","reason":str(exc),
                "broker_write":False}

if __name__=="__main__":
    print(json.dumps(preview_live(),ensure_ascii=False,default=str))
