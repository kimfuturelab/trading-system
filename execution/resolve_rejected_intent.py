#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from broker_cli import KiwoomCliBroker
from safety import ExecutionConfig, load_env_file
from state_store import StateStore
from v0_engine import holding_qty, open_order_rows

DEFAULT_AUTH_ENV = Path.home() / "api-read-v2.env"
DEFAULT_EXEC_ENV = Path.home() / "trading-execution.env"


def load_runtime(auth_env: Path, exec_env: Path) -> ExecutionConfig:
    load_env_file(auth_env, required=True)
    load_env_file(exec_env, required=False)
    return ExecutionConfig.from_env()


def main() -> int:
    p = argparse.ArgumentParser(description="Resolve a broker-declared rejected intent to terminal REJECTED")
    p.add_argument("--intent-id", required=True)
    p.add_argument("--auth-env", default=str(DEFAULT_AUTH_ENV))
    p.add_argument("--exec-env", default=str(DEFAULT_EXEC_ENV))
    p.add_argument("--mode", choices=["real", "demo"], default="real")
    p.add_argument("--i-understand-this-only-mutates-local-state", action="store_true")
    args = p.parse_args()

    if not args.i_understand_this_only_mutates_local_state:
        print("[BLOCKED] explicit local-state acknowledgement is required")
        return 2

    cfg = load_runtime(Path(args.auth_env), Path(args.exec_env))
    store = StateStore(cfg.state_db)
    row = store.get_intent(args.intent_id)
    if row is None:
        print("[BLOCKED] intent not found")
        return 2

    status = str(row.get("status") or "")
    order_no = str(row.get("broker_order_no") or "").strip()
    last_error = str(row.get("last_error") or "")
    symbol = str(row.get("symbol") or "").strip().upper()
    side = str(row.get("side") or "").strip().upper()

    if status != "SUBMIT_ERROR_REVIEW":
        print(f"[BLOCKED] status must be SUBMIT_ERROR_REVIEW, got {status}")
        return 2
    if order_no:
        print("[BLOCKED] broker_order_no exists; reconcile broker truth instead")
        return 2
    if "return_code=20" not in last_error:
        print("[BLOCKED] no definitive Kiwoom return_code=20 rejection evidence")
        return 2

    broker = KiwoomCliBroker(mode=args.mode)
    snapshot = {
        "holdings": broker.holdings(basis="total", exchange="NXT").payload,
        "open_orders": broker.list_open_orders().payload,
    }
    opens = open_order_rows(snapshot)
    held = holding_qty(snapshot, symbol)
    if opens:
        print("[BLOCKED] broker has open orders; do not terminalize locally")
        return 2
    if side == "BUY" and held > 0:
        print("[BLOCKED] broker holding exists for rejected BUY; reconcile manually")
        return 2

    store.update_intent(
        args.intent_id,
        status="REJECTED",
        last_error=last_error + " | terminalized after broker return_code=20 + no order_no/open orders/holding",
    )
    print("===== DEFINITIVE REJECTION RESOLVED =====")
    print(json.dumps({
        "intent_id": args.intent_id,
        "status": "REJECTED",
        "broker_order_no": None,
        "broker_open_order_count": 0,
        "broker_holding_qty": held,
    }, ensure_ascii=False, indent=2))
    print("[SAFE] local state only. No broker order was sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
