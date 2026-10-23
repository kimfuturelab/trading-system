"""3211 isolated GOLD Stage0: disabled Stage5 warning, never fake human/account gates."""
from copy import deepcopy
from test_scalp3211_gold_isolated_bridge_v1 import NOW, valid_packet
from scalp3211_gold_signal_sheet_v1 import (
    decision_fields, stage0_independent_of_disabled_stage5,
)


def complete_stage0():
    packet=valid_packet()
    packet["stages"]["stage0"]={
        "status":"PENDING","source_at":"2026-10-21",
        "reason":"STAGE5_NOT_OK",
        "value":{
            "self_gate":"통과","mode_gate":"통과",
            "capital_gate":"통과","account_gate":"통과",
            "start_decision":"허용","active_account":"DAY+SWING",
            "next_operation_mode":"생활비모드",
        },
    }
    return packet


def main():
    packet=complete_stage0()
    original=deepcopy(packet)
    assert decision_fields(packet,now=NOW,modules={"stage5":"ON"})["status"]=="PENDING"
    resolved=decision_fields(packet,now=NOW,modules={"stage5":"OFF"})
    assert resolved["status"]=="READY",resolved
    assert "STAGE5_DISABLED_STAGE0_OTHER_GATES_VERIFIED" in resolved["warnings"]
    assert packet==original,"Read-only normalized view must not modify producer"
    for key,value in (
        ("self_gate","대기"),("account_gate","대기"),
        ("mode_gate","차단"),("capital_gate","대기"),
        ("start_decision","대기"),("active_account","대기"),
    ):
        bad=complete_stage0()
        bad["stages"]["stage0"]["value"][key]=value
        assert not stage0_independent_of_disabled_stage5(
            bad["stages"]["stage0"],today="2026-10-21"),key
        r=decision_fields(bad,now=NOW,modules={"stage5":"OFF"})
        assert r["status"]=="PENDING" and "STAGE0_NOT_OK" in r["reasons"],(key,r)
    for key,value in (
        ("source_at","2026-09-02"),
        ("reason","STAGE5_NOT_OK|GATE_NOT_READY"),
        ("reason","HARD_GATE_BLOCK"),
    ):
        bad=complete_stage0()
        bad["stages"]["stage0"][key]=value
        assert decision_fields(bad,now=NOW,modules={"stage5":"OFF"})["status"]=="PENDING",key
    print("PASS 3211 STAGE5 OFF isolation, self/account approval remains mandatory, no source mutation")


if __name__=="__main__":
    main()
