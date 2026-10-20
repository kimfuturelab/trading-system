"""Isolated 3211 account CAPITAL OWNER for GOLD. Read-only, never an order permit.

Avoids pretending that the 4311 DAY + 5311 SWING weekly-settlement key
belongs to the distinct 3211 one-share test account. Risk sizing and trade
approval stay with the existing Stage4/GOLD owner; this module supplies
authenticated broker equity/cash without mutating the other two accounts.
"""
from __future__ import annotations
from typing import Any, Mapping

SYMBOL = "005930"
ACCOUNT_ALIAS = "test"
ACCOUNT_SUFFIX = "3211"
MAX_QTY = 1


def capture_3211_capital_owner(broker_receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize an actual account_snapshot_readonly.collect('test') result."""
    result = {
        "owner": "SCALP3211_ISOLATED_BROKER_CAPITAL_V1",
        "account_alias": ACCOUNT_ALIAS,
        "account_suffix": ACCOUNT_SUFFIX,
        "symbol": SYMBOL,
        "max_order_qty": MAX_QTY,
        "buy_route": "SOR",
        "leverage": 1,
        "source": "KIWOOM_ACCOUNT_SNAPSHOT_READONLY",
        "status": "BLOCK",
        "reason": [],
        "estimated_assets_krw": None,
        "orderable_cash_krw": None,
        "first_fill_stop_armed": False,
        "stage4_trade_approved": False,
        "order_allowed": False,
        "broker_write": False,
    }
    problems: list[str] = []
    if str(broker_receipt.get("alias") or "") != ACCOUNT_ALIAS:
        problems.append("NOT_TEST_ACCOUNT")
    if str(broker_receipt.get("expected_suffix") or "") != ACCOUNT_SUFFIX:
        problems.append("BROKER_SUFFIX_MISMATCH")
    if str(broker_receipt.get("status") or "").upper() != "PASS":
        problems.append("BROKER_SNAPSHOT_NOT_PASS")
    if broker_receipt.get("errors"):
        problems.append("BROKER_READ_ERRORS")
    capital = broker_receipt.get("estimated_assets")
    cash = broker_receipt.get("orderable_cash")
    if (type(capital) is not int or capital <= 0
            or type(cash) is not int or cash < 0 or cash > capital):
        problems.append("BROKER_EQUITY_OR_CASH_INVALID")
    if broker_receipt.get("cash_misoo") not in (0, "0"):
        problems.append("BROKER_CASH_MISOO_NOT_ZERO")
    positions = broker_receipt.get("positions")
    if not isinstance(positions, list):
        problems.append("BROKER_POSITIONS_UNKNOWN")
    elif any(int(p.get("qty_total") or 0) > 0 for p in positions if isinstance(p, dict)):
        problems.append("EXISTING_HOLDING_REQUIRES_MANAGEMENT")
    result["reason"] = problems
    if problems:
        return result
    result["status"] = "BROKER_CAPITAL_VERIFIED_ONLY"
    result["estimated_assets_krw"] = capital
    result["orderable_cash_krw"] = cash
    result["risk_reference_krw_1p5pct"] = capital * 15 // 1000
    # Existing Stage4 risk, live exit and deduplication remain mandatory.
    result["order_allowed"] = False
    return result
