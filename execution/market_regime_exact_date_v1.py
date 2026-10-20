"""Read-only exact-trade-date market regime selector for an overnight precomputed sheet.

Unlike the dashboard card, the history table contains *both* the current session
and the already published following session. Trading decisions must never copy
the next-session regime into the current 08:00–20:00 SOR session.
"""
from __future__ import annotations
from datetime import date, datetime
from typing import Any

KST_DATE = "%Y-%m-%d"
COL_DATE, COL_LABEL, COL_TIME, COL_STRATEGY, COL_CODE = 0, 2, 3, 4, 39
VALID_REGIMES = frozenset({"BIG_BLUE", "BLUE", "GREEN", "RED+", "RED"})


def _as_iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        try:
            return date.fromisoformat(text[:10]).isoformat()
        except ValueError:
            return ""
    return ""


def select_market_regime(history: list[list[Any]], trade_date: str) -> dict[str, Any]:
    """Fail closed if the exact date row is missing or ambiguous.

    Expected range: 시장국면_개선판!A16:AN60 (not A1:AP8 dashboard).
    Header A15 is excluded. H '기록 확정' is a trade-journal switch, NOT
    a requirement to recognize the market regime.
    """
    target = _as_iso(trade_date)
    if not target:
        return {"status": "BLOCK", "reason": "TRADE_DATE_INVALID", "source_at": None}
    matches = [r for r in history if r and _as_iso(r[COL_DATE]) == target]
    if len(matches) != 1:
        return {
            "status": "PENDING" if not matches else "DATA_CONFLICT",
            "reason": "TRADE_DATE_ROW_MISSING" if not matches else "DUPLICATE_TRADE_DATE_ROWS",
            "source_at": None,
        }
    r = matches[0]
    if len(r) <= COL_CODE:
        return {"status": "PENDING", "reason": "REGIME_CODE_COLUMN_MISSING", "source_at": target}
    label, allowed_time, allowed_strategy, code = [
        str(r[i] or "").strip() for i in (COL_LABEL, COL_TIME, COL_STRATEGY, COL_CODE)
    ]
    if not all((label, allowed_time, allowed_strategy)) or code not in VALID_REGIMES:
        return {"status": "PENDING", "reason": "REGIME_FIELDS_MISSING_OR_INVALID", "source_at": target}
    return {
        "status": "OK",
        "reason": "",
        "source": "sheet:시장국면_개선판.A16:AN60:EXACT_TRADE_DATE",
        "source_at": target,
        "value": {
            "regime_code": code, "label": label,
            "allowed_time": allowed_time, "allowed_strategy": allowed_strategy,
            # The dashboard's max_hold belongs to tomorrow after 16:00.
            # Never inherit it when selecting today's row.
            "max_hold": "기존 보유 청산만" if code == "RED" else None,
            "trade_date": target,
            "date_verified": True,
        },
    }
