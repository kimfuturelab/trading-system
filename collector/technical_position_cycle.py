from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

CYCLE_ENGINE_VERSION = "box-cycle-python-v1"
EDGE_RATIO = 0.10


@dataclass(frozen=True)
class CycleState:
    market: str
    struct_box_low: float
    struct_box_high: float
    box_signature: str
    cycle_count: int
    cycle_bucket: str
    cycle_box_low: float
    cycle_box_high: float
    low_zone_max: float
    high_zone_min: float
    anchor_side: str
    waiting_for_side: str
    last_touch_at: str
    last_cycle_at: str
    cycle_status: str
    current_price: float | None
    updated_at: str
    source: str = CYCLE_ENGINE_VERSION
    note: str = ""


@dataclass(frozen=True)
class CycleEvent:
    event_at: str
    trade_date: str
    market: str
    box_signature: str
    event_type: str
    cycle_no: int
    from_side: str
    to_side: str
    observed_price: float
    prev_cycle_low: float
    prev_cycle_high: float
    next_cycle_low: float
    next_cycle_high: float
    source: str = CYCLE_ENGINE_VERSION
    note: str = ""


def _as_float(value: Any) -> float:
    n = float(value)
    if not (n == n):
        raise ValueError("NaN is not allowed")
    return n


def _signature(low: float, high: float) -> str:
    return f"{high:.12g}|{low:.12g}"


def _bucket(count: int) -> str:
    if count <= 1:
        return "NEW"
    if count == 2:
        return "MATURE"
    return "EXHAUSTED"


def _zones(low: float, high: float) -> tuple[float, float]:
    width = high - low
    if width <= 0:
        raise ValueError("cycle box high must be greater than low")
    return low + width * EDGE_RATIO, high - width * EDGE_RATIO


def _initial_state(
    *,
    market: str,
    struct_low: float,
    struct_high: float,
    observed_at: str,
    current_price: float | None,
) -> CycleState:
    low_zone_max, high_zone_min = _zones(struct_low, struct_high)
    return CycleState(
        market=market,
        struct_box_low=struct_low,
        struct_box_high=struct_high,
        box_signature=_signature(struct_low, struct_high),
        cycle_count=0,
        cycle_bucket=_bucket(0),
        cycle_box_low=struct_low,
        cycle_box_high=struct_high,
        low_zone_max=low_zone_max,
        high_zone_min=high_zone_min,
        anchor_side="NONE",
        waiting_for_side="FIRST_TOUCH",
        last_touch_at="",
        last_cycle_at="",
        cycle_status="WAIT_FIRST",
        current_price=current_price,
        updated_at=observed_at,
        note="struct_box_initialized",
    )


def state_to_dict(state: CycleState) -> dict[str, Any]:
    return asdict(state)


def state_from_dict(data: dict[str, Any]) -> CycleState:
    return CycleState(**data)


def event_to_dict(event: CycleEvent) -> dict[str, Any]:
    return asdict(event)


def load_state(path: Path) -> CycleState | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("cycle state file must contain a JSON object")
    return state_from_dict(data)


def save_state(path: Path, state: CycleState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(state_to_dict(state), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def advance_cycle(
    state: CycleState | None,
    *,
    market: str,
    struct_low: Any,
    struct_high: Any,
    current_price: Any,
    observed_at: str,
    trade_date: str,
) -> tuple[CycleState, list[CycleEvent]]:
    """Advance the BOX cycle state by one observed market price.

    Rules fixed by D-017/D-018/D-019/D-020:
      * STRUCT BOX is immutable machine input for this call.
      * current > STRUCT_HIGH => BROKEN_UP immediately.
      * current < STRUCT_LOW  => BROKEN_DOWN immediately.
      * equality is not a structural break.
      * first touch of either 10% edge zone only establishes the anchor.
      * touching the opposite 10% edge zone increments cycle_count by one.
      * after each completed traversal, CYCLE BOX contracts to its inner 80%.
      * repeated observations on the same side do not increment.
      * changing STRUCT BOX signature resets the cycle to zero.
    """
    low = _as_float(struct_low)
    high = _as_float(struct_high)
    price = _as_float(current_price)

    if high <= low:
        raise ValueError("struct_high must be greater than struct_low")

    sig = _signature(low, high)
    events: list[CycleEvent] = []

    if state is None or state.market != market or state.box_signature != sig:
        prev_low = state.cycle_box_low if state is not None else low
        prev_high = state.cycle_box_high if state is not None else high
        state = _initial_state(
            market=market,
            struct_low=low,
            struct_high=high,
            observed_at=observed_at,
            current_price=price,
        )
        events.append(
            CycleEvent(
                event_at=observed_at,
                trade_date=trade_date,
                market=market,
                box_signature=sig,
                event_type="STRUCT_RESET",
                cycle_no=0,
                from_side="",
                to_side="",
                observed_price=price,
                prev_cycle_low=prev_low,
                prev_cycle_high=prev_high,
                next_cycle_low=low,
                next_cycle_high=high,
                note="new_struct_box_signature",
            )
        )

    state = replace(
        state,
        struct_box_low=low,
        struct_box_high=high,
        box_signature=sig,
        current_price=price,
        updated_at=observed_at,
    )

    if price > high or price < low:
        direction = "UP" if price > high else "DOWN"
        broken_status = f"BROKEN_{direction}"
        if state.cycle_status != broken_status:
            events.append(
                CycleEvent(
                    event_at=observed_at,
                    trade_date=trade_date,
                    market=market,
                    box_signature=sig,
                    event_type="STRUCT_BREAK",
                    cycle_no=state.cycle_count,
                    from_side=state.anchor_side,
                    to_side=direction,
                    observed_price=price,
                    prev_cycle_low=state.cycle_box_low,
                    prev_cycle_high=state.cycle_box_high,
                    next_cycle_low=state.cycle_box_low,
                    next_cycle_high=state.cycle_box_high,
                    note="D-017 immediate numeric break",
                )
            )
        return (
            replace(
                state,
                cycle_status=broken_status,
                waiting_for_side="NONE",
                note="struct_box_broken_cycle_frozen",
            ),
            events,
        )

    if state.cycle_status in {"BROKEN_UP", "BROKEN_DOWN"}:
        return state, events

    in_low_zone = price <= state.low_zone_max
    in_high_zone = price >= state.high_zone_min

    if state.anchor_side == "NONE":
        side = "LOW" if in_low_zone else ("HIGH" if in_high_zone else "")
        if not side:
            return replace(state, cycle_status="WAIT_FIRST", note="waiting_first_edge_touch"), events

        waiting = "HIGH" if side == "LOW" else "LOW"
        state = replace(
            state,
            anchor_side=side,
            waiting_for_side=waiting,
            last_touch_at=observed_at,
            cycle_status="WAIT_OPPOSITE",
            note=f"first_touch_{side.lower()}",
        )
        events.append(
            CycleEvent(
                event_at=observed_at,
                trade_date=trade_date,
                market=market,
                box_signature=sig,
                event_type="FIRST_TOUCH",
                cycle_no=state.cycle_count,
                from_side="NONE",
                to_side=side,
                observed_price=price,
                prev_cycle_low=state.cycle_box_low,
                prev_cycle_high=state.cycle_box_high,
                next_cycle_low=state.cycle_box_low,
                next_cycle_high=state.cycle_box_high,
            )
        )
        return state, events

    target = state.waiting_for_side
    reached_target = (target == "LOW" and in_low_zone) or (
        target == "HIGH" and in_high_zone
    )

    if not reached_target:
        return replace(state, cycle_status="WAIT_OPPOSITE", note=f"waiting_{target.lower()}"), events

    prev_low = state.cycle_box_low
    prev_high = state.cycle_box_high
    width = prev_high - prev_low
    next_low = prev_low + width * EDGE_RATIO
    next_high = prev_high - width * EDGE_RATIO
    next_low_zone_max, next_high_zone_min = _zones(next_low, next_high)
    new_count = state.cycle_count + 1
    from_side = state.anchor_side
    to_side = target
    next_wait = "HIGH" if to_side == "LOW" else "LOW"

    state = replace(
        state,
        cycle_count=new_count,
        cycle_bucket=_bucket(new_count),
        cycle_box_low=next_low,
        cycle_box_high=next_high,
        low_zone_max=next_low_zone_max,
        high_zone_min=next_high_zone_min,
        anchor_side=to_side,
        waiting_for_side=next_wait,
        last_touch_at=observed_at,
        last_cycle_at=observed_at,
        cycle_status="WAIT_OPPOSITE",
        note=f"cycle_complete_{new_count}",
    )

    events.append(
        CycleEvent(
            event_at=observed_at,
            trade_date=trade_date,
            market=market,
            box_signature=sig,
            event_type="CYCLE_COMPLETE",
            cycle_no=new_count,
            from_side=from_side,
            to_side=to_side,
            observed_price=price,
            prev_cycle_low=prev_low,
            prev_cycle_high=prev_high,
            next_cycle_low=next_low,
            next_cycle_high=next_high,
            note=f"bucket={state.cycle_bucket}",
        )
    )
    return state, events
