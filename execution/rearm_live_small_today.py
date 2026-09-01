#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from broker_cli import KiwoomCliBroker
from safety import ExecutionConfig, load_env_file
from v0_engine import account_mask_matches, broker_account_mask

KST = ZoneInfo("Asia/Seoul")
AUTH_ENV = Path.home() / "api-read-v2.env"
EXEC_ENV = Path.home() / "trading-execution.env"
SYMBOL = "005930"


def replace_or_add(lines: list[str], key: str, value: str) -> list[str]:
    prefix = key + "="
    out: list[str] = []
    found = False
    for line in lines:
        if line.startswith(prefix):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Re-arm existing LIVE_SMALL config for today; no order")
    p.add_argument("--i-understand-this-enables-live-small", action="store_true")
    args = p.parse_args()
    if not args.i_understand_this_enables_live_small:
        print("[BLOCKED] explicit re-arm acknowledgement flag required.")
        return 2
    if not EXEC_ENV.exists():
        print("[BLOCKED] ~/trading-execution.env missing; use configure_live_small.py first.")
        return 2

    load_env_file(AUTH_ENV, required=True)
    load_env_file(EXEC_ENV, required=True)
    cfg = ExecutionConfig.from_env()
    if not cfg.allowed_account:
        print("[BLOCKED] ALLOWED_ACCOUNT missing")
        return 2
    if SYMBOL not in cfg.allowed_symbols:
        print(f"[BLOCKED] {SYMBOL} not in ALLOWED_SYMBOLS")
        return 2
    if int(cfg.max_order_qty) != 1:
        print(f"[BLOCKED] MAX_ORDER_QTY must remain 1, got {cfg.max_order_qty}")
        return 2

    broker = KiwoomCliBroker(mode="real")
    snapshot = {"account": broker.account_list().payload}
    masked = broker_account_mask(snapshot)
    if not account_mask_matches(cfg.allowed_account, masked):
        print("[BLOCKED] configured account suffix does not match broker account")
        return 2

    today = datetime.now(KST).strftime("%Y-%m-%d")
    lines = EXEC_ENV.read_text(encoding="utf-8").splitlines()
    lines = replace_or_add(lines, "AUTO_MODE", "LIVE_SMALL")
    lines = replace_or_add(lines, "KILL_SWITCH", "OFF")
    lines = replace_or_add(lines, "ALLOW_LIVE_ORDERS", "YES")
    lines = replace_or_add(lines, "DAILY_ARM_DATE", today)
    EXEC_ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(EXEC_ENV, 0o600)

    print("===== LIVE_SMALL RE-ARMED FOR TODAY =====")
    print(f"broker_account={masked}")
    print(f"DAILY_ARM_DATE={today}")
    print("ALLOWED_SYMBOLS=005930 / MAX_ORDER_QTY=1")
    print("KILL_SWITCH=OFF / ALLOW_LIVE_ORDERS=YES")
    print("[SAFE] config only. No broker order was sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
