#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json


def main() -> int:
    p = argparse.ArgumentParser(description="Kiwoom official SDK NXT quote bridge")
    p.add_argument("--symbol", required=True)
    p.add_argument("--mode", choices=["real", "demo"], default="real")
    args = p.parse_args()

    symbol = str(args.symbol or "").strip().upper()
    if len(symbol) != 6 or not symbol.isalnum():
        print(json.dumps({"ok": False, "error": "INVALID_SYMBOL"}, ensure_ascii=False))
        return 2

    try:
        from kiwoom import get_client

        client = get_client(mode=args.mode)
        response = client.fetch_page(
            api_id="ka10001",
            path="/api/dostk/stkinfo",
            body={"stk_cd": f"{symbol}_NX"},
        )
        payload = response.body
        print(json.dumps({"ok": True, "payload": payload}, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:
        # Never print credential values. Official SDK exceptions are reduced to
        # type + message only so the parent process can fail closed.
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                },
                ensure_ascii=False,
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
