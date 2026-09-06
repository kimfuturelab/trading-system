from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

KST = ZoneInfo("Asia/Seoul")
DEFAULT_ALERT_ENV = Path.home() / "telegram-alert.env"
DEFAULT_STATE_DIR = Path.home() / ".cache" / "trading-system" / "alerts"


def _now() -> datetime:
    return datetime.now(KST)


def _now_text() -> str:
    return _now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
    except ValueError:
        return None


def _duration_text(seconds: float) -> str:
    seconds = max(0, int(seconds))
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}시간 {minutes}분 {sec}초"
    if minutes:
        return f"{minutes}분 {sec}초"
    return f"{sec}초"


def _safe_slug(text: str) -> str:
    slug = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", text).strip("_")
    return slug or "alert"


class TelegramAlertManager:
    """Stateful Telegram alerting with dedupe, delayed alerts and recovery notices."""

    def __init__(
        self,
        component: str,
        *,
        state_path: str | Path | None = None,
        repeat_after_seconds: int | None = None,
    ) -> None:
        alert_env = Path(
            os.getenv("TELEGRAM_ALERT_ENV", str(DEFAULT_ALERT_ENV))
        ).expanduser()
        load_dotenv(alert_env, override=False)

        self.component = component
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
        self.repeat_after_seconds = int(
            repeat_after_seconds
            if repeat_after_seconds is not None
            else os.getenv("ALERT_REPEAT_SECONDS", "1800")
        )
        self.state_path = Path(
            state_path
            if state_path is not None
            else DEFAULT_STATE_DIR / f"{_safe_slug(component)}.json"
        ).expanduser()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {
                "status": "HEALTHY",
                "first_failure_at": "",
                "last_alert_at": "",
                "last_reason": "",
                "last_detail": "",
            }
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {
            "status": "HEALTHY",
            "first_failure_at": "",
            "last_alert_at": "",
            "last_reason": "",
            "last_detail": "",
        }

    def _save_state(self, state: dict[str, Any]) -> None:
        tmp = self.state_path.with_suffix(
            self.state_path.suffix + f".{os.getpid()}.tmp"
        )
        tmp.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self.state_path)

    def _send(self, text: str) -> bool:
        if not self.enabled:
            print(
                f"ALERT DISABLED: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID missing | {text}",
                file=sys.stderr,
            )
            return False

        try:
            response = requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                data={
                    "chat_id": self.chat_id,
                    "text": text,
                    "disable_web_page_preview": "true",
                },
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
            if not data.get("ok"):
                raise RuntimeError(str(data))
            return True
        except Exception as exc:
            print(f"TELEGRAM ALERT ERROR: {exc}", file=sys.stderr)
            return False

    def problem(
        self,
        reason: str,
        detail: str = "",
        *,
        immediate: bool = False,
        alert_after_seconds: int = 180,
    ) -> bool:
        now = _now()
        now_text = now.strftime("%Y-%m-%d %H:%M:%S")
        state = self._load_state()

        first_failure = _parse_dt(str(state.get("first_failure_at", "")))
        if str(state.get("status", "HEALTHY")) == "HEALTHY" or first_failure is None:
            first_failure = now
            state["first_failure_at"] = now_text
            state["status"] = "DEGRADED"

        elapsed = (now - first_failure).total_seconds()
        last_alert = _parse_dt(str(state.get("last_alert_at", "")))
        alert_due = immediate or elapsed >= alert_after_seconds
        repeat_due = (
            str(state.get("status")) == "ALERTING"
            and last_alert is not None
            and (now - last_alert).total_seconds() >= self.repeat_after_seconds
        )

        sent = False
        if alert_due and (str(state.get("status")) != "ALERTING" or repeat_due):
            title = "[긴급]" if immediate else "[경고]"
            if str(state.get("status")) == "ALERTING" and repeat_due:
                title = "[경고-지속]"
            lines = [
                f"{title} {self.component} 장애",
                f"발생: {state.get('first_failure_at') or now_text}",
                f"원인: {reason}",
            ]
            if detail:
                lines.append(f"상세: {detail}")
            lines.extend(
                [
                    f"지속: {_duration_text(elapsed)}",
                    "상태: PENDING / 매수판정 사용금지",
                    "자동복구: 계속 감시 중",
                ]
            )
            sent = self._send("\n".join(lines))
            if sent:
                state["status"] = "ALERTING"
                state["last_alert_at"] = now_text

        state["last_reason"] = reason
        state["last_detail"] = detail
        state["last_seen_at"] = now_text
        self._save_state(state)
        return sent

    def healthy(self, detail: str = "") -> bool:
        now = _now()
        now_text = now.strftime("%Y-%m-%d %H:%M:%S")
        state = self._load_state()
        was_alerting = str(state.get("status", "HEALTHY")) == "ALERTING"
        first_failure = _parse_dt(str(state.get("first_failure_at", "")))

        sent = False
        if was_alerting:
            elapsed = (
                (now - first_failure).total_seconds()
                if first_failure is not None
                else 0
            )
            lines = [
                f"[복구] {self.component} 정상화",
                f"복구: {now_text}",
                f"장애 지속: {_duration_text(elapsed)}",
                "상태: 데이터 정상 수신",
            ]
            if detail:
                lines.append(f"상세: {detail}")
            sent = self._send("\n".join(lines))

        state.update(
            {
                "status": "HEALTHY",
                "first_failure_at": "",
                "last_alert_at": "",
                "last_reason": "",
                "last_detail": "",
                "last_healthy_at": now_text,
                "last_seen_at": now_text,
            }
        )
        self._save_state(state)
        return sent


if __name__ == "__main__":
    manager = TelegramAlertManager("알림 테스트")
    if "--problem" in sys.argv:
        manager.problem("수동 테스트", "alert_manager.py", immediate=True)
    else:
        manager.healthy("alert_manager.py")
