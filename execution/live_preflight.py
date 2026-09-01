#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from broker_cli import KiwoomCliBroker
from capacity import buy_capacity_errors, capacity_summary
from safety import validate_order_request
from state_store import StateStore
from v0_engine import (
    load_runtime,
    read_and_save_snapshot,
    reconciliation_report,
    semantic_order_errors,
    snapshot_summary,
    sync_open_intents,
)

DEFAULT_AUTH_ENV = Path.home() / "api-read-v2.env"
DEFAULT_EXEC_ENV = Path.home() / "trading-execution.env"


def main() -> int:
    p = argparse.ArgumentParser(description="V0 LIVE_SMALL no-order preflight")
    p.add_argument("--symbol", default="005930")
    p.add_argument("--qty", type=int, default=1)
    p.add_argument(
        "--chance-price",
        type=int,
        required=True,
        help="Kiwoom chance capacity simulation reference price. BUY gate compares max orderable amount >= price*qty.",
    )
    p.add_argument("--auth-env", default=str(DEFAULT_AUTH_ENV))
    p.add_argument("--exec-env", default=str(DEFAULT_EXEC_ENV))
    args = p.parse_args()

    cfg = load_runtime(Path(args.auth_env), Path(args.exec_env))
    broker = KiwoomCliBroker(mode="real")
    store = StateStore(cfg.state_db)

    snapshot = read_and_save_snapshot(broker, store)
    transitions = sync_open_intents(store, snapshot)
    report = reconciliation_report(cfg, store, snapshot)

    # Symbol-specific Kiwoom chance is the sole BUY-capacity truth in V0.
    # 주문가능현금/예수금 are displayed only as references and never block.
    chance = broker.order_chance(
        symbol=args.symbol,
        side="buy",
        price=args.chance_price,
    )
    chance_payload = chance.payload

    reasons = validate_order_request(
        cfg,
        account=cfg.allowed_account,
        symbol=args.symbol,
        qty=args.qty,
        side="BUY",
        intent_id="PREFLIGHT-ONLY-NOT-RESERVED",
    )
    reasons.extend(
        semantic_order_errors(
            snapshot,
            side="BUY",
            symbol=args.symbol,
            qty=args.qty,
        )
    )
    reasons.extend(report["block_reasons"])
    reasons.extend(
        buy_capacity_errors(
            chance_payload,
            qty=args.qty,
            reference_price=args.chance_price,
        )
    )

    # Remove the legacy cash-only reason if an older v0_engine is still loaded.
    # Capacity truth is now exclusively the chance-derived maximum amount.
    reasons = [r for r in reasons if r != "NO_ORDERABLE_CASH"]

    # stable unique ordering
    deduped: list[str] = []
    for item in reasons:
        if item not in deduped:
            deduped.append(item)

    print("===== V0 LIVE_SMALL PREFLIGHT — NO ORDER SENT =====")
    print(json.dumps(snapshot_summary(snapshot), ensure_ascii=False, indent=2))
    print("===== MAX ORDERABLE CAPACITY — SOURCE OF TRUTH =====")
    cap = capacity_summary(chance_payload)
    cap["reference_price"] = args.chance_price
    cap["qty"] = args.qty
    cap["required_notional"] = args.chance_price * args.qty
    print(json.dumps(cap, ensure_ascii=False, indent=2))
    print("===== INTENT SYNC =====")
    print(json.dumps(transitions, ensure_ascii=False, indent=2))
    print("===== GATE =====")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"AUTO_MODE={cfg.auto_mode}")
    print(f"KILL_SWITCH={'ON' if cfg.kill_switch else 'OFF'}")
    print(f"ALLOW_LIVE_ORDERS={'YES' if cfg.allow_live_orders else 'NO'}")
    print(f"DAILY_ARM_DATE={cfg.daily_arm_date}")
    print(f"ALLOWED_SYMBOLS={','.join(sorted(cfg.allowed_symbols)) or '(none)'}")
    print(f"MAX_ORDER_QTY={cfg.max_order_qty}")

    if deduped:
        print("[BLOCKED] " + ", ".join(deduped))
        print("[SAFE] 주문은 전송되지 않았습니다.")
        return 2

    preview = broker.preview_order(
        side="buy",
        symbol=args.symbol,
        qty=args.qty,
        exchange="KRX",
        order_type="market",
    )
    print("===== BROKER PREVIEW — NOT SENT =====")
    print(preview.text)
    print("[PASS] LIVE_SMALL BUY preflight passed. NO ORDER SENT.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
