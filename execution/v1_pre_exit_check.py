#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from broker_cli import KiwoomCliBroker
from exit_plan import ExitPlanStore, pre_exit_gate_errors
from safety import ExecutionConfig, load_env_file, mask_account
from state_store import StateStore
from v0_engine import (
    account_mask_matches,
    broker_account_mask,
    holding_qty,
    open_order_rows,
    snapshot_summary,
)

DEFAULT_AUTH_ENV = Path.home() / "api-read-v2.env"
DEFAULT_EXEC_ENV = Path.home() / "trading-execution.env"
DEFAULT_V0_DB = Path.home() / ".trading-execution-v0.sqlite3"
DEFAULT_V1_DB = Path.home() / ".trading-execution-v1.sqlite3"


def load_runtime(auth_env: Path, exec_env: Path) -> ExecutionConfig:
    load_env_file(auth_env, required=True)
    load_env_file(exec_env, required=False)
    return ExecutionConfig.from_env()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="V1 real-broker pre-exit readiness check. READ only; never sends SELL."
    )
    p.add_argument("--plan-id", required=True)
    p.add_argument("--auth-env", default=str(DEFAULT_AUTH_ENV))
    p.add_argument("--exec-env", default=str(DEFAULT_EXEC_ENV))
    p.add_argument("--v0-db", default=str(DEFAULT_V0_DB))
    p.add_argument("--v1-db", default=str(DEFAULT_V1_DB))
    p.add_argument("--mode", choices=["real", "demo"], default="real")
    return p


def main() -> int:
    args = build_parser().parse_args()

    cfg = load_runtime(Path(args.auth_env), Path(args.exec_env))
    v1 = ExitPlanStore(Path(args.v1_db))
    plan = v1.get_plan(args.plan_id)
    if plan is None:
        print(f"[BLOCKED] plan_id 없음: {args.plan_id}")
        return 2

    broker = KiwoomCliBroker(mode=args.mode)
    snapshot = broker.read_snapshot()
    v0 = StateStore(Path(args.v0_db))

    symbol = str(plan.get("symbol") or "").strip().upper()
    broker_qty = holding_qty(snapshot, symbol)
    broker_opens = len(open_order_rows(snapshot))
    local_opens = len(v0.open_intents())
    broker_masked = broker_account_mask(snapshot)

    reasons: list[str] = []
    if not cfg.allowed_account:
        reasons.append("ALLOWED_ACCOUNT_NOT_SET")
    elif not account_mask_matches(cfg.allowed_account, broker_masked):
        reasons.append("BROKER_ACCOUNT_SUFFIX_MISMATCH")

    reasons.extend(
        pre_exit_gate_errors(
            plan,
            broker_holding_qty=broker_qty,
            broker_open_order_count=broker_opens,
            local_open_intent_count=local_opens,
        )
    )

    print("===== V1 REAL BROKER PRE-EXIT CHECK — READ ONLY / NO ORDER =====")
    print(json.dumps(snapshot_summary(snapshot), ensure_ascii=False, indent=2))
    print("===== EXIT PLAN / BROKER MATCH =====")
    print(
        json.dumps(
            {
                "plan_id": plan.get("plan_id"),
                "status": plan.get("status"),
                "symbol": symbol,
                "remaining_qty": int(plan.get("remaining_qty") or 0),
                "trigger_reason": plan.get("trigger_reason"),
                "exit_intent_id": plan.get("exit_intent_id"),
                "broker_order_no": plan.get("broker_order_no"),
                "broker_holding_qty": broker_qty,
                "broker_open_order_count": broker_opens,
                "local_open_intent_count": local_opens,
                "broker_account": broker_masked or "(unknown)",
                "configured_account": mask_account(cfg.allowed_account),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print("===== PRE-EXIT GATE =====")
    if reasons:
        print(json.dumps({"ready_for_sell": False, "block_reasons": reasons}, ensure_ascii=False, indent=2))
        print("[BLOCKED] V1 pre-exit gate correctly refused SELL readiness.")
        print("[SAFE] READ only. No broker SELL and no --confirm.")
        return 2

    print(json.dumps({"ready_for_sell": True, "block_reasons": []}, ensure_ascii=False, indent=2))
    print("[PASS] V1 pre-exit broker/local state is ready for SELL handoff.")
    print("[SAFE] READ only. No broker SELL and no --confirm.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
