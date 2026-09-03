from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo
from typing import Any

import requests
from dotenv import load_dotenv
from websockets.sync.client import connect

from kiwoom_collector import KiwoomClient, Settings

VERSION = "stage3-stock-supply-v2"
KST = ZoneInfo("Asia/Seoul")
WS_URL = "wss://api.kiwoom.com:10000/api/dostk/websocket"
STOCKS = (
    {"code": "005930", "name": "삼성전자"},
    {"code": "000660", "name": "SK하이닉스"},
)


def num(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    if text.startswith("--"):
        text = "-" + text.lstrip("-")
    elif text.startswith("++"):
        text = "+" + text.lstrip("+")
    try:
        return float(text)
    except ValueError:
        return None


def now() -> datetime:
    return datetime.now(KST)


def now_text() -> str:
    return now().strftime("%Y-%m-%d %H:%M:%S")


def today_api() -> str:
    return now().strftime("%Y%m%d")


def today_text() -> str:
    return now().strftime("%Y-%m-%d")


def hhmmss(value: Any) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) >= 6:
        digits = digits[-6:]
        return f"{digits[0:2]}:{digits[2:4]}:{digits[4:6]}"
    return str(value or "")


def post_api(client: KiwoomClient, api_id: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    token = client.issue_token()
    response = client.session.post(
        f"{client.s.base_url}{path}",
        headers={"authorization": f"Bearer {token}", "api-id": api_id},
        json=body,
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    if int(data.get("return_code", -1)) != 0:
        raise RuntimeError(f"{api_id} error: {data}")
    return data


def provisional(client: KiwoomClient, code: str) -> dict[str, Any]:
    data = post_api(
        client,
        "ka10064",
        "/api/dostk/chart",
        {"mrkt_tp": "000", "amt_qty_tp": "1", "trde_tp": "0", "stk_cd": code},
    )
    rows = data.get("opmr_invsr_trde_chart") or []
    if not rows:
        raise RuntimeError(f"ka10064 no rows: {code}")
    row = max(rows, key=lambda x: str(x.get("tm", "000000")))
    return {
        "time": hhmmss(row.get("tm")),
        "foreign": num(row.get("frgnr_invsr")),
        "institution": num(row.get("orgn")),
    }


def program_rest(client: KiwoomClient, code: str) -> dict[str, Any]:
    data = post_api(
        client,
        "ka90008",
        "/api/dostk/mrkcond",
        {"amt_qty_tp": "1", "stk_cd": code, "date": today_api()},
    )
    rows = data.get("stk_tm_prm_trde_trnsn") or []
    if not rows:
        raise RuntimeError(f"ka90008 no rows: {code}")
    row = max(rows, key=lambda x: str(x.get("tm", "000000")))
    return {
        "time": hhmmss(row.get("tm")),
        "amount": num(row.get("prm_netprps_amt")),
        "quantity": num(row.get("prm_netprps_qty")),
    }


def final_supply(client: KiwoomClient, code: str) -> dict[str, Any]:
    date_key = today_api()
    data = post_api(
        client,
        "ka10060",
        "/api/dostk/chart",
        {"dt": date_key, "stk_cd": code, "amt_qty_tp": "1", "trde_tp": "0", "unit_tp": "1"},
    )
    rows = data.get("stk_invsr_orgn_chart") or []
    if not rows:
        raise RuntimeError(f"ka10060 no rows: {code}")
    row = next((x for x in rows if str(x.get("dt", "")) == date_key), rows[0])
    return {
        "time": now().strftime("%H:%M:%S"),
        "foreign": num(row.get("frgnr_invsr")),
        "institution": num(row.get("orgn")),
        "financial_investment": num(row.get("fnnc_invt")),
        "insurance": num(row.get("insrnc")),
        "investment_trust": num(row.get("invtrt")),
        "bank": num(row.get("bank")),
        "pension": num(row.get("penfnd_etc")),
        "private_fund": num(row.get("samo_fund")),
        "state": num(row.get("natn")),
        "other_corp": num(row.get("etc_corp")),
    }


def empty_state() -> dict[str, dict[str, Any]]:
    return {
        s["code"]: {
            "code": s["code"], "name": s["name"],
            "provisional_time": "", "provisional_foreign": None, "provisional_institution": None,
            "program_time": "", "program_amount": None, "program_quantity": None, "program_received": "",
            "final_time": "", "final_foreign": None, "final_institution": None,
            "error": "",
        }
        for s in STOCKS
    }


def mood(foreign: float | None, institution: float | None) -> str:
    if foreign is None or institution is None:
        return "잠정대기"
    if foreign > 0 and institution > 0:
        return "동반매수"
    if foreign < 0 and institution < 0:
        return "동반매도"
    return "혼조"


def age_seconds(text: str) -> float | None:
    if not text:
        return None
    try:
        parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
        return (now() - parsed).total_seconds()
    except ValueError:
        return None


def rows_from_state(state: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for stock in STOCKS:
        r = state[stock["code"]]
        age = age_seconds(r["program_received"])
        if r["program_amount"] is None or r["program_quantity"] is None:
            status = "PROGRAM_PENDING"
        elif age is None or age > 30:
            status = "PROGRAM_STALE"
        elif r["provisional_foreign"] is None or r["provisional_institution"] is None:
            status = "LIVE / 잠정대기"
        else:
            status = "LIVE"
        if r["final_time"]:
            status = "FINAL"
        result.append({
            "date": today_text(),
            "stock_code": r["code"], "stock_name": r["name"],
            "provisional_time": r["provisional_time"],
            "provisional_foreign": r["provisional_foreign"],
            "provisional_institution": r["provisional_institution"],
            "mood": mood(r["provisional_foreign"], r["provisional_institution"]),
            "program_time": r["program_time"],
            "program_amount": r["program_amount"], "program_quantity": r["program_quantity"],
            "final_time": r["final_time"], "final_foreign": r["final_foreign"],
            "final_institution": r["final_institution"], "status": status, "error": r["error"],
        })
    return result


def send(settings: Settings, payload_type: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    payload = {
        "secret": settings.ingest_secret,
        "type": payload_type,
        "source": "kiwoom-stage3-stock-v2",
        "collector_version": VERSION,
        "captured_at": now_text(),
        "rows": rows,
    }
    response = requests.post(settings.webhook_url, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"webhook {payload_type} failed: {data}")
    return data


def refresh_rest(client: KiwoomClient, state: dict[str, dict[str, Any]], include_program: bool = False) -> None:
    for stock in STOCKS:
        r = state[stock["code"]]
        try:
            p = provisional(client, stock["code"])
            r["provisional_time"] = p["time"]
            r["provisional_foreign"] = p["foreign"]
            r["provisional_institution"] = p["institution"]
            r["error"] = ""
        except Exception as exc:
            r["error"] = f"ka10064:{exc}"
        if include_program:
            try:
                p = program_rest(client, stock["code"])
                r["program_time"] = p["time"]
                r["program_amount"] = p["amount"]
                r["program_quantity"] = p["quantity"]
                r["program_received"] = now_text()
            except Exception as exc:
                r["error"] = (r["error"] + " | " if r["error"] else "") + f"ka90008:{exc}"
        time.sleep(0.2)


def refresh_final(client: KiwoomClient, state: dict[str, dict[str, Any]]) -> bool:
    complete = True
    for stock in STOCKS:
        r = state[stock["code"]]
        try:
            f = final_supply(client, stock["code"])
            r["final_time"] = f["time"]
            r["final_foreign"] = f["foreign"]
            r["final_institution"] = f["institution"]
            r["error"] = ""
        except Exception as exc:
            complete = False
            r["error"] = f"ka10060:{exc}"
        time.sleep(0.2)
    return complete


def parse_hhmm(value: str, default: str) -> dt_time:
    hh, mm = (value or default).strip().split(":", 1)
    return dt_time(int(hh), int(mm))


def websocket_loop(client: KiwoomClient, state: dict[str, dict[str, Any]], lock: threading.Lock, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            token = client.issue_token()
            with connect(WS_URL, open_timeout=20, ping_interval=None, close_timeout=10) as ws:
                ws.send(json.dumps({"trnm": "LOGIN", "token": token}, ensure_ascii=False))
                login = json.loads(ws.recv(timeout=20))
                if int(login.get("return_code", -1)) != 0:
                    raise RuntimeError(f"LOGIN:{login}")
                ws.send(json.dumps({
                    "trnm": "REG", "grp_no": "31", "refresh": "1",
                    "data": [{"item": [s["code"] for s in STOCKS], "type": ["0w"]}],
                }, ensure_ascii=False))
                while not stop_event.is_set():
                    try:
                        raw = ws.recv(timeout=5)
                    except TimeoutError:
                        continue
                    msg = json.loads(raw)
                    if str(msg.get("trnm", "")).upper() == "PING":
                        ws.send(json.dumps(msg, ensure_ascii=False))
                        continue
                    if str(msg.get("trnm", "")).upper() != "REAL":
                        continue
                    for item in msg.get("data") or []:
                        if str(item.get("type", "")) != "0w":
                            continue
                        code = str(item.get("item", "")).split("_", 1)[0]
                        if code not in state:
                            continue
                        values = item.get("values") or {}
                        with lock:
                            r = state[code]
                            r["program_time"] = hhmmss(values.get("20"))
                            r["program_quantity"] = num(values.get("210"))
                            r["program_amount"] = num(values.get("212"))
                            r["program_received"] = now_text()
                            r["error"] = ""
        except Exception as exc:
            with lock:
                for r in state.values():
                    r["error"] = f"0w:{exc}"
            if not stop_event.wait(3):
                continue


def periodic_loop(client: KiwoomClient, settings: Settings, state: dict[str, dict[str, Any]], lock: threading.Lock, stop_event: threading.Event, final_start: dt_time) -> None:
    last_provisional = 0.0
    last_live = 0.0
    last_minute = ""
    final_sent = False
    while not stop_event.is_set():
        current = now()
        clock = time.monotonic()
        if clock - last_provisional >= 60:
            with lock:
                refresh_rest(client, state, include_program=False)
            last_provisional = clock
        if clock - last_live >= 10:
            with lock:
                payload = rows_from_state(state)
            try:
                send(settings, "stock_supply_live", payload)
            except Exception as exc:
                print(f"LIVE PUSH ERROR: {exc}", file=sys.stderr)
            last_live = clock
        minute_key = current.strftime("%Y-%m-%d %H:%M")
        if minute_key != last_minute:
            with lock:
                payload = rows_from_state(state)
            if any(r.get("program_amount") is not None for r in payload):
                try:
                    send(settings, "stock_supply_minute", payload)
                    last_minute = minute_key
                except Exception as exc:
                    print(f"MINUTE PUSH ERROR: {exc}", file=sys.stderr)
        if current.time() >= final_start and not final_sent:
            with lock:
                complete = refresh_final(client, state)
                payload = rows_from_state(state)
            if complete:
                try:
                    send(settings, "stock_supply_final", payload)
                    final_sent = True
                except Exception as exc:
                    print(f"FINAL PUSH ERROR: {exc}", file=sys.stderr)
        stop_event.wait(1)


def probe(settings: Settings, push: bool = False) -> int:
    client = KiwoomClient(settings)
    state = empty_state()
    refresh_rest(client, state, include_program=True)
    data = rows_from_state(state)
    print(json.dumps(data, ensure_ascii=False, indent=2))
    if push:
        print(json.dumps(send(settings, "stock_supply_live", data), ensure_ascii=False))
    return 0


def run_service(settings: Settings) -> int:
    start = parse_hhmm(os.getenv("STOCK_SUPPLY_ACTIVE_START", "08:55"), "08:55")
    end = parse_hhmm(os.getenv("STOCK_SUPPLY_ACTIVE_END", "15:40"), "15:40")
    final_start = parse_hhmm(os.getenv("STOCK_SUPPLY_FINAL_START", "15:31"), "15:31")
    while True:
        current = now().time()
        if not (start <= current <= end):
            time.sleep(15)
            continue
        client = KiwoomClient(settings)
        state = empty_state()
        lock = threading.RLock()
        stop_event = threading.Event()
        with lock:
            refresh_rest(client, state, include_program=True)
        ws_thread = threading.Thread(target=websocket_loop, args=(client, state, lock, stop_event), daemon=True)
        periodic_thread = threading.Thread(target=periodic_loop, args=(client, settings, state, lock, stop_event, final_start), daemon=True)
        ws_thread.start()
        periodic_thread.start()
        try:
            while now().time() <= end:
                time.sleep(5)
        except KeyboardInterrupt:
            stop_event.set()
            return 0
        stop_event.set()
        ws_thread.join(timeout=10)
        periodic_thread.join(timeout=10)
        time.sleep(15)


def main() -> int:
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    try:
        settings = Settings.from_env()
    except Exception as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2
    if "--probe" in sys.argv:
        return probe(settings, push="--push" in sys.argv)
    return run_service(settings)


if __name__ == "__main__":
    raise SystemExit(main())
