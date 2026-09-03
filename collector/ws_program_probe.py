from __future__ import annotations

import json
import time

from websockets.sync.client import connect

from kiwoom_collector import KiwoomClient, Settings

WS_URL = "wss://api.kiwoom.com:10000/api/dostk/websocket"
STOCKS = {"005930": "삼성전자", "000660": "SK하이닉스"}


def number(value):
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text.startswith("--"):
        text = "-" + text.lstrip("-")
    elif text.startswith("++"):
        text = "+" + text.lstrip("+")
    return float(text) if text else None


def main() -> int:
    settings = Settings.from_env()
    client = KiwoomClient(settings)
    token = client.issue_token()
    seen = {code: [] for code in STOCKS}

    with connect(WS_URL, open_timeout=20, ping_interval=None, close_timeout=10) as ws:
        ws.send(json.dumps({"trnm": "LOGIN", "token": token}, ensure_ascii=False))
        login = json.loads(ws.recv(timeout=20))
        if int(login.get("return_code", -1)) != 0:
            raise RuntimeError(f"LOGIN failed: {login}")

        ws.send(json.dumps({
            "trnm": "REG",
            "grp_no": "31",
            "refresh": "1",
            "data": [{"item": list(STOCKS), "type": ["0w"]}],
        }, ensure_ascii=False))

        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            try:
                raw = ws.recv(timeout=5)
            except TimeoutError:
                continue
            msg = json.loads(raw)
            trnm = str(msg.get("trnm", "")).upper()
            if trnm == "PING":
                ws.send(json.dumps(msg, ensure_ascii=False))
                continue
            if trnm != "REAL":
                continue

            for item in msg.get("data") or []:
                if str(item.get("type", "")) != "0w":
                    continue
                code = str(item.get("item", "")).split("_", 1)[0]
                if code not in STOCKS:
                    continue
                values = item.get("values") or {}
                tm = str(values.get("20", ""))
                qty = number(values.get("210"))
                amount_million = number(values.get("212"))
                sample = (tm, qty, amount_million)
                if sample in seen[code]:
                    continue
                seen[code].append(sample)
                amount_eok = None if amount_million is None else amount_million / 100.0
                print(
                    f"{code} {STOCKS[code]} | time={tm} | "
                    f"program_qty={qty}주 | program_amount={amount_million}백만원 "
                    f"({amount_eok}억원)"
                )

            if all(len(v) >= 2 for v in seen.values()):
                print("WS_PROBE_PASS: both stocks received at least 2 distinct real-time 0w samples")
                return 0

    missing = {STOCKS[k]: len(v) for k, v in seen.items() if len(v) < 2}
    raise RuntimeError(f"WS_PROBE_INCOMPLETE: {missing}")


if __name__ == "__main__":
    raise SystemExit(main())
