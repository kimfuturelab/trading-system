#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
DEFAULT_PROJECT_ENV = Path.home() / "market-flow.env"
DEFAULT_CACHE_FILE = Path.home() / ".market-flow-theme-cache.json"
DEFAULT_LATEST_TOP100_FILE = Path.home() / ".market-flow-top100-latest.json"
DEFAULT_INSTRUMENT_CACHE_FILE = Path.home() / ".market-flow-instrument-cache.json"
DEFAULT_OVERRIDE_FILE = Path.home() / "market-flow" / "theme_overrides.json"


def load_env_file(path: Path) -> None:
    if not path.exists():
        raise RuntimeError(f"환경파일 없음: {path}")
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip()


def require_env(names: list[str]) -> None:
    missing = [name for name in names if not os.environ.get(name, "").strip()]
    if missing:
        raise RuntimeError("환경변수 누락: " + ", ".join(missing))


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


def load_optional_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return load_json(path)


def build_instrument_index(instrument_cache: dict) -> dict[str, dict]:
    rows = instrument_cache.get("rows") or []
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("종목유형 캐시 rows가 비어 있습니다.")
    index: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("stock_code", "")).strip()
        if code:
            index[code] = row
    return index


def build_override_index(override_payload: dict) -> tuple[dict[str, dict], str]:
    overrides = override_payload.get("overrides") or {}
    reviewed_at = str(override_payload.get("reviewed_at") or "").strip()
    if not isinstance(overrides, dict):
        raise RuntimeError("보조 테마 overrides 형식이 잘못되었습니다.")

    index: dict[str, dict] = {}
    for raw_code, row in overrides.items():
        if not isinstance(row, dict):
            continue
        code = str(raw_code or "").strip()
        theme_code = str(row.get("theme_code") or "").strip()
        theme_name = str(row.get("theme_name") or "").strip()
        if not code or not theme_code or not theme_name:
            continue
        index[code] = row
    return index, reviewed_at


def build_rows(
    latest: dict,
    cache: dict,
    instrument_cache: dict,
    override_payload: dict,
) -> list[dict]:
    top_rows = latest.get("rows") or []
    stocks = cache.get("stocks") or {}
    instruments = build_instrument_index(instrument_cache)
    overrides, override_reviewed_at = build_override_index(override_payload)

    if not isinstance(top_rows, list) or not top_rows:
        raise RuntimeError("최신 TOP100 rows가 비어 있습니다.")
    if not isinstance(stocks, dict):
        raise RuntimeError("테마 캐시 stocks 형식이 잘못되었습니다.")

    result: list[dict] = []

    for row in top_rows[:100]:
        code = str(row.get("stock_code", "")).strip()
        if not code:
            continue

        entry = stocks.get(code) or {}
        inst = instruments.get(code) or {}
        themes = entry.get("themes") or []
        if not isinstance(themes, list):
            themes = []

        checked_at = str(entry.get("checked_at", "")).strip()
        instrument_type = str(inst.get("instrument_type") or "UNKNOWN").strip()
        aggregation_policy = str(inst.get("aggregation_policy") or "REVIEW").strip()
        needs_theme_enrichment = bool(inst.get("needs_theme_enrichment", False))
        status = "KIWOOM_THEME" if themes else ("NO_THEME" if entry else "CACHE_MISSING")
        mapping_source = "kiwoom-ka90001-stock-cache"
        supplemental = False
        supplemental_confidence = ""

        # 보조 테마는 다음 조건에서만 사용한다.
        # 1) 키움 공식 테마가 0개
        # 2) 실제 집계 대상 일반주(INCLUDE)
        # 키움 공식 테마가 하나라도 있으면 절대 덮어쓰지 않는다.
        override = overrides.get(code)
        if (
            not themes
            and aggregation_policy == "INCLUDE"
            and isinstance(override, dict)
        ):
            themes = [
                {
                    "theme_code": str(override.get("theme_code") or "").strip(),
                    "theme_name": str(override.get("theme_name") or "").strip(),
                    "supplemental": True,
                    "confidence": str(override.get("confidence") or "").strip(),
                }
            ]
            status = "SUPPLEMENTAL_THEME"
            mapping_source = "manual-override-v1"
            needs_theme_enrichment = False
            supplemental = True
            supplemental_confidence = str(override.get("confidence") or "").strip()
            if override_reviewed_at:
                checked_at = override_reviewed_at

        result.append({
            "rank": row.get("rank"),
            "stock_code": code,
            "stock_name": str(row.get("stock_name", "")).strip()
            or str(entry.get("stock_name", "")).strip(),
            "theme_count": len(themes),
            "themes": themes,
            "mapping_status": status,
            "mapping_source": mapping_source,
            "checked_at": checked_at,
            "instrument_type": instrument_type,
            "aggregation_policy": aggregation_policy,
            "needs_theme_enrichment": needs_theme_enrichment,
            "supplemental_theme": supplemental,
            "supplemental_confidence": supplemental_confidence,
        })

    result.sort(key=lambda r: (r.get("rank") is None, r.get("rank") or 9999))
    return result


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
    parser = argparse.ArgumentParser(
        description="3.5단계 테마/종목유형/보조테마 → Google Sheet exporter"
    )
    parser.add_argument("--project-env", default=str(DEFAULT_PROJECT_ENV))
    parser.add_argument("--cache-file", default=str(DEFAULT_CACHE_FILE))
    parser.add_argument("--latest-top100-file", default=str(DEFAULT_LATEST_TOP100_FILE))
    parser.add_argument("--instrument-cache-file", default=str(DEFAULT_INSTRUMENT_CACHE_FILE))
    parser.add_argument("--override-file", default=str(DEFAULT_OVERRIDE_FILE))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        load_env_file(Path(args.project_env).expanduser())
        require_env(["WEBHOOK_URL", "WEBHOOK_SECRET"])

        cache = load_json(Path(args.cache_file).expanduser())
        latest = load_json(Path(args.latest_top100_file).expanduser())
        instrument_cache = load_json(Path(args.instrument_cache_file).expanduser())
        override_payload = load_optional_json(Path(args.override_file).expanduser())

        rows = build_rows(latest, cache, instrument_cache, override_payload)
        if len(rows) != 100:
            print(f"[WARN] theme_map rows={len(rows)} (expected 100)", flush=True)

        zero_theme = sum(1 for r in rows if r["theme_count"] == 0)
        multi_theme = sum(1 for r in rows if r["theme_count"] > 1)
        missing = sum(1 for r in rows if r["mapping_status"] == "CACHE_MISSING")
        include = sum(1 for r in rows if r["aggregation_policy"] == "INCLUDE")
        exclude = sum(1 for r in rows if r["aggregation_policy"] == "EXCLUDE")
        review = sum(1 for r in rows if r["aggregation_policy"] == "REVIEW")
        enrich = sum(1 for r in rows if r["needs_theme_enrichment"])
        unknown = sum(1 for r in rows if r["instrument_type"] == "UNKNOWN")
        supplemental = sum(1 for r in rows if r.get("supplemental_theme"))

        print(
            f"rows={len(rows)} zero_theme={zero_theme} multi_theme={multi_theme} "
            f"cache_missing={missing} include={include} exclude={exclude} "
            f"review={review} theme_enrichment={enrich} unknown_type={unknown} "
            f"supplemental={supplemental}"
        )

        for r in rows[:10]:
            theme_text = ", ".join(
                f"{t.get('theme_code','')}:{t.get('theme_name','')}"
                for t in r.get("themes", [])
            ) or "NO_THEME"
            print(
                f"{r.get('rank'):>3} {r['stock_code']} {r['stock_name']} "
                f"type={r['instrument_type']} policy={r['aggregation_policy']} "
                f"enrich={'Y' if r['needs_theme_enrichment'] else 'N'} "
                f"themes={r['theme_count']} {theme_text}"
            )

        if supplemental:
            print()
            print("===== SUPPLEMENTAL THEME OVERRIDES =====")
            for r in rows:
                if not r.get("supplemental_theme"):
                    continue
                t = (r.get("themes") or [{}])[0]
                print(
                    f"[SUPP] {str(r.get('rank')).rjust(3)} {r['stock_code']} "
                    f"{r['stock_name']} -> {t.get('theme_code')}:{t.get('theme_name')} "
                    f"confidence={r.get('supplemental_confidence') or '-'}"
                )

        if args.dry_run:
            print("[DRY-RUN] Webhook 전송 생략")
            return 0

        payload = {
            "secret": os.environ["WEBHOOK_SECRET"].strip(),
            "type": "theme_map",
            "source": "theme+instrument+supplemental-override",
            "captured_at": latest.get("captured_at")
            or datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
            "row_count": len(rows),
            "rows": rows,
        }
        result = post_webhook(payload)
        print(
            f"[OK] sheet={result.get('sheet')} received={result.get('received')} "
            f"captured_at={payload['captured_at']}"
        )
        return 0
    except Exception as exc:
        print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
