from __future__ import annotations

from dataclasses import asdict
from typing import Any

from sell_handoff import SellHandoff


def preview_sell_handoff(broker: Any, handoff: SellHandoff):
    """Send a V1 SELL handoff only to the broker PREVIEW path.

    Safety contract:
    - calls `preview_order`, never `submit_order`
    - no `--confirm`
    - does not mutate EXIT_PLAN state
    - caller must prepare/gate the handoff first
    """
    return broker.preview_order(
        side="sell",
        symbol=handoff.symbol,
        qty=int(handoff.qty),
        exchange=handoff.exchange,
        order_type=handoff.order_type,
        price=None,
    )


def handoff_summary(handoff: SellHandoff) -> dict[str, Any]:
    return asdict(handoff)
