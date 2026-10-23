"""One-day direct 3211 current-price plan, no broker reads or writes."""
import tempfile
from datetime import datetime,timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo
from scalp3211_switch_v1 import read_entry_control,set_entry_state,authorize_trial,trial_authorized
from scalp3211_user_trial_v1 import build_user_trial,trial_contract, SOURCE, PREFIX
from scalp3211_fill_exit_bridge_v1 import register_existing_v1_plan
from exit_plan import ExitPlanStore
KST=ZoneInfo("Asia/Seoul")

def main():
    today=datetime.now(KST).date().isoformat()
    with tempfile.TemporaryDirectory() as td:
        path=Path(td)/"control.json"
        set_entry_state("OFF",path)
        with patch("scalp3211_switch_v1.CONTROL",path):
            ctrl=authorize_trial(today,path)
            assert ctrl["user_trial_date"]==today
            assert trial_authorized(today,path)
            assert read_entry_control(path)["state"]=="OFF"
            set_entry_state("ON",path)
            assert trial_authorized(today,path)
            set_entry_state("OFF",path)
            assert trial_authorized(today,path)
        cap={"status":"BROKER_CAPITAL_VERIFIED_ONLY","account_alias":"test",
             "account_suffix":"3211","symbol":"005930","max_order_qty":1,
             "estimated_assets_krw":285345,"orderable_cash_krw":285345,
             "risk_reference_krw_1p5pct":4280}
        quote=SimpleNamespace(symbol="005930",exchange="SOR",last_price=214500,
                              captured_at=datetime.now(KST))
        p=build_user_trial(quote=quote,capital=cap,today=today,authorized=True)
        assert p["planned_entry_price"]==214500
        assert p["stop_price"]==208000
        assert p["target1_price"]==221000
        assert p["expected_loss"]==6500 and p["risk_warning"] is True
        assert p["handoff_id"]==PREFIX+today and p["operation_mode"]=="생활비모드"
        contract=trial_contract(p)
        assert contract["source"]==SOURCE
        assert contract["target1_price"]==221000
        assert contract["timecut_policy"]=="GOLD_DAY_24_COMPLETE_5M_BARS"
        with tempfile.TemporaryDirectory() as db:
            store=ExitPlanStore(Path(db)/"exit.sqlite3")
            fill={"handoff_id":p["handoff_id"],"price":214500,"qty":1,
                  "first_fill_at":datetime.now(KST).isoformat(),
                  "order_no":"MOCK-NO","fill_no":"MOCK-FILL","broker_source":"FIXTURE"}
            first=register_existing_v1_plan(fill=fill,approved_exit=contract,store=store)
            assert first["status"]=="V1_EXIT_ARMED",first
            again=register_existing_v1_plan(fill=fill,approved_exit=contract,store=store)
            assert again["status"]=="ALREADY_ARMED",again
        for badcap in ({**cap,"estimated_assets_krw":100_000_001},
                       {**cap,"account_suffix":"4311"}):
            try:build_user_trial(quote=quote,capital=badcap,today=today,authorized=True)
            except ValueError:pass
            else:raise AssertionError("bad owner allowed")
        try:
            build_user_trial(quote=quote,capital=cap,today=today,authorized=False)
        except ValueError:pass
        else:raise AssertionError("unauthorized")
        try:
            build_user_trial(quote=SimpleNamespace(**{**vars(quote),
                "captured_at":datetime.now(KST)-timedelta(minutes=5)}),
                capital=cap,today=today,authorized=True)
        except ValueError:pass
        else:raise AssertionError("stale price accepted")
        print("PASS USER3211 214500 -> 208000 stop, 221000 target, 120min lifecycle; real-order=0")
if __name__=="__main__":main()
