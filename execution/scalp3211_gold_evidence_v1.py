"""Read-only GOLD evidence check for a 3211 Stage4 handoff.

The legacy packet is SHADOW_READ_ONLY: this proves input consistency but
does not turn its shadow status into a live order permission.
"""


def evidence_errors(packet, *, trade_date, plan):
    errors = []
    if packet.get("trade_date") != trade_date:
        errors.append("GOLD_WRONG_TRADE_DATE")
    stages = packet.get("stages") or {}
    for name in ("stage1", "stage2", "rejuscha", "entry", "stage4"):
        if (stages.get(name) or {}).get("status") != "OK":
            errors.append("GOLD_" + name.upper() + "_NOT_OK")
    regime = stages.get("stage1") or {}
    if regime.get("source_at") != trade_date:
        errors.append("REGIME_SOURCE_DATE_MISMATCH")
    entry = (stages.get("entry") or {}).get("value") or {}
    stage4 = (stages.get("stage4") or {}).get("value") or {}
    if entry.get("action_status") != "READY_CANDIDATE":
        errors.append("ENTRY_ACTION_NOT_READY")
    if entry.get("target_symbol") != "005930":
        errors.append("ENTRY_SYMBOL_NOT_SAMSUNG")
    if stage4.get("action_status") != "READY":
        errors.append("STAGE4_ACTION_NOT_READY")
    if stage4.get("handoff_id") != plan.get("handoff_id"):
        errors.append("STAGE4_HANDOFF_NOT_IDENTICAL")
    if stage4.get("symbol_code") != "005930":
        errors.append("STAGE4_SYMBOL_NOT_IDENTICAL")
    if stage4.get("entry_price") != plan.get("planned_entry_price"):
        errors.append("STAGE4_PRICE_NOT_IDENTICAL")
    if stage4.get("stop_price") != plan.get("stop_price"):
        errors.append("STAGE4_STOP_NOT_IDENTICAL")
    if stage4.get("qty") != 1:
        errors.append("STAGE4_QTY_NOT_ONE")
    if not stage4.get("final_r") or stage4.get("final_r") != plan.get("final_r"):
        errors.append("STAGE4_R_NOT_IDENTICAL")
    if (stages.get("rejuscha") or {}).get("value", {}).get("action_status") != "READY":
        errors.append("REJUSCHA_ACTION_NOT_READY")
    return errors
