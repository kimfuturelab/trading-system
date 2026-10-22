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
MODULES = ("stage5","stage0","stage1","stage2","material","supply",
           "chart","entry","stage4")
CAPITAL_WARN_ONLY_CEILING_KRW = 100_000_000


def _modules(doc: dict) -> dict:
    saved = doc.get("modules", {})
    if not isinstance(saved, dict) or any(
        k not in MODULES or v not in VALID_STATES for k, v in saved.items()
    ):
        return {k: "ON" for k in MODULES}
    return {k: saved.get(k, "ON") for k in MODULES}


def module_modes_for_capital(capital: dict, path: Path = CONTROL) -> dict:
    """Only the authenticated 3211 <=1억원 account uses WARN-ONLY overrides."""
    amount=capital.get("estimated_assets_krw")
    if type(amount) is not int or not (0 < amount <= CAPITAL_WARN_ONLY_CEILING_KRW):
        return {name: "ON" for name in MODULES}
    return read_entry_control(path)["modules"]


def module_enabled(name: str, path: Path = CONTROL) -> bool:
    if name not in MODULES:
        raise ValueError("UNKNOWN_3211_MODULE")
    return read_entry_control(path)["modules"][name] == "ON"



def read_entry_control(path: Path = CONTROL) -> dict:
    if not path.is_file():
        return {"state": "OFF", "reason": "NOT_INITIALIZED",
                "modules": {k:"ON" for k in MODULES}}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "OFF", "reason": "CONTROL_FILE_INVALID",
                "modules": {k:"ON" for k in MODULES}}
    if (doc.get("account_suffix") != "3211"
            or doc.get("symbol") != "005930"
            or doc.get("state") not in VALID_STATES):
        return {"state": "OFF", "reason": "CONTROL_SCOPE_INVALID",
                "modules": {k:"ON" for k in MODULES}}
    return {"state": doc["state"], "reason": "CONFIGURED",
            "changed_at": doc.get("changed_at"),
            "modules": _modules(doc)}


def entry_switch_on(path: Path = CONTROL) -> bool:
    return read_entry_control(path).get("state") == "ON"


def _write_control(doc: dict, path: Path) -> dict:
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


def set_entry_state(value: str, path: Path = CONTROL) -> dict:
    state = str(value or "").upper()
    if state not in VALID_STATES:
        raise ValueError("ENTRY_STATE_MUST_BE_ON_OR_OFF")
    previous = read_entry_control(path)
    doc = {"account_suffix": "3211", "symbol": "005930",
           "state": state, "changed_at": datetime.now(KST).isoformat(timespec="seconds"),
           "previous": previous["state"], "modules": previous["modules"]}
    return _write_control(doc, path)


def set_module_state(name: str, value: str, path: Path = CONTROL) -> dict:
    if name not in MODULES:
        raise ValueError("UNKNOWN_3211_MODULE")
    state = str(value or "").upper()
    if state not in VALID_STATES:
        raise ValueError("MODULE_STATE_MUST_BE_ON_OR_OFF")
    previous = read_entry_control(path)
    modules = dict(previous["modules"])
    modules[name] = state
    doc = {"account_suffix": "3211", "symbol": "005930",
           "state": previous["state"], "changed_at": datetime.now(KST).isoformat(timespec="seconds"),
           "previous": previous["state"], "modules": modules}
    return _write_control(doc, path)


def main() -> None:
    parser = argparse.ArgumentParser(description="3211 ENTRY + per-module ON/OFF")
    parser.add_argument("action", choices=("status", "on", "off", "module"))
    parser.add_argument("module_name", nargs="?")
    parser.add_argument("module_state", nargs="?", choices=("on","off"))
    args = parser.parse_args()
    if args.action == "module":
        if not args.module_name or not args.module_state:
            parser.error("module requires NAME on|off")
        out = set_module_state(args.module_name, args.module_state)
    else:
        if args.module_name or args.module_state:
            parser.error("only module takes additional arguments")
        out = (read_entry_control() if args.action == "status"
               else set_entry_state(args.action))
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
