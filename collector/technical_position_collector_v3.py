from __future__ import annotations

from dataclasses import replace

import technical_position_collector as base


COLLECTOR_VERSION = "technical-position-live-v3-session-fix"


def session_started_live(client, target_date: str) -> bool:
    """Confirm an active trading session from intraday data first.

    ka20006 daily rows can lag intraday. Using it as the only 09:00 session gate
    can incorrectly classify a normal trading day as closed. Prefer same-day
    60-minute bars and keep ka20006 only as a fallback.
    """
    for cfg in base.MARKETS:
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
    # Never give up on a weekday at 09:20. Keep probing until the live session
    # ends, so delayed Kiwoom session evidence cannot suppress the whole day.
    runtime = replace(runtime, session_confirm_until=runtime.live_end)
    return settings, runtime


base.session_started = session_started_live
base.load_runtime = load_runtime_keep_probing
base.COLLECTOR_VERSION = COLLECTOR_VERSION


if __name__ == "__main__":
    raise SystemExit(base.main())
