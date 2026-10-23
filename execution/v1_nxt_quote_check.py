#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json

from quote_reader import QuoteTransportError, read_nxt_quote_official


def main() -> int:
    p = argparse.ArgumentParser(
        description="V1 NXT quote READ check via Kiwoom official SDK/kwcli runtime"
    )
    p.add_argument("--symbol", default="005930")
    p.add_argument("--mode", choices=["real", "demo"], default="real")
    args = p.parse_args()

    print("===== V1 NXT QUOTE CHECK — READ ONLY =====")
    print("[INFO] Reusing the official kiwoomcli credential/token runtime; no new token flow.")
    try:
        quote = read_nxt_quote_official(symbol=args.symbol, mode=args.mode)
    except (QuoteTransportError, ValueError) as exc:
        print(f"[BLOCKED] {type(exc).__name__}: {exc}")
        return 2

    print(
        json.dumps(
            {
                "symbol": quote.symbol,
                "exchange": quote.exchange,
                "query_code": quote.query_code,
                "last_price": quote.last_price,
                "captured_at": quote.captured_at.isoformat(timespec="seconds"),
                "return_code": quote.payload.get("return_code")
                if isinstance(quote.payload, dict)
                else None,
                "return_msg": quote.payload.get("return_msg")
                if isinstance(quote.payload, dict)
                else None,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print("[PASS] NXT quote read succeeded through Kiwoom official SDK ka10001.")
    print("[SAFE] READ only. No broker order endpoint was called.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
