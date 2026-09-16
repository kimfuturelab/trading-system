#!/usr/bin/env python3
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from broker_cli import KiwoomCliBroker

KST = ZoneInfo("Asia/Seoul")
EXEC_ENV = Path.home() / "trading-execution.env"
SYMBOL = "005930"


def parse_env(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    return data


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


def broker_masked_account() -> str:
    payload = KiwoomCliBroker(mode="real").account_list().payload
    if not isinstance(payload, dict):
        raise RuntimeError("계좌번호 조회 응답 형식이 예상과 다릅니다.")
    return str(payload.get("계좌번호") or payload.get("acnt_no") or "").strip()


def main() -> int:
    print("===== V0 LIVE_SMALL ARM =====")
    print("[SAFE] 이 스크립트는 주문을 전송하지 않습니다.")

    if not EXEC_ENV.exists():
        print("[STOP] ~/trading-execution.env 없음. configure_live_small.py를 먼저 실행하세요.")
        return 2

    cfg = parse_env(EXEC_ENV)
    account = cfg.get("ALLOWED_ACCOUNT", "")
    masked = broker_masked_account()

    if len(account) < 4 or len(masked) < 4 or account[-4:] != masked[-4:]:
        print("[STOP] 저장 계좌와 현재 키움 실계좌가 일치하지 않습니다.")
        return 2
    if cfg.get("ALLOWED_SYMBOLS") != SYMBOL:
        print("[STOP] ALLOWED_SYMBOLS가 005930 단일 종목이 아닙니다.")
        return 2
    if cfg.get("MAX_ORDER_QTY") != "1":
        print("[STOP] MAX_ORDER_QTY가 1이 아닙니다.")
        return 2

    print(f"계좌 확인: {masked}")
    print(f"종목 확인: {SYMBOL} / 최대 1주")
    phrase = input(f"계속하려면 정확히 'ARM LIVE_SMALL {SYMBOL}' 입력: ").strip()
    if phrase != f"ARM LIVE_SMALL {SYMBOL}":
        print("[STOP] 확인 문구가 일치하지 않아 종료합니다.")
        return 2

    today = datetime.now(KST).strftime("%Y-%m-%d")
    lines = EXEC_ENV.read_text(encoding="utf-8").splitlines()
    for key, value in (
        ("AUTO_MODE", "LIVE_SMALL"),
        ("KILL_SWITCH", "OFF"),
        ("ALLOW_LIVE_ORDERS", "YES"),
        ("DAILY_ARM_DATE", today),
    ):
        lines = replace_or_add(lines, key, value)

    EXEC_ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(EXEC_ENV, 0o600)

    print("[OK] LIVE_SMALL 당일 ARM 완료")
    print("AUTO_MODE=LIVE_SMALL")
    print("KILL_SWITCH=OFF")
    print("ALLOW_LIVE_ORDERS=YES")
    print(f"DAILY_ARM_DATE={today}")
    print("[IMPORTANT] 아직 주문은 전송되지 않았습니다. 반드시 live_preflight.py를 먼저 실행하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
