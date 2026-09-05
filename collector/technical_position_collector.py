from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo
from typing import Any

import requests
from dotenv import load_dotenv

from kiwoom_collector import KiwoomClient, Settings

COLLECTOR_VERSION = "technical-position-v1"
KST = ZoneInfo("Asia/Seoul")

MARKETS = (
    {"market": "KOSPI", "industry_code": "001"},
    {"market": "KOSDAQ", "industry_code": "101"},
)


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
        n = abs(float(raw))
    except ValueError:
        return None
    if "." in raw:
        return n
    return n / 100.0 if n >= 10000 else n


def _volume(value: Any) -> float:
    n = _plain_number(value)
    return abs(n) if n is not None else 0.0


def now_kst() -> datetime:
    return datetime.now(KST)


def now_kst_string() -> str:
    return now_kst().strftime("%Y-%m-%d %H:%M:%S")


def yyyymmdd(dt: datetime | None = None) -> str:
    return (dt or now_kst()).strftime("%Y%m%d")


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
        retry_after = response.headers.get("Retry-After", "").strip()
        try:
            retry_seconds = int(float(retry_after)) if retry_after else 0
        except ValueError:
            retry_seconds = 0
        wait_seconds = max(waits[rate_attempt], retry_seconds)
        time.sleep(wait_seconds)

    raise RuntimeError(f"{api_id} request failed")


def fetch_pages(
    client: KiwoomClient,
    api_id: str,
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
            "/api/dostk/chart",
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
        time.sleep(0.4)
    return rows


def fetch_daily(client: KiwoomClient, industry_code: str, base_date: str) -> list[dict[str, Any]]:
    rows = fetch_pages(
        client,
        "ka20006",
        {"inds_cd": industry_code, "base_dt": base_date},
        "inds_dt_pole_qry",
        max_pages=3,
    )
    rows.sort(key=lambda r: str(r.get("dt", "")), reverse=True)
    return rows


def fetch_60m(client: KiwoomClient, industry_code: str, base_date: str) -> list[dict[str, Any]]:
    # Current Kiwoom official examples support native 60-minute sector bars.
    rows = fetch_pages(
        client,
        "ka20005",
        {"inds_cd": industry_code, "tic_scope": "60", "base_dt": base_date},
        "inds_min_pole_qry",
        max_pages=10,
    )
    rows.sort(key=lambda r: str(r.get("cntr_tm", "")), reverse=True)
    return rows


def _trade_date(row: dict[str, Any]) -> str:
    return str(row.get("dt", "")).strip().replace("-", "")[:8]


def _parse_ts(value: Any) -> datetime | None:
    text = str(value or "").strip().replace("-", "").replace(":", "").replace(" ", "")
    if len(text) < 12:
        return None
    text = text[:14].ljust(14, "0")
    try:
        return datetime.strptime(text, "%Y%m%d%H%M%S").replace(tzinfo=KST)
    except ValueError:
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
        high = _index_price(row.get("high_pric"))
        low = _index_price(row.get("low_pric"))
        if high is None or low is None:
            continue
        candidates.append(
            {
                "start": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "high": high,
                "low": low,
                "volume": _volume(row.get("trde_qty")),
                "date": date_key,
            }
        )

    if not candidates:
        return None
    return max(candidates, key=lambda x: (x["volume"], x["start"]))


def build_row(client: KiwoomClient, market: str, industry_code: str, base_date: str) -> dict[str, Any]:
    captured_at = now_kst_string()
    row: dict[str, Any] = {
        "captured_at": captured_at,
        "market": market,
        "industry_code": industry_code,
        "trade_date": "",
        "current_price": None,
        "open_price": None,
        "high_price": None,
        "low_price": None,
        "previous_close": None,
        "high_20d": None,
        "low_20d": None,
        "max60_start": "",
        "max60_high": None,
        "max60_low": None,
        "max60_volume": None,
        "daily_api_id": "ka20006",
        "minute_api_id": "ka20005",
        "status": "OK",
        "error": "",
        "note": "20D includes latest available trading day; max60 uses native ka20005 60-minute sector bars",
    }

    try:
        daily = fetch_daily(client, industry_code, base_date)
        if not daily:
            raise RuntimeError("ka20006 returned no daily rows")

        unique_daily: list[dict[str, Any]] = []
        seen_dates: set[str] = set()
        for item in daily:
            d = _trade_date(item)
            if not d or d in seen_dates:
                continue
            seen_dates.add(d)
            unique_daily.append(item)
            if len(unique_daily) >= 20:
                break

        if len(unique_daily) < 20:
            row["status"] = "PENDING"
            row["error"] = f"daily_history_short:{len(unique_daily)}"

        latest = unique_daily[0]
        trade_date = _trade_date(latest)
        row["trade_date"] = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
        row["current_price"] = _index_price(latest.get("cur_prc"))
        row["open_price"] = _index_price(latest.get("open_pric"))
        row["high_price"] = _index_price(latest.get("high_pric"))
        row["low_price"] = _index_price(latest.get("low_pric"))
        row["previous_close"] = _index_price(latest.get("pred_close_pric"))
        if row["previous_close"] is None and len(unique_daily) > 1:
            row["previous_close"] = _index_price(unique_daily[1].get("cur_prc"))

        highs = [_index_price(x.get("high_pric")) for x in unique_daily]
        lows = [_index_price(x.get("low_pric")) for x in unique_daily]
        highs = [x for x in highs if x is not None]
        lows = [x for x in lows if x is not None]
        if highs:
            row["high_20d"] = max(highs)
        if lows:
            row["low_20d"] = min(lows)

        minute = fetch_60m(client, industry_code, base_date)
        valid_dates = {_trade_date(x) for x in unique_daily if _trade_date(x)}
        max60 = select_max_volume_60m(minute, valid_dates)
        if max60:
            row["max60_start"] = max60["start"]
            row["max60_high"] = max60["high"]
            row["max60_low"] = max60["low"]
            row["max60_volume"] = max60["volume"]
        else:
            row["status"] = "PENDING"
            row["error"] = (row["error"] + " | " if row["error"] else "") + "max60_missing"

        required = ("current_price", "high_20d", "low_20d", "max60_high", "max60_low")
        missing = [key for key in required if row.get(key) is None]
        if missing:
            row["status"] = "PENDING"
            row["error"] = (row["error"] + " | " if row["error"] else "") + "missing:" + ",".join(missing)
    except Exception as exc:
        row["status"] = "ERROR"
        row["error"] = str(exc)

    return row


def send_rows(rows: list[dict[str, Any]], settings: Settings) -> dict[str, Any]:
    payload = {
        "secret": settings.ingest_secret,
        "type": "technical_position",
        "source": "kiwoom-rest-ka20006-ka20005",
        "collector_version": COLLECTOR_VERSION,
        "captured_at": now_kst_string(),
        "row_count": len(rows),
        "rows": rows,
    }
    response = requests.post(settings.webhook_url, json=payload, timeout=30)
    response.raise_for_status()
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(f"Apps Script technical_position ingestion failed: {result}")
    return result


def run_once(settings: Settings, base_date: str, *, force: bool = False) -> None:
    client = KiwoomClient(settings)
    rows = [build_row(client, cfg["market"], cfg["industry_code"], base_date) for cfg in MARKETS]

    today = yyyymmdd()
    if not force and base_date == today:
        trade_dates = {str(r.get("trade_date", "")).replace("-", "") for r in rows if r.get("trade_date")}
        if today not in trade_dates:
            print(f"[{now_kst_string()}] no current trading-day data; skip send | latest={sorted(trade_dates)}")
            return

    result = send_rows(rows, settings)
    summary = ", ".join(f"{r['market']}={r['status']}({r['trade_date']})" for r in rows)
    print(f"[{now_kst_string()}] technical_position sent {len(rows)} rows | {summary} | sheet={result.get('sheet')}")


def parse_hhmm(value: str) -> dt_time:
    hh, mm = value.strip().split(":", 1)
    return dt_time(hour=int(hh), minute=int(mm))


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect KOSPI/KOSDAQ technical-position reference data")
    parser.add_argument("--base-date", default=yyyymmdd(), help="YYYYMMDD; default=today KST")
    parser.add_argument("--force", action="store_true", help="send even when latest trade date is not today")
    parser.add_argument("--loop", action="store_true", help="run once per weekday after --run-time")
    parser.add_argument("--run-time", default=os.getenv("TECH_POSITION_RUN_TIME", "15:20"), help="HH:MM KST")
    args = parser.parse_args()

    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    try:
        settings = Settings.from_env()
        target_time = parse_hhmm(args.run_time)
    except Exception as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2

    if not args.loop:
        try:
            run_once(settings, args.base_date, force=args.force)
            return 0
        except Exception as exc:
            print(f"RUN ERROR: {exc}", file=sys.stderr)
            return 1

    last_attempt_date = ""
    while True:
        try:
            current = now_kst()
            today_key = current.strftime("%Y%m%d")
            if current.weekday() < 5 and current.time() >= target_time and last_attempt_date != today_key:
                run_once(settings, today_key, force=False)
                last_attempt_date = today_key
        except KeyboardInterrupt:
            return 0
        except Exception as exc:
            print(f"RUN ERROR: {exc}", file=sys.stderr)
        time.sleep(60)


if __name__ == "__main__":
    raise SystemExit(main())
