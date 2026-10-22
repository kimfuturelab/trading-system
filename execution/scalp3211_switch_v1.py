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
            "modules": _modules(doc),
            "user_trial_date": doc.get("user_trial_date")}


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
           "previous": previous["state"], "modules": previous["modules"],
           "user_trial_date": previous.get("user_trial_date")}
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
           "previous": previous["state"], "modules": modules,
           "user_trial_date": previous.get("user_trial_date")}
    return _write_control(doc, path)


def entry_session_open(at: datetime) -> bool:
    """Existing 3211 SOR order window: weekdays 08:00 <= KST < 20:00."""
    local=at.astimezone(KST)
    hm=local.hour*100+local.minute
    return local.weekday()<5 and 800<=hm<2000


def trial_authorized(today: str, path: Path = CONTROL) -> bool:
    control=read_entry_control(path)
    return (control.get("reason")=="CONFIGURED"
            and control.get("user_trial_date")==today)


def authorize_trial(today: str, path: Path = CONTROL) -> dict:
    if datetime.strptime(today,"%Y-%m-%d").date()!=datetime.now(KST).date():
        raise ValueError("USER_TRIAL_MUST_BE_TODAY")
    previous=read_entry_control(path)
    if previous.get("reason")!="CONFIGURED":
        raise ValueError("CONTROL_MUST_EXIST")
    doc={"account_suffix":"3211","symbol":"005930",
         "state":previous["state"],"modules":previous["modules"],
         "changed_at":datetime.now(KST).isoformat(timespec="seconds"),
         "previous":previous["state"],"user_trial_date":today}
    return _write_control(doc,path)


def arm_today_one_click(path: Path = CONTROL,
                        env_path: Path | None = None) -> dict:
    """User-driven SINGLE ON: verify 3211 and enable existing V0 daily guards.

    This function NEVER submits a broker order. The existing 1-minute live
    cycle remains the only BUY owner. OFF never interrupts protective SELL.
    """
    from scalp3211_user_trial_v1 import build_user_trial
    from safety import load_env_file,ExecutionConfig
    from scalp3211_capital_owner_v1 import capture_3211_capital_owner
    from quote_reader import read_quote_snapshot
    from broker_cli import KiwoomCliBroker
    from scalp3211_live_cycle_v1 import protective_watch_runtime_ready
    from v0_engine import open_order_rows
    from exit_plan import ExitPlanStore
    import sys,stat
    import sqlite3

    today=datetime.now(KST).date().isoformat()
    if not trial_authorized(today,path):
        raise ValueError("EXPLICIT_TODAY_3211_TRIAL_AUTHORIZATION_REQUIRED")
    env_file=env_path or Path.home()/"trading-execution.env"
    load_env_file(Path.home()/"api-read-v2.env",required=True)
    load_env_file(env_file,required=True)
    cfg=ExecutionConfig.from_env()
    if (cfg.auto_mode!="LIVE_SMALL" or
            not cfg.allowed_account.endswith("3211") or
            cfg.allowed_symbols!=frozenset({"005930"}) or
            cfg.max_order_qty!=1):
        raise ValueError("3211_ENV_SCOPE_NOT_LIVE_SMALL_1SH")
    sys.path.insert(0,str(Path.home()/"trading-system/collector"))
    from account_snapshot_readonly import collect
    owner=capture_3211_capital_owner(collect("test"))
    broker=KiwoomCliBroker(mode="real")
    snapshot=broker.read_snapshot()
    if open_order_rows(snapshot):
        raise ValueError("3211_BROKER_HAS_OPEN_ORDERS")
    quote=read_quote_snapshot(broker,symbol="005930",exchange="SOR")
    preview=build_user_trial(quote=quote,capital=owner,today=today,authorized=True)
    if not protective_watch_runtime_ready():
        raise ValueError("3211_PROTECTIVE_SELL_WATCHER_NOT_READY")
    if any(str(x.get("status") or "") not in ("CLOSED","CANCELLED")
           for x in ExitPlanStore(Path.home()/".trading-execution-3211-v1.sqlite3").list_plans()):
        raise ValueError("3211_EXISTING_ACTIVE_EXIT_PLAN")
    if not entry_session_open(datetime.now(KST)):
        raise ValueError("3211_OUTSIDE_USER_TRIAL_ENTRY_SESSION")
    raw=env_file.read_text(encoding="utf-8")
    keys={"KILL_SWITCH":"OFF",
          "ALLOW_LIVE_ORDERS":"YES",
          "DAILY_ARM_DATE":today}
    lines=raw.splitlines()
    seen=set()
    for i,line in enumerate(lines):
        if not line.strip().startswith("#") and "=" in line:
            key=line.split("=",1)[0].strip()
            if key in keys:
                lines[i]=key+"="+keys[key]
                seen.add(key)
    for k,v in keys.items():
        if k not in seen:lines.append(k+"="+v)
    prepared="\n".join(lines)+"\n"
    target=env_file.with_name(env_file.name+".tmp."+str(os.getpid()))
    try:
        with target.open("x",encoding="utf-8") as f:
            f.write(prepared)
            f.flush()
            os.fsync(f.fileno())
        target.chmod(stat.S_IMODE(env_file.stat().st_mode))
        os.replace(target,env_file)
    finally:
        target.unlink(missing_ok=True)
    # Leave protections intact if this last step fails: independent SELL and
    # broker write gates are governed by the existing V0/3211 account.
    changed=set_entry_state("ON",path)
    return {"status":"ARMED_3211_ONE_TIME",
            "trade_date":today,
            "account_suffix":"3211",
            "symbol":"005930",
            "qty":1,
            "preview_buy_price":preview["planned_entry_price"],
            "stop":preview["stop_price"],
            "take_profit":preview["target1_price"],
            "buy_switch":changed["state"],
            "orders_sent":False}


def main() -> None:
    parser = argparse.ArgumentParser(description="3211 ENTRY + per-module ON/OFF")
    parser.add_argument("action", choices=("status", "on", "off", "module", "authorize-trial"))
    parser.add_argument("module_name", nargs="?")
    parser.add_argument("module_state", nargs="?", choices=("on","off"))
    args = parser.parse_args()
    if args.action == "authorize-trial":
        if args.module_name or args.module_state:
            parser.error("authorize-trial takes no arguments")
        out=authorize_trial(datetime.now(KST).date().isoformat())
    elif args.action == "module":
        if not args.module_name or not args.module_state:
            parser.error("module requires NAME on|off")
        out = set_module_state(args.module_name, args.module_state)
    else:
        if args.module_name or args.module_state:
            parser.error("only module takes additional arguments")
        out = (read_entry_control() if args.action == "status"
               else arm_today_one_click() if args.action == "on"
               else set_entry_state(args.action))
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
