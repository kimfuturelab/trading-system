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
DEFAULT_LATEST_TOP100_FILE = Path.home() / ".market-flow-top100-latest.json"
DEFAULT_THEME_CACHE_FILE = Path.home() / ".market-flow-theme-cache.json"
DEFAULT_INSTRUMENT_CACHE_FILE = Path.home() / ".market-flow-instrument-cache.json"
DEFAULT_OVERRIDE_FILE = Path.home() / "market-flow" / "theme_overrides.json"
DEFAULT_OUTPUT_FILE = Path.home() / ".market-flow-theme-aggregate-latest.json"
SELECTION_RULE = "peer-top100-trading-value-ex-self-v1"


def load_env_file(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(f"환경파일 없음: {path}")
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


def num(v, default=0.0) -> float:
    try:
        if v is None or v == "":
            return float(default)
        return float(v)
    except Exception:
        return float(default)


def canonical_code(value) -> str:
    code = str(value or "").strip()
    if "_" in code:
        code = code.split("_", 1)[0]
    return code


def save_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def build_instrument_index(payload: dict) -> dict[str, dict]:
    rows = payload.get("rows") or []
    if not isinstance(rows, list):
        raise RuntimeError("instrument cache rows 형식 오류")
    out = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = canonical_code(row.get("stock_code"))
        if code:
            out[code] = row
    return out


def build_override_index(payload: dict) -> dict[str, dict]:
    rows = payload.get("overrides") or {}
    if not isinstance(rows, dict):
        raise RuntimeError("theme overrides 형식 오류")
    return {canonical_code(k): v for k, v in rows.items() if canonical_code(k) and isinstance(v, dict)}


def normalize_themes(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    seen = set()
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        code = str(item.get("theme_code") or "").strip()
        name = str(item.get("theme_name") or "").strip()
        if not code or not name or code in seen:
            continue
        seen.add(code)
        out.append({"theme_code": code, "theme_name": name})
    return out


def build_stock_rows(latest: dict, theme_cache: dict, instrument_cache: dict, overrides: dict) -> list[dict]:
    raw_rows = latest.get("rows") or []
    if not isinstance(raw_rows, list) or not raw_rows:
        raise RuntimeError("최신 TOP100 rows가 비어 있습니다.")
    theme_stocks = theme_cache.get("stocks") or {}
    if not isinstance(theme_stocks, dict):
        raise RuntimeError("theme cache stocks 형식 오류")
    instruments = build_instrument_index(instrument_cache)
    override_index = build_override_index(overrides)

    result = []
    for raw in raw_rows[:100]:
        if not isinstance(raw, dict):
            continue
        code = canonical_code(raw.get("stock_code") or raw.get("raw_stock_code"))
        if not code:
            continue
        inst = instruments.get(code) or {}
        policy = str(inst.get("aggregation_policy") or "REVIEW").strip()
        entry = theme_stocks.get(code) or {}
        themes = normalize_themes(entry.get("themes"))
        source = "KIWOOM"

        if not themes and policy == "INCLUDE":
            override = override_index.get(code)
            if override:
                o_code = str(override.get("theme_code") or "").strip()
                o_name = str(override.get("theme_name") or "").strip()
                if o_code and o_name:
                    themes = [{"theme_code": o_code, "theme_name": o_name}]
                    source = "SUPPLEMENTAL"

        if not themes and policy == "INCLUDE":
            source = "UNCLASSIFIED"

        result.append({
            "rank": int(num(raw.get("rank"), 9999)),
            "stock_code": code,
            "stock_name": str(raw.get("stock_name") or entry.get("stock_name") or "").strip(),
            "trading_value_eok": num(raw.get("trading_value_eok")),
            "change_rate_pct": num(raw.get("change_rate_pct")),
            "aggregation_policy": policy,
            "instrument_type": str(inst.get("instrument_type") or "UNKNOWN").strip(),
            "themes": themes,
            "theme_source": source,
        })
    result.sort(key=lambda r: r["rank"])
    return result


def choose_representatives(stocks: list[dict]) -> list[dict]:
    # Fail-soft: INCLUDE이지만 테마가 아직 없는 종목은 대표테마 선정에서 제외한다.
    # 이 종목의 거래대금은 aggregate_themes() diagnostics의 unclassified_*로 별도 추적한다.
    eligible = [
        r for r in stocks
        if r["aggregation_policy"] == "INCLUDE" and r["themes"]
    ]

    # 복수테마 대표선정용 보조 강도. 최종 집계가 아니라 선택에만 사용한다.
    # 한 종목이 여러 후보에 속해도 여기서는 후보별 '동료 존재감'을 보기 위해 모두 더한다.
    candidate_strength = defaultdict(float)
    for row in eligible:
        amount = row["trading_value_eok"]
        for theme in row["themes"]:
            candidate_strength[theme["theme_code"]] += amount

    reps = []
    for row in eligible:
        candidates = row["themes"]
        if len(candidates) == 1:
            chosen = candidates[0]
            reason = "SINGLE_THEME" if row["theme_source"] == "KIWOOM" else "SUPPLEMENTAL_THEME"
            peer_support = max(0.0, candidate_strength[chosen["theme_code"]] - row["trading_value_eok"])
        else:
            scored = []
            for theme in candidates:
                peer = max(0.0, candidate_strength[theme["theme_code"]] - row["trading_value_eok"])
                scored.append((peer, theme["theme_code"], theme))
            scored.sort(key=lambda x: (-x[0], x[1]))
            peer_support, _, chosen = scored[0]
            reason = "MULTI_PEER_STRENGTH"

        reps.append({
            "rank": row["rank"],
            "stock_code": row["stock_code"],
            "stock_name": row["stock_name"],
            "trading_value_eok": round(row["trading_value_eok"], 2),
            "change_rate_pct": round(row["change_rate_pct"], 4),
            "theme_code": chosen["theme_code"],
            "theme_name": chosen["theme_name"],
            "candidate_count": len(candidates),
            "selection_reason": reason,
            "peer_support_eok": round(peer_support, 2),
            "theme_source": row["theme_source"],
        })
    reps.sort(key=lambda r: r["rank"])
    return reps


def aggregate_themes(stocks: list[dict], representatives: list[dict]) -> tuple[list[dict], dict]:
    raw_total = sum(r["trading_value_eok"] for r in stocks)
    eligible_rows = [r for r in stocks if r["aggregation_policy"] == "INCLUDE"]
    eligible_total = sum(r["trading_value_eok"] for r in eligible_rows)
    excluded_total = sum(r["trading_value_eok"] for r in stocks if r["aggregation_policy"] == "EXCLUDE")
    review_total = sum(r["trading_value_eok"] for r in stocks if r["aggregation_policy"] == "REVIEW")
    unclassified_rows = [r for r in eligible_rows if not r["themes"]]
    unclassified_total = sum(r["trading_value_eok"] for r in unclassified_rows)

    buckets = {}
    for rep in representatives:
        code = rep["theme_code"]
        if code not in buckets:
            buckets[code] = {
                "theme_code": code,
                "theme_name": rep["theme_name"],
                "trading_value_eok": 0.0,
                "top20_count": 0,
                "stock_count": 0,
                "change_rates": [],
                "members": [],
            }
        b = buckets[code]
        b["trading_value_eok"] += rep["trading_value_eok"]
        b["stock_count"] += 1
        if rep["rank"] <= 20:
            b["top20_count"] += 1
        b["change_rates"].append(rep["change_rate_pct"])
        b["members"].append({
            "rank": rep["rank"],
            "stock_code": rep["stock_code"],
            "stock_name": rep["stock_name"],
            "trading_value_eok": rep["trading_value_eok"],
        })

    rows = []
    for b in buckets.values():
        members = sorted(b["members"], key=lambda x: (-x["trading_value_eok"], x["rank"]))
        amount = b["trading_value_eok"]
        avg_change = sum(b["change_rates"]) / len(b["change_rates"]) if b["change_rates"] else 0.0
        rows.append({
            "theme_code": b["theme_code"],
            "theme_name": b["theme_name"],
            "trading_value_eok": round(amount, 2),
            "share_top100_pct": round((amount / raw_total * 100.0) if raw_total else 0.0, 4),
            "share_eligible_pct": round((amount / eligible_total * 100.0) if eligible_total else 0.0, 4),
            "top20_count": b["top20_count"],
            "stock_count": b["stock_count"],
            "avg_change_rate_pct": round(avg_change, 4),
            "leader_stock_code": members[0]["stock_code"] if members else "",
            "leader_stock_name": members[0]["stock_name"] if members else "",
            "leader_trading_value_eok": members[0]["trading_value_eok"] if members else 0.0,
            "member_names": ", ".join(m["stock_name"] for m in members),
        })

    rows.sort(key=lambda r: (-r["trading_value_eok"], r["theme_code"]))
    for idx, row in enumerate(rows, 1):
        row["theme_rank"] = idx

    assigned_total = sum(r["trading_value_eok"] for r in representatives)
    diagnostics = {
        "raw_top100_total_eok": round(raw_total, 2),
        "eligible_total_eok": round(eligible_total, 2),
        "excluded_total_eok": round(excluded_total, 2),
        "review_total_eok": round(review_total, 2),
        "assigned_total_eok": round(assigned_total, 2),
        "unclassified_total_eok": round(unclassified_total, 2),
        "unclassified_share_top100_pct": round((unclassified_total / raw_total * 100.0) if raw_total else 0.0, 4),
        "unclassified_share_eligible_pct": round((unclassified_total / eligible_total * 100.0) if eligible_total else 0.0, 4),
        "coverage_eligible_pct": round((assigned_total / eligible_total * 100.0) if eligible_total else 0.0, 4),
        "top100_count": len(stocks),
        "eligible_stock_count": len(eligible_rows),
        "excluded_stock_count": sum(1 for r in stocks if r["aggregation_policy"] == "EXCLUDE"),
        "unclassified_stock_count": len(unclassified_rows),
        "unclassified_stocks": [
            {
                "rank": r["rank"],
                "stock_code": r["stock_code"],
                "stock_name": r["stock_name"],
                "trading_value_eok": round(r["trading_value_eok"], 2),
            }
            for r in unclassified_rows
        ],
        "representative_count": len(representatives),
        "theme_count": len(rows),
    }
    return rows, diagnostics


def post_webhook(payload: dict) -> dict:
    req = Request(
        os.environ["WEBHOOK_URL"].strip(),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8", errors="replace")
            if resp.status != 200:
                raise RuntimeError(f"Webhook HTTP {resp.status}: {text[:500]}")
    except HTTPError as exc:
        text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Webhook HTTP {exc.code}: {text[:500]}") from exc
    except URLError as exc:
        raise RuntimeError(f"Webhook 연결 실패: {exc.reason}") from exc
    try:
        result = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Webhook JSON 응답 파싱 실패: {text[:500]}") from exc
    if not result.get("ok"):
        raise RuntimeError(f"Webhook 오류: {result.get('error', 'unknown')}")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="3.5단계 대표테마 선정 + 테마 거래대금 집계")
    parser.add_argument("--project-env", default=str(DEFAULT_PROJECT_ENV))
    parser.add_argument("--latest-top100-file", default=str(DEFAULT_LATEST_TOP100_FILE))
    parser.add_argument("--theme-cache-file", default=str(DEFAULT_THEME_CACHE_FILE))
    parser.add_argument("--instrument-cache-file", default=str(DEFAULT_INSTRUMENT_CACHE_FILE))
    parser.add_argument("--override-file", default=str(DEFAULT_OVERRIDE_FILE))
    parser.add_argument("--output-file", default=str(DEFAULT_OUTPUT_FILE))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        latest = load_json(Path(args.latest_top100_file).expanduser())
        theme_cache = load_json(Path(args.theme_cache_file).expanduser())
        instrument_cache = load_json(Path(args.instrument_cache_file).expanduser())
        overrides = load_json(Path(args.override_file).expanduser())
        stocks = build_stock_rows(latest, theme_cache, instrument_cache, overrides)
        if len(stocks) != 100:
            raise RuntimeError(f"TOP100 행수 오류: {len(stocks)}")

        representatives = choose_representatives(stocks)
        theme_rows, diagnostics = aggregate_themes(stocks, representatives)
        captured_at = str(latest.get("captured_at") or datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"))
        payload = {
            "version": 2,
            "type": "theme_aggregate",
            "source": "representative-theme-v1-fail-soft",
            "captured_at": captured_at,
            "selection_rule": SELECTION_RULE,
            "diagnostics": diagnostics,
            "representatives": representatives,
            "rows": theme_rows,
        }
        save_json(Path(args.output_file).expanduser(), payload)

        print("===== THEME AGGREGATION SUMMARY =====")
        print(f"captured_at={captured_at}")
        print(f"raw_top100_total_eok={diagnostics['raw_top100_total_eok']}")
        print(f"eligible_total_eok={diagnostics['eligible_total_eok']}")
        print(f"excluded_total_eok={diagnostics['excluded_total_eok']}")
        print(f"eligible_stock_count={diagnostics['eligible_stock_count']}")
        print(f"representative_count={diagnostics['representative_count']}")
        print(f"unclassified_stock_count={diagnostics['unclassified_stock_count']}")
        print(f"unclassified_total_eok={diagnostics['unclassified_total_eok']}")
        print(f"unclassified_share_top100_pct={diagnostics['unclassified_share_top100_pct']}")
        print(f"theme_count={diagnostics['theme_count']}")
        print(f"coverage_eligible_pct={diagnostics['coverage_eligible_pct']}")
        print(f"selection_rule={SELECTION_RULE}")

        if diagnostics["unclassified_stock_count"]:
            print()
            print("===== UNCLASSIFIED INCLUDE STOCKS =====")
            for r in diagnostics["unclassified_stocks"]:
                print(
                    f"{r['rank']:>3} {r['stock_code']} {r['stock_name']} "
                    f"amount_eok={r['trading_value_eok']:.2f}"
                )

        multi = [r for r in representatives if r["candidate_count"] > 1]
        print()
        print("===== MULTI-THEME REPRESENTATIVE DECISIONS =====")
        for r in multi:
            print(
                f"{r['rank']:>3} {r['stock_code']} {r['stock_name']} -> "
                f"{r['theme_code']}:{r['theme_name']} peer_support_eok={r['peer_support_eok']}"
            )
        if not multi:
            print("NONE")

        print()
        print("===== TOP THEMES =====")
        for row in theme_rows[:15]:
            print(
                f"{row['theme_rank']:>2} {row['theme_code']} {row['theme_name']} "
                f"amount_eok={row['trading_value_eok']:.2f} "
                f"share_top100={row['share_top100_pct']:.2f}% "
                f"stocks={row['stock_count']} top20={row['top20_count']} "
                f"avg_chg={row['avg_change_rate_pct']:.2f}% "
                f"leader={row['leader_stock_name']}"
            )

        if args.dry_run:
            print(f"[DRY-RUN] output_file={Path(args.output_file).expanduser()} webhook 전송 생략")
            return 0

        load_env_file(Path(args.project_env).expanduser())
        if not os.environ.get("WEBHOOK_URL", "").strip() or not os.environ.get("WEBHOOK_SECRET", "").strip():
            raise RuntimeError("WEBHOOK_URL 또는 WEBHOOK_SECRET 누락")
        payload["secret"] = os.environ["WEBHOOK_SECRET"].strip()
        result = post_webhook(payload)
        print(
            f"[OK] sheet={result.get('sheet')} themes={result.get('received')} "
            f"representatives={result.get('representatives')} captured_at={captured_at}"
        )
        return 0
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
