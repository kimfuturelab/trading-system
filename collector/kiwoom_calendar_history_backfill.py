#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

import pandas as pd
import requests

KST = ZoneInfo("Asia/Seoul")
DEFAULT_AUTH_ENV = Path.home() / "api-read-v2.env"
DEFAULT_THEME_MASTER = Path.home() / ".market-flow-theme-master.json"
DEFAULT_OVERRIDE_FILE = Path.home() / "market-flow" / "theme_overrides.json"
DEFAULT_MARCAP_CACHE = Path.home() / ".cache" / "market-flow" / "marcap-2026.parquet"
DEFAULT_HISTORY_CACHE = Path.home() / ".market-flow-calendar-history-cache-202608.json"
DEFAULT_OUTPUT = Path.home() / ".market-flow-calendar-backfill-20260801-20260827.json"
MARCAP_URL = "https://raw.githubusercontent.com/FinanceData/marcap/master/data/marcap-2026.parquet"
SELECTION_RULE = "peer-top100-trading-value-ex-self-v1"
PREFERRED_RE = re.compile(r"(?:\d+)?우(?:B|C)?$")

MARKETS = [
    ("0", "KOSPI_STOCK"),
    ("10", "KOSDAQ_STOCK"),
    ("8", "ETF"),
    ("60", "ETN"),
    ("70", "ETN"),
    ("90", "ETN"),
    ("6", "REIT"),
    ("2", "INFRA"),
]


def load_env(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(f"환경파일 없음: {path}")
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ[k.strip()] = v.strip()


def num(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return float(default)
        return float(str(v).replace(",", "").strip())
    except Exception:
        return float(default)


def canonical_code(v: Any) -> str:
    s = str(v or "").strip()
    if "_" in s:
        s = s.split("_", 1)[0]
    return s


def load_json(path: Path, default: dict | None = None) -> dict:
    if not path.exists():
        if default is not None:
            return default
        raise RuntimeError(f"파일 없음: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"JSON 파싱 실패 {path}: {exc}") from exc
    return data if isinstance(data, dict) else (default or {})


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def ensure_marcap(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > 100_000:
        return path
    print(f"[DOWNLOAD] {MARCAP_URL}", flush=True)
    req = Request(MARCAP_URL, headers={"User-Agent": "market-flow-backfill/2.0"})
    tmp = path.with_suffix(path.suffix + ".tmp")
    with urlopen(req, timeout=120) as resp, tmp.open("wb") as out:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            out.write(chunk)
    if tmp.stat().st_size < 100_000:
        raise RuntimeError("marcap 다운로드 결과가 비정상적으로 작습니다.")
    tmp.replace(path)
    return path


def build_marcap_candidates(path: Path, from_date: str, to_date: str, per_day: int) -> tuple[set[str], dict[str, str], list[str]]:
    df = pd.read_parquet(path)
    dates = pd.to_datetime(df["Date"], errors="coerce")
    start = pd.Timestamp(datetime.strptime(from_date, "%Y%m%d").date())
    end = pd.Timestamp(datetime.strptime(to_date, "%Y%m%d").date())
    x = df.loc[(dates >= start) & (dates <= end), ["Date", "Code", "Name", "Amount"]].copy()
    x["Date"] = pd.to_datetime(x["Date"], errors="coerce")
    x["Code"] = x["Code"].astype(str).str.zfill(6)
    x["Name"] = x["Name"].astype(str)
    x["Amount"] = pd.to_numeric(x["Amount"], errors="coerce").fillna(0)
    codes: set[str] = set()
    names: dict[str, str] = {}
    days: list[str] = []
    for dt, g in x.groupby("Date"):
        if pd.isna(dt):
            continue
        day = dt.strftime("%Y%m%d")
        days.append(day)
        top = g.nlargest(per_day, "Amount")
        for _, row in top.iterrows():
            code = canonical_code(row["Code"])
            if not code:
                continue
            codes.add(code)
            names[code] = str(row["Name"] or "").strip()
    return codes, names, sorted(set(days))


def normalize_themes(raw: Any) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        tc = str(item.get("theme_code") or "").strip()
        tn = str(item.get("theme_name") or "").strip()
        if not tc or not tn or (tc, tn) in seen:
            continue
        seen.add((tc, tn))
        out.append({"theme_code": tc, "theme_name": tn})
    return out


def build_theme_index(master: dict, overrides: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    reverse = master.get("reverse_index") or {}
    if isinstance(reverse, dict):
        for raw_code, themes in reverse.items():
            code = canonical_code(raw_code)
            norm = normalize_themes(themes)
            if code and norm:
                out[code] = norm
    ovs = overrides.get("overrides") or {}
    if isinstance(ovs, dict):
        for raw_code, item in ovs.items():
            code = canonical_code(raw_code)
            if not code or code in out or not isinstance(item, dict):
                continue
            tc = str(item.get("theme_code") or "").strip()
            tn = str(item.get("theme_name") or "").strip()
            if tc and tn:
                out[code] = [{"theme_code": tc, "theme_name": tn}]
    return out


class KiwoomREST:
    def __init__(self, app_key: str, app_secret: str, mode: str = "real") -> None:
        self.app_key = app_key
        self.app_secret = app_secret
        self.base_url = "https://mockapi.kiwoom.com" if mode == "demo" else "https://api.kiwoom.com"
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json;charset=UTF-8"})
        self.token = ""

    def issue_token(self) -> None:
        r = self.session.post(
            self.base_url + "/oauth2/token",
            json={"grant_type": "client_credentials", "appkey": self.app_key, "secretkey": self.app_secret},
            timeout=20,
        )
        r.raise_for_status()
        d = r.json()
        if int(d.get("return_code", -1)) != 0:
            raise RuntimeError(f"토큰 발급 실패: {d}")
        self.token = str(d.get("token") or "")
        if not self.token:
            raise RuntimeError("토큰 응답에 token이 없습니다.")

    def request(self, api_id: str, body: dict, *, cont_yn: str | None = None, next_key: str | None = None) -> tuple[dict, str | None, str | None]:
        if not self.token:
            self.issue_token()
        headers = {"authorization": f"Bearer {self.token}", "api-id": api_id}
        if cont_yn is not None:
            headers["cont-yn"] = cont_yn
        if next_key is not None:
            headers["next-key"] = next_key

        last_error = None
        for attempt in range(7):
            r = self.session.post(self.base_url + "/api/dostk/stkinfo", headers=headers, json=body, timeout=30)
            if r.status_code == 401 and attempt == 0:
                self.issue_token()
                headers["authorization"] = f"Bearer {self.token}"
                continue
            if r.status_code == 429 or r.status_code >= 500:
                wait = min(20.0, 1.5 * (2 ** attempt))
                print(f"[RETRY] HTTP {r.status_code} wait={wait:.1f}s", flush=True)
                time.sleep(wait)
                last_error = f"HTTP {r.status_code}"
                continue
            r.raise_for_status()
            d = r.json()
            rc = d.get("return_code")
            if rc not in (None, 0, "0"):
                msg = str(d.get("return_msg") or "")
                if "8005" in msg and attempt == 0:
                    self.issue_token()
                    headers["authorization"] = f"Bearer {self.token}"
                    continue
                raise RuntimeError(f"{api_id} 오류 return_code={rc} msg={msg}")
            return d, r.headers.get("cont-yn") or r.headers.get("Cont-Yn"), r.headers.get("next-key") or r.headers.get("Next-Key")
        raise RuntimeError(last_error or f"{api_id} 반복 실패")

    def list_market(self, market_type: str, sleep_s: float) -> list[dict]:
        rows: list[dict] = []
        cont = None
        key = None
        for _ in range(20):
            d, cont, key = self.request("ka10099", {"mrkt_tp": market_type}, cont_yn=cont, next_key=key)
            page = d.get("list") or []
            if isinstance(page, list):
                rows.extend(x for x in page if isinstance(x, dict))
            if cont != "Y":
                break
            time.sleep(sleep_s)
        return rows

    def daily_history(self, query_code: str, from_date: str, to_date: str, sleep_s: float) -> dict[str, dict]:
        out: dict[str, dict] = {}
        cont = None
        key = None
        for _ in range(5):
            d, cont, key = self.request("ka10015", {"stk_cd": query_code, "strt_dt": to_date}, cont_yn=cont, next_key=key)
            page = d.get("daly_trde_dtl") or []
            if isinstance(page, list):
                for row in page:
                    if not isinstance(row, dict):
                        continue
                    day = str(row.get("dt") or "").strip().replace("-", "")
                    if not (len(day) == 8 and day.isdigit()):
                        continue
                    if day > to_date:
                        continue
                    if day < from_date:
                        continue
                    out[day] = {
                        "trading_value_raw": abs(num(row.get("trde_prica"))),
                        "change_rate_pct": num(row.get("flu_rt")),
                    }
            if out and min(out) <= from_date:
                break
            if cont != "Y":
                break
            time.sleep(sleep_s)
        return out


def refine_type(base_type: str, name: str) -> str:
    if base_type in {"KOSPI_STOCK", "KOSDAQ_STOCK"}:
        if PREFERRED_RE.search(name):
            return "PREFERRED_STOCK"
        if name.endswith("스팩") or "기업인수목적" in name:
            return "SPAC"
    return base_type


def policy_for(stock_type: str) -> str:
    if stock_type in {"KOSPI_STOCK", "KOSDAQ_STOCK"}:
        return "INCLUDE"
    if stock_type in {"ETF", "ETN", "PREFERRED_STOCK", "SPAC"}:
        return "EXCLUDE"
    return "REVIEW"


def nxt_enabled(v: Any) -> bool:
    return str(v or "").strip().upper() in {"1", "Y", "YES", "TRUE"}


def build_universe(client: KiwoomREST, stock_candidates: set[str], marcap_names: dict[str, str], to_date: str, sleep_s: float) -> dict[str, dict]:
    universe: dict[str, dict] = {}
    stock_meta: dict[str, dict] = {}

    for market_type, base_type in MARKETS:
        rows = client.list_market(market_type, sleep_s)
        print(f"[LIST] market={market_type} type={base_type} rows={len(rows)}", flush=True)
        for row in rows:
            code = canonical_code(row.get("code"))
            if not code:
                continue
            reg = str(row.get("regDay") or "").strip().replace("-", "")
            if reg and len(reg) == 8 and reg > to_date:
                continue
            name = str(row.get("name") or marcap_names.get(code) or "").strip()
            meta = {
                "stock_code": code,
                "stock_name": name,
                "base_type": base_type,
                "nxt_enable": row.get("nxtEnable"),
            }
            if base_type in {"KOSPI_STOCK", "KOSDAQ_STOCK"}:
                stock_meta[code] = meta
            else:
                prev = universe.get(code)
                if prev is None or prev.get("base_type") in {"KOSPI_STOCK", "KOSDAQ_STOCK"}:
                    universe[code] = meta
        time.sleep(sleep_s)

    for code in stock_candidates:
        meta = stock_meta.get(code) or {
            "stock_code": code,
            "stock_name": marcap_names.get(code, ""),
            "base_type": "KOSPI_STOCK",
            "nxt_enable": "",
        }
        if code not in universe:
            universe[code] = meta

    return universe


def collect_histories(client: KiwoomREST, universe: dict[str, dict], cache_path: Path, from_date: str, to_date: str, sleep_s: float) -> dict:
    cache = load_json(cache_path, {"version": 2, "histories": {}, "errors": {}})
    histories = cache.setdefault("histories", {})
    errors = cache.setdefault("errors", {})
    items = sorted(universe.values(), key=lambda x: (x.get("base_type", ""), x.get("stock_code", "")))
    total = len(items)

    for idx, meta in enumerate(items, 1):
        code = meta["stock_code"]
        old = histories.get(code)
        if isinstance(old, dict) and old.get("from_date") <= from_date and old.get("to_date") >= to_date:
            if idx % 100 == 0 or idx == total:
                print(f"[CACHE] {idx}/{total}", flush=True)
            continue

        base_type = refine_type(str(meta.get("base_type") or ""), str(meta.get("stock_name") or ""))
        query_code = code + "_AL" if base_type in {"KOSPI_STOCK", "KOSDAQ_STOCK"} and nxt_enabled(meta.get("nxt_enable")) else code
        try:
            rows = client.daily_history(query_code, from_date, to_date, sleep_s)
            if not rows and query_code.endswith("_AL"):
                rows = client.daily_history(code, from_date, to_date, sleep_s)
                query_code = code
            histories[code] = {
                "stock_code": code,
                "stock_name": str(meta.get("stock_name") or ""),
                "base_type": base_type,
                "aggregation_policy": policy_for(base_type),
                "query_code": query_code,
                "from_date": from_date,
                "to_date": to_date,
                "rows": rows,
                "checked_at": datetime.now(KST).isoformat(timespec="seconds"),
            }
            errors.pop(code, None)
        except Exception as exc:
            errors[code] = {"error": str(exc), "at": datetime.now(KST).isoformat(timespec="seconds")}
            print(f"[ERROR] {idx}/{total} {code} {meta.get('stock_name','')}: {exc}", flush=True)

        if idx % 25 == 0 or idx == total:
            cache["updated_at"] = datetime.now(KST).isoformat(timespec="seconds")
            save_json(cache_path, cache)
            print(f"[HISTORY] {idx}/{total} cached={len(histories)} errors={len(errors)}", flush=True)
        time.sleep(sleep_s)

    cache["updated_at"] = datetime.now(KST).isoformat(timespec="seconds")
    save_json(cache_path, cache)
    return cache


def build_day_top100(cache: dict, day: str) -> list[dict]:
    rows: list[dict] = []
    for code, entry in (cache.get("histories") or {}).items():
        if not isinstance(entry, dict):
            continue
        day_row = (entry.get("rows") or {}).get(day)
        if not isinstance(day_row, dict):
            continue
        amount = num(day_row.get("trading_value_raw"))
        if amount <= 0:
            continue
        rows.append({
            "stock_code": canonical_code(code),
            "stock_name": str(entry.get("stock_name") or ""),
            "instrument_type": str(entry.get("base_type") or ""),
            "aggregation_policy": str(entry.get("aggregation_policy") or "REVIEW"),
            "trading_value_raw": amount,
            "trading_value_eok": amount / 100.0,
            "change_rate_pct": num(day_row.get("change_rate_pct")),
        })
    rows.sort(key=lambda r: (-r["trading_value_raw"], r["stock_code"]))
    top = rows[:100]
    for i, row in enumerate(top, 1):
        row["rank"] = i
    return top


def choose_representatives(top100: list[dict], theme_index: dict[str, list[dict]]) -> tuple[list[dict], list[dict]]:
    eligible: list[dict] = []
    unclassified: list[dict] = []
    for row in top100:
        if row.get("aggregation_policy") != "INCLUDE":
            continue
        themes = theme_index.get(row["stock_code"]) or []
        x = {**row, "themes": themes}
        if themes:
            eligible.append(x)
        else:
            unclassified.append(x)

    strength = defaultdict(float)
    for row in eligible:
        for theme in row["themes"]:
            strength[theme["theme_code"]] += row["trading_value_eok"]

    reps: list[dict] = []
    for row in eligible:
        candidates = row["themes"]
        if len(candidates) == 1:
            chosen = candidates[0]
        else:
            scored = []
            for theme in candidates:
                peer = max(0.0, strength[theme["theme_code"]] - row["trading_value_eok"])
                scored.append((peer, theme["theme_code"], theme))
            scored.sort(key=lambda x: (-x[0], x[1]))
            chosen = scored[0][2]
        reps.append({**row, "theme_code": chosen["theme_code"], "theme_name": chosen["theme_name"]})
    return reps, unclassified


def aggregate(top100: list[dict], reps: list[dict]) -> list[dict]:
    raw_total = sum(r["trading_value_eok"] for r in top100)
    buckets: dict[str, dict] = {}
    for rep in reps:
        tc = rep["theme_code"]
        b = buckets.setdefault(tc, {"theme_code": tc, "theme_name": rep["theme_name"], "amount": 0.0, "leader": "", "leader_amount": -1.0})
        b["amount"] += rep["trading_value_eok"]
        if rep["trading_value_eok"] > b["leader_amount"]:
            b["leader_amount"] = rep["trading_value_eok"]
            b["leader"] = rep["stock_name"] or rep["stock_code"]
    out = []
    for b in buckets.values():
        out.append({
            "theme_code": b["theme_code"],
            "theme_name": b["theme_name"],
            "trading_value_eok": round(b["amount"], 2),
            "share_top100_pct": round((b["amount"] / raw_total * 100.0) if raw_total else 0.0, 4),
            "leader_stock_name": b["leader"],
        })
    out.sort(key=lambda x: (-x["trading_value_eok"], x["theme_code"]))
    return out


def market_weight(top1_share: float, gap: float) -> str:
    if gap < 3:
        return "균형"
    if gap < 8:
        return "우세"
    if gap < 15:
        return "강한 우세"
    if top1_share >= 30:
        return "지배"
    return "강한 우세"


def calendar_row(day: str, top100: list[dict], theme_index: dict[str, list[dict]]) -> dict | None:
    reps, unclassified = choose_representatives(top100, theme_index)
    themes = aggregate(top100, reps)
    if not themes:
        return None
    first = themes[0]
    second = themes[1] if len(themes) > 1 else None
    second_share = num(second.get("share_top100_pct")) if second else 0.0
    gap = num(first.get("share_top100_pct")) - second_share
    total = sum(r["trading_value_eok"] for r in top100)
    unclass_amt = sum(r["trading_value_eok"] for r in unclassified)
    excluded_amt = sum(r["trading_value_eok"] for r in top100 if r.get("aggregation_policy") == "EXCLUDE")
    dt = datetime.strptime(day, "%Y%m%d")
    return {
        "date": dt.strftime("%Y-%m-%d"),
        "weekday": "월화수목금토일"[dt.weekday()],
        "close_top1_theme": first["theme_name"],
        "close_top1_share_pct": round(num(first["share_top100_pct"]), 2),
        "close_top2_theme": second["theme_name"] if second else "",
        "top1_top2_gap_pp": round(gap, 2),
        "market_weight": market_weight(num(first["share_top100_pct"]), gap),
        "intraday_representative_theme": "",
        "intraday_max_share_pct": "",
        "lead_switch_count": "",
        "close_lifecycle": "",
        "lead_duration_minutes": "",
        "major_money_move": "",
        "leader_stock": first.get("leader_stock_name") or "",
        "market_money_state": "",
        "record_status": "과거복원",
        "note": "Kiwoom ka10015 일별거래대금 기반 TOP100 복원 / KOSPI·KOSDAQ+ETF·ETN 포함 / 장중 스냅샷 항목은 복원하지 않음",
        "diagnostics": {
            "raw_top100_count": len(top100),
            "include_count": sum(1 for r in top100 if r.get("aggregation_policy") == "INCLUDE"),
            "exclude_count": sum(1 for r in top100 if r.get("aggregation_policy") == "EXCLUDE"),
            "review_count": sum(1 for r in top100 if r.get("aggregation_policy") == "REVIEW"),
            "unclassified_stock_count": len(unclassified),
            "unclassified_share_top100_pct": round((unclass_amt / total * 100.0) if total else 0.0, 4),
            "excluded_share_top100_pct": round((excluded_amt / total * 100.0) if total else 0.0, 4),
            "selection_rule": SELECTION_RULE,
        },
    }


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Kiwoom ka10015 기반 3.5 주도테마 캘린더 과거복원")
    p.add_argument("--from-date", default="20260801")
    p.add_argument("--to-date", default="20260827")
    p.add_argument("--auth-env", default=str(DEFAULT_AUTH_ENV))
    p.add_argument("--theme-master", default=str(DEFAULT_THEME_MASTER))
    p.add_argument("--overrides", default=str(DEFAULT_OVERRIDE_FILE))
    p.add_argument("--marcap-cache", default=str(DEFAULT_MARCAP_CACHE))
    p.add_argument("--history-cache", default=str(DEFAULT_HISTORY_CACHE))
    p.add_argument("--output", default=str(DEFAULT_OUTPUT))
    p.add_argument("--stock-candidate-rank", type=int, default=350)
    p.add_argument("--sleep", type=float, default=0.25)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.from_date > args.to_date:
        raise RuntimeError("from-date가 to-date보다 늦습니다.")
    if args.stock_candidate_rank < 100:
        raise RuntimeError("stock-candidate-rank는 100 이상이어야 합니다.")

    load_env(Path(args.auth_env).expanduser())
    app_key = os.environ.get("APP_KEY", "").strip() or os.environ.get("KIWOOM_APP_KEY", "").strip()
    app_secret = os.environ.get("APP_SECRET", "").strip() or os.environ.get("KIWOOM_APP_SECRET", "").strip()
    mode = os.environ.get("KIWOOM_MODE", "real").strip() or "real"
    if not app_key or not app_secret:
        raise RuntimeError("APP_KEY/APP_SECRET 또는 KIWOOM_APP_KEY/KIWOOM_APP_SECRET이 없습니다.")

    marcap_path = ensure_marcap(Path(args.marcap_cache).expanduser())
    stock_candidates, marcap_names, business_days = build_marcap_candidates(
        marcap_path, args.from_date, args.to_date, args.stock_candidate_rank
    )
    print(f"[CANDIDATES] stock_union={len(stock_candidates)} business_days={len(business_days)}", flush=True)

    master = load_json(Path(args.theme_master).expanduser())
    overrides = load_json(Path(args.overrides).expanduser(), {"overrides": {}})
    theme_index = build_theme_index(master, overrides)
    if not theme_index:
        raise RuntimeError("theme index가 비어 있습니다.")

    client = KiwoomREST(app_key, app_secret, mode)
    client.issue_token()
    universe = build_universe(client, stock_candidates, marcap_names, args.to_date, args.sleep)
    print(f"[UNIVERSE] history_targets={len(universe)}", flush=True)

    cache = collect_histories(
        client,
        universe,
        Path(args.history_cache).expanduser(),
        args.from_date,
        args.to_date,
        args.sleep,
    )

    rows = []
    for day in business_days:
        top100 = build_day_top100(cache, day)
        if len(top100) < 100:
            print(f"[WARN] {day} top100_count={len(top100)}", flush=True)
        row = calendar_row(day, top100, theme_index)
        if row:
            rows.append(row)
            q = row["diagnostics"]
            print(
                f"[BACKFILL] {day} #1={row['close_top1_theme']} {row['close_top1_share_pct']:.2f}% "
                f"#2={row['close_top2_theme']} gap={row['top1_top2_gap_pp']:.2f}%p "
                f"EX={q['exclude_count']} exshare={q['excluded_share_top100_pct']:.2f}% "
                f"unclassified={q['unclassified_share_top100_pct']:.2f}%",
                flush=True,
            )

    output = {
        "version": 2,
        "source": "kiwoom-ka10015-sor-history-reconstruction",
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "from_date": args.from_date,
        "to_date": args.to_date,
        "row_count": len(rows),
        "rows": rows,
        "history_target_count": len(universe),
        "history_cached_count": len(cache.get("histories") or {}),
        "history_error_count": len(cache.get("errors") or {}),
        "limitations": [
            "보통주 후보는 marcap 일별 거래대금 상위 union으로 줄여 API 호출량을 제한",
            "ETF/ETN은 ka10099 현재 상장목록 전체를 후보로 포함",
            "과거 장중 최고점유율·교체횟수·지속시간·Δ5/15/30/60은 복원하지 않음",
            "현재 키움 테마 마스터/보조테마를 과거 날짜에 적용",
        ],
    }
    save_json(Path(args.output).expanduser(), output)
    print(f"[SAVED] {Path(args.output).expanduser()} rows={len(rows)}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
