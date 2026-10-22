"""User-controlled 3211 module toggles. No real broker, files outside temporary dir, or trading orders."""
from pathlib import Path
from tempfile import TemporaryDirectory
from copy import deepcopy
from scalp3211_switch_v1 import (read_entry_control, set_entry_state,
    set_module_state, module_enabled, module_modes_for_capital, MODULES)
from scalp3211_gold_signal_sheet_v1 import decision_fields
from scalp3211_live_cycle_v1 import current_entry_is_allowed
from test_scalp3211_gold_isolated_bridge_v1 import valid_packet, NOW

def main():
    with TemporaryDirectory() as tmp:
        path=Path(tmp)/"switch.json"
        assert read_entry_control(path)["state"]=="OFF"
        assert all(module_enabled(name,path) for name in MODULES)
        assert set_module_state("stage1","OFF",path)["modules"]["stage1"]=="OFF"
        assert not module_enabled("stage1",path)
        set_entry_state("ON",path)
        assert read_entry_control(path)["modules"]["stage1"]=="OFF"
        set_entry_state("OFF",path)
        assert not module_enabled("stage1",path) and read_entry_control(path)["state"]=="OFF"
        set_module_state("material","OFF",path)
        assert not module_enabled("material",path)
        under=module_modes_for_capital({"estimated_assets_krw":100_000_000},path)
        over=module_modes_for_capital({"estimated_assets_krw":100_000_001},path)
        assert under["material"]=="OFF" and over["material"]=="ON"
        assert module_modes_for_capital({"estimated_assets_krw":None},path)["material"]=="ON"
        try:
            set_module_state("unknown","OFF",path)
            raise AssertionError("unrecognized module accepted")
        except ValueError: pass
        path.write_text("{CORRUPT")
        assert read_entry_control(path)["state"]=="OFF"
        assert all(module_enabled(name,path) for name in MODULES)

    packet=valid_packet()
    packet["stages"]["stage0"]["value"]["self_gate"]="통과"
    row=packet["stages"]["stage4"]["value"]
    plan={"handoff_id":row["handoff_id"],"planned_entry_price":200000,
          "stop_price":198000,"planned_qty":1,"planned_invest_amount":200000,
          "expected_loss":2000,"final_r":"0.5R","stage_decision":"진입가능"}
    assert decision_fields(packet,now=NOW)["status"]=="READY"
    assert current_entry_is_allowed(packet,plan)
    warn=deepcopy(packet)
    warn["stages"]["stage1"]["status"]="PENDING"
    assert decision_fields(warn,now=NOW)["status"]=="PENDING"
    assert not current_entry_is_allowed(warn,plan)
    off={"stage1":"OFF"}
    r=decision_fields(warn,now=NOW,modules=off)
    assert r["status"]=="READY" and "STAGE1_NOT_OK" in r["warnings"],r
    assert current_entry_is_allowed(warn,plan,modules=off)
    warn2=deepcopy(packet)
    warn2["stages"]["rejuscha"]["value"]["material"]={"status":"PENDING"}
    warn2["stages"]["rejuscha"]["status"]="PENDING"
    assert not current_entry_is_allowed(warn2,plan)
    r2=decision_fields(warn2,now=NOW,modules={"material":"OFF"})
    assert r2["status"]=="READY",r2
    assert current_entry_is_allowed(warn2,plan,modules={"material":"OFF"})
    blocked4=deepcopy(packet)
    blocked4["stages"]["stage4"]["status"]="PENDING"
    blocked4["stages"]["stage4"]["value"]["action_status"]="BLOCKED"
    blocked4["stages"]["stage4"]["value"]["final_approval"]="주문 금지"
    assert not current_entry_is_allowed(blocked4,plan)
    assert current_entry_is_allowed(blocked4,plan,modules={"stage4":"OFF"})
    assert decision_fields(blocked4,now=NOW,modules={"stage4":"OFF"})["status"]=="READY"
    # Invalid account and broker-plan identity are not silently forged by a module toggle.
    bad=deepcopy(packet)
    bad["stages"]["stage4"]["value"]["account_alias"]="day"
    assert decision_fields(bad,now=NOW,modules={x:"OFF" for x in MODULES})["status"]=="PENDING"
    assert not current_entry_is_allowed(packet,{**plan,"planned_qty":4},
                                        modules={x:"OFF" for x in MODULES})
    print("3211_MODULE_SWITCHES_PASS: persisted OFF, warn-only source, real plan identity retained; no broker orders")

if __name__=="__main__":main()
