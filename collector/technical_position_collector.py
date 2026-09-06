from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, time as dt_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

from kiwoom_collector import KiwoomClient, Settings

COLLECTOR_VERSION = "technical-position-live-v2"
KST = ZoneInfo("Asia/Seoul")
BASE_DIR = Path(__file__).resolve().parent

MARKETS = (
    {"market": "KOSPI", "industry_code": "001", "market_type": "0"},
    {"market": "KOSDAQ", "industry_code": "101", "market_type": "1"},
)


@dataclass(frozen=True)
class TechnicalRuntime:
    webhook_url: str
    ingest_secret: str
    poll_seconds: int
    prep_time: dt_time
    live_start: dt_time
    session_confirm_until: dt_time
    live_end: dt_time
    final_time: dt_time
    env_file: Path


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def _parse_hhmm(value: str) -> dt_time:
    hh, mm = value.strip().split(":", 1)
    return dt_time(hour=int(hh), minute=int(mm))


def load_runtime() -> tuple[Settings, TechnicalRuntime]:
    load_dotenv(BASE_DIR / ".env")

    env_file = Path(
        os.getenv(
            "TECH_POSITION_ENV_FILE",
            str(Path.home() / "technical-position.env"),
        )
    ).expanduser()

    if env_file.exists():
        load_dotenv(env_file, override=True)

    app_key = _env_first("KIWOOM_APP_KEY", "APP_KEY")
    app_secret = _env_first("KIWOOM_APP_SECRET", "APP_SECRET")
    base_url = _env_first("KIWOOM_BASE_URL", default="https://api.kiwoom.com").rstrip("/")

    missing = []
    if not app_key:
        missing.append("KIWOOM_APP_KEY or APP_KEY")
    if not app_secret:
        missing.append("KIWOOM_APP_SECRET or APP_SECRET")

    webhook_url = _env_first("TECH_POSITION_WEBHOOK_URL")
    ingest_secret = _env_first("TECH_POSITION_INGEST_SECRET")

    if not webhook_url:
        missing.append("TECH_POSITION_WEBHOOK_URL")
    if not ingest_secret:
        missing.append("TECH_POSITION_INGEST_SECRET")

    if missing:
        raise RuntimeError("Missing required environment values: " + ", ".join(missing))

    settings = Settings(
        app_key=app_key,
        app_secret=app_secret,
        base_url=base_url,
        webhook_url="unused-by-technical-position",
        ingest_secret="unused-by-technical-position",
        market_type=os.getenv("MARKET_TYPE", "000").strip(),
        exchange_type=os.getenv("EXCHANGE_TYPE", "3").strip(),
        exclude_managed=os.getenv("EXCLUDE_MANAGED", "1").strip(),
        poll_seconds=int(os.getenv("POLL_SECONDS", "300")),
        trading_value_divisor=float(os.getenv("TRADING_VALUE_DIVISOR", "100")),
    )

    runtime = TechnicalRuntime(
        webhook_url=webhook_url,
        ingest_secret=ingest_secret,
        poll_seconds=max(15, int(os.getenv("TECH_POSITION_POLL_SECONDS", "60"))),
        prep_time=_parse_hhmm(os.getenv("TECH_POSITION_PREP_TIME", "08:50")),
        live_start=_parse_hhmm(os.getenv("TECH_POSITION_LIVE_START", "09:00")),
        session_confirm_until=_parse_hhmm(
            os.getenv("TECH_POSITION_SESSION_CONFIRM_UNTIL", "09:20")
        ),
        live_end=_parse_hhmm(os.getenv("TECH_POSITION_LIVE_END", "15:30")),
        final_time=_parse_hhmm(os.getenv("TECH_POSITION_FINAL_TIME", "15:35")),
        env_file=env_file,
    )

    if runtime.final_time < runtime.live_end:
        raise RuntimeError("TECH_POSITION_FINAL_TIME must be >= TECH_POSITION_LIVE_END")

    return settings, runtime


def _plain_number(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _index_price(value: Any) -> float | None:
    """Normalize Kiwoom sector-index price strings to human index points."""
    if value is None:
        return None
    raw = str(value).strip().replace(",", "")
    if not raw:
        return None
    try:
        number = abs(float(raw))
    except ValueError:
        return None
    if "." in raw:
        return number
    return number / 100.0 if number >= 10000 else number


def _volume(value: Any) -> float | None:
    number = _plain_number(value)
    if number is None:
        return None
    return abs(number)


def now_kst() -> datetime:
    return datetime.now(KST)


def now_kst_string() -> str:
    return now_kst().strftime("%Y-%m-%d %H:%M:%S")


def yyyymmdd(dt: datetime | None = None) -> str:
    return (dt or now_kst()).strftime("%Y%m%d")


def iso_date(date_key: str) -> str:
    return f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:8]}"


def _trade_date(row: dict[str, Any]) -> str:
    return str(row.get("dt", "")).strip().replace("-", "")[:8]


def _parse_ts(value: Any) -> datetime | None:
    text = (
        str(value or "")
        .strip()
        .replace("-", "")
        .replace(":", "")
        .replace(" ", "")
    )
    if len(text) < 12:
        return None
    text = text[:14].ljust(14, "0")
    try:
        return datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=KST)
    except ValueError:
        return None


def _is_invalid_token(data: dict[str, Any]) -> bool:
    if int(data.get("return_code", -1)) == 0:
        return False
    msg = str(data.get("return_msg", ""))
    return "8005" in msg or "Token이 유효하지 않습니다" in msg


def request_page(
    client: KiwoomClient,
    api_id: str,
    path: str,
    body: dict[str, Any],
    *,
    cont_yn: str = "N",
    next_key: str = "",
    timeout: int = 30,
) -> tuple[dict[str, Any], dict[str, str]]:
    waits = (2, 5, 10, 20)

    for rate_attempt in range(len(waits) + 1):
        response: requests.Response | None = None

        for token_attempt in range(2):
            token = client.issue_token()

            headers = {
                "authorization": f"Bearer {token}",
                "api-id": api_id,
                "Content-Type": "application/json;charset=UTF-8",
            }

            if cont_yn == "Y" and next_key:
                headers["cont-yn"] = "Y"
                headers["next-key"] = next_key

            response = client.session.post(
                f"{client.s.base_url}{path}",
                headers=headers,
                json=body,
                timeout=timeout,
            )

            if response.status_code == 429:
                break

            response.raise_for_status()
            data = response.json()

            if int(data.get("return_code", -1)) == 0:
                return data, {
                    "cont-yn": str(response.headers.get("cont-yn", "N")).upper(),
                    "next-key": str(response.headers.get("next-key", "")),
                }

            if token_attempt == 0 and _is_invalid_token(data):
                client.invalidate_token(token)
                continue

            raise RuntimeError(f"{api_id} error: {data}")

        if rate_attempt >= len(waits):
            raise RuntimeError(f"{api_id} HTTP 429 after retries")

        retry_after = ""
        if response is not None:
            retry_after = response.headers.get("Retry-After", "").strip()

        try:
            retry_seconds = int(float(retry_after)) if retry_after else 0
        except ValueError:
            retry_seconds = 0

        time.sleep(max(waits[rate_attempt], retry_seconds))

    raise RuntimeError(f"{api_id} request failed")


def fetch_pages(
    client: KiwoomClient,
    api_id: str,
    path: str,
    body: dict[str, Any],
    table_key: str,
    *,
    max_pages: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cont_yn = "N"
    next_key = ""

    for _ in range(max_pages):
        data, page_headers = request_page(
            client,
            api_id,
            path,
            body,
            cont_yn=cont_yn,
            next_key=next_key,
        )

        page_rows = data.get(table_key) or []
        if isinstance(page_rows, list):
            rows.extend(r for r in page_rows if isinstance(r, dict))

        cont_yn = page_headers["cont-yn"]
        next_key = page_headers["next-key"]

        if cont_yn != "Y" or not next_key:
            break

        time.sleep(0.35)

    return rows


def fetch_daily(
    client: KiwoomClient,
    industry_code: str,
    base_date: str,
) -> list[dict[str, Any]]:
    rows = fetch_pages(
        client,
        "ka20006",
        "/api/dostk/chart",
        {"inds_cd": industry_code, "base_dt": base_date},
        "inds_dt_pole_qry",
        max_pages=3,
    )
    rows.sort(key=lambda r: str(r.get("dt", "")), reverse=True)
    return rows


def fetch_60m(
    client: KiwoomClient,
    industry_code: str,
    base_date: str,
) -> list[dict[str, Any]]:
    rows = fetch_pages(
        client,
        "ka20005",
        "/api/dostk/chart",
        {"inds_cd": industry_code, "tic_scope": "60", "base_dt": base_date},
        "inds_min_pole_qry",
        max_pages=3,
    )
    rows.sort(key=lambda r: str(r.get("cntr_tm", "")), reverse=True)
    return rows


def fetch_sector_current(
    client: KiwoomClient,
    market_type: str,
    industry_code: str,
) -> dict[str, Any]:
    data, _ = request_page(
        client,
        "ka20001",
        "/api/dostk/sect",
        {"mrkt_tp": market_type, "inds_cd": industry_code},
    )
    return data


def unique_daily_before(
    daily_rows: list[dict[str, Any]],
    target_date: str,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()

    for row in daily_rows:
        date_key = _trade_date(row)
        if not date_key or date_key >= target_date or date_key in seen:
            continue
        seen.add(date_key)
        selected.append(row)
        if len(selected) >= limit:
            break

    return selected


def find_daily_row(
    daily_rows: list[dict[str, Any]],
    target_date: str,
) -> dict[str, Any] | None:
    for row in daily_rows:
        if _trade_date(row) == target_date:
            return row
    return None


def select_max_volume_60m(
    minute_rows: list[dict[str, Any]],
    valid_dates: set[str],
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    for row in minute_rows:
        ts = _parse_ts(row.get("cntr_tm"))
        if ts is None:
            continue

        date_key = ts.strftime("%Y%m%d")
        if date_key not in valid_dates:
            continue

        if ts.time() < dt_time(9, 0) or ts.time() > dt_time(15, 40):
            continue

        ts_key = ts.strftime("%Y%m%d%H%M%S")
        if ts_key in seen:
            continue
        seen.add(ts_key)

        open_price = _index_price(row.get("open_pric"))
        high = _index_price(row.get("high_pric"))
        low = _index_price(row.get("low_pric"))
        close = _index_price(row.get("cur_prc"))
        volume = _volume(row.get("trde_qty"))

        if None in (open_price, high, low, close, volume):
            continue

        candidates.append(
            {
                "timestamp": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "open": open_price,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "date": date_key,
            }
        )

    if not candidates:
        return None

    return max(candidates, key=lambda x: (x["volume"], x["timestamp"]))


def build_reference(
    client: KiwoomClient,
    market: str,
    industry_code: str,
    target_date: str,
) -> dict[str, Any]:
    daily = fetch_daily(client, industry_code, target_date)
    prior = unique_daily_before(daily, target_date, limit=20)

    if len(prior) < 20:
        raise RuntimeError(
            f"{market} reference history short: {len(prior)} rows before {target_date}"
        )

    asof_date = _trade_date(prior[0])
    previous_close = _index_price(prior[0].get("cur_prc"))

    highs = [_index_price(row.get("high_pric")) for row in prior]
    lows = [_index_price(row.get("low_pric")) for row in prior]

    clean_highs = [value for value in highs if value is not None]
    clean_lows = [value for value in lows if value is not None]

    if len(clean_highs) != 20 or len(clean_lows) != 20 or previous_close is None:
        raise RuntimeError(f"{market} invalid 20D reference data before {target_date}")

    valid_dates = {_trade_date(row) for row in prior}
    minute_rows = fetch_60m(client, industry_code, asof_date)
    max60 = select_max_volume_60m(minute_rows, valid_dates)

    if not max60:
        raise RuntimeError(f"{market} MAX60 reference missing before {target_date}")

    return {
        "market": market,
        "industry_code": industry_code,
        "target_date": target_date,
        "ref_asof_date": asof_date,
        "previous_close": previous_close,
        "high_20d": max(clean_highs),
        "low_20d": min(clean_lows),
        "max60_start": max60["timestamp"],
        "max60_open": max60["open"],
        "max60_high": max60["high"],
        "max60_low": max60["low"],
        "max60_close": max60["close"],
        "max60_volume": max60["volume"],
        "max60_trading_value": None,
    }


def build_live_row(
    client: KiwoomClient,
    cfg: dict[str, str],
    reference: dict[str, Any],
    target_date: str,
) -> dict[str, Any]:
    snapshot_at = now_kst_string()

    data = fetch_sector_current(
        client,
        cfg["market_type"],
        cfg["industry_code"],
    )

    row = {
        "snapshot_at": snapshot_at,
        "captured_at": snapshot_at,
        "market": cfg["market"],
        "industry_code": cfg["industry_code"],
        "trade_date": iso_date(target_date),
        "current_price": _index_price(data.get("cur_prc")),
        "open_price": _index_price(data.get("open_pric")),
        "high_price": _index_price(data.get("high_pric")),
        "low_price": _index_price(data.get("low_pric")),
        "previous_close": reference["previous_close"],
        "high_20d": reference["high_20d"],
        "low_20d": reference["low_20d"],
        "max60_start": reference["max60_start"],
        "max60_open": reference["max60_open"],
        "max60_high": reference["max60_high"],
        "max60_low": reference["max60_low"],
        "max60_close": reference["max60_close"],
        "max60_volume": reference["max60_volume"],
        "max60_trading_value": None,
        "status": "OK",
        "error": "",
        "note": (
            f"ref_asof_date={reference['ref_asof_date']}; "
            "20D_and_MAX60_fixed_from_prior_20_completed_sessions; "
            "max60_rule=MAX_VOLUME_60M"
        ),
    }

    required = (
        "current_price",
        "open_price",
        "high_price",
        "low_price",
        "previous_close",
        "high_20d",
        "low_20d",
        "max60_start",
        "max60_open",
        "max60_high",
        "max60_low",
        "max60_close",
        "max60_volume",
    )

    missing = [key for key in required if row.get(key) in (None, "")]
    if missing:
        row["status"] = "PENDING"
        row["error"] = "missing:" + ",".join(missing)

    return row


def build_final_row(
    client: KiwoomClient,
    cfg: dict[str, str],
    reference: dict[str, Any],
    target_date: str,
) -> dict[str, Any]:
    snapshot_at = now_kst_string()
    daily = fetch_daily(client, cfg["industry_code"], target_date)
    final = find_daily_row(daily, target_date)

    if not final:
        raise RuntimeError(f"{cfg['market']} final daily row not found for {target_date}")

    return {
        "snapshot_at": snapshot_at,
        "captured_at": snapshot_at,
        "market": cfg["market"],
        "industry_code": cfg["industry_code"],
        "trade_date": iso_date(target_date),
        "current_price": _index_price(final.get("cur_prc")),
        "open_price": _index_price(final.get("open_pric")),
        "high_price": _index_price(final.get("high_pric")),
        "low_price": _index_price(final.get("low_pric")),
        "previous_close": reference["previous_close"],
        "high_20d": reference["high_20d"],
        "low_20d": reference["low_20d"],
        "max60_start": reference["max60_start"],
        "max60_open": reference["max60_open"],
        "max60_high": reference["max60_high"],
        "max60_low": reference["max60_low"],
        "max60_close": reference["max60_close"],
        "max60_volume": reference["max60_volume"],
        "max60_trading_value": None,
        "status": "OK",
        "error": "",
        "note": (
            f"FINAL_CLOSE; ref_asof_date={reference['ref_asof_date']}; "
            "20D_and_MAX60_fixed_from_prior_20_completed_sessions; "
            "max60_rule=MAX_VOLUME_60M"
        ),
    }


def session_started(
    client: KiwoomClient,
    target_date: str,
) -> bool:
    for cfg in MARKETS:
        daily = fetch_daily(client, cfg["industry_code"], target_date)
        if find_daily_row(daily, target_date) is None:
            return False
    return True


def send_rows(
    rows: list[dict[str, Any]],
    runtime: TechnicalRuntime,
    *,
    mode: str,
) -> dict[str, Any]:
    payload = {
        "secret": runtime.ingest_secret,
        "type": "technical_position",
        "mode": mode,
        "source": "kiwoom-rest-ka20001-ka20006-ka20005",
        "collector_version": COLLECTOR_VERSION,
        "captured_at": now_kst_string(),
        "row_count": len(rows),
        "rows": rows,
    }

    response = requests.post(
        runtime.webhook_url,
        json=payload,
        timeout=30,
    )
    response.raise_for_status()

    try:
        result = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Apps Script returned non-JSON: {response.text[:500]}"
        ) from exc

    if not result.get("ok"):
        raise RuntimeError(f"Apps Script technical_position ingestion failed: {result}")

    return result


def build_references_for_day(
    client: KiwoomClient,
    target_date: str,
) -> dict[str, dict[str, Any]]:
    references: dict[str, dict[str, Any]] = {}

    for cfg in MARKETS:
        references[cfg["market"]] = build_reference(
            client,
            cfg["market"],
            cfg["industry_code"],
            target_date,
        )
        time.sleep(0.25)

    return references


def send_live_snapshot(
    client: KiwoomClient,
    runtime: TechnicalRuntime,
    references: dict[str, dict[str, Any]],
    target_date: str,
) -> None:
    rows: list[dict[str, Any]] = []

    for idx, cfg in enumerate(MARKETS):
        rows.append(
            build_live_row(
                client,
                cfg,
                references[cfg["market"]],
                target_date,
            )
        )
        if idx + 1 < len(MARKETS):
            time.sleep(0.2)

    result = send_rows(rows, runtime, mode="LIVE")
    summary = ", ".join(
        f"{row['market']}={row['status']} current={row.get('current_price')}"
        for row in rows
    )
    print(
        f"[{now_kst_string()}] LIVE sent | {summary} | sheet={result.get('sheet')}",
        flush=True,
    )


def send_final_snapshot(
    client: KiwoomClient,
    runtime: TechnicalRuntime,
    references: dict[str, dict[str, Any]],
    target_date: str,
) -> None:
    rows: list[dict[str, Any]] = []

    for idx, cfg in enumerate(MARKETS):
        rows.append(
            build_final_row(
                client,
                cfg,
                references[cfg["market"]],
                target_date,
            )
        )
        if idx + 1 < len(MARKETS):
            time.sleep(0.2)

    result = send_rows(rows, runtime, mode="LIVE")
    summary = ", ".join(
        f"{row['market']} close={row.get('current_price')}"
        for row in rows
    )
    print(
        f"[{now_kst_string()}] FINAL sent | {summary} | sheet={result.get('sheet')}",
        flush=True,
    )


def run_once(
    settings: Settings,
    runtime: TechnicalRuntime,
    target_date: str,
    *,
    final: bool = False,
) -> int:
    client = KiwoomClient(settings)
    references = build_references_for_day(client, target_date)

    if not session_started(client, target_date):
        print(
            f"[{now_kst_string()}] {target_date} is not confirmed as an active session; no send",
            flush=True,
        )
        return 0

    if final:
        send_final_snapshot(client, runtime, references, target_date)
    else:
        send_live_snapshot(client, runtime, references, target_date)

    return 0


def seconds_until_next_prep(current: datetime, prep_time: dt_time) -> int:
    tomorrow = current.date().toordinal() + 1
    next_date = datetime.fromordinal(tomorrow).date()
    target = datetime.combine(next_date, prep_time, tzinfo=KST)
    return max(30, int((target - current).total_seconds()))


def run_loop(
    settings: Settings,
    runtime: TechnicalRuntime,
) -> int:
    client = KiwoomClient(settings)

    active_date = ""
    references: dict[str, dict[str, Any]] = {}
    session_confirmed = False
    final_sent = False
    session_probe_at = 0.0

    print(
        f"[{now_kst_string()}] technical-position LIVE loop start | "
        f"poll={runtime.poll_seconds}s prep={runtime.prep_time.strftime('%H:%M')} "
        f"live={runtime.live_start.strftime('%H:%M')}..{runtime.live_end.strftime('%H:%M')} "
        f"final={runtime.final_time.strftime('%H:%M')}",
        flush=True,
    )

    while True:
        try:
            current = now_kst()
            today = current.strftime("%Y%m%d")

            if current.weekday() >= 5:
                time.sleep(min(1800, seconds_until_next_prep(current, runtime.prep_time)))
                continue

            if active_date != today:
                active_date = today
                references = {}
                session_confirmed = False
                final_sent = False
                session_probe_at = 0.0

            if current.time() < runtime.prep_time:
                time.sleep(30)
                continue

            if not references:
                references = build_references_for_day(client, today)
                asofs = ", ".join(
                    f"{market}:{ref['ref_asof_date']}"
                    for market, ref in references.items()
                )
                print(
                    f"[{now_kst_string()}] references locked for {today} | {asofs}",
                    flush=True,
                )

            if current.time() < runtime.live_start:
                time.sleep(15)
                continue

            if not session_confirmed:
                now_mono = time.monotonic()

                if now_mono >= session_probe_at:
                    session_confirmed = session_started(client, today)
                    session_probe_at = now_mono + 60

                if not session_confirmed:
                    if current.time() >= runtime.session_confirm_until:
                        print(
                            f"[{now_kst_string()}] no {today} session confirmed by "
                            f"{runtime.session_confirm_until.strftime('%H:%M')}; sleep until next day",
                            flush=True,
                        )
                        time.sleep(min(3600, seconds_until_next_prep(current, runtime.prep_time)))
                    else:
                        time.sleep(15)
                    continue

                print(
                    f"[{now_kst_string()}] session confirmed for {today}",
                    flush=True,
                )

            if current.time() <= runtime.live_end:
                send_live_snapshot(client, runtime, references, today)
                time.sleep(runtime.poll_seconds)
                continue

            if not final_sent and current.time() >= runtime.final_time:
                send_final_snapshot(client, runtime, references, today)
                final_sent = True
                time.sleep(60)
                continue

            if final_sent:
                time.sleep(min(1800, seconds_until_next_prep(current, runtime.prep_time)))
            else:
                time.sleep(15)

        except KeyboardInterrupt:
            print("Stopped by user", flush=True)
            return 130
        except Exception as exc:
            print(
                f"[{now_kst_string()}] LOOP ERROR: {exc}",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(max(30, runtime.poll_seconds))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "LIVE-first KOSPI/KOSDAQ technical-position collector. "
            "References are locked from the prior 20 completed sessions; "
            "today's live price updates the same two DAILY rows."
        )
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="run continuously; intended for systemd",
    )
    parser.add_argument(
        "--base-date",
        default=yyyymmdd(),
        help="YYYYMMDD; one-shot target date, default=today KST",
    )
    parser.add_argument(
        "--final",
        action="store_true",
        help="one-shot final close snapshot using ka20006",
    )

    args = parser.parse_args()

    try:
        settings, runtime = load_runtime()
    except Exception as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2

    if args.loop:
        return run_loop(settings, runtime)

    try:
        return run_once(
            settings,
            runtime,
            args.base_date,
            final=args.final,
        )
    except Exception as exc:
        print(f"RUN ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
