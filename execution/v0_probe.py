#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from safety import ExecutionConfig, load_env_file, mask_account, validate_order_request
from state_store import DuplicateIntentError, StateStore

KST = ZoneInfo("Asia/Seoul")
DEFAULT_AUTH_ENV = Path.home() / "api-read-v2.env"
DEFAULT_EXEC_ENV = Path.home() / "trading-execution.env"

API_MAP = {
    "ka00001": "계좌번호조회",
    "kt00018": "계좌평가잔고내역",
    "ka10075": "미체결요청",
    "ka10076": "체결요청",
    "kt10000": "주식 매수주문",
    "kt10001": "주식 매도주문",
    "kt10002": "주식 정정주문",
    "kt10003": "주식 취소주문",
}


def find_cli() -> str:
    found = shutil.which("kiwoomcli")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "kiwoomcli"
    if fallback.exists():
        return str(fallback)
    raise RuntimeError("kiwoomcli를 찾을 수 없습니다.")


def run_text(cmd: list[str], *, timeout: int = 20) -> tuple[int, str]:
    cp = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=os.environ.copy(),
    )
    text = ((cp.stdout or "") + ("\n" + cp.stderr if cp.stderr else "")).strip()
    return cp.returncode, text


def load_runtime_env(auth_env: Path, exec_env: Path) -> None:
    # 기존 공통 인증을 먼저 읽고, execution 설정이 있으면 그 뒤에 읽는다.
    load_env_file(auth_env, required=True)
    load_env_file(exec_env, required=False)


def cmd_doctor(args: argparse.Namespace) -> int:
    auth_env = Path(args.auth_env).expanduser()
    exec_env = Path(args.exec_env).expanduser()
    print("===== V0 DOCTOR (READ-ONLY) =====")
    print(f"time_kst={datetime.now(KST).isoformat(timespec='seconds')}")
    print(f"auth_env={auth_env} exists={auth_env.exists()}")
    print(f"execution_env={exec_env} exists={exec_env.exists()}")

    try:
        load_runtime_env(auth_env, exec_env)
    except Exception as exc:
        print(f"[FAIL] env load: {exc}")
        return 2

    missing = [name for name in ("KIWOOM_MODE", "APP_KEY", "APP_SECRET") if not os.getenv(name, "").strip()]
    if missing:
        print("[FAIL] auth env missing=" + ",".join(missing))
        return 2

    print(f"KIWOOM_MODE={os.getenv('KIWOOM_MODE','').strip()}")
    print("APP_KEY=SET")
    print("APP_SECRET=SET")

    try:
        cli = find_cli()
    except Exception as exc:
        print(f"[FAIL] {exc}")
        return 2
    print(f"kiwoomcli={cli}")

    code, help_text = run_text([cli, "--help"])
    print(f"kiwoomcli_help_exit={code}")
    if code != 0:
        print("[WARN] kiwoomcli --help 실패")
        print(help_text[-700:])
    else:
        print("[OK] kiwoomcli 실행 가능")

    # 인증 상태 조회는 주문을 발생시키지 않는다. 환경/CLI 버전 차이로 실패해도 doctor 전체는 경고로 처리한다.
    code, auth_text = run_text([cli, "auth", "status"])
    print(f"auth_status_exit={code}")
    if code == 0:
        print("[OK] kiwoomcli auth status 응답")
    else:
        print("[WARN] auth status 실패 또는 현재 env 인증 방식과 CLI profile 방식이 다름")
        if auth_text:
            print(auth_text[-700:])

    cfg = ExecutionConfig.from_env()
    print(f"AUTO_MODE={cfg.auto_mode}")
    print(f"KILL_SWITCH={'ON' if cfg.kill_switch else 'OFF'}")
    print(f"ALLOW_LIVE_ORDERS={'YES' if cfg.allow_live_orders else 'NO'}")
    print(f"ALLOWED_ACCOUNT={mask_account(cfg.allowed_account)}")
    print(f"ALLOWED_SYMBOLS={','.join(sorted(cfg.allowed_symbols)) or '(none)'}")
    print(f"MAX_ORDER_QTY={cfg.max_order_qty}")
    print(f"STATE_DB={cfg.state_db}")

    if cfg.auto_mode == "OFF" and cfg.kill_switch:
        print("[SAFE] 기본 주문차단 상태 정상")
    else:
        print("[WARN] 기본 권장값은 AUTO_MODE=OFF, KILL_SWITCH=ON")
    return 0


def cmd_api_map(_: argparse.Namespace) -> int:
    print("===== KIWOOM V0 API MAP =====")
    for api_id, name in API_MAP.items():
        mode = "WRITE" if api_id.startswith("kt1000") else "READ"
        print(f"{api_id}  {mode:<5}  {name}")
    print("\nV0에서는 READ 경로 확인 후에만 WRITE를 연결합니다.")
    return 0


def cmd_safety_test(args: argparse.Namespace) -> int:
    auth_env = Path(args.auth_env).expanduser()
    exec_env = Path(args.exec_env).expanduser()
    load_runtime_env(auth_env, exec_env)
    cfg = ExecutionConfig.from_env()

    symbol = args.symbol.strip().upper()
    account = args.account.strip() or cfg.allowed_account or "TEST_ACCOUNT"
    reasons = validate_order_request(
        cfg,
        account=account,
        symbol=symbol,
        qty=args.qty,
        side="BUY",
        intent_id="V0-SAFETY-TEST",
    )
    print("===== V0 SAFETY TEST =====")
    print(f"mode={cfg.auto_mode} kill_switch={'ON' if cfg.kill_switch else 'OFF'}")
    print(f"account={mask_account(account)} symbol={symbol} qty={args.qty}")
    if reasons:
        print("[BLOCKED] " + ", ".join(reasons))
        return 0
    print("[PASS] gate 조건이 모두 충족됨. 이 명령은 실제 주문을 호출하지 않습니다.")
    return 0


def cmd_state_test(_: argparse.Namespace) -> int:
    print("===== V0 IDEMPOTENCY TEST =====")
    with tempfile.TemporaryDirectory(prefix="trading-exec-v0-") as tmp:
        store = StateStore(Path(tmp) / "state.sqlite3")
        kwargs = dict(
            intent_id="TEST-INTENT-001",
            account_masked="****1234",
            symbol="005930",
            side="BUY",
            qty=1,
            requested_price=None,
            mode="PAPER",
        )
        store.reserve_intent(**kwargs)
        print("[OK] first reserve accepted")
        try:
            store.reserve_intent(**kwargs)
        except DuplicateIntentError:
            print("[OK] duplicate intent blocked")
            return 0
    print("[FAIL] duplicate intent was not blocked")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="V0 자동매매 서버/안전 점검기 — 실주문 없음")
    parser.add_argument("--auth-env", default=str(DEFAULT_AUTH_ENV))
    parser.add_argument("--exec-env", default=str(DEFAULT_EXEC_ENV))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor")
    sub.add_parser("api-map")

    safety = sub.add_parser("safety-test")
    safety.add_argument("--symbol", default="005930")
    safety.add_argument("--qty", type=int, default=1)
    safety.add_argument("--account", default="")

    sub.add_parser("state-test")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    handlers = {
        "doctor": cmd_doctor,
        "api-map": cmd_api_map,
        "safety-test": cmd_safety_test,
        "state-test": cmd_state_test,
    }
    try:
        return handlers[args.command](args)
    except KeyboardInterrupt:
        print("[STOP] 사용자 중지")
        return 130
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
