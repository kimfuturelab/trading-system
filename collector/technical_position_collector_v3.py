from __future__ import annotations

from dataclasses import replace

import technical_position_collector as base


COLLECTOR_VERSION = "technical-position-live-v3-session-fix2"


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
    """Confirm an active trading session without depending on ka20006 alone.

    Priority:
      1) ka20001 current/open/high/low are all live and non-zero
      2) same-day native 60-minute bar exists
      3) same-day daily row exists (fallback / after close)

    This prevents a normal trading day from being dropped because the daily-bar
    endpoint has not exposed today's row yet.
    """
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


_original_load_runtime = base.load_runtime


def load_runtime_keep_probing():
    settings, runtime = _original_load_runtime()
    # Never give up at 09:20. Keep probing through the live session so delayed
    # exchange/API evidence cannot suppress the whole trading day.
    runtime = replace(runtime, session_confirm_until=runtime.live_end)
    return settings, runtime


base.session_started = session_started_live
base.load_runtime = load_runtime_keep_probing
base.COLLECTOR_VERSION = COLLECTOR_VERSION


if __name__ == "__main__":
    raise SystemExit(base.main())
