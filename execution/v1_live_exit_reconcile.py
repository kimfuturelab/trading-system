#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from broker_cli import BrokerCliError, KiwoomCliBroker
from exit_plan import ExitPlanStore
from exit_write_journal import ExitWriteJournal
from exit_write_reconcile import reconcile_exit_write
from safety import load_env_file

DEFAULT_AUTH_ENV = Path.home() / "api-read-v2.env"
DEFAULT_V1_DB = Path.home() / ".trading-execution-v1.sqlite3"
DEFAULT_WRITE_DB = Path.home() / ".trading-execution-v1-write.sqlite3"


def snapshot_for_exchange(broker: KiwoomCliBroker, exchange: str) -> dict:
    return {
        "holdings": broker.holdings(basis="total", exchange=exchange).payload,
        "open_orders": broker.list_open_orders().payload,
    }


def main() -> int:
    p = argparse.ArgumentParser(description="V1 live EXIT broker reconciliation; READ ONLY, never resends")
    p.add_argument("--plan-id", required=True)
    p.add_argument("--exchange", choices=["KRX", "NXT"], default="KRX")
    p.add_argument("--auth-env", default=str(DEFAULT_AUTH_ENV))
    p.add_argument("--v1-db", default=str(DEFAULT_V1_DB))
    p.add_argument("--write-db", default=str(DEFAULT_WRITE_DB))
    p.add_argument("--mode", choices=["real", "demo"], default="real")
    args = p.parse_args()

    load_env_file(Path(args.auth_env), required=True)
    v1 = ExitPlanStore(Path(args.v1_db))
    journal = ExitWriteJournal(Path(args.write_db))
    row = v1.get_plan(args.plan_id)
    if row is None:
        print(f"[BLOCKED] plan_id 없음: {args.plan_id}")
        return 2

    if str(row.get("status") or "") == "CLOSED":
        print("===== V1 LIVE EXIT RECONCILE — READ ONLY =====")
        print(json.dumps({"plan": row, "action": "ALREADY_CLOSED"}, ensure_ascii=False, indent=2))
        print("[PASS] plan already CLOSED. No resend.")
        return 0

    exit_intent_id = str(row.get("exit_intent_id") or "").strip()
    journal_row = journal.get(exit_intent_id) if exit_intent_id else None
    broker = KiwoomCliBroker(mode=args.mode)
    try:
        snap = snapshot_for_exchange(broker, args.exchange)
        result = reconcile_exit_write(
            plan_row=row,
            snapshot=snap,
            journal_row=journal_row,
            mark_exit_submitted=v1.mark_exit_submitted,
            reconcile_holding=v1.reconcile_holding,
        )
    except (BrokerCliError, RuntimeError, ValueError) as exc:
        print(f"[BLOCKED/REVIEW] {type(exc).__name__}: {exc}")
        print("[SAFE] READ/reconcile only. DO NOT RESEND.")
        return 2

    final = v1.get_plan(args.plan_id)
    print("===== V1 LIVE EXIT RECONCILE — READ ONLY =====")
    print(json.dumps({"exchange": args.exchange, "reconcile": asdict(result), "final_plan": final}, ensure_ascii=False, indent=2, default=str))
    if final and final.get("status") == "CLOSED":
        print("[PASS] broker holding is flat -> V1 CLOSED.")
    else:
        print("[PENDING/REVIEW] not CLOSED. No automatic resend; reconcile broker truth only.")
    print("[SAFE] no broker order endpoint was called.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
