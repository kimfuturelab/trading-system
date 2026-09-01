from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from exit_plan import pre_exit_gate_errors
from v0_engine import holding_qty, open_order_rows


@dataclass(frozen=True)
class SellHandoff:
    plan_id: str
    exit_intent_id: str
    symbol: str
    qty: int
    side: str = "SELL"
    exchange: str = "KRX"
    order_type: str = "market"


def prepare_sell_handoff(
    plan_row: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    local_open_intent_count: int,
) -> SellHandoff:
    """Convert a triggered EXIT_PLAN into one broker SELL handoff.

    Pure function: never calls Kiwoom and never sends an order. Broker holdings
    and open orders from the supplied snapshot are the gate truth.
    """
    symbol = str(plan_row.get("symbol") or "").strip().upper()
    remaining = int(plan_row.get("remaining_qty") or 0)
    broker_qty = holding_qty(snapshot, symbol)
    broker_opens = len(open_order_rows(snapshot))

    errors = pre_exit_gate_errors(
        plan_row,
        broker_holding_qty=broker_qty,
        broker_open_order_count=broker_opens,
        local_open_intent_count=int(local_open_intent_count),
    )
    if errors:
        raise RuntimeError("SELL_HANDOFF_BLOCKED: " + ", ".join(errors))

    plan_id = str(plan_row.get("plan_id") or "").strip()
    exit_intent_id = str(plan_row.get("exit_intent_id") or "").strip()
    if not plan_id:
        raise RuntimeError("SELL_HANDOFF_BLOCKED: PLAN_ID_MISSING")
    if not symbol:
        raise RuntimeError("SELL_HANDOFF_BLOCKED: SYMBOL_MISSING")
    if remaining <= 0:
        raise RuntimeError("SELL_HANDOFF_BLOCKED: REMAINING_QTY_NOT_POSITIVE")

    return SellHandoff(
        plan_id=plan_id,
        exit_intent_id=exit_intent_id,
        symbol=symbol,
        qty=remaining,
    )
