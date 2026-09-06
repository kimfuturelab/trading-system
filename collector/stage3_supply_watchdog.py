from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo

from alert_manager import TelegramAlertManager

KST = ZoneInfo("Asia/Seoul")
DEFAULT_HEALTH_FILE = Path.home() / ".cache" / "trading-system" / "stage3_market_supply_health.json"


def parse_hhmm(value: str, default: str) -> dt_time:
    text = (value or default).strip()
    hh, mm = text.split(":", 1)
    return dt_time(hour=int(hh), minute=int(mm))


def now_kst() -> datetime:
    return datetime.now(KST)


def inside_active_window(start: dt_time, end: dt_time) -> bool:
    current = now_kst().time().replace(second=0, microsecond=0)
    return start <= current <= end


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
    except ValueError:
        return None


def read_health(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def evaluate_once(
    manager: TelegramAlertManager,
    health_file: Path,
    stale_seconds: int,
) -> None:
    now = now_kst()
    health = read_health(health_file)
    if not health:
        manager.problem(
            "3. 수급 헬스파일 없음",
            str(health_file),
            alert_after_seconds=stale_seconds,
        )
        return

    updated_at = parse_dt(str(health.get("updated_at", "")))
    if updated_at is None:
        manager.problem(
            "3. 수급 헬스시각 해석 실패",
            str(health.get("updated_at", "")),
            alert_after_seconds=stale_seconds,
        )
        return

    age = (now - updated_at).total_seconds()
    if age > stale_seconds:
        manager.problem(
            f"정상 수신 {stale_seconds}초 초과",
            f"마지막 헬스: {health.get('updated_at')} / 경과 {int(age)}초",
            immediate=True,
            alert_after_seconds=stale_seconds,
        )
        return

    status = str(health.get("status", "UNKNOWN")).upper()
    detail = str(health.get("detail", "")).strip()
    if status == "OK":
        manager.healthy(
            f"마지막 정상 수신: {health.get('captured_at') or health.get('updated_at')}"
        )
        return

    immediate = "8005" in detail or "Token이 유효하지 않습니다" in detail
    manager.problem(
        f"수급 데이터 상태 {status}",
        detail or "필수 수급값 미수신",
        immediate=immediate,
        alert_after_seconds=stale_seconds,
    )


def main() -> int:
    health_file = Path(
        os.getenv("STAGE3_SUPPLY_HEALTH_FILE", str(DEFAULT_HEALTH_FILE))
    ).expanduser()
    stale_seconds = int(os.getenv("SUPPLY_ALERT_STALE_SECONDS", "180"))
    check_seconds = int(os.getenv("SUPPLY_ALERT_CHECK_SECONDS", "20"))
    active_start = parse_hhmm(os.getenv("SUPPLY_ACTIVE_START", "08:50"), "08:50")
    active_end = parse_hhmm(os.getenv("SUPPLY_ACTIVE_END", "15:40"), "15:40")

    if stale_seconds < 60:
        print("CONFIG ERROR: SUPPLY_ALERT_STALE_SECONDS must be >= 60", file=sys.stderr)
        return 2
    if check_seconds < 10:
        print("CONFIG ERROR: SUPPLY_ALERT_CHECK_SECONDS must be >= 10", file=sys.stderr)
        return 2

    manager = TelegramAlertManager("3. 수급")
    loop = "--loop" in sys.argv

    while True:
        try:
            if not loop or inside_active_window(active_start, active_end):
                evaluate_once(manager, health_file, stale_seconds)
        except KeyboardInterrupt:
            print("Stopped by user")
            return 0
        except Exception as exc:
            print(f"WATCHDOG ERROR: {exc}", file=sys.stderr)

        if not loop:
            return 0
        time.sleep(check_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
