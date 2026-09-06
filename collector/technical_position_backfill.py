from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from kiwoom_collector import KiwoomClient, Settings
from technical_position_collector import MARKETS, build_row, send_rows

KST = ZoneInfo("Asia/Seoul")


def parse_date(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y%m%d").replace(tzinfo=KST)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must be YYYYMMDD") from exc


def date_range(start: datetime, end: datetime):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill KOSPI/KOSDAQ technical-position DAILY rows from Kiwoom REST"
    )
    parser.add_argument("--start-date", default="20260101", help="YYYYMMDD; visible 2026 dataset start")
    parser.add_argument("--end-date", default="20260904", help="YYYYMMDD; default latest session before 2026-09-06")
    parser.add_argument(
        "--warmup-start",
        default="20251201",
        help="YYYYMMDD; reserved/documented RAW warm-up start for exact 20D calculations",
    )
    parser.add_argument("--sleep-seconds", type=float, default=2.0, help="pause between trading days")
    parser.add_argument("--market-sleep-seconds", type=float, default=0.8, help="pause between KOSPI/KOSDAQ calls")
    parser.add_argument("--fail-fast", action="store_true", help="stop at first failed trading day")
    args = parser.parse_args()

    start = parse_date(args.start_date)
    end = parse_date(args.end_date)
    warmup = parse_date(args.warmup_start)
    if start > end:
        parser.error("--start-date must be <= --end-date")
    if warmup > start:
        parser.error("--warmup-start must be <= --start-date")
    if args.sleep_seconds < 0 or args.market_sleep_seconds < 0:
        parser.error("sleep values must be >= 0")

    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    try:
        settings = Settings.from_env()
    except Exception as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2

    client = KiwoomClient(settings)
    attempted = 0
    sent_days = 0
    skipped_non_sessions = 0
    errors = 0

    print(
        f"BACKFILL START visible={start:%Y-%m-%d}..{end:%Y-%m-%d} "
        f"warmup={warmup:%Y-%m-%d} markets=KOSPI,KOSDAQ",
        flush=True,
    )

    for dt in date_range(start, end):
        # Weekend can be skipped without touching Kiwoom. Korean exchange holidays are
        # detected by checking that returned trade_date equals the requested date.
        if dt.weekday() >= 5:
            continue

        requested = dt.strftime("%Y%m%d")
        attempted += 1
        rows = []
        try:
            for idx, cfg in enumerate(MARKETS):
                row = build_row(client, cfg["market"], cfg["industry_code"], requested)
                rows.append(row)
                if idx + 1 < len(MARKETS):
                    time.sleep(args.market_sleep_seconds)

            returned_dates = {
                str(r.get("trade_date", "")).replace("-", "")
                for r in rows
                if r.get("trade_date")
            }

            # If Kiwoom returns the preceding session for a holiday (e.g. 2026-01-01),
            # do not create a fake row for the holiday.
            if returned_dates != {requested}:
                skipped_non_sessions += 1
                print(
                    f"SKIP {requested}: exchange non-session or incomplete date match "
                    f"returned={sorted(returned_dates)}",
                    flush=True,
                )
                time.sleep(args.sleep_seconds)
                continue

            bad = [r for r in rows if str(r.get("status", "")).upper() == "ERROR"]
            if bad:
                raise RuntimeError(
                    " | ".join(f"{r.get('market')}:{r.get('error')}" for r in bad)
                )

            result = send_rows(rows, settings)
            sent_days += 1
            summary = ", ".join(
                f"{r.get('market')}={r.get('status')} 20D={r.get('low_20d')}~{r.get('high_20d')} "
                f"MAX60={r.get('max60_low')}~{r.get('max60_high')}"
                for r in rows
            )
            print(
                f"PASS {requested}: {summary} | sheet={result.get('sheet')}",
                flush=True,
            )
        except KeyboardInterrupt:
            print("Stopped by user", file=sys.stderr)
            return 130
        except Exception as exc:
            errors += 1
            print(f"ERROR {requested}: {exc}", file=sys.stderr, flush=True)
            if args.fail_fast:
                return 1

        time.sleep(args.sleep_seconds)

    print(
        f"BACKFILL DONE attempted_weekdays={attempted} sent_sessions={sent_days} "
        f"skipped_non_sessions={skipped_non_sessions} errors={errors}",
        flush=True,
    )
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
