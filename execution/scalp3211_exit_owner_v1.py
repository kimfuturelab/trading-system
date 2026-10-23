"""Read an approved 1-share exit target from the EXISTING daily Stage4 sheet.

The original quick-calculation approval and order-detail row are authoritative.
Blank, stale, cross-account or mismatching output never creates a target.
"""
from __future__ import annotations

from typing import Any


def _n(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(str(value).replace("₩", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def approved_exit_contract(*, plan: dict, trade_date: str,
                           quick_approval_row: list,
                           quick_order_row: list) -> dict | None:
    def val(r, idx):
        return r[idx] if len(r) > idx else None

    if (plan.get("trade_date") != trade_date
            or plan.get("account_alias") != "test"
            or plan.get("symbol") != "005930"
            or _n(plan.get("planned_qty")) != 1
            or plan.get("stage_decision") not in ("진입가능", "축소진입")):
        return None
    # ORIGINAL 4단계 daily A18:AC19: '최종승인' is Y19 (index 24).
    # AB19 is the separate ENTRY_HOLD_MODE; AC19 is EXIT_DEADLINE.
    # Never let an adjacent, unrelated cell grant a real order.
    if str(val(quick_approval_row, 24) or "").strip() not in (
            "주문 승인", "주문 가능", "매수 가능", "매수 승인"):
        return None
    label = str(val(quick_order_row, 1) or "").strip()
    if label not in ("3211", "3211 시험계좌"):
        return None
    symbol = str(val(quick_order_row, 2) or "").strip()
    if symbol not in ("005930", "삼성전자"):
        return None
    if str(val(quick_order_row, 3) or "").strip() != "신규매수":
        return None
    price, qty, stop, target = (
        _n(val(quick_order_row, 6)), _n(val(quick_order_row, 7)),
        _n(val(quick_order_row, 9)), _n(val(quick_order_row, 10)))
    if (price != _n(plan.get("planned_entry_price")) or qty != 1
            or stop != _n(plan.get("stop_price"))
            or not target or target <= price):
        return None
    loss = _n(plan.get("expected_loss"))
    if loss is None or loss != price - stop:
        return None
    return {
        "source": "STAGE4_APPROVED",
        "handoff_id": str(plan["handoff_id"]),
        "trade_date": trade_date,
        "stop_price": stop, "target1_price": target,
        "max_loss_krw": loss,
        "timecut_policy": "GOLD_DAY_24_COMPLETE_5M_BARS",
        "execution_qty": 1,
        "account_alias": "test",
        "symbol": "005930",
    }
