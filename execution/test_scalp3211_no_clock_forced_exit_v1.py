"""Prove an actual 3211 first-fill EXIT is never armed with a 19:55 deadline.

Uses only a temporary SQLite and fixture fill; no Kiwoom/API/orders.
"""
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo
from exit_plan import ExitPlanStore, evaluate_exit_trigger
from v1_live_exit_once import _plan_from_row
from scalp3211_fill_exit_bridge_v1 import register_existing_v1_plan
from scalp3211_exit_readonly_audit_v1 import classify_exit

KST=ZoneInfo("Asia/Seoul")


def main():
    for hh, mm in ((9, 0), (19, 54), (19, 55), (19, 59)):
        first=datetime(2026, 10, 22, hh, mm, tzinfo=KST)
        handoff=f"USER3211-SAMSUNG-1SH-2026-10-22-{hh:02d}{mm:02d}"
        fill={"handoff_id":handoff,"qty":1,"price":214000,
              "first_fill_at":first.isoformat(timespec="seconds"),
              "order_no":f"FAKE-{hh}{mm}","fill_no":"FAKE-FILL",
              "broker_source":"FIXTURE"}
        contract={"source":"USER_DIRECT_3211_ONE_TIME",
                  "handoff_id":handoff,"stop_price":207500,
                  "target1_price":220500,"max_loss_krw":6500,
                  "timecut_policy":"GOLD_DAY_24_COMPLETE_5M_BARS"}
        with TemporaryDirectory(prefix="3211-clock-test-") as td:
            v1=ExitPlanStore(Path(td)/"v1.sqlite3")
            out=register_existing_v1_plan(fill=fill,approved_exit=contract,store=v1)
            row=v1.get_plan(out["plan_id"])
            assert row["timecut_at"].startswith("9999-12-31T23:59:"),row
            # 19:55 does not liquidate without a real stop/target or 24 bars.
            at=datetime(2026,10,22,19,59,tzinfo=KST)
            assert evaluate_exit_trigger(_plan_from_row(row),last_price=214000,now=at) is None
            warning=classify_exit(plan=row,submitted_at=None,now=at)
            assert not warning["requires_review"],warning
            assert not any("1955" in item or "CLOSE" in item
                           for item in warning["reasons"]),warning
            # Independent price-triggered SELL remains intact.
            assert evaluate_exit_trigger(_plan_from_row(row),
                                         last_price=207500,now=at).reason=="STOP"
            assert evaluate_exit_trigger(_plan_from_row(row),
                                         last_price=220500,now=at).reason=="TAKE_PROFIT"
            again=register_existing_v1_plan(fill=fill,approved_exit=contract,store=v1)
            assert again["status"]=="ALREADY_ARMED",again
        print(f"PASS 3211 fill {hh:02d}:{mm:02d} -> 19:59 no clock sell; STOP/TARGET retained")

if __name__=="__main__":
    main()
