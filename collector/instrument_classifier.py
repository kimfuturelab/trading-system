#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
DEFAULT_AUTH_ENV = Path.home() / "api-read-v2.env"
DEFAULT_LATEST_TOP100_FILE = Path.home() / ".market-flow-top100-latest.json"
DEFAULT_THEME_CACHE_FILE = Path.home() / ".market-flow-theme-cache.json"
DEFAULT_OUTPUT_FILE = Path.home() / ".market-flow-instrument-cache.json"

# 실전 TOP100에서 우선 확인할 시장. 공식 ka10099 시장구분을 그대로 사용합니다.
PRIMARY_MARKETS = [
    ("etf", "ETF"),
    ("etn", "ETN"),
    ("loss-limit-etn", "ETN"),
    ("volatility-etn", "ETN"),
    ("reit", "REIT"),
    ("infrastructure", "INFRA"),
    ("kospi", "KOSPI_STOCK"),
    ("kosdaq", "KOSDAQ_STOCK"),
    ("konex", "KONEX_STOCK"),
]

# 1차 시장에서 못 찾은 TOP100 종목이 있을 때만 조회합니다.
SECONDARY_MARKETS = [
    ("kotc", "KOTC"),
    ("gold", "GOLD"),
    ("elw", "ELW"),
    ("mutual-fund", "FUND"),
    ("warrant", "WARRANT"),
    ("warrant-certificate", "WARRANT_CERT"),
    ("high-yield-fund", "FUND"),
]

PREFERRED_RE = re.compile(r"(?:\d+)?우(?:B|C)?$")


def load_env_file(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(f"환경파일 없음: {path}")
    text = path.read_text(encoding="utf-8-sig")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip()


def require_env(names: list[str]) -> None:
    missing = [name for name in names if not os.environ.get(name, "").strip()]
    if missing:
        raise RuntimeError("환경변수 누락: " + ", ".join(missing))


def find_cli() -> str:
    found = shutil.which("kiwoomcli")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "kiwoomcli"
    if fallback.exists():
        return str(fallback)
    raise RuntimeError("kiwoomcli를 찾을 수 없습니다.")


def run_cli(cli: str, *args: str) -> dict:
    cp = subprocess.run(
        [cli, *args, "--format", "json"],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=60,
    )
    if cp.returncode != 0:
        msg = (cp.stderr or cp.stdout or "").strip()
        raise RuntimeError(f"kiwoomcli 실패(code={cp.returncode}): {msg[:700]}")
    out = cp.stdout.strip()
    if not out:
        raise RuntimeError("kiwoomcli 응답이 비어 있습니다.")
    starts = [i for i in (out.find("{"), out.find("[")) if i >= 0]
    if starts:
        out = out[min(starts):]
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"kiwoomcli JSON 파싱 실패: {exc}") from exc


def canonical_stock_code(raw_code: str) -> str:
    code = str(raw_code or "").strip()
    if "_" in code:
        code = code.split("_", 1)[0]
    return code


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_latest_top100(path: Path) -> tuple[str, list[dict]]:
    payload = load_json(path, None)
    if not isinstance(payload, dict):
        raise RuntimeError(f"최신 TOP100 파일 파싱 실패 또는 없음: {path}")
    rows = payload.get("rows") or []
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("최신 TOP100 파일에 rows가 없습니다.")

    targets = []
    for row in rows[:100]:
        if not isinstance(row, dict):
            continue
        code = canonical_stock_code(row.get("stock_code") or row.get("raw_stock_code") or "")
        if not code:
            continue
        targets.append({
            "stock_code": code,
            "stock_name": str(row.get("stock_name") or "").strip(),
            "rank": row.get("rank"),
            "captured_at": str(row.get("captured_at") or payload.get("captured_at") or ""),
            "trading_value_eok": row.get("trading_value_eok"),
        })
    targets.sort(key=lambda r: r.get("rank") if r.get("rank") is not None else 9999)
    return str(payload.get("captured_at") or ""), targets[:100]


def fetch_market_list(cli: str, market_type: str) -> list[dict]:
    mode = os.environ.get("KIWOOM_MODE", "real").strip() or "real"
    data = run_cli(
        cli,
        "domestic", "stocks", "info-list",
        "--market-type", market_type,
        "--pages", "0",
        "--mode", mode,
    )
    if data.get("return_code") not in (0, "0", None):
        raise RuntimeError(
            f"ka10099 market={market_type} 오류: {data.get('return_msg', 'unknown')}"
        )
    rows = data.get("list") or []
    return rows if isinstance(rows, list) else []


def market_record(row: dict, market_type: str, base_type: str) -> dict:
    return {
        "source_market_type": market_type,
        "base_type": base_type,
        "market_code": str(row.get("marketCode") or "").strip(),
        "market_name": str(row.get("marketName") or "").strip(),
        "industry_name": str(row.get("upName") or "").strip(),
        "company_size": str(row.get("upSizeName") or "").strip(),
        "company_class": str(row.get("companyClassName") or "").strip(),
        "state": str(row.get("state") or "").strip(),
        "audit_info": str(row.get("auditInfo") or "").strip(),
        "order_warning": str(row.get("orderWarning") or "").strip(),
        "nxt_enable": str(row.get("nxtEnable") or "").strip(),
        "reg_day": str(row.get("regDay") or "").strip(),
    }


def refine_type(base_type: str, stock_name: str) -> str:
    # 우선주는 ka10099에서 KOSPI/KOSDAQ 주식군으로 들어오므로 이름 패턴을 보조판정으로 사용합니다.
    if base_type in {"KOSPI_STOCK", "KOSDAQ_STOCK", "KONEX_STOCK"}:
        if PREFERRED_RE.search(stock_name):
            return "PREFERRED_STOCK"
        if stock_name.endswith("스팩") or "기업인수목적" in stock_name:
            return "SPAC"
    return base_type


def aggregation_policy(stock_type: str) -> str:
    if stock_type in {"KOSPI_STOCK", "KOSDAQ_STOCK", "KONEX_STOCK"}:
        return "INCLUDE"
    if stock_type in {
        "ETF", "ETN", "ELW", "FUND", "WARRANT", "WARRANT_CERT",
        "GOLD", "PREFERRED_STOCK", "SPAC",
    }:
        return "EXCLUDE"
    return "REVIEW"


def theme_count_for(code: str, theme_cache: dict) -> int | None:
    stocks = theme_cache.get("stocks", {}) if isinstance(theme_cache, dict) else {}
    entry = stocks.get(code)
    if not isinstance(entry, dict):
        return None
    themes = entry.get("themes")
    if not isinstance(themes, list):
        return None
    return len(themes)


def build_membership(
    cli: str,
    target_codes: set[str],
    sleep_seconds: float,
) -> tuple[dict[str, dict], list[str]]:
    membership: dict[str, dict] = {}
    queried_markets: list[str] = []

    def query_groups(groups: list[tuple[str, str]], only_unmatched: bool) -> None:
        nonlocal membership
        for market_type, base_type in groups:
            if only_unmatched and not (target_codes - membership.keys()):
                break
            rows = fetch_market_list(cli, market_type)
            queried_markets.append(market_type)
            matched_this_market = 0
            for row in rows:
                code = canonical_stock_code(row.get("code") or "")
                if code not in target_codes or code in membership:
                    continue
                membership[code] = market_record(row, market_type, base_type)
                matched_this_market += 1
            print(
                f"[MARKET] {market_type:<20} rows={len(rows):>5} "
                f"matched_top100={matched_this_market:>3} total_matched={len(membership):>3}",
                flush=True,
            )
            if sleep_seconds:
                time.sleep(sleep_seconds)

    query_groups(PRIMARY_MARKETS, only_unmatched=False)
    if target_codes - membership.keys():
        query_groups(SECONDARY_MARKETS, only_unmatched=True)

    return membership, queried_markets


def save_output(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="3.5단계 TOP100 종목유형 분류기 (ka10099)")
    parser.add_argument("--auth-env", default=str(DEFAULT_AUTH_ENV))
    parser.add_argument("--latest-top100-file", default=str(DEFAULT_LATEST_TOP100_FILE))
    parser.add_argument("--theme-cache-file", default=str(DEFAULT_THEME_CACHE_FILE))
    parser.add_argument("--output-file", default=str(DEFAULT_OUTPUT_FILE))
    parser.add_argument("--sleep", type=float, default=0.3, help="시장구분 API 호출 사이 대기초")
    args = parser.parse_args()

    if args.sleep < 0:
        print("CONFIG ERROR: --sleep must be >= 0", file=sys.stderr)
        return 2

    try:
        load_env_file(Path(args.auth_env).expanduser())
        require_env(["KIWOOM_MODE", "APP_KEY", "APP_SECRET"])
        cli = find_cli()
        latest_path = Path(args.latest_top100_file).expanduser()
        theme_cache_path = Path(args.theme_cache_file).expanduser()
        output_path = Path(args.output_file).expanduser()
        captured_at, targets = load_latest_top100(latest_path)
        theme_cache = load_json(theme_cache_path, {"stocks": {}})
    except Exception as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2

    target_codes = {row["stock_code"] for row in targets}
    print(f"latest_top100_captured_at={captured_at}")
    print(f"targets={len(targets)}")

    try:
        membership, queried_markets = build_membership(cli, target_codes, args.sleep)
    except Exception as exc:
        print(f"API ERROR: {exc}", file=sys.stderr)
        return 1

    result_rows = []
    type_counter = Counter()
    policy_counter = Counter()
    general_no_theme = []
    unmatched = []

    for target in targets:
        code = target["stock_code"]
        name = target["stock_name"]
        info = membership.get(code)
        if info:
            stock_type = refine_type(info["base_type"], name)
            record = dict(info)
        else:
            stock_type = "UNKNOWN"
            record = {
                "source_market_type": "",
                "base_type": "UNKNOWN",
                "market_code": "",
                "market_name": "",
                "industry_name": "",
                "company_size": "",
                "company_class": "",
                "state": "",
                "audit_info": "",
                "order_warning": "",
                "nxt_enable": "",
                "reg_day": "",
            }
            unmatched.append(target)

        policy = aggregation_policy(stock_type)
        theme_count = theme_count_for(code, theme_cache)
        needs_theme_enrichment = policy == "INCLUDE" and theme_count == 0

        row = {
            "rank": target.get("rank"),
            "stock_code": code,
            "stock_name": name,
            "trading_value_eok": target.get("trading_value_eok"),
            "instrument_type": stock_type,
            "aggregation_policy": policy,
            "theme_count": theme_count,
            "needs_theme_enrichment": needs_theme_enrichment,
            **record,
        }
        result_rows.append(row)
        type_counter[stock_type] += 1
        policy_counter[policy] += 1
        if needs_theme_enrichment:
            general_no_theme.append(row)

    payload = {
        "version": 1,
        "checked_at": datetime.now(KST).isoformat(timespec="seconds"),
        "top100_captured_at": captured_at,
        "source": "kiwoom-ka10099",
        "queried_markets": queried_markets,
        "summary": {
            "targets": len(targets),
            "matched": len(targets) - len(unmatched),
            "unmatched": len(unmatched),
            "type_counts": dict(type_counter),
            "policy_counts": dict(policy_counter),
            "general_no_theme": len(general_no_theme),
        },
        "rows": result_rows,
    }
    save_output(output_path, payload)

    print()
    print("===== INSTRUMENT TYPE SUMMARY =====")
    print(f"matched={len(targets) - len(unmatched)}")
    print(f"unmatched={len(unmatched)}")
    for key, count in sorted(type_counter.items(), key=lambda x: (-x[1], x[0])):
        print(f"type {key:<18} = {count}")
    for key in ("INCLUDE", "EXCLUDE", "REVIEW"):
        print(f"policy {key:<7} = {policy_counter.get(key, 0)}")
    print(f"general_no_theme={len(general_no_theme)}")
    print(f"output_file={output_path}")

    print()
    print("===== GENERAL STOCK + NO_THEME (NEXT TARGET) =====")
    if general_no_theme:
        for row in general_no_theme:
            print(
                f"{str(row.get('rank')).rjust(3)} {row['stock_code']} "
                f"{row['stock_name']} type={row['instrument_type']} "
                f"amount_eok={row.get('trading_value_eok')}"
            )
    else:
        print("NONE")

    if unmatched:
        print()
        print("===== UNMATCHED =====")
        for row in unmatched:
            print(f"{row.get('rank')} {row['stock_code']} {row['stock_name']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
