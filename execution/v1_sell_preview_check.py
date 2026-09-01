#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from broker_cli import KiwoomCliBroker
from exit_plan import ExitPlanStore
from safety import ExecutionConfig, load_env_file, mask_account
from sell_handoff import prepare_sell_handoff
from sell_preview import handoff_summary, preview_sell_handoff
from state_store import StateStore
from v0_engine import account_mask_matches, broker_account_mask, snapshot_summary

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
        description="V1 gated SELL Preview Shadow. Never sends broker SELL."
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
    broker_masked = broker_account_mask(snapshot)
    v0 = StateStore(Path(args.v0_db))
    local_open_count = len(v0.open_intents())

    print("===== V1 GATED SELL PREVIEW SHADOW — NO ORDER =====")
    print(json.dumps(snapshot_summary(snapshot), ensure_ascii=False, indent=2))

    if not cfg.allowed_account:
        print(json.dumps({"preview_ready": False, "block_reasons": ["ALLOWED_ACCOUNT_NOT_SET"]}, ensure_ascii=False, indent=2))
        print("[BLOCKED] account gate failed before SELL preview.")
        print("[SAFE] no broker SELL and no --confirm.")
        return 2
    if not account_mask_matches(cfg.allowed_account, broker_masked):
        print(json.dumps({
            "preview_ready": False,
            "block_reasons": ["BROKER_ACCOUNT_SUFFIX_MISMATCH"],
            "broker_account": broker_masked or "(unknown)",
            "configured_account": mask_account(cfg.allowed_account),
        }, ensure_ascii=False, indent=2))
        print("[BLOCKED] account gate failed before SELL preview.")
        print("[SAFE] no broker SELL and no --confirm.")
        return 2

    try:
        handoff = prepare_sell_handoff(
            plan,
            snapshot,
            local_open_intent_count=local_open_count,
        )
    except RuntimeError as exc:
        print(json.dumps({
            "preview_ready": False,
            "block_reasons": [str(exc)],
            "plan_id": plan.get("plan_id"),
            "status": plan.get("status"),
            "remaining_qty": int(plan.get("remaining_qty") or 0),
            "broker_account": broker_masked or "(unknown)",
            "configured_account": mask_account(cfg.allowed_account),
            "local_open_intent_count": local_open_count,
        }, ensure_ascii=False, indent=2))
        print("[BLOCKED] broker/local truth refused SELL preview handoff.")
        print("[SAFE] no broker SELL and no --confirm.")
        return 2

    print("===== SELL HANDOFF =====")
    print(json.dumps(handoff_summary(handoff), ensure_ascii=False, indent=2))
    result = preview_sell_handoff(broker, handoff)
    print("===== KIWOOOM SELL PREVIEW — NOT SENT =====")
    print(result.text)
    print("[PASS] gated SELL handoff reached broker PREVIEW only.")
    print("[SAFE] preview_order only; no submit_order and no --confirm.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
