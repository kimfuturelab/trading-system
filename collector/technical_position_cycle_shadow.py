from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from box_control_sync import BoxControl, DEFAULT_CACHE_PATH, load_cache, save_cache
from technical_position_cycle import (
    CYCLE_ENGINE_VERSION,
    advance_cycle,
    event_to_dict,
    load_state,
    save_state,
)

SHADOW_VERSION = "technical-position-cycle-shadow-v1"
DEFAULT_STATE_PATH = Path.home() / ".cache" / "trading-system" / "box_cycle_kospi.json"
DEFAULT_EVENT_LOG_PATH = Path.home() / ".cache" / "trading-system" / "box_cycle_kospi_events.jsonl"


def _append_events(path: Path, events: list[Any]) -> None:
    if not events:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event_to_dict(event), ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def observe_live_row_shadow(
    row: dict[str, Any],
    *,
    cache_path: Path = DEFAULT_CACHE_PATH,
    state_path: Path = DEFAULT_STATE_PATH,
    event_log_path: Path = DEFAULT_EVENT_LOG_PATH,
) -> dict[str, Any]:
    """Observe one technical-position LIVE row without mutating the row.

    Shadow guarantees:
      * KOSPI only.
      * PREP/PENDING/ERROR rows are ignored.
      * Missing/invalid BOX control returns PENDING; no guessed BOX.
      * No Telegram, no Sheet write, no packet/ENTRY/order mutation.
      * State persists locally so service restarts do not double-count.
    """
    market = str(row.get("market") or "").strip().upper()
    source_status = str(row.get("status") or "").strip().upper()

    if market != "KOSPI":
        return {
            "shadow_status": "IGNORED",
            "reason": "NOT_KOSPI",
            "market": market,
            "shadow_version": SHADOW_VERSION,
        }

    if source_status != "OK":
        return {
            "shadow_status": "IGNORED",
            "reason": f"SOURCE_STATUS_{source_status or 'EMPTY'}",
            "market": market,
            "shadow_version": SHADOW_VERSION,
        }

    current_price = row.get("current_price")
    if current_price in (None, ""):
        return {
            "shadow_status": "PENDING",
            "reason": "CURRENT_PRICE_MISSING",
            "market": market,
            "shadow_version": SHADOW_VERSION,
        }

    try:
        control = load_cache(cache_path)
    except Exception as exc:
        return {
            "shadow_status": "PENDING",
            "reason": "BOX_CONTROL_CACHE_INVALID",
            "detail": str(exc),
            "market": market,
            "shadow_version": SHADOW_VERSION,
        }

    if control is None:
        return {
            "shadow_status": "PENDING",
            "reason": "BOX_CONTROL_CACHE_MISSING",
            "market": market,
            "shadow_version": SHADOW_VERSION,
        }

    if control.status != "READY":
        return {
            "shadow_status": "PENDING",
            "reason": f"BOX_CONTROL_STATUS_{control.status}",
            "market": market,
            "shadow_version": SHADOW_VERSION,
        }

    trade_date = str(row.get("trade_date") or "").strip()
    observed_at = str(row.get("snapshot_at") or row.get("captured_at") or "").strip()
    if not trade_date or not observed_at:
        return {
            "shadow_status": "PENDING",
            "reason": "ROW_TIME_MISSING",
            "market": market,
            "shadow_version": SHADOW_VERSION,
        }

    try:
        previous_state = load_state(state_path)
    except Exception as exc:
        return {
            "shadow_status": "PENDING",
            "reason": "CYCLE_STATE_INVALID",
            "detail": str(exc),
            "market": market,
            "shadow_version": SHADOW_VERSION,
        }

    state, events = advance_cycle(
        previous_state,
        market="KOSPI",
        struct_low=control.box_low,
        struct_high=control.box_high,
        current_price=current_price,
        observed_at=observed_at,
        trade_date=trade_date,
    )

    save_state(state_path, state)
    _append_events(event_log_path, events)

    return {
        "shadow_status": "OK",
        "market": "KOSPI",
        "current_price": float(current_price),
        "struct_box_low": state.struct_box_low,
        "struct_box_high": state.struct_box_high,
        "box_signature": state.box_signature,
        "cycle_count": state.cycle_count,
        "cycle_bucket": state.cycle_bucket,
        "cycle_box_low": state.cycle_box_low,
        "cycle_box_high": state.cycle_box_high,
        "low_zone_max": state.low_zone_max,
        "high_zone_min": state.high_zone_min,
        "anchor_side": state.anchor_side,
        "waiting_for_side": state.waiting_for_side,
        "cycle_status": state.cycle_status,
        "event_types": [event.event_type for event in events],
        "control_effective_date": control.effective_date,
        "cycle_engine_version": CYCLE_ENGINE_VERSION,
        "shadow_version": SHADOW_VERSION,
    }


def self_test() -> None:
    tmpdir = Path(tempfile.mkdtemp(prefix="cycle_shadow_test_"))
    cache = tmpdir / "box_control.json"
    state = tmpdir / "box_cycle.json"
    events = tmpdir / "events.jsonl"

    save_cache(
        cache,
        BoxControl(
            market="KOSPI",
            effective_date="2026-09-07",
            box_high=7216.0,
            box_low=6400.0,
            box_signature="7216|6400",
            source="TRADING_MASTER_00_수동입력",
            source_updated_at="2026-09-16 16:00:00",
            fetched_at="2026-09-16 16:01:00",
            status="READY",
        ),
    )

    def row(price: float, ts: str, status: str = "OK", market: str = "KOSPI") -> dict[str, Any]:
        return {
            "market": market,
            "status": status,
            "trade_date": "2026-09-16",
            "snapshot_at": ts,
            "current_price": price,
        }

    r0 = observe_live_row_shadow(
        row(6673.86, "2026-09-16 14:00:00"),
        cache_path=cache,
        state_path=state,
        event_log_path=events,
    )
    assert r0["shadow_status"] == "OK"
    assert r0["cycle_count"] == 0
    assert r0["anchor_side"] == "NONE"

    r1 = observe_live_row_shadow(
        row(6481.60, "2026-09-16 14:01:00"),
        cache_path=cache,
        state_path=state,
        event_log_path=events,
    )
    assert r1["cycle_count"] == 0
    assert r1["anchor_side"] == "LOW"

    r2 = observe_live_row_shadow(
        row(7134.40, "2026-09-16 14:02:00"),
        cache_path=cache,
        state_path=state,
        event_log_path=events,
    )
    assert r2["cycle_count"] == 1
    assert r2["anchor_side"] == "HIGH"

    ignored_prep = observe_live_row_shadow(
        row(7134.40, "2026-09-16 08:50:00", status="PREP"),
        cache_path=cache,
        state_path=state,
        event_log_path=events,
    )
    assert ignored_prep["shadow_status"] == "IGNORED"

    ignored_kosdaq = observe_live_row_shadow(
        row(3000.0, "2026-09-16 14:03:00", market="KOSDAQ"),
        cache_path=cache,
        state_path=state,
        event_log_path=events,
    )
    assert ignored_kosdaq["shadow_status"] == "IGNORED"

    print("CYCLE_SHADOW_ORCHESTRATION = PASS")
    print("KOSPI_ONLY = PASS")
    print("PREP_PENDING_IGNORE = PASS")
    print("LOCAL_STATE_PERSIST = PASS")
    print("ROW_MUTATION = NO")
    print("SHEET_WRITE = NO")
    print("TELEGRAM = NO")
    print("ORDER_EFFECT = NO")


if __name__ == "__main__":
    self_test()
