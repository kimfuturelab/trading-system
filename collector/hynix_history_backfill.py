from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://api.kiwoom.com"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip().rstrip("\r")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def env_first(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def to_number(value: Any, *, absolute: bool = False) -> float:
    if value is None:
        return 0.0
    text = str(value).strip().replace(",", "")
    if not text:
        return 0.0
    try:
        number = float(text)
    except ValueError:
        return 0.0
    return abs(number) if absolute else number


def issue_token(session: requests.Session, base_url: str, app_key: str, app_secret: str) -> str:
    response = session.post(
        f"{base_url}/oauth2/token",
        json={
            "grant_type": "client_credentials",
            "appkey": app_key,
            "secretkey": app_secret,
        },
        timeout=20,
    )
    response.raise_for_status()
    data = response.json()
    if int(data.get("return_code", -1)) != 0:
        raise RuntimeError(f"Kiwoom token error: {data}")
    token = str(data.get("token", "")).strip()
    if not token:
        raise RuntimeError(f"Token missing: {data}")
    return token


def fetch_pages(
    session: requests.Session,
    base_url: str,
    token: str,
    api_id: str,
    path: str,
    body: dict[str, Any],
    table_key: str,
    column_keys: list[str] | None = None,
    max_pages: int = 10,
) -> list[dict[str, Any]]:
    """Fetch paginated Kiwoom REST rows.

    Kiwoom TR responses may contain each table row either as a dict or as a
    positional list/tuple. Official examples explicitly support both shapes,
    so this helper normalizes both into dict rows.
    """
    rows: list[dict[str, Any]] = []
    cont_yn = "N"
    next_key = ""
    last_debug: dict[str, Any] | None = None

    for page in range(max_pages):
        headers = {
            "authorization": f"Bearer {token}",
            "api-id": api_id,
            "Content-Type": "application/json;charset=UTF-8",
        }
        if page > 0:
            headers["cont-yn"] = cont_yn
            headers["next-key"] = next_key

        response = session.post(f"{base_url}{path}", headers=headers, json=body, timeout=30)
        response.raise_for_status()
        data = response.json()
        last_debug = data
        if int(data.get("return_code", 0)) not in (0,):
            raise RuntimeError(f"{api_id} error: {data}")

        records = data.get(table_key) or []
        if isinstance(records, list):
            for record in records:
                if isinstance(record, dict):
                    rows.append(record)
                elif isinstance(record, (list, tuple)) and column_keys:
                    rows.append(dict(zip(column_keys, record)))

        cont_yn = str(response.headers.get("cont-yn", "N")).upper()
        next_key = str(response.headers.get("next-key", ""))
        if cont_yn != "Y" or not next_key:
            break

    if not rows and last_debug is not None:
        visible = {
            key: (f"list[{len(value)}]" if isinstance(value, list) else type(value).__name__)
            for key, value in last_debug.items()
            if key not in {"token", "authorization"}
        }
        print(
            json.dumps(
                {"debug_api": api_id, "expected_table_key": table_key, "response_shape": visible},
                ensure_ascii=False,
                indent=2,
            )
        )

    return rows


def find_sk_broker_code(session: requests.Session, base_url: str, token: str) -> tuple[str, str]:
    rows = fetch_pages(
        session,
        base_url,
        token,
        api_id="ka10102",
        path="/api/dostk/stkinfo",
        body={},
        table_key="list",
        column_keys=["code", "name", "gb"],
    )
    if not rows:
        raise RuntimeError("ka10102 returned no broker list after dict/list normalization")

    exact: list[tuple[str, str]] = []
    fuzzy: list[tuple[str, str]] = []
    for row in rows:
        code = str(row.get("code", "")).strip()
        name = str(row.get("name", "")).strip()
        normalized = name.replace(" ", "")
        if normalized == "SK증권":
            exact.append((code, name))
        elif "SK" in normalized.upper() and "증권" in normalized:
            fuzzy.append((code, name))

    candidates = exact or fuzzy
    if not candidates:
        sample = [{"code": r.get("code"), "name": r.get("name"), "gb": r.get("gb")} for r in rows[:20]]
        raise RuntimeError(
            "SK증권 member code not found in ka10102. sample="
            + json.dumps(sample, ensure_ascii=False)
        )
    return candidates[0]


def fetch_broker_daily(
    session: requests.Session,
    base_url: str,
    token: str,
    broker_code: str,
    stock_code: str,
    start_date: str,
    end_date: str,
) -> dict[str, dict[str, Any]]:
    rows = fetch_pages(
        session,
        base_url,
        token,
        api_id="ka10078",
        path="/api/dostk/mrkcond",
        body={
            "mmcm_cd": broker_code,
            "stk_cd": stock_code,
            "strt_dt": start_date,
            "end_dt": end_date,
        },
        table_key="sec_stk_trde_trend",
        column_keys=[
            "dt",
            "cur_prc",
            "pre_sig",
            "pred_pre",
            "flu_rt",
            "acc_trde_qty",
            "netprps_qty",
            "buy_qty",
            "sell_qty",
        ],
    )
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        dt = str(row.get("dt", "")).strip().replace("-", "")
        if dt:
            result[dt] = row
    return result


def fetch_daily_market(
    session: requests.Session,
    base_url: str,
    token: str,
    stock_code: str,
    start_date: str,
) -> dict[str, dict[str, Any]]:
    rows = fetch_pages(
        session,
        base_url,
        token,
        api_id="ka10015",
        path="/api/dostk/stkinfo",
        body={"stk_cd": stock_code, "strt_dt": start_date},
        table_key="daly_trde_dtl",
        column_keys=[
            "dt",
            "close_pric",
            "pred_pre_sig",
            "pred_pre",
            "flu_rt",
            "trde_qty",
            "trde_prica",
            "bf_mkrt_trde_qty",
            "bf_mkrt_trde_wght",
            "opmr_trde_qty",
            "opmr_trde_wght",
            "af_mkrt_trde_qty",
            "af_mkrt_trde_wght",
            "tot_3",
            "prid_trde_qty",
            "cntr_str",
            "for_poss",
            "for_wght",
            "for_netprps",
            "orgn_netprps",
            "ind_netprps",
            "frgn",
            "crd_remn_rt",
            "prm",
            "bf_mkrt_trde_prica",
            "bf_mkrt_trde_prica_wght",
            "opmr_trde_prica",
            "opmr_trde_prica_wght",
            "af_mkrt_trde_prica",
            "af_mkrt_trde_prica_wght",
        ],
    )
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        dt = str(row.get("dt", "")).strip().replace("-", "")
        if dt:
            result[dt] = row
    return result


def infer_market_vwap(trading_value_raw: float, volume: float, reference_price: float) -> tuple[float, float]:
    if trading_value_raw <= 0 or volume <= 0:
        raise RuntimeError("Cannot infer VWAP from zero trading value/volume")

    multipliers = (1.0, 100.0, 1_000.0, 10_000.0, 100_000.0, 1_000_000.0)
    candidates: list[tuple[float, float, float]] = []
    for multiplier in multipliers:
        vwap = trading_value_raw * multiplier / volume
        if vwap <= 0:
            continue
        if reference_price > 0:
            ratio = vwap / reference_price
            if 0.2 <= ratio <= 5.0:
                distance = abs(vwap - reference_price) / reference_price
                candidates.append((distance, multiplier, vwap))
        else:
            candidates.append((0.0, multiplier, vwap))

    if not candidates:
        raise RuntimeError(
            f"Could not infer trading-value unit: raw={trading_value_raw}, volume={volume}, reference={reference_price}"
        )

    _, multiplier, vwap = min(candidates, key=lambda x: x[0])
    return multiplier, vwap


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill SK hynix SK Securities estimated buy history from Kiwoom REST")
    parser.add_argument("--start", default="20260820", help="YYYYMMDD")
    parser.add_argument("--end", default="20260821", help="YYYYMMDD")
    parser.add_argument("--stock", default="000660", help="SK hynix stock code")
    parser.add_argument("--broker-code", default="", help="Optional SK Securities member code override")
    parser.add_argument("--env", default="~/api-read.env", help="Environment file path")
    args = parser.parse_args()

    load_env_file(Path(args.env).expanduser())
    load_env_file(Path(__file__).resolve().parent / ".env")

    app_key = env_first("KIWOOM_APP_KEY", "KIWOOM_APPKEY", "APP_KEY")
    app_secret = env_first("KIWOOM_APP_SECRET", "KIWOOM_APPSECRET", "APP_SECRET")
    base_url = env_first("KIWOOM_BASE_URL") or BASE_URL
    if not app_key or not app_secret:
        raise SystemExit("Missing Kiwoom credentials in environment. Expected KIWOOM_APP_KEY/KIWOOM_APP_SECRET (or compatible aliases).")

    session = requests.Session()
    session.headers.update({"Content-Type": "application/json;charset=UTF-8"})
    token = issue_token(session, base_url, app_key, app_secret)

    if args.broker_code:
        broker_code = args.broker_code.strip()
        broker_name = "SK증권(override)"
    else:
        broker_code, broker_name = find_sk_broker_code(session, base_url, token)

    broker_daily = fetch_broker_daily(
        session, base_url, token, broker_code, args.stock, args.start, args.end
    )
    market_daily = fetch_daily_market(session, base_url, token, args.stock, args.end)

    dates = sorted(dt for dt in broker_daily if args.start <= dt <= args.end)
    if not dates:
        raise SystemExit(f"No ka10078 rows for {args.start}~{args.end}")

    output_rows = []
    for dt in dates:
        broker_row = broker_daily[dt]
        market_row = market_daily.get(dt)
        if not market_row:
            raise RuntimeError(f"ka10015 daily row not found for {dt}")

        buy_qty = int(round(to_number(broker_row.get("buy_qty"), absolute=True)))
        close_price = to_number(market_row.get("close_pric"), absolute=True)
        if close_price <= 0:
            close_price = to_number(broker_row.get("cur_prc"), absolute=True)
        market_volume = to_number(market_row.get("trde_qty"), absolute=True)
        trading_value_raw = to_number(market_row.get("trde_prica"), absolute=True)

        multiplier, market_vwap = infer_market_vwap(trading_value_raw, market_volume, close_price)
        estimated_amount = int(round(buy_qty * market_vwap))

        output_rows.append(
            {
                "date": f"{dt[:4]}-{dt[4:6]}-{dt[6:8]}",
                "sk_broker_code": broker_code,
                "sk_broker_name": broker_name,
                "sk_buy_qty_est": buy_qty,
                "market_vwap_est": round(market_vwap, 3),
                "estimated_buy_amount": estimated_amount,
                "market_volume": int(round(market_volume)),
                "trading_value_raw": trading_value_raw,
                "trading_value_multiplier_inferred": multiplier,
                "method": "ka10078 SK증권 매수수량 × ka10015 시장 VWAP",
                "warning": "SK증권 일반 고객 주문이 포함될 수 있는 추정치",
            }
        )

    result = {
        "ok": True,
        "stock": args.stock,
        "broker_code": broker_code,
        "broker_name": broker_name,
        "start": args.start,
        "end": args.end,
        "rows": output_rows,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
