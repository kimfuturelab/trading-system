#!/usr/bin/env python3
"""Pre-ON proof: isolated SOR BUY handoff, real fill reconciliation, V1 arm.
All broker/order/SQLite edges are mocks; no real account or network writes.
"""
from contextlib import ExitStack
from datetime import datetime
import sys
from types import SimpleNamespace
from scalp3211_user_trial_v1 import PREFIX, SOURCE
from unittest.mock import patch

import scalp3211_live_cycle_v1 as engine

def main():
    today = datetime.now(engine.KST).date().isoformat()
    plan = {
        "handoff_id":PREFIX+today,"trade_date":today,
        "account_alias":"test","symbol":"005930","planned_qty":1,
        "planned_entry_price":214500,"stop_price":208000,
        "planned_invest_amount":214500,"expected_loss":6500,
        "final_r":"USER_1SH_WARN_ONLY","stage_decision":"진입가능","status":"OPEN",
        "created_at":datetime.now(engine.KST).isoformat(timespec="seconds")
    }
    packet = {"trade_date":today,"stages":{
        "stage0":{"value":{"self_gate":"통과"}},
        "stage1":{"status":"OK","source_at":today,
                  "value":{"allowed_strategy":"돌파","max_hold_stage":"DAY"}},
        "stage2":{"status":"OK"},
        "rejuscha":{"status":"OK","value":{
            "action_status":"READY","executable_actions":["돌파"]}},
        "entry":{"status":"OK","value":{
            "action_status":"READY_CANDIDATE","target_symbol":"005930",
            "approved_method":"돌파"}},
        "stage4":{"status":"OK","value":{
            "action_status":"READY","handoff_id":plan["handoff_id"],
            "symbol_code":"005930","entry_price":200000,
            "stop_price":198000,"qty":1,"final_r":"0.50R",
            "approved_method":"돌파"}}
    }}
    assert not engine.current_entry_is_allowed(packet,plan)  # user trial uses separate explicit approval
    cfg = SimpleNamespace(
        allowed_account="test3211",allowed_symbols=frozenset({"005930"}),
        max_order_qty=1,state_db="MOCKED-SQLITE-NOT-USED",
        kill_switch=False,allow_live_orders=True,daily_arm_date=today
    )
    capital = {"status":"BROKER_CAPITAL_VERIFIED_ONLY","reason":[],
               "orderable_cash_krw":285345,"risk_reference_krw_1p5pct":4280,
               "estimated_assets_krw":285345}
    contract = {"source":SOURCE,"handoff_id":plan["handoff_id"],
                "stop_price":208000,"target1_price":221000,
                "max_loss_krw":6500,
                "timecut_policy":"GOLD_DAY_24_COMPLETE_5M_BARS"}
    fill = {"handoff_id":plan["handoff_id"],"price":214500,"qty":1,
            "first_fill_at":datetime.now(engine.KST).isoformat(),
            "order_no":"BROKER-FIXTURE-NO","fill_no":"BROKER-FIXTURE-FILL",
            "broker_source":"FIXTURE_ONLY"}
    with ExitStack() as stack:
        stack.enter_context(patch.dict(sys.modules,{
            "account_snapshot_readonly":SimpleNamespace(collect=lambda x: {})
        }))
        stack.enter_context(patch.object(engine,"load_env_file",return_value=None))
        stack.enter_context(patch.object(engine.ExecutionConfig,"from_env",return_value=cfg))
        owner=stack.enter_context(patch.object(
            engine,"capture_3211_capital_owner",return_value=capital))
        store=stack.enter_context(patch.object(engine,"ExitPlanStore"))
        store.return_value.list_plans.return_value=[]
        plans=stack.enter_context(patch.object(engine,"stage4_plans",return_value=[plan]))
        stack.enter_context(patch.object(engine,"entry_switch_on",return_value=True))
        stack.enter_context(patch.object(engine,"trial_authorized",return_value=True))
        stack.enter_context(patch("quote_reader.read_quote_snapshot",
                return_value=SimpleNamespace(last_price=214500)))
        stack.enter_context(patch.object(engine,"original_packet",return_value=packet))
        stack.enter_context(patch.object(engine,"protective_watch_runtime_ready",return_value=True))
        approved=stack.enter_context(patch.object(
            engine,"approved_contract_for_cycle",return_value=contract))
        broker=stack.enter_context(patch.object(engine,"KiwoomCliBroker"))
        broker.return_value.read_snapshot.return_value={"open_orders":{"미체결":[]}}
        state=stack.enter_context(patch.object(engine,"StateStore"))
        state.return_value.get_intent.return_value=None
        state.return_value.open_intents.return_value=[]
        state.return_value.list_intents.return_value=[{"status":"FILLED"}]
        stack.enter_context(patch.object(engine,"sync_open_intents"))
        stack.enter_context(patch.object(engine,"intent_id_from_stage4",
                         return_value="SCALP3211-BUY-FIXTURE"))
        stack.enter_context(patch.object(engine,"verified_first_fill",return_value=fill))
        register=stack.enter_context(patch.object(
            engine,"register_existing_v1_plan",return_value={"status":"V1_EXIT_ARMED"}))
        submit=stack.enter_context(patch.object(engine,"cmd_live_order"))
        def fake_submitted(args):
            assert (args.symbol,args.qty,args.exchange,args.order_type) == (
                "005930",1,"SOR","limit")
            assert args.price=="214500" and args.side=="buy"
            assert args.intent_id=="SCALP3211-BUY-FIXTURE"
            state.return_value.get_intent.return_value={"status":"FILLED"}
            owner.return_value={"status":"BLOCK","reason":[
                "EXISTING_HOLDING_REQUIRES_MANAGEMENT"]}
            return 0
        submit.side_effect=fake_submitted
        result=engine.once(execute=True)
        assert result["state"]=="V0_BUY_COMMAND_RETURNED",result
        assert result["exit_code"]==0
        assert result["post_submit_fill_and_exit"]["state"]=="FIRST_FILL_PROTECTED"
        assert submit.call_count==1 and register.call_count==1
        assert approved.call_args.kwargs["allow_new"] is False
        result=engine.once(execute=True)
        assert result["state"]=="FIRST_FILL_PROTECTED",result
        assert submit.call_count==1,"duplicate buy after restart"
        print("PASS approved SOR 1-share BUY->real-fill verification->V1 protect->restart no second BUY")

        # No method proof, no chance to BUY. This is an independent scenario.
        state.return_value.get_intent.return_value=None
        owner.return_value=capital
        bad={"trade_date":today,"stages":dict(packet["stages"])}
        bad["stages"]["stage4"]={
            "status":"OK","value":dict(packet["stages"]["stage4"]["value"],
                                       approved_method="미승인 매매법")}
        assert not engine.current_entry_is_allowed(bad,plan)
        print("PASS original method mismatch blocked, no invented approval")
    print("3211_PREON_BUY_FILL_CYCLE_PASS no broker API/orders/DB writes")

if __name__=="__main__":
    main()
