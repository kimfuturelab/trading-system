"""Standalone 3211 BUY ON/OFF control, completely separate from GOLD signals.

OFF pauses NEW BUY only. Existing Kiwoom executions must still reconcile and
the protective V1 SELL watcher must continue uninterrupted.

This is an *entry enable*; it cannot override the existing V0 daily arm,
kill switch, Kiwoom cash limits, GOLD approval or protective exit readiness.
"""
from __future__ import annotations
import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
CONTROL = Path.home() / "trading-system/runtime/scalp3211_entry_control.json"
VALID_STATES = frozenset({"ON", "OFF"})


def read_entry_control(path: Path = CONTROL) -> dict:
    if not path.is_file():
        return {"state": "OFF", "reason": "NOT_INITIALIZED"}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "OFF", "reason": "CONTROL_FILE_INVALID"}
    if (doc.get("account_suffix") != "3211"
            or doc.get("symbol") != "005930"
            or doc.get("state") not in VALID_STATES):
        return {"state": "OFF", "reason": "CONTROL_SCOPE_INVALID"}
    return {"state": doc["state"], "reason": "CONFIGURED",
            "changed_at": doc.get("changed_at")}


def entry_switch_on(path: Path = CONTROL) -> bool:
    return read_entry_control(path).get("state") == "ON"


def set_entry_state(value: str, path: Path = CONTROL) -> dict:
    state = str(value or "").upper()
    if state not in VALID_STATES:
        raise ValueError("ENTRY_STATE_MUST_BE_ON_OR_OFF")
    previous = read_entry_control(path)
    doc = {"account_suffix": "3211", "symbol": "005930",
           "state": state, "changed_at": datetime.now(KST).isoformat(timespec="seconds"),
           "previous": previous["state"]}
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp." + str(os.getpid()))
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(doc, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return doc


def main() -> None:
    parser = argparse.ArgumentParser(description="3211 entry-only control; SELL always independent")
    parser.add_argument("action", choices=("status", "on", "off"))
    args = parser.parse_args()
    out = read_entry_control() if args.action == "status" else set_entry_state(args.action)
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
