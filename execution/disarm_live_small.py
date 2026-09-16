#!/usr/bin/env python3
from __future__ import annotations

import os
from pathlib import Path

EXEC_ENV = Path.home() / "trading-execution.env"


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
    print("===== V0 LIVE_SMALL DISARM =====")
    print("[SAFE] 이 스크립트는 주문을 전송하지 않습니다.")

    if not EXEC_ENV.exists():
        print("[OK] ~/trading-execution.env 없음. 이미 비활성 상태입니다.")
        return 0

    lines = EXEC_ENV.read_text(encoding="utf-8").splitlines()
    lines = replace_or_add(lines, "KILL_SWITCH", "ON")
    lines = replace_or_add(lines, "ALLOW_LIVE_ORDERS", "NO")
    lines = replace_or_add(lines, "DAILY_ARM_DATE", "")
    EXEC_ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(EXEC_ENV, 0o600)

    print("[OK] LIVE_SMALL DISARM 완료")
    print("KILL_SWITCH=ON")
    print("ALLOW_LIVE_ORDERS=NO")
    print("DAILY_ARM_DATE=(empty)")
    print("[SAFE] ALLOWED_ACCOUNT / ALLOWED_SYMBOLS / MAX_ORDER_QTY는 유지했습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
