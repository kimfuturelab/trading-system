from __future__ import annotations


def tick_size(price: int) -> int:
    p = int(price)
    if p <= 0:
        raise ValueError("price must be positive")
    if p < 2_000:
        return 1
    if p < 5_000:
        return 5
    if p < 20_000:
        return 10
    if p < 50_000:
        return 50
    if p < 200_000:
        return 100
    if p < 500_000:
        return 500
    return 1_000


def aggressive_limit_price(last_price: int, *, side: str, ticks: int = 2) -> int:
    """Return a valid aggressive NXT limit price around the latest trade.

    BUY crosses upward by `ticks`; SELL crosses downward by `ticks`.
    This is a bounded-price substitute for the unavailable generic market route.
    It does not guarantee execution and must still be reconciled against broker truth.
    """
    p = int(last_price)
    t = tick_size(p)
    n = int(ticks)
    if n <= 0:
        raise ValueError("ticks must be positive")
    side = str(side or "").strip().upper()
    if side == "BUY":
        raw = p + t * n
        return ((raw + t - 1) // t) * t
    if side == "SELL":
        raw = max(t, p - t * n)
        return (raw // t) * t
    raise ValueError("side must be BUY|SELL")
