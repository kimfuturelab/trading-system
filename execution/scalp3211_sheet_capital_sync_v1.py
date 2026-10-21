"""Synchronize ONLY authenticated Kiwoom 3211 capital and entry switch to the
dedicated 3211 Stage4 sheet. Never modifies DAY/SWING cells or trade signals.

The sheet contains a finance preview, NOT broker order approval authority.
On broker failure the financial cells are cleared (fail-closed).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from scalp3211_capital_owner_v1 import capture_3211_capital_owner
from scalp3211_switch_v1 import read_entry_control

HOME = Path.home()
KST = ZoneInfo("Asia/Seoul")
STAGE3 = HOME / "trading-system-stage3"
SHEET = "3211_독립계산"
ACCOUNT = "test"
SYMBOL = "005930"


def _sheets_serial(moment: datetime) -> float:
    # Google Sheets numeric time serial. Spreadsheet timezone is Asia/Seoul.
    dt = moment.astimezone(KST).replace(tzinfo=None)
    base = datetime(1899, 12, 30)
    return (dt - base).total_seconds() / 86400


def capital_cells(owner: dict, switch: dict, now: datetime) -> dict:
    """Pure, fail-closed mapping from the original broker capital owner."""
    good = (
        owner.get("status") == "BROKER_CAPITAL_VERIFIED_ONLY"
        and not owner.get("reason")
        and owner.get("owner") == "SCALP3211_ISOLATED_BROKER_CAPITAL_V1"
        and owner.get("account_alias") == ACCOUNT
        and owner.get("account_suffix") == "3211"
        and owner.get("symbol") == SYMBOL
        and owner.get("max_order_qty") == 1
        and type(owner.get("estimated_assets_krw")) is int
        and type(owner.get("orderable_cash_krw")) is int
        and owner["estimated_assets_krw"] > 0
        and 0 <= owner["orderable_cash_krw"] <= owner["estimated_assets_krw"]
        and type(owner.get("risk_reference_krw_1p5pct")) is int
        and owner["risk_reference_krw_1p5pct"]
            == owner["estimated_assets_krw"] * 15 // 1000
    )
    entry_state = "ON" if switch.get("state") == "ON" else "OFF"
    return {
        "capital": int(owner["estimated_assets_krw"]) if good else "",
        "cash": int(owner["orderable_cash_krw"]) if good else "",
        "snapshot_serial": _sheets_serial(now) if good else "",
        "state": "BROKER_CAPITAL_VERIFIED_ONLY" if good else "BROKER_CAPITAL_BLOCK",
        "entry_switch": entry_state,
        "safe_to_trade": False,
    }


def sync_once() -> dict:
    for folder in (HOME / "trading-system/collector",
                   STAGE3 / "collector"):
        if str(folder) not in sys.path:
            sys.path.insert(0, str(folder))
    from account_snapshot_readonly import collect
    from stage4_p047_handoff import session, get_values, update_values, STAGE4_ID

    at = datetime.now(KST)
    switch = read_entry_control()
    try:
        owner = capture_3211_capital_owner(collect(ACCOUNT))
        receipt = capital_cells(owner, switch, at)
    except Exception as exc:
        # Do not write a plausible stale 'verified' snapshot on API failure.
        receipt = {"capital":"","cash":"","snapshot_serial":"",
                   "state":"BROKER_CAPITAL_BLOCK",
                   "entry_switch":"ON" if switch.get("state") == "ON" else "OFF",
                   "safe_to_trade":False}
    s = session()
    # Confirm account identifier and symbol in the isolated tab before writes.
    scope = get_values(s, STAGE4_ID, SHEET, "B4:B5")
    if len(scope) < 2 or scope[0][0] != "3211" or scope[1][0] != SYMBOL:
        raise RuntimeError("3211_DEDICATED_SHEET_SCOPE_MISMATCH")
    updates = [
        {"sheet":SHEET,"range":"B6:B7",
         "values":[[receipt["capital"]],[receipt["cash"]]]},
        {"sheet":SHEET,"range":"B9",
         "values":[[receipt["snapshot_serial"]]]},
        {"sheet":SHEET,"range":"B26",
         "values":[[receipt["entry_switch"]]]},
    ]
    update_values(s, STAGE4_ID, updates)
    # B10 is a Sheets formula that auto-expires stale receipts.
    return {
        "status":"CAPITAL_SYNCED" if receipt["state"] ==
                 "BROKER_CAPITAL_VERIFIED_ONLY" else "CAPITAL_BLOCKED",
        "account":"3211","symbol":SYMBOL,
        "capital":receipt["capital"],"cash":receipt["cash"],
        "switch":receipt["entry_switch"],"orders_sent":False,
    }


if __name__ == "__main__":
    try:
        print(json.dumps(sync_once(),ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"status":"ERROR_FAIL_CLOSED",
                          "type":type(exc).__name__,
                          "orders_sent":False},ensure_ascii=False))
        raise SystemExit(2)
