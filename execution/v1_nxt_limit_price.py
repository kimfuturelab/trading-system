#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

from broker_cli import KiwoomCliBroker
from nxt_order_policy import aggressive_limit_price
from quote_reader import read_quote_snapshot
from safety import load_env_file

DEFAULT_AUTH_ENV = Path.home() / "api-read-v2.env"


def main() -> int:
    p = argparse.ArgumentParser(description="READ-only NXT aggressive limit price helper")
    p.add_argument("--symbol", default="005930")
    p.add_argument("--side", choices=["buy", "sell"], required=True)
    p.add_argument("--ticks", type=int, default=2)
    p.add_argument("--auth-env", default=str(DEFAULT_AUTH_ENV))
    p.add_argument("--mode", choices=["real", "demo"], default="real")
    p.add_argument("--plain", action="store_true")
    args = p.parse_args()

    load_env_file(Path(args.auth_env), required=True)
    broker = KiwoomCliBroker(mode=args.mode)
    q = read_quote_snapshot(broker, symbol=args.symbol, exchange="NXT")
    px = aggressive_limit_price(q.last_price, side=args.side, ticks=args.ticks)
    if args.plain:
        print(px)
        return 0
    print("===== NXT LIMIT PRICE — READ ONLY =====")
    print(f"symbol={q.symbol} exchange=NXT last_price={q.last_price}")
    print(f"side={args.side.upper()} ticks={args.ticks} limit_price={px}")
    print("[SAFE] quote calculation only. No broker order was sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
