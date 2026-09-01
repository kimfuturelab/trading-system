#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

from broker_cli import KiwoomCliBroker
from safety import ExecutionConfig, load_env_file
from state_store import StateStore

AUTH_ENV = Path.home() / "api-read-v2.env"
EXEC_ENV = Path.home() / "trading-execution.env"


def main() -> int:
    load_env_file(AUTH_ENV, required=True)
    load_env_file(EXEC_ENV, required=False)
    cfg = ExecutionConfig.from_env()

    broker = KiwoomCliBroker(mode="real")
    store = StateStore(cfg.state_db)

    orders = broker.order_fill_status().payload
    holdings = broker.holdings(basis="total", exchange="KRX").payload
    intents = store.open_intents()

    print("===== BROKER ORDER/FILL STATUS (READ-ONLY) =====")
    print(json.dumps(orders, ensure_ascii=False, indent=2))
    print("\n===== BROKER HOLDINGS (READ-ONLY) =====")
    print(json.dumps(holdings, ensure_ascii=False, indent=2))
    print("\n===== LOCAL OPEN INTENTS =====")
    print(json.dumps(intents, ensure_ascii=False, indent=2, default=str))
    print("\n[SAFE] 이 스크립트는 조회만 하며 주문을 전송하지 않습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
