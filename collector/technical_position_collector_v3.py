from __future__ import annotations

import time
from dataclasses import replace

import technical_position_collector as base


COLLECTOR_VERSION = "technical-position-live-v3-premarket"


def _has_live_sector_values(client, cfg: dict[str, str]) -> bool:
    """Return True when ka20001 shows a live/open session for this market."""
    data = base.fetch_sector_current(
        client,
        cfg["market_type"],
        cfg["industry_code"],
    )

    values = [
        base._index_price(data.get("cur_prc")),
        base._index_price(data.get("open_pric")),
        base._index_price(data.get("high_pric")),
        base._index_price(data.get("low_pric")),
    ]

    return all(value is not None and value > 0 for value in values)


def session_started_live(client, target_date: str) -> bool:
    """Confirm a trading session without depending on the daily bar alone."""
    for cfg in base.MARKETS:
        try:
            if _has_live_sector_values(client, cfg):
                continue
        except Exception:
            pass

        minute_rows = base.fetch_60m(client, cfg["industry_code"], target_date)
        has_today_intraday = False
        for row in minute_rows:
            ts = base._parse_ts(row.get("cntr_tm"))
            if ts is not None and ts.strftime("%Y%m%d") == target_date:
                has_today_intraday = True
                break

        if has_today_intraday:
            continue

        daily = base.fetch_daily(client, cfg["industry_code"], target_date)
        if base.find_daily_row(daily, target_date) is None:
            return False

    return True


def build_prep_row(
    cfg: dict[str, str],
    reference: dict,
    target_date: str,
) -> dict:
    """Build the premarket row before the opening auction.

    The daily row must already exist before 09:00 so the operator can review
    technical context and, only when market structure changes, overwrite the
    carried BOX HIGH/LOW. Before the session opens, previous close is used as
    the current reference price; live OHLC replaces it after 09:00.
    """
    snapshot_at = base.now_kst_string()
    return {
        "snapshot_at": snapshot_at,
        "captured_at": snapshot_at,
        "market": cfg["market"],
        "industry_code": cfg["industry_code"],
        "trade_date": base.iso_date(target_date),
        "current_price": reference["previous_close"],
        "open_price": None,
        "high_price": None,
        "low_price": None,
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
        "status": "PREP",
        "error": "",
        "note": (
            f"PREMARKET_PREP; ref_asof_date={reference['ref_asof_date']}; "
            "current_price=previous_close_until_live; "
            "20D_and_MAX60_fixed_from_prior_20_completed_sessions"
        ),
    }


def send_prep_snapshot(
    client,
    runtime,
    references: dict[str, dict],
    target_date: str,
) -> None:
    rows = [
        build_prep_row(cfg, references[cfg["market"]], target_date)
        for cfg in base.MARKETS
    ]
    result = base.send_rows(rows, runtime, mode="LIVE")
    summary = ", ".join(
        f"{row['market']} prev_close={row['previous_close']}"
        for row in rows
    )
    print(
        f"[{base.now_kst_string()}] PREP sent | {summary} | sheet={result.get('sheet')}",
        flush=True,
    )


_original_load_runtime = base.load_runtime


def load_runtime_v3():
    settings, runtime = _original_load_runtime()
    # A delayed exchange/API signal must not suppress the whole trading day.
    runtime = replace(runtime, session_confirm_until=runtime.live_end)
    return settings, runtime


def run_loop_v3(settings, runtime) -> int:
    client = base.KiwoomClient(settings)

    active_date = ""
    references: dict[str, dict] = {}
    prep_sent = False
    session_confirmed = False
    final_sent = False
    session_probe_at = 0.0

    print(
        f"[{base.now_kst_string()}] technical-position LIVE loop start | "
        f"poll={runtime.poll_seconds}s prep={runtime.prep_time.strftime('%H:%M')} "
        f"live={runtime.live_start.strftime('%H:%M')}..{runtime.live_end.strftime('%H:%M')} "
        f"final={runtime.final_time.strftime('%H:%M')}",
        flush=True,
    )

    while True:
        try:
            current = base.now_kst()
            today = current.strftime("%Y%m%d")

            if current.weekday() >= 5:
                time.sleep(min(1800, base.seconds_until_next_prep(current, runtime.prep_time)))
                continue

            if active_date != today:
                active_date = today
                references = {}
                prep_sent = False
                session_confirmed = False
                final_sent = False
                session_probe_at = 0.0

            if current.time() < runtime.prep_time:
                time.sleep(30)
                continue

            if not references:
                references = base.build_references_for_day(client, today)
                asofs = ", ".join(
                    f"{market}:{ref['ref_asof_date']}"
                    for market, ref in references.items()
                )
                print(
                    f"[{base.now_kst_string()}] references locked for {today} | {asofs}",
                    flush=True,
                )

            # Critical: create/update today's DAILY rows BEFORE 09:00.
            if not prep_sent:
                send_prep_snapshot(client, runtime, references, today)
                prep_sent = True

            if current.time() < runtime.live_start:
                time.sleep(15)
                continue

            if not session_confirmed:
                now_mono = time.monotonic()
                if now_mono >= session_probe_at:
                    session_confirmed = session_started_live(client, today)
                    session_probe_at = now_mono + 60

                if not session_confirmed:
                    time.sleep(15)
                    continue

                print(
                    f"[{base.now_kst_string()}] session confirmed for {today}",
                    flush=True,
                )

            if current.time() <= runtime.live_end:
                base.send_live_snapshot(client, runtime, references, today)
                time.sleep(runtime.poll_seconds)
                continue

            if not final_sent and current.time() >= runtime.final_time:
                base.send_final_snapshot(client, runtime, references, today)
                final_sent = True
                time.sleep(60)
                continue

            if final_sent:
                time.sleep(min(1800, base.seconds_until_next_prep(current, runtime.prep_time)))
            else:
                time.sleep(15)

        except KeyboardInterrupt:
            print("Stopped by user", flush=True)
            return 130
        except Exception as exc:
            print(
                f"[{base.now_kst_string()}] LOOP ERROR: {exc}",
                flush=True,
            )
            time.sleep(max(30, runtime.poll_seconds))


base.session_started = session_started_live
base.load_runtime = load_runtime_v3
base.run_loop = run_loop_v3
base.COLLECTOR_VERSION = COLLECTOR_VERSION


if __name__ == "__main__":
    raise SystemExit(base.main())
