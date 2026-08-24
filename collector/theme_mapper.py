#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
DEFAULT_AUTH_ENV = Path.home() / "api-read-v2.env"
DEFAULT_CACHE_FILE = Path.home() / ".market-flow-theme-cache.json"
DEFAULT_LATEST_TOP100_FILE = Path.home() / ".market-flow-top100-latest.json"


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
        timeout=45,
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


def to_number(value) -> float | None:
    if value is None or value == "":
        return None
    text = str(value).replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def fetch_themes_for_stock(cli: str, stock_code: str) -> list[dict]:
    mode = os.environ.get("KIWOOM_MODE", "real").strip() or "real"
    data = run_cli(
        cli,
        "domestic", "themes", "lookup",
        "--kind", "stock",
        "--code", stock_code,
        "--days", "1",
        "--sort", "change-top",
        "--exchange", "ALL",
        "--pages", "0",
        "--mode", mode,
    )
    if data.get("return_code") not in (0, "0", None):
        raise RuntimeError(
            f"ka90001 stock={stock_code} 오류: {data.get('return_msg', 'unknown')}"
        )

    rows = data.get("thema_grp") or []
    themes: list[dict] = []
    for row in rows:
        themes.append({
            "theme_code": str(row.get("thema_grp_cd", "")).strip(),
            "theme_name": str(row.get("thema_nm", "")).strip(),
            "group_change_rate_pct": to_number(row.get("flu_rt")),
            "stock_count": int(to_number(row.get("stk_num")) or 0),
            "rising_stock_count": int(to_number(row.get("rising_stk_num")) or 0),
            "falling_stock_count": int(to_number(row.get("fall_stk_num")) or 0),
            "main_stocks": str(row.get("main_stk", "")).strip(),
        })
    return themes


def load_cache(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "stocks": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "stocks": {}}
    if not isinstance(data, dict):
        return {"version": 1, "stocks": {}}
    if not isinstance(data.get("stocks"), dict):
        data["stocks"] = {}
    data["version"] = 1
    return data


def save_cache(path: Path, cache: dict) -> None:
    cache["updated_at"] = datetime.now(KST).isoformat(timespec="seconds")
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def parse_checked_at(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(value)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def cache_is_fresh(entry: dict, max_age_hours: float) -> bool:
    checked = parse_checked_at(str(entry.get("checked_at", "")))
    if checked is None:
        return False
    return datetime.now(KST) - checked <= timedelta(hours=max_age_hours)


def load_latest_top100(path: Path) -> tuple[str, list[dict]]:
    if not path.exists():
        raise RuntimeError(
            f"최신 TOP100 파일 없음: {path}. market-flow collector를 1회 실행해 파일을 먼저 생성하세요."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"최신 TOP100 파일 파싱 실패: {exc}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("최신 TOP100 파일 형식이 잘못되었습니다.")
    rows = payload.get("rows") or []
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("최신 TOP100 파일에 rows가 없습니다.")

    targets: list[dict] = []
    for row in rows[:100]:
        if not isinstance(row, dict):
            continue
        code = canonical_stock_code(row.get("stock_code") or row.get("raw_stock_code") or "")
        if not code:
            continue
        targets.append({
            "stock_code": code,
            "raw_stock_code": str(row.get("raw_stock_code") or code),
            "stock_name": str(row.get("stock_name") or "").strip(),
            "rank": row.get("rank"),
            "captured_at": str(row.get("captured_at") or payload.get("captured_at") or ""),
            "current_price": row.get("current_price"),
            "change_rate_pct": row.get("change_rate_pct"),
            "trading_value_eok": row.get("trading_value_eok"),
        })
    targets.sort(key=lambda row: row.get("rank") if row.get("rank") is not None else 9999)
    return str(payload.get("captured_at") or ""), targets[:100]


def resolve_targets(codes_arg: str, latest_path: Path, cache: dict) -> tuple[str, list[dict]]:
    latest_captured_at = ""
    latest_rows: list[dict] = []
    if latest_path.exists():
        try:
            latest_captured_at, latest_rows = load_latest_top100(latest_path)
        except Exception:
            latest_rows = []

    latest_by_code = {row["stock_code"]: row for row in latest_rows}

    if codes_arg.strip():
        requested = [canonical_stock_code(x) for x in codes_arg.split(",") if x.strip()]
        targets: list[dict] = []
        for code in requested:
            if not code:
                continue
            if code in latest_by_code:
                targets.append(latest_by_code[code])
                continue
            entry = cache.get("stocks", {}).get(code, {})
            targets.append({
                "stock_code": code,
                "raw_stock_code": code,
                "stock_name": str(entry.get("stock_name", "")),
                "rank": entry.get("top100_rank"),
                "captured_at": entry.get("top100_captured_at", ""),
                "current_price": entry.get("current_price"),
                "change_rate_pct": entry.get("change_rate_pct"),
                "trading_value_eok": entry.get("trading_value_eok"),
            })
        return latest_captured_at, targets

    if not latest_rows:
        latest_captured_at, latest_rows = load_latest_top100(latest_path)
    return latest_captured_at, latest_rows


def update_entry_market_snapshot(entry: dict, target: dict) -> bool:
    changed = False
    fields = {
        "raw_stock_code": target.get("raw_stock_code"),
        "stock_name": target.get("stock_name"),
        "top100_rank": target.get("rank"),
        "top100_captured_at": target.get("captured_at"),
        "current_price": target.get("current_price"),
        "change_rate_pct": target.get("change_rate_pct"),
        "trading_value_eok": target.get("trading_value_eok"),
    }
    for key, value in fields.items():
        if value in (None, "") and key == "stock_name":
            continue
        if entry.get(key) != value:
            entry[key] = value
            changed = True
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description="3.5단계 TOP100 종목→키움 등록테마 캐시")
    parser.add_argument("--auth-env", default=str(DEFAULT_AUTH_ENV))
    parser.add_argument("--cache-file", default=str(DEFAULT_CACHE_FILE))
    parser.add_argument("--latest-top100-file", default=str(DEFAULT_LATEST_TOP100_FILE))
    parser.add_argument("--codes", default="", help="쉼표 구분 종목코드. 비우면 collector의 최신 TOP100 전체 스냅샷 사용")
    parser.add_argument("--limit", type=int, default=0, help="이번 실행에서 최대 신규조회 종목 수. 0=제한없음")
    parser.add_argument("--sleep", type=float, default=0.4, help="종목별 API 호출 간 대기초")
    parser.add_argument("--max-age-hours", type=float, default=24.0)
    parser.add_argument("--force", action="store_true", help="캐시가 신선해도 재조회")
    args = parser.parse_args()

    if args.limit < 0:
        print("CONFIG ERROR: --limit must be >= 0", file=sys.stderr)
        return 2
    if args.sleep < 0:
        print("CONFIG ERROR: --sleep must be >= 0", file=sys.stderr)
        return 2
    if args.max_age_hours <= 0:
        print("CONFIG ERROR: --max-age-hours must be > 0", file=sys.stderr)
        return 2

    try:
        load_env_file(Path(args.auth_env))
        require_env(["KIWOOM_MODE", "APP_KEY", "APP_SECRET"])
        cli = find_cli()
        cache_path = Path(args.cache_file).expanduser()
        latest_path = Path(args.latest_top100_file).expanduser()
        cache = load_cache(cache_path)
        latest_captured_at, targets = resolve_targets(args.codes, latest_path, cache)
    except Exception as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2

    queried = 0
    reused = 0
    zero_theme = 0
    multi_theme = 0
    cache_changed = False

    if latest_captured_at:
        print(f"latest_top100_captured_at={latest_captured_at}")

    for target in targets:
        code = target["stock_code"]
        entry = cache["stocks"].get(code, {})
        if not args.force and entry and cache_is_fresh(entry, args.max_age_hours):
            themes = entry.get("themes") or []
            reused += 1
            if update_entry_market_snapshot(entry, target):
                cache_changed = True

            display_name = entry.get("stock_name", "") or target.get("stock_name", "")
            print(f"[CACHE] {code} {display_name} themes={len(themes)}")
            if len(themes) == 0:
                zero_theme += 1
            elif len(themes) > 1:
                multi_theme += 1
            continue

        if args.limit and queried >= args.limit:
            continue

        try:
            themes = fetch_themes_for_stock(cli, code)
        except Exception as exc:
            print(f"[ERROR] {code} {target.get('stock_name','')}: {exc}", file=sys.stderr, flush=True)
            continue

        checked_at = datetime.now(KST).isoformat(timespec="seconds")
        old_name = str(entry.get("stock_name", "")).strip() if entry else ""
        stock_name = str(target.get("stock_name", "")).strip() or old_name
        new_entry = {
            "stock_code": code,
            "checked_at": checked_at,
            "themes": themes,
        }
        update_entry_market_snapshot(new_entry, {**target, "stock_name": stock_name})
        cache["stocks"][code] = new_entry
        save_cache(cache_path, cache)
        queried += 1
        cache_changed = False

        theme_text = ", ".join(
            f"{theme['theme_code']}:{theme['theme_name']}" for theme in themes
        ) or "NO_THEME"
        print(f"[QUERY] {code} {stock_name} themes={len(themes)} {theme_text}", flush=True)

        if len(themes) == 0:
            zero_theme += 1
        elif len(themes) > 1:
            multi_theme += 1

        if args.sleep:
            time.sleep(args.sleep)

    if cache_changed:
        save_cache(cache_path, cache)

    print()
    print("===== THEME CACHE SUMMARY =====")
    print(f"targets={len(targets)}")
    print(f"queried={queried}")
    print(f"cache_reused={reused}")
    print(f"zero_theme={zero_theme}")
    print(f"multi_theme={multi_theme}")
    print(f"cache_file={cache_path}")
    print(f"latest_top100_file={latest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
