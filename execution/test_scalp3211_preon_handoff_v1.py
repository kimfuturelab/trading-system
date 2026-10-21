"""Single regression covering the three ON-preparation corrections.

No Kiwoom calls, no live order, no real DB, no cron or config changes.
"""
import tempfile
from pathlib import Path

from scalp3211_exit_owner_v1 import approved_exit_contract
from scalp3211_switch_v1 import entry_switch_on, read_entry_control, set_entry_state


def main():
    approval = [""] * 29
    approval[27] = "주문 가능"  # AB19 final approval, not AC19
    approval[28] = "EXIT_DEADLINE_NOT_APPROVAL"
    order = [""] * 29
    order[1] = "3211 시험계좌"
    order[2] = "삼성전자"
    order[3] = "신규매수"
    order[6], order[7] = 200000, 1
    order[9], order[10] = 198000, 202000
    plan = {"trade_date":"2026-10-21", "account_alias":"test",
            "symbol":"005930", "planned_qty":1,
            "stage_decision":"진입가능", "planned_entry_price":200000,
            "stop_price":198000, "expected_loss":2000,
            "handoff_id":"TEST-3211"}
    contract = approved_exit_contract(
        plan=plan, trade_date="2026-10-21",
        quick_approval_row=approval, quick_order_row=order)
    assert contract is not None, "AB19 valid approval incorrectly blocked"
    assert contract["stop_price"] == 198000
    approval[27] = "주문 금지"
    assert approved_exit_contract(
        plan=plan, trade_date="2026-10-21",
        quick_approval_row=approval, quick_order_row=order) is None

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "3211-switch.json"
        assert read_entry_control(path)["state"] == "OFF"
        assert not entry_switch_on(path)
        assert set_entry_state("ON",path)["state"] == "ON"
        assert entry_switch_on(path)
        assert set_entry_state("OFF",path)["state"] == "OFF"
        assert not entry_switch_on(path)
    print("[PASS] AB19 approval + restricted 3211 exit contract + isolated OFF/ON")


if __name__ == "__main__":
    main()
