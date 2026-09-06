from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from kiwoom_collector import KiwoomClient
from technical_position_collector import (
    COLLECTOR_VERSION,
    MARKETS,
    _index_price,
    build_reference,
    fetch_daily,
    find_daily_row,
    iso_date,
    load_runtime,
    send_rows,
)

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


def build_backfill_row(
    client: KiwoomClient,
    cfg: dict[str, str],
    target_date: str,
) -> dict[str, Any] | None:
    daily = fetch_daily(client, cfg["industry_code"], target_date)
    target = find_daily_row(daily, target_date)

    if target is None:
        return None

    reference = build_reference(
        client,
        cfg["market"],
        cfg["industry_code"],
        target_date,
    )

    snapshot_at = f"{iso_date(target_date)} 15:30:00"

    row = {
        "snapshot_at": snapshot_at,
        "captured_at": snapshot_at,
        "market": cfg["market"],
        "industry_code": cfg["industry_code"],
        "trade_date": iso_date(target_date),
        "current_price": _index_price(target.get("cur_prc")),
        "open_price": _index_price(target.get("open_pric")),
        "high_price": _index_price(target.get("high_pric")),
        "low_price": _index_price(target.get("low_pric")),
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
            f"BACKFILL_CLOSE; ref_asof_date={reference['ref_asof_date']}; "
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


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill technical-position DAILY closing rows. "
            "Each target day uses only the prior 20 completed sessions "
            "for 20D/MAX60 references, so no future/current-day reference leakage occurs."
        )
    )
    parser.add_argument(
        "--start-date",
        default="20260102",
        help="YYYYMMDD; first visible 2026 exchange-session candidate",
    )
    parser.add_argument(
        "--end-date",
        default="20260904",
        help="YYYYMMDD; default latest completed session before 2026-09-06",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=1.5,
        help="pause between completed dates",
    )
    parser.add_argument(
        "--market-sleep-seconds",
        type=float,
        default=0.4,
        help="pause between KOSPI/KOSDAQ calls",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="stop at first failed trading day",
    )

    args = parser.parse_args()

    start = parse_date(args.start_date)
    end = parse_date(args.end_date)

    if start > end:
        parser.error("--start-date must be <= --end-date")

    if args.sleep_seconds < 0 or args.market_sleep_seconds < 0:
        parser.error("sleep values must be >= 0")

    try:
        settings, runtime = load_runtime()
    except Exception as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2

    client = KiwoomClient(settings)

    attempted_weekdays = 0
    sent_sessions = 0
    skipped_non_sessions = 0
    errors = 0

    print(
        f"BACKFILL START {start:%Y-%m-%d}..{end:%Y-%m-%d} "
        f"mode=BACKFILL collector={COLLECTOR_VERSION}",
        flush=True,
    )

    for dt in date_range(start, end):
        if dt.weekday() >= 5:
            continue

        target_date = dt.strftime("%Y%m%d")
        attempted_weekdays += 1

        try:
            rows: list[dict[str, Any]] = []
            non_session = False

            for idx, cfg in enumerate(MARKETS):
                row = build_backfill_row(
                    client,
                    cfg,
                    target_date,
                )

                if row is None:
                    non_session = True
                    break

                rows.append(row)

                if idx + 1 < len(MARKETS):
                    time.sleep(args.market_sleep_seconds)

            if non_session or len(rows) != len(MARKETS):
                skipped_non_sessions += 1
                print(
                    f"SKIP {target_date}: no exact KOSPI/KOSDAQ daily row; "
                    "treated as exchange non-session",
                    flush=True,
                )
                time.sleep(args.sleep_seconds)
                continue

            bad = [
                row
                for row in rows
                if str(row.get("status", "")).upper() == "ERROR"
            ]
            if bad:
                raise RuntimeError(
                    " | ".join(
                        f"{row.get('market')}:{row.get('error')}"
                        for row in bad
                    )
                )

            result = send_rows(
                rows,
                runtime,
                mode="BACKFILL",
            )

            sent_sessions += 1

            summary = ", ".join(
                f"{row['market']}={row['status']} "
                f"close={row.get('current_price')} "
                f"20D={row.get('low_20d')}~{row.get('high_20d')} "
                f"MAX60={row.get('max60_low')}~{row.get('max60_high')}"
                for row in rows
            )

            print(
                f"PASS {target_date}: {summary} | sheet={result.get('sheet')}",
                flush=True,
            )

        except KeyboardInterrupt:
            print("Stopped by user", file=sys.stderr)
            return 130
        except Exception as exc:
            errors += 1
            print(
                f"ERROR {target_date}: {exc}",
                file=sys.stderr,
                flush=True,
            )
            if args.fail_fast:
                return 1

        time.sleep(args.sleep_seconds)

    print(
        f"BACKFILL DONE attempted_weekdays={attempted_weekdays} "
        f"sent_sessions={sent_sessions} "
        f"skipped_non_sessions={skipped_non_sessions} "
        f"errors={errors}",
        flush=True,
    )

    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
