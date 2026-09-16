from __future__ import annotations

import tempfile
from pathlib import Path

from technical_position_cycle import advance_cycle, load_state, save_state


def step(state, price, low=6400.0, high=7216.0, t="2026-09-16 14:00:00"):
    return advance_cycle(
        state,
        market="KOSPI",
        struct_low=low,
        struct_high=high,
        current_price=price,
        observed_at=t,
        trade_date="2026-09-16",
    )


state = None

state, events = step(state, 6673.86)
assert state.cycle_count == 0
assert state.anchor_side == "NONE"
assert state.waiting_for_side == "FIRST_TOUCH"
assert [e.event_type for e in events] == ["STRUCT_RESET"]

state, events = step(state, 6481.60, t="2026-09-16 14:01:00")
assert state.cycle_count == 0
assert state.anchor_side == "LOW"
assert state.waiting_for_side == "HIGH"
assert [e.event_type for e in events] == ["FIRST_TOUCH"]

state, events = step(state, 6470.00, t="2026-09-16 14:02:00")
assert state.cycle_count == 0
assert events == []

state, events = step(state, 7134.40, t="2026-09-16 14:03:00")
assert state.cycle_count == 1
assert state.anchor_side == "HIGH"
assert state.waiting_for_side == "LOW"
assert round(state.cycle_box_low, 6) == 6481.6
assert round(state.cycle_box_high, 6) == 7134.4
assert [e.event_type for e in events] == ["CYCLE_COMPLETE"]

tmp = Path(tempfile.mkdtemp()) / "cycle_state.json"
save_state(tmp, state)
state = load_state(tmp)
state, events = step(state, 7135.00, t="2026-09-16 14:04:00")
assert state.cycle_count == 1
assert events == []

state, events = step(state, 6546.88, t="2026-09-16 14:05:00")
assert state.cycle_count == 2
assert state.cycle_bucket == "MATURE"
assert round(state.cycle_box_low, 6) == 6546.88
assert round(state.cycle_box_high, 6) == 7069.12

state, events = step(state, 7016.896, t="2026-09-16 14:06:00")
assert state.cycle_count == 3
assert state.cycle_bucket == "EXHAUSTED"
assert round(state.cycle_box_low, 6) == 6599.104
assert round(state.cycle_box_high, 6) == 7016.896

state, events = step(state, 7217.00, t="2026-09-16 14:07:00")
assert state.cycle_count == 3
assert state.cycle_status == "BROKEN_UP"
assert [e.event_type for e in events] == ["STRUCT_BREAK"]

state, events = step(state, 7000.00, t="2026-09-16 14:08:00")
assert state.cycle_count == 3
assert state.cycle_status == "BROKEN_UP"
assert events == []

state, events = step(
    state,
    6900.00,
    low=6500.0,
    high=7300.0,
    t="2026-09-16 14:09:00",
)
assert state.cycle_count == 0
assert state.cycle_status == "WAIT_FIRST"
assert state.anchor_side == "NONE"
assert [e.event_type for e in events] == ["STRUCT_RESET"]

print("BOX_CYCLE_PYTHON_PURE_E2E = PASS")
print("RESTART_PERSISTENCE = PASS")
print("SAME_SIDE_DEDUPE = PASS")
print("CYCLE_0_1_2_3 = PASS")
print("STRUCT_BREAK_PRIORITY = PASS")
print("NEW_BOX_RESET = PASS")
