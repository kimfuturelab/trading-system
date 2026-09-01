#!/usr/bin/env python3
from __future__ import annotations

import getpass
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from broker_cli import KiwoomCliBroker

KST = ZoneInfo("Asia/Seoul")
EXEC_ENV = Path.home() / "trading-execution.env"
SYMBOL = "005930"


def normalize_account(value: str) -> str:
    return re.sub(r"[^0-9]", "", str(value or ""))


def broker_masked_account() -> str:
    payload = KiwoomCliBroker(mode="real").account_list().payload
    if not isinstance(payload, dict):
        raise RuntimeError("계좌번호 조회 응답 형식이 예상과 다릅니다.")
    value = str(payload.get("계좌번호") or payload.get("acnt_no") or "").strip()
    if len(value) < 4:
        raise RuntimeError("키움에서 계좌번호 suffix를 확인하지 못했습니다.")
    return value


def main() -> int:
    print("===== V0 LIVE_SMALL PRIVATE CONFIGURATOR =====")
    print("[SAFE] 이 스크립트는 주문을 전송하지 않습니다.")
    print(f"대상 종목: {SYMBOL} / 최대수량: 1주 / 모드: LIVE_SMALL")

    masked = broker_masked_account()
    print(f"키움이 보고한 실계좌: {masked}")

    phrase = input(f"계속하려면 정확히 'ARM LIVE_SMALL {SYMBOL}' 입력: ").strip()
    if phrase != f"ARM LIVE_SMALL {SYMBOL}":
        print("[STOP] 확인 문구가 일치하지 않아 종료합니다.")
        return 2

    account = normalize_account(
        getpass.getpass("실계좌 전체번호 입력 (화면/쉘 히스토리에 표시되지 않음): ")
    )
    if len(account) < 4:
        print("[STOP] 계좌번호 형식이 올바르지 않습니다.")
        return 2
    if account[-4:] != masked[-4:]:
        print("[STOP] 입력 계좌 suffix가 키움 실계좌와 다릅니다.")
        return 2

    if EXEC_ENV.exists():
        stamp = datetime.now(KST).strftime("%Y%m%d-%H%M%S")
        backup = EXEC_ENV.with_name(EXEC_ENV.name + f".bak-{stamp}")
        shutil.copy2(EXEC_ENV, backup)
        os.chmod(backup, 0o600)
        print(f"기존 설정 백업: {backup}")

    today = datetime.now(KST).strftime("%Y-%m-%d")
    text = f"""# V0 LIVE_SMALL server-only configuration
# Created {datetime.now(KST).isoformat(timespec='seconds')}
AUTO_MODE=LIVE_SMALL
KILL_SWITCH=OFF
ALLOW_LIVE_ORDERS=YES
DAILY_ARM_DATE={today}
ALLOWED_ACCOUNT={account}
ALLOWED_SYMBOLS={SYMBOL}
MAX_ORDER_QTY=1
STATE_DB=~/.trading-execution-v0.sqlite3
AUTH_ENV=~/api-read-v2.env
"""
    EXEC_ENV.write_text(text, encoding="utf-8")
    os.chmod(EXEC_ENV, 0o600)

    print("[OK] ~/trading-execution.env 생성 완료 (권한 600)")
    print(f"ALLOWED_ACCOUNT={'*' * max(0, len(account)-4)}{account[-4:]}")
    print(f"DAILY_ARM_DATE={today}")
    print(f"ALLOWED_SYMBOLS={SYMBOL}")
    print("[IMPORTANT] 아직 주문은 전송되지 않았습니다. 다음은 live_preflight.py 입니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
