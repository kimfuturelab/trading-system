"""Read the existing, authoritative completed 5-minute bars for 3211 DAY horizon.

No extra bar collector and no exchange orders. This is the identical bar
counting convention used by position_structure_action_v2.py.
"""
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import sqlite3

KST = ZoneInfo("Asia/Seoul")
BAR_DB = Path.home() / "trading-system-stage3/runtime/stock_bar_raw_v1.sqlite3"


def completed_bars_since(first_fill_at: str, *, db: Path = BAR_DB) -> int:
    executed = datetime.fromisoformat(first_fill_at.replace("Z", "+00:00"))
    if executed.tzinfo is None or executed.utcoffset() is None:
        raise ValueError("FIRST_FILL_TIME_MUST_BE_BROKER_VERIFIED_AND_AWARE")
    cutoff = executed.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")
    if not db.is_file():
        raise RuntimeError("AUTHORITATIVE_BAR_DB_MISSING")
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5) as con:
        row = con.execute(
            "SELECT COUNT(*) FROM stock_bars "
            "WHERE symbol=? AND timeframe=? AND is_complete=1 AND bar_time>=?",
            ("005930", "5m", cutoff),
        ).fetchone()
    if row is None:
        raise RuntimeError("BAR_HORIZON_READ_FAILED")
    return int(row[0] or 0)


def completed_24_bar_timecut(first_fill_at: str, *, db: Path = BAR_DB) -> bool:
    return completed_bars_since(first_fill_at, db=db) >= 24
