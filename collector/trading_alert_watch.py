from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from alert_manager import TelegramAlertManager

KST = ZoneInfo("Asia/Seoul")
BASE_DIR = Path(__file__).resolve().parent
STATE_PATH = Path.home() / ".cache" / "trading-system" / "trading_alert_watch.json"


def now_kst() -> datetime:
    return datetime.now(KST)


def today_iso() -> str:
    return now_kst().strftime("%Y-%m-%d")


def env_first(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def load_runtime_env() -> tuple[str, str, int]:
    load_dotenv(BASE_DIR / ".env", override=False)
    for path in (
        Path.home() / "stage3-supply.env",
        Path.home() / "api-read-v2.env",
        Path.home() / "technical-position.env",
    ):
        if path.exists():
            load_dotenv(path, override=False)

    endpoint = env_first(
        "SHEETS_WEBHOOK_URL",
        "TECH_POSITION_WEBHOOK_URL",
        "WEBHOOK_URL",
    )
    secret = env_first(
        "INGEST_SECRET",
        "TECH_POSITION_INGEST_SECRET",
        "WEBHOOK_SECRET",
    )
    poll_seconds = int(os.getenv("TRADING_ALERT_POLL_SECONDS", "60"))

    if not endpoint:
        raise RuntimeError("missing Sheets Web App URL")
    if not secret:
        raise RuntimeError("missing INGEST secret")
    if poll_seconds < 30:
        raise RuntimeError("TRADING_ALERT_POLL_SECONDS must be >= 30")

    return endpoint, secret, poll_seconds


def fetch_packet(endpoint: str, secret: str) -> dict[str, Any]:
    response = requests.get(
        endpoint,
        params={"type": "trading_alert_packet", "secret": secret},
        timeout=30,
    )
    response.raise_for_status()
    try:
        packet = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Apps Script returned non-JSON: {response.text[:500]}"
        ) from exc

    if not packet.get("ok"):
        raise RuntimeError(f"Apps Script packet error: {packet}")
    if packet.get("type") != "trading_alert_packet":
        raise RuntimeError(f"unexpected packet type: {packet.get('type')}")
    return packet


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, STATE_PATH)


def score_text(value: Any) -> str:
    if value is None:
        return "미수신/제외"
    n = int(value)
    return f"+{n}" if n > 0 else str(n)


def signed_number(value: Any) -> str:
    if value is None:
        return "미수신/제외"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    if n.is_integer():
        return f"{int(n):+,}"
    return f"{n:+,.2f}"


def material_lines(material: dict[str, Any]) -> list[str]:
    lines = [f"- 재료점수: {score_text(material.get('score'))}"]
    for item in material.get("items") or []:
        label = str(item.get("label") or "").strip()
        score = item.get("score")
        if not label:
            continue
        if score is None:
            lines.append(f"- {label}: 미수신/제외")
        else:
            lines.append(f"- {label}: {score_text(score)}")
    return lines


def supply_lines(supply: dict[str, Any]) -> list[str]:
    values = supply.get("values") or {}
    return [
        f"- 수급점수: {score_text(supply.get('score'))}",
        f"- 외국인 현물: {signed_number(values.get('외국인 현물'))}",
        f"- 기관 현물: {signed_number(values.get('기관 현물'))}",
        f"- 프로그램: {signed_number(values.get('프로그램'))}",
        f"- 기준시각: {supply.get('as_of') or '미수신'}",
    ]


def build_message(packet: dict[str, Any]) -> str:
    stage0 = packet.get("stage0") or {}
    stage1 = packet.get("stage1") or {}
    stage2 = packet.get("stage2") or {}
    material = packet.get("material") or {}
    supply = packet.get("supply") or {}
    moneyflow = packet.get("moneyflow") or {}
    chart = packet.get("chart") or {}

    lines = [
        "[SK하이닉스 재수차 변경]",
        f"기준시각: {packet.get('as_of') or ''}",
        "",
        "0단계",
        (
            f"- {stage0.get('self_mode') or '미수신'} / "
            f"{stage0.get('operation_mode') or '미수신'} / "
            f"{stage0.get('active_account') or '미수신'} / "
            f"{stage0.get('system_start') or '미수신'}"
        ),
        f"- 단타자본: {stage0.get('actual_day_capital') or '미수신'}",
        "",
        "1단계",
        f"- 시장국면: {stage1.get('regime') or '미수신/제외'}",
        f"- 시장 허용매매: {stage1.get('allowed_trades') or '미수신/제외'}",
        "",
        "2단계",
        (
            f"- 장세점수: {stage2.get('total_score') or '미수신'} / "
            f"{stage2.get('tollgate') or '미수신'} / "
            f"Base R {stage2.get('base_r') or '미수신'}"
        ),
        "",
        "재료",
    ]
    lines.extend(material_lines(material))
    lines.extend(["", "수급"])
    lines.extend(supply_lines(supply))

    lines.extend(
        [
            "",
            "3.5 자금흐름",
            (
                f"- {moneyflow.get('leader') or '미수신'} / "
                f"점유율 {moneyflow.get('share') or '미수신'} / "
                f"생애주기 {moneyflow.get('lifecycle') or '미수신'}"
            ),
            f"- 30분: {moneyflow.get('delta_30m') or '미수신'} / {moneyflow.get('money_move') or '미수신'}",
            "",
            "차트",
            f"- 차트점수: {score_text(chart.get('score'))}",
        ]
    )

    chart_missing = chart.get("missing") or []
    if chart_missing:
        lines.append("- 미수신/제외: " + ", ".join(str(x) for x in chart_missing))

    axis_parts = []
    for name in ("material", "supply", "chart"):
        axis = packet.get(name) or {}
        if axis.get("score") is not None:
            axis_parts.append(score_text(axis.get("score")))

    score = packet.get("score")
    level = packet.get("max_level")
    lines.extend(
        [
            "",
            "재수차",
            f"- 확인축 합산: {' + '.join(axis_parts) if axis_parts else '확인값 없음'} = {score_text(score)}",
            f"- 최대 허용 Level: {level if level is not None else '미산출'}",
            "",
            "현재 허용 액션",
        ]
    )

    actions = packet.get("allowed_actions") or []
    if actions:
        lines.extend(f"- {action}" for action in actions)
    else:
        lines.append("- 확인 가능한 허용 액션 없음")

    excluded = packet.get("excluded") or []
    if excluded:
        lines.extend(["", "미수신/제외", "- " + " / ".join(str(x) for x in excluded)])

    return "\n".join(lines)


def score_key(packet: dict[str, Any]) -> str:
    value = packet.get("score")
    return "NA" if value is None else str(int(value))


def run_once(endpoint: str, secret: str, *, force: bool = False) -> bool:
    packet = fetch_packet(endpoint, secret)

    if packet.get("trade_date") != today_iso():
        print(
            f"SKIP: packet trade_date={packet.get('trade_date')} today={today_iso()}",
            flush=True,
        )
        return False

    current_key = score_key(packet)
    state = load_state()
    previous_key = str(state.get("last_score_key", ""))

    should_send = force or not previous_key or previous_key != current_key
    if not should_send:
        print(
            f"NO CHANGE: score={current_key} as_of={packet.get('as_of')}",
            flush=True,
        )
        return False

    manager = TelegramAlertManager("SK하이닉스 재수차")
    message = build_message(packet)
    sent = manager.send(message)

    if not sent:
        raise RuntimeError("Telegram send failed")

    save_state(
        {
            "last_score_key": current_key,
            "last_score": packet.get("score"),
            "last_sent_at": packet.get("as_of"),
            "last_trade_date": packet.get("trade_date"),
            "last_available_axis_count": packet.get("available_axis_count"),
        }
    )
    print(
        f"SENT: score {previous_key or 'NONE'} -> {current_key} | "
        f"level={packet.get('max_level')} | as_of={packet.get('as_of')}",
        flush=True,
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Poll the Sheets trading packet and Telegram on 재수차 score changes."
    )
    parser.add_argument("--loop", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help="send current packet once even when the score did not change",
    )
    args = parser.parse_args()

    try:
        endpoint, secret, poll_seconds = load_runtime_env()
    except Exception as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2

    if not args.loop:
        try:
            run_once(endpoint, secret, force=args.force)
            return 0
        except Exception as exc:
            print(f"RUN ERROR: {exc}", file=sys.stderr)
            return 1

    while True:
        try:
            run_once(endpoint, secret, force=False)
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            print(f"LOOP ERROR: {exc}", file=sys.stderr, flush=True)

        time.sleep(poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
