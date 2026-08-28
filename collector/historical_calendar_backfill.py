#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
DEFAULT_PROJECT_ENV = Path.home() / "market-flow.env"
DEFAULT_THEME_MASTER = Path.home() / ".market-flow-theme-master.json"
DEFAULT_OVERRIDE_FILE = Path.home() / "market-flow" / "theme_overrides.json"
DEFAULT_OUTPUT_FILE = Path.home() / ".market-flow-calendar-backfill.json"
DEFAULT_MARCAP_CACHE = Path.home() / ".cache" / "market-flow" / "marcap-2026.parquet"
MARCAP_URL_TEMPLATE = "https://raw.githubusercontent.com/FinanceData/marcap/master/data/marcap-{year}.parquet"
SELECTION_RULE = "peer-top100-trading-value-ex-self-v1"
CALENDAR_STATUS = "과거복원"


def load_env_file(path: Path, *, required: bool = False) -> None:
    if not path.exists():
        if required:
            raise RuntimeError(f"환경파일 없음: {path}")
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip()


def load_json(path: Path) -> dict:
    if not path.exists():
        raise RuntimeError(f"파일 없음: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"JSON 파싱 실패 {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"JSON 최상위 형식 오류: {path}")
    return data


def canonical_code(value: object) -> str:
    code = str(value or "").strip()
    if "_" in code:
        code = code.split("_", 1)[0]
    return code


def num(value: object, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return float(default)
        return float(str(value).replace(",", "").strip())
    except Exception:
        return float(default)


def normalize_theme_list(raw: object) -> list[dict]:
    if not isinstance(raw, list):
        return []
    out = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        tcode = str(item.get("theme_code") or "").strip()
        tname = str(item.get("theme_name") or "").strip()
        key = (tcode, tname)
        if not tcode or not tname or key in seen:
            continue
        seen.add(key)
        out.append({"theme_code": tcode, "theme_name": tname})
    return out


def build_theme_index(theme_master: dict, overrides: dict) -> dict[str, list[dict]]:
    reverse = theme_master.get("reverse_index") or {}
    if not isinstance(reverse, dict):
        raise RuntimeError("theme master reverse_index 형식 오류")

    index: dict[str, list[dict]] = {}
    for raw_code, themes in reverse.items():
        code = canonical_code(raw_code)
        normalized = normalize_theme_list(themes)
        if code and normalized:
            index[code] = normalized

    raw_overrides = overrides.get("overrides") or {}
    if not isinstance(raw_overrides, dict):
        raw_overrides = {}
    for raw_code, item in raw_overrides.items():
        code = canonical_code(raw_code)
        if not code or code in index or not isinstance(item, dict):
            continue
        tcode = str(item.get("theme_code") or "").strip()
        tname = str(item.get("theme_name") or "").strip()
        if tcode and tname:
            index[code] = [{"theme_code": tcode, "theme_name": tname}]

    return index


def import_pandas():
    try:
        import pandas as pd  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "pandas/pyarrow가 필요합니다. `pip install -r collector/requirements-backfill.txt`를 실행하세요."
        ) from exc
    return pd


def ensure_marcap_file(year: int, cache_path: Path) -> Path:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists() and cache_path.stat().st_size > 100_000:
        return cache_path

    url = MARCAP_URL_TEMPLATE.format(year=year)
    print(f"[DOWNLOAD] {url}", flush=True)
    req = Request(url, headers={"User-Agent": "market-flow-backfill/1.0"})
    tmp = cache_path.with_suffix(cache_path.suffix + ".tmp")
    try:
        with urlopen(req, timeout=120) as resp, tmp.open("wb") as out:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    except HTTPError as exc:
        raise RuntimeError(f"marcap 다운로드 HTTP {exc.code}: {url}") from exc
    except URLError as exc:
        raise RuntimeError(f"marcap 다운로드 실패: {exc.reason}") from exc

    if not tmp.exists() or tmp.stat().st_size < 100_000:
        raise RuntimeError("marcap 파일 다운로드 결과가 비정상적으로 작습니다.")
    tmp.replace(cache_path)
    return cache_path


def load_marcap_period(pd, parquet_path: Path, from_date: str, to_date: str):
    df = pd.read_parquet(parquet_path)
    required = {"Date", "Code", "Name", "Close", "Volume", "Amount"}
    missing = sorted(required - set(df.columns))
    if missing:
        raise RuntimeError("marcap 필수 컬럼 누락: " + ", ".join(missing))

    dates = pd.to_datetime(df["Date"], errors="coerce")
    start = pd.Timestamp(datetime.strptime(from_date, "%Y%m%d").date())
    end = pd.Timestamp(datetime.strptime(to_date, "%Y%m%d").date())
    filtered = df.loc[(dates >= start) & (dates <= end)].copy()
    filtered["Date"] = pd.to_datetime(filtered["Date"], errors="coerce")
    filtered["Code"] = filtered["Code"].astype(str).str.zfill(6)
    filtered["Name"] = filtered["Name"].astype(str)
    filtered["Amount"] = pd.to_numeric(filtered["Amount"], errors="coerce").fillna(0)
    filtered["Close"] = pd.to_numeric(filtered["Close"], errors="coerce").fillna(0)
    if "ChagesRatio" in filtered.columns:
        filtered["_change_rate"] = pd.to_numeric(
            filtered["ChagesRatio"], errors="coerce"
        ).fillna(0)
    elif "ChangesRatio" in filtered.columns:
        filtered["_change_rate"] = pd.to_numeric(
            filtered["ChangesRatio"], errors="coerce"
        ).fillna(0)
    else:
        filtered["_change_rate"] = 0.0
    return filtered


def looks_like_excluded_security(name: str) -> bool:
    text = str(name or "").strip().upper()
    if not text:
        return False
    # marcap은 주식 중심 데이터지만 우선주/스팩 등이 섞일 수 있어
    # 실시간 집계의 EXCLUDE 정책과 최대한 맞춘다.
    preferred_tokens = ("우", "우B", "우C", "1우", "2우", "3우")
    if any(text.endswith(token.upper()) for token in preferred_tokens):
        return True
    if "스팩" in text or "SPAC" in text:
        return True
    return False


def fetch_day_universe(day_df) -> list[dict]:
    if day_df is None or day_df.empty:
        return []

    rows: list[dict] = []
    for _, row in day_df.iterrows():
        code = canonical_code(row.get("Code"))
        name = str(row.get("Name") or "").strip()
        amount = num(row.get("Amount"))
        if not code or amount <= 0:
            continue
        rows.append({
            "stock_code": code,
            "stock_name": name,
            "security_type": "EXCLUDE" if looks_like_excluded_security(name) else "STOCK",
            "trading_value_raw": amount,
            "change_rate_pct": num(row.get("_change_rate")),
        })

    rows.sort(key=lambda r: (-r["trading_value_raw"], r["stock_code"]))
    top100 = rows[:100]
    for idx, row in enumerate(top100, 1):
        row["rank"] = idx
        row["trading_value_eok"] = row["trading_value_raw"] / 100_000_000.0
    return top100


def choose_representatives(
    top100: list[dict],
    theme_index: dict[str, list[dict]],
) -> tuple[list[dict], list[dict]]:
    eligible: list[dict] = []
    unclassified: list[dict] = []

    for row in top100:
        if row["security_type"] != "STOCK":
            continue
        code = row["stock_code"]
        themes = theme_index.get(code) or []
        enriched = {**row, "themes": themes}
        if themes:
            eligible.append(enriched)
        else:
            unclassified.append(enriched)

    candidate_strength = defaultdict(float)
    for row in eligible:
        for theme in row["themes"]:
            candidate_strength[theme["theme_code"]] += row["trading_value_eok"]

    reps = []
    for row in eligible:
        candidates = row["themes"]
        if len(candidates) == 1:
            chosen = candidates[0]
        else:
            scored = []
            for theme in candidates:
                peer = max(
                    0.0,
                    candidate_strength[theme["theme_code"]] - row["trading_value_eok"],
                )
                scored.append((peer, theme["theme_code"], theme))
            scored.sort(key=lambda x: (-x[0], x[1]))
            chosen = scored[0][2]

        reps.append({
            "rank": row["rank"],
            "stock_code": row["stock_code"],
            "stock_name": row["stock_name"],
            "trading_value_eok": row["trading_value_eok"],
            "change_rate_pct": row["change_rate_pct"],
            "theme_code": chosen["theme_code"],
            "theme_name": chosen["theme_name"],
        })

    return reps, unclassified


def aggregate_themes(top100: list[dict], reps: list[dict]) -> list[dict]:
    raw_total = sum(r["trading_value_eok"] for r in top100)
    buckets: dict[str, dict] = {}

    for rep in reps:
        code = rep["theme_code"]
        if code not in buckets:
            buckets[code] = {
                "theme_code": code,
                "theme_name": rep["theme_name"],
                "trading_value_eok": 0.0,
                "top20_count": 0,
                "stock_count": 0,
                "weighted_change_numer": 0.0,
                "leader_stock_code": "",
                "leader_stock_name": "",
                "leader_trading_value_eok": -1.0,
            }
        b = buckets[code]
        amount = rep["trading_value_eok"]
        b["trading_value_eok"] += amount
        b["stock_count"] += 1
        if rep["rank"] <= 20:
            b["top20_count"] += 1
        b["weighted_change_numer"] += amount * rep["change_rate_pct"]
        if amount > b["leader_trading_value_eok"]:
            b["leader_trading_value_eok"] = amount
            b["leader_stock_code"] = rep["stock_code"]
            b["leader_stock_name"] = rep["stock_name"]

    rows = []
    for b in buckets.values():
        amount = b["trading_value_eok"]
        rows.append({
            "theme_code": b["theme_code"],
            "theme_name": b["theme_name"],
            "trading_value_eok": round(amount, 2),
            "share_top100_pct": round(
                (amount / raw_total * 100.0) if raw_total else 0.0,
                4,
            ),
            "top20_count": b["top20_count"],
            "stock_count": b["stock_count"],
            "avg_change_rate_pct": round(
                (b["weighted_change_numer"] / amount) if amount else 0.0,
                4,
            ),
            "leader_stock_code": b["leader_stock_code"],
            "leader_stock_name": b["leader_stock_name"],
            "leader_trading_value_eok": round(
                max(0.0, b["leader_trading_value_eok"]), 2
            ),
        })

    rows.sort(key=lambda r: (-r["trading_value_eok"], r["theme_code"]))
    for idx, row in enumerate(rows, 1):
        row["theme_rank"] = idx
    return rows


def classify_market_weight(top1_share: float, gap: float) -> str:
    if gap < 3:
        return "균형"
    if gap < 8:
        return "우세"
    if gap < 15:
        return "강한 우세"
    if top1_share >= 30:
        return "지배"
    return "강한 우세"


def day_to_calendar_row(
    yyyymmdd: str,
    top100: list[dict],
    theme_index: dict[str, list[dict]],
) -> dict | None:
    reps, unclassified = choose_representatives(top100, theme_index)
    themes = aggregate_themes(top100, reps)
    if not themes:
        return None

    first = themes[0]
    second = themes[1] if len(themes) > 1 else None
    second_share = num(second.get("share_top100_pct")) if second else 0.0
    gap = num(first.get("share_top100_pct")) - second_share

    raw_total = sum(r["trading_value_eok"] for r in top100)
    unclassified_total = sum(r["trading_value_eok"] for r in unclassified)
    excluded_count = sum(1 for r in top100 if r["security_type"] != "STOCK")

    dt = datetime.strptime(yyyymmdd, "%Y%m%d")
    weekday_ko = "월화수목금토일"[dt.weekday()]

    return {
        "date": dt.strftime("%Y-%m-%d"),
        "weekday": weekday_ko,
        "close_top1_theme": first["theme_name"],
        "close_top1_share_pct": round(num(first["share_top100_pct"]), 2),
        "close_top2_theme": second["theme_name"] if second else "",
        "top1_top2_gap_pp": round(gap, 2),
        "market_weight": classify_market_weight(num(first["share_top100_pct"]), gap),
        "intraday_representative_theme": "",
        "intraday_max_share_pct": "",
        "lead_switch_count": "",
        "close_lifecycle": "",
        "lead_duration_minutes": "",
        "major_money_move": "",
        "leader_stock": first.get("leader_stock_name") or first.get("leader_stock_code") or "",
        "market_money_state": "",
        "record_status": CALENDAR_STATUS,
        "note": (
            "FinanceData/marcap 일별 전종목 Amount로 TOP100 재구성 / "
            "현재 키움 테마 마스터로 대표테마 재선정 / "
            "ETF·ETN은 marcap 모집단에 없어 실시간 ka10032 대비 점유율 오차 가능 / "
            "장중 최고점유율·교체횟수·지속시간은 과거복원 불가"
        ),
        "diagnostics": {
            "raw_top100_count": len(top100),
            "raw_top100_total_eok": round(raw_total, 2),
            "classified_stock_count": len(reps),
            "unclassified_stock_count": len(unclassified),
            "unclassified_share_top100_pct": round(
                (unclassified_total / raw_total * 100.0) if raw_total else 0.0, 4
            ),
            "excluded_security_count": excluded_count,
            "theme_count": len(themes),
            "selection_rule": SELECTION_RULE,
        },
    }


def post_webhook(rows: list[dict]) -> dict:
    url = os.environ.get("WEBHOOK_URL", "").strip()
    secret = os.environ.get("WEBHOOK_SECRET", "").strip()
    if not url or not secret:
        raise RuntimeError("WEBHOOK_URL 또는 WEBHOOK_SECRET이 없습니다.")

    payload = {
        "secret": secret,
        "type": "calendar_backfill",
        "source": "financedata-marcap-daily-top100-reconstruction",
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "rows": rows,
    }
    req = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=60) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            if resp.status != 200:
                raise RuntimeError(f"Webhook HTTP {resp.status}: {text[:700]}")
    except HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Webhook HTTP {exc.code}: {text[:700]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Webhook 연결 실패: {exc.reason}") from exc

    result = json.loads(text)
    if not result.get("ok"):
        raise RuntimeError(f"Webhook 오류: {result}")
    return result


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="3.5단계 주도테마 캘린더 과거 일별 백필")
    p.add_argument("--from-date", required=True, help="YYYYMMDD")
    p.add_argument("--to-date", required=True, help="YYYYMMDD")
    p.add_argument("--theme-master", default=str(DEFAULT_THEME_MASTER))
    p.add_argument("--overrides", default=str(DEFAULT_OVERRIDE_FILE))
    p.add_argument("--project-env", default=str(DEFAULT_PROJECT_ENV))
    p.add_argument("--output", default=str(DEFAULT_OUTPUT_FILE))
    p.add_argument("--marcap-cache", default=str(DEFAULT_MARCAP_CACHE))
    p.add_argument("--post", action="store_true", help="Apps Script webhook으로 전송")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.from_date > args.to_date:
        raise RuntimeError("from-date가 to-date보다 늦습니다.")
    if args.from_date[:4] != args.to_date[:4]:
        raise RuntimeError("현재 백필은 같은 연도 범위만 지원합니다.")

    pd = import_pandas()
    year = int(args.from_date[:4])
    parquet_path = ensure_marcap_file(year, Path(args.marcap_cache).expanduser())
    period_df = load_marcap_period(pd, parquet_path, args.from_date, args.to_date)
    if period_df.empty:
        raise RuntimeError("marcap 기간 데이터가 비어 있습니다.")

    theme_master = load_json(Path(args.theme_master).expanduser())
    overrides_path = Path(args.overrides).expanduser()
    overrides = load_json(overrides_path) if overrides_path.exists() else {"overrides": {}}
    theme_index = build_theme_index(theme_master, overrides)
    if not theme_index:
        raise RuntimeError("사용 가능한 테마 역인덱스가 없습니다.")

    rows = []
    grouped = period_df.groupby(period_df["Date"].dt.strftime("%Y%m%d"), sort=True)
    for yyyymmdd, day_df in grouped:
        print(f"[BACKFILL] {yyyymmdd}", flush=True)
        top100 = fetch_day_universe(day_df)
        if not top100:
            print(f"[SKIP] {yyyymmdd} empty", flush=True)
            continue
        calendar = day_to_calendar_row(yyyymmdd, top100, theme_index)
        if calendar:
            rows.append(calendar)
            print(
                f"  #1={calendar['close_top1_theme']} "
                f"share={calendar['close_top1_share_pct']:.2f}% "
                f"#2={calendar['close_top2_theme']} "
                f"gap={calendar['top1_top2_gap_pp']:.2f}%p "
                f"unclassified={calendar['diagnostics']['unclassified_share_top100_pct']:.2f}%",
                flush=True,
            )

    output = {
        "version": 2,
        "source": "financedata-marcap-daily-top100-reconstruction",
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "from_date": args.from_date,
        "to_date": args.to_date,
        "selection_rule": SELECTION_RULE,
        "row_count": len(rows),
        "rows": rows,
        "limitations": [
            "과거 ka10032 순위 원본이 아니라 FinanceData/marcap 일별 Amount로 TOP100을 재구성한 값",
            "marcap 주식 모집단에는 ETF/ETN이 없어 실시간 ka10032 TOP100 분모와 차이가 날 수 있음",
            "테마 구성은 현재 키움 테마 마스터/보조테마를 과거 날짜에 적용",
            "장중 최고점유율·주도교체·지속시간·Δ5/15/30/60은 스냅샷 미보유 과거일에 복원하지 않음",
            "8/25 실시간 마감 실측값과 반드시 교차검증한 뒤 시트 반영",
        ],
    }

    out_path = Path(args.output).expanduser()
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[SAVED] {out_path} rows={len(rows)}", flush=True)

    if args.post:
        load_env_file(Path(args.project_env).expanduser(), required=True)
        result = post_webhook(rows)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        raise SystemExit(1)
