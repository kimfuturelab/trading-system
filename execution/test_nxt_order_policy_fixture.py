#!/usr/bin/env python3
from __future__ import annotations

from broker_cli import KiwoomCliBroker
from nxt_order_policy import aggressive_limit_price, tick_size


def main() -> int:
    print("===== NXT ORDER POLICY FIXTURE =====")
    print("[SAFE] no network and no broker write")

    assert tick_size(256500) == 500
    assert aggressive_limit_price(256500, side="BUY", ticks=2) == 257500
    assert aggressive_limit_price(256500, side="SELL", ticks=2) == 255500
    print("[PASS] Samsung-price tick=500; BUY +2 ticks / SELL -2 ticks")

    try:
        KiwoomCliBroker._order_args(
            side="buy", exchange="NXT", symbol="005930", qty=1,
            order_type="market", price=None, mode="real", confirm=True,
        )
    except ValueError as exc:
        assert "NXT_MARKET_ORDER_NOT_SUPPORTED" in str(exc)
    else:
        raise AssertionError("NXT market order must be blocked locally")
    print("[PASS] NXT market request blocked before broker command construction")

    args = KiwoomCliBroker._order_args(
        side="buy", exchange="NXT", symbol="005930", qty=1,
        order_type="limit", price="257500", mode="real", confirm=True,
    )
    assert "--order-type" in args and "limit" in args
    assert "--price" in args and "257500" in args
    print("[PASS] NXT priced limit route remains available")
    print("[SAFE] no network or real order call occurred")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
