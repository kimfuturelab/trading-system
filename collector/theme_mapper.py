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
DEFAULT_RANK_STATE_FILE = Path.home() / ".market-flow-rank-state.json"


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
            "falling_stock_count": int(to_number(row.get("falling_stk_num")) or 0),
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


def load_rank_state(path: Path) -> list[dict]:
    if not path.exists():
        raise RuntimeError(
            f"TOP100 순위 상태파일 없음: {path}. market-flow collector가 먼저 정상 수집되어야 합니다."
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"TOP100 순위 상태파일 파싱 실패: {exc}") from exc
    if not isinstance(raw, dict) or not raw:
        raise RuntimeError("TOP100 순위 상태파일이 비어 있거나 형식이 잘못되었습니다.")

    targets: list[dict] = []
    for raw_code, raw_rank in raw.items():
        code = canonical_stock_code(raw_code)
        if not code:
            continue
        try:
            rank = int(raw_rank)
        except (TypeError, ValueError):
            rank = None
        targets.append({
            "stock_code": code,
            "raw_stock_code": str(raw_code),
            "stock_name": "",
            "rank": rank,
        })
    targets.sort(key=lambda row: row.get("rank") if row.get("rank") is not None else 9999)
    return targets[:100]


def resolve_targets(codes_arg: str, rank_state_path: Path) -> list[dict]:
    if codes_arg.strip():
        requested = [canonical_stock_code(x) for x in codes_arg.split(",") if x.strip()]
        return [
            {
                "stock_code": code,
                "raw_stock_code": code,
                "stock_name": "",
                "rank": None,
            }
            for code in requested
            if code
        ]
    return load_rank_state(rank_state_path)


def main() -> int:
    parser = argparse.ArgumentParser(description="3.5단계 TOP100 종목→키움 등록테마 캐시")
    parser.add_argument("--auth-env", default=str(DEFAULT_AUTH_ENV))
    parser.add_argument("--cache-file", default=str(DEFAULT_CACHE_FILE))
    parser.add_argument("--rank-state-file", default=str(DEFAULT_RANK_STATE_FILE))
    parser.add_argument("--codes", default="", help="쉼표 구분 종목코드. 비우면 collector의 최신 TOP100 순위 상태파일 사용")
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
        rank_state_path = Path(args.rank_state_file).expanduser()
        cache = load_cache(cache_path)
        targets = resolve_targets(args.codes, rank_state_path)
    except Exception as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2

    queried = 0
    reused = 0
    zero_theme = 0
    multi_theme = 0
    cache_changed = False

    for target in targets:
        code = target["stock_code"]
        entry = cache["stocks"].get(code, {})
        if not args.force and entry and cache_is_fresh(entry, args.max_age_hours):
            themes = entry.get("themes") or []
            reused += 1

            if target.get("rank") is not None and entry.get("top100_rank") != target.get("rank"):
                entry["top100_rank"] = target.get("rank")
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
        cache["stocks"][code] = {
            "stock_code": code,
            "raw_stock_code": target.get("raw_stock_code", code),
            "stock_name": stock_name,
            "top100_rank": target.get("rank"),
            "checked_at": checked_at,
            "themes": themes,
        }
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
    print(f"rank_state_file={rank_state_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
