"""Pure 3211 watch decision tests; no market data/remote order calls."""
from dataclasses import replace
from pathlib import Path
from safety import ExecutionConfig
from scalp3211_exit_watch_v1 import evaluate_watch

def main():
    cfg=ExecutionConfig(auto_mode="OFF",kill_switch=True,allow_live_orders=False,
        daily_arm_date="",allowed_account="1234563211",
        allowed_symbols=frozenset({"005930"}),max_order_qty=1,
        state_db=Path("/tmp/test-3211-v0.sqlite3"),
        auth_env=Path("/tmp/test-3211-auth.env"))
    cash={"status":"BROKER_CAPITAL_VERIFIED_ONLY","reason":[]}
    held={"status":"BLOCK","reason":["EXISTING_HOLDING_REQUIRES_MANAGEMENT"]}
    plan={"plan_id":"SCALP3211-TEST-01","symbol":"005930",
          "qty":1,"status":"EXIT_ARMED"}
    assert evaluate_watch(capital=cash,account_config=cfg,
                          plans=[],execute=False,active_window=True)["status"]=="IDLE_FLAT"
    assert evaluate_watch(capital=held,account_config=cfg,
                          plans=[],execute=False,active_window=True)["status"]=="HALT"
    assert evaluate_watch(capital=held,account_config=cfg,
                          plans=[plan],execute=False,active_window=True)["status"]=="READY_SHADOW"
    assert evaluate_watch(capital=held,account_config=cfg,
                          plans=[plan],execute=True,active_window=True)["status"]=="RUN_LIVE_EXIT_ONCE"
    assert evaluate_watch(capital=held,account_config=cfg,
                          plans=[{**plan,"status":"EXIT_SUBMITTED"}],
                          execute=True,active_window=True)["status"]=="RECONCILE_ONLY"
    assert evaluate_watch(capital=cash,account_config=cfg,
                          plans=[{**plan,"status":"EXIT_SUBMITTED"}],
                          execute=False,active_window=True)["status"]=="RECONCILE_ONLY"
    for data in [
        {"capital":{**held,"reason":held["reason"]+["BROKER_SUFFIX_MISMATCH"]}},
        {"account_config":replace(cfg,allowed_account="1234564311")},
        {"account_config":replace(cfg,allowed_symbols=frozenset({"000660","005930"}))},
        {"account_config":replace(cfg,max_order_qty=2)},
        {"plans":[plan,{**plan,"plan_id":"SCALP3211-TEST-02"}]},
        {"plans":[{**plan,"plan_id":"DAY-4311-TEST"}]},
    ]:
        x=dict(capital=held,account_config=cfg,plans=[plan],execute=True,
               active_window=True)
        x.update(data)
        assert evaluate_watch(**x)["status"]=="HALT", data
    assert evaluate_watch(capital=held,account_config=cfg,plans=[plan],
                          execute=True,active_window=False)["status"]=="HALT"
    print("[PASS] 3211 isolated SELL watcher: flat, held, duplicate, provenance, no resend, 08–20")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
