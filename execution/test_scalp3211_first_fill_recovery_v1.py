#!/usr/bin/env python3
"""Fixture: broker already holds first BUY share before its V1 exit is armed.
No network, no Kiwoom APIs, no orders, no persistent database writes.
"""
from contextlib import ExitStack
from datetime import datetime
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import scalp3211_live_cycle_v1 as engine

PLAN = {
    "handoff_id": "3211-FILL-RESTART-TEST",
    "trade_date": datetime.now(engine.KST).date().isoformat(),
    "account_alias": "test", "symbol": "005930",
    "planned_qty": 1, "planned_entry_price": 200000,
    "stop_price": 198000, "expected_loss": 2000,
    "planned_invest_amount": 200000,
    "stage_decision": "진입가능", "status": "OPEN",
}
FILL = {"handoff_id": PLAN["handoff_id"], "qty": 1,
        "order_no": "fixture-ack", "fill_no": "fixture-fill",
        "price": 200000, "first_fill_at": datetime.now(engine.KST).isoformat()}
CONTRACT = {"source":"STAGE4_APPROVED","handoff_id":PLAN["handoff_id"],
            "stop_price":198000,"target1_price":202000,
            "timecut_policy":"GOLD_DAY_24_COMPLETE_5M_BARS"}

def main():
    cfg = SimpleNamespace(allowed_account="account3211",
                          allowed_symbols=frozenset({"005930"}),max_order_qty=1,state_db="FIXTURE-NO-WRITE")
    with ExitStack() as st:
        st.enter_context(patch.dict(sys.modules, {
            "account_snapshot_readonly":SimpleNamespace(collect=lambda x:{})
        }))
        st.enter_context(patch.object(engine,"load_env_file",return_value=None))
        st.enter_context(patch.object(engine.ExecutionConfig,"from_env",return_value=cfg))
        capital=st.enter_context(patch.object(engine,"capture_3211_capital_owner",
                              return_value={"status":"BLOCK","reason":["EXISTING_HOLDING_REQUIRES_MANAGEMENT"]}))
        stored=st.enter_context(patch.object(engine,"ExitPlanStore"))
        stored.return_value.list_plans.return_value=[]
        plans=st.enter_context(patch.object(engine,"stage4_plans",return_value=[PLAN]))
        broker=st.enter_context(patch.object(engine,"KiwoomCliBroker"))
        broker.return_value.read_snapshot.return_value={}
        state=st.enter_context(patch.object(engine,"StateStore"))
        state.return_value.get_intent.return_value={"status":"FILLED"}
        state.return_value.list_intents.return_value=[{"status":"FILLED"}]
        st.enter_context(patch.object(engine,"sync_open_intents"))
        st.enter_context(patch.object(engine,"intent_id_from_stage4",return_value="TEST-BUY-ID"))
        st.enter_context(patch.object(engine,"entry_switch_on",return_value=False))
        fill=st.enter_context(patch.object(engine,"verified_first_fill",return_value=FILL))
        contract=st.enter_context(patch.object(engine,"approved_contract_for_cycle",return_value=CONTRACT))
        register=st.enter_context(patch.object(engine,"register_existing_v1_plan",
                           return_value={"status":"V1_EXIT_ARMED"}))
        order=st.enter_context(patch.object(engine,"cmd_live_order"))

        # OFF must not disable pre-existing first-fill exit recovery.
        result=engine.once(execute=False)
        assert result["state"]=="FIRST_FILL_PROTECTED",result
        assert register.call_count==1 and fill.call_count==1
        assert contract.call_args.kwargs["allow_new"] is False
        order.assert_not_called()
        print("PASS filled 3211 1-share holding attaches V1 exit with entry OFF")

        # An unexplained holding MUST remain blocked.
        state.return_value.get_intent.return_value=None
        result=engine.once(execute=False)
        assert result["state"]=="HALT_UNPROTECTED_3211_HOLDING",result
        print("PASS held share without authenticated fill HALT, no BUY")

        state.return_value.get_intent.return_value={"status":"OPEN"}
        result=engine.once(execute=False)
        assert result["state"]=="HALT_UNPROTECTED_3211_HOLDING",result
        print("PASS open BUY order plus held share HALT, no SELL fabrication")

        state.return_value.get_intent.return_value={"status":"FILLED"}
        fill.return_value=None
        result=engine.once(execute=False)
        assert result["state"]=="HALT_UNPROTECTED_3211_HOLDING",result
        print("PASS broker share without corroborated fill HALT")
        fill.return_value=FILL

        plans.return_value=[]
        result=engine.once(execute=False)
        assert result["state"]=="HALT_UNPROTECTED_3211_HOLDING",result
        print("PASS held share without matched Stage4 plan HALT")
        plans.return_value=[PLAN]

        capital.return_value={"status":"BROKER_CAPITAL_VERIFIED_ONLY","reason":[]}
        plans.return_value=[]
        result=engine.once(execute=False)
        assert result["state"]=="WAIT_STAGE4_ONE_APPROVED_PLAN",result
        print("PASS flat account still waits for original Stage4 approval")
        order.assert_not_called()

    print("3211_FIRST_FILL_PROTECTION_FIXTURE_PASS: no network or broker write")

if __name__=="__main__":
    main()
