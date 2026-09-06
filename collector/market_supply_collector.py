from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any

import requests
from dotenv import load_dotenv

from kiwoom_collector import KiwoomClient, Settings

COLLECTOR_VERSION = "stage3-supply-v1-auth-recovery-watchdog"
KST = ZoneInfo("Asia/Seoul")
HEALTH_FILE = Path(
    os.getenv(
        "STAGE3_SUPPLY_HEALTH_FILE",
        str(Path.home() / ".cache" / "trading-system" / "stage3_market_supply_health.json"),
    )
).expanduser()

MARKETS = (
    {
        "market": "KOSPI",
        "investor_market_code": "0",     # ka10051: KOSPI
        "program_market_code": "P00101", # ka90005: KOSPI / KRX
    },
    {
        "market": "KOSDAQ",
        "investor_market_code": "1",     # ka10051: KOSDAQ
        "program_market_code": "P10102", # ka90005: KOSDAQ / KRX
    },
)


def _number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None

    if text.startswith("--"):
        text = "-" + text.lstrip("-")
    elif text.startswith("++"):
        text = "+" + text.lstrip("+")

    try:
        return float(text)
    except ValueError:
        return None


def now_kst_string() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def today_yyyymmdd() -> str:
    return datetime.now(KST).strftime("%Y%m%d")


def write_health(
    status: str,
    detail: str = "",
    *,
    captured_at: str | None = None,
    rows: list[dict[str, Any]] | None = None,
) -> None:
    payload = {
        "updated_at": now_kst_string(),
        "captured_at": captured_at or "",
        "status": status,
        "detail": detail,
        "collector_version": COLLECTOR_VERSION,
        "markets": [
            {
                "market": str(r.get("market", "")),
                "status": str(r.get("status", "")),
                "error": str(r.get("error", "")),
            }
            for r in (rows or [])
        ],
    }
    try:
        HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = HEALTH_FILE.with_suffix(HEALTH_FILE.suffix + f".{os.getpid()}.tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, HEALTH_FILE)
    except Exception as exc:
        print(f"HEALTH WRITE ERROR: {exc}", file=sys.stderr)


def post_kiwoom(client: KiwoomClient, api_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    return client.post_authorized(api_id, path, body, timeout=20)


def fetch_market_investor_supply(client: KiwoomClient, market_code: str) -> dict[str, Any]:
    data = post_kiwoom(
        client,
        "ka10051",
        "/api/dostk/sect",
        {
            "mrkt_tp": market_code,
            "amt_qty_tp": "0",
            "base_dt": today_yyyymmdd(),
            "stex_tp": "1",
        },
    )
    rows = data.get("inds_netprps") or []
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f"ka10051 returned no rows for market={market_code}")

    aggregate = next((r for r in rows if "종합" in str(r.get("inds_nm", ""))), rows[0])
    return {
        "individual_net": _number(aggregate.get("ind_netprps")),
        "foreign_net": _number(aggregate.get("frgnr_netprps")),
        "institution_net": _number(aggregate.get("orgn_netprps")),
        "raw_industry_code": str(aggregate.get("inds_cd", "")),
        "raw_industry_name": str(aggregate.get("inds_nm", "")),
    }


def fetch_market_program_supply(client: KiwoomClient, program_market_code: str) -> dict[str, Any]:
    data = post_kiwoom(
        client,
        "ka90005",
        "/api/dostk/mrkcond",
        {
            "date": today_yyyymmdd(),
            "amt_qty_tp": "1",
            "mrkt_tp": program_market_code,
            "min_tic_tp": "1",
            "stex_tp": "1",
        },
    )
    rows = data.get("prm_trde_trnsn") or []
    if not isinstance(rows, list) or not rows:
        raise RuntimeError(f"ka90005 returned no rows for market={program_market_code}")

    latest = max(rows, key=lambda r: str(r.get("cntr_tm", "000000")))
    return {
        "program_net": _number(latest.get("all_netprps")),
        "program_time": str(latest.get("cntr_tm", "")),
    }


def build_market_row(client: KiwoomClient, config: dict[str, str], captured_at: str) -> dict[str, Any]:
    row: dict[str, Any] = {
        "captured_at": captured_at,
        "market": config["market"],
        "individual_net": None,
        "foreign_net": None,
        "institution_net": None,
        "program_net": None,
        "investor_api_id": "ka10051",
        "program_api_id": "ka90005",
        "investor_exchange": "KRX",
        "program_exchange": "KRX",
        "investor_market_code": config["investor_market_code"],
        "program_market_code": config["program_market_code"],
        "status": "OK",
        "error": "",
        "note": "",
    }

    errors: list[str] = []
    try:
        investor = fetch_market_investor_supply(client, config["investor_market_code"])
        row.update(
            individual_net=investor["individual_net"],
            foreign_net=investor["foreign_net"],
            institution_net=investor["institution_net"],
            note=f"{investor['raw_industry_name']} {investor['raw_industry_code']}",
        )
    except Exception as exc:
        errors.append(f"ka10051:{exc}")

    time.sleep(0.25)

    try:
        program = fetch_market_program_supply(client, config["program_market_code"])
        row["program_net"] = program["program_net"]
        if program["program_time"]:
            row["note"] = (row["note"] + " | " if row["note"] else "") + f"program_time={program['program_time']}"
    except Exception as exc:
        errors.append(f"ka90005:{exc}")

    if errors:
        row["status"] = "ERROR"
        row["error"] = " || ".join(errors)
    elif any(row[k] is None for k in ("foreign_net", "institution_net", "program_net")):
        row["status"] = "PENDING"
        row["error"] = "required_gate_value_missing"

    return row


def send_market_supply(rows: list[dict[str, Any]], settings: Settings) -> dict[str, Any]:
    captured_at = rows[0]["captured_at"] if rows else now_kst_string()
    payload = {
        "secret": settings.ingest_secret,
        "type": "market_supply",
        "source": "kiwoom-rest-ka10051-ka90005",
        "collector_version": COLLECTOR_VERSION,
        "captured_at": captured_at,
        "row_count": len(rows),
        "rows": rows,
    }
    response = requests.post(settings.webhook_url, json=payload, timeout=30)
    response.raise_for_status()
    try:
        result = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Apps Script returned non-JSON: {response.text[:500]}") from exc
    if not result.get("ok"):
        raise RuntimeError(f"Apps Script market_supply ingestion failed: {result}")
    return result


def run_once(settings: Settings) -> None:
    client = KiwoomClient(settings)
    captured_at = now_kst_string()
    rows: list[dict[str, Any]] = []
    for config in MARKETS:
        rows.append(build_market_row(client, config, captured_at))
        time.sleep(0.25)

    result = send_market_supply(rows, settings)
    summary = ", ".join(f"{r['market']}={r['status']}" for r in rows)

    bad_rows = [r for r in rows if str(r.get("status", "")).upper() != "OK"]
    if bad_rows:
        detail = " || ".join(
            f"{r.get('market')}:{r.get('status')}:{r.get('error')}" for r in bad_rows
        )
        overall_status = "ERROR" if any(
            str(r.get("status", "")).upper() == "ERROR" for r in bad_rows
        ) else "PENDING"
        write_health(overall_status, detail, captured_at=captured_at, rows=rows)
    else:
        write_health("OK", summary, captured_at=captured_at, rows=rows)

    print(f"[{captured_at}] market_supply sent {len(rows)} rows | {summary} | sheet={result.get('sheet')}")


def parse_hhmm(value: str, default: str) -> dt_time:
    text = (value or default).strip()
    hh, mm = text.split(":", 1)
    return dt_time(hour=int(hh), minute=int(mm))


def inside_active_window(start: dt_time, end: dt_time) -> bool:
    current = datetime.now(KST).time().replace(second=0, microsecond=0)
    return start <= current <= end


def main() -> int:
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    try:
        settings = Settings.from_env()
        poll_seconds = int(os.getenv("SUPPLY_POLL_SECONDS", "60"))
        active_start = parse_hhmm(os.getenv("SUPPLY_ACTIVE_START", "08:50"), "08:50")
        active_end = parse_hhmm(os.getenv("SUPPLY_ACTIVE_END", "15:40"), "15:40")
    except Exception as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        write_health("ERROR", f"CONFIG ERROR: {exc}")
        return 2

    if poll_seconds < 30:
        print("CONFIG ERROR: SUPPLY_POLL_SECONDS must be >= 30", file=sys.stderr)
        write_health("ERROR", "SUPPLY_POLL_SECONDS must be >= 30")
        return 2

    loop = "--loop" in sys.argv
    while True:
        try:
            if not loop or inside_active_window(active_start, active_end):
                run_once(settings)
        except KeyboardInterrupt:
            print("Stopped by user")
            return 0
        except Exception as exc:
            print(f"RUN ERROR: {exc}", file=sys.stderr)
            write_health("ERROR", f"RUN ERROR: {exc}")
            if not loop:
                return 1

        if not loop:
            return 0
        time.sleep(poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
