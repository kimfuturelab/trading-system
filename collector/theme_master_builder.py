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
DEFAULT_MASTER_FILE = Path.home() / ".market-flow-theme-master.json"
DEFAULT_INSTRUMENT_FILE = Path.home() / ".market-flow-instrument-cache.json"


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
        raise RuntimeError(f"kiwoomcli 실패(code={cp.returncode}): {msg[:900]}")

    out = cp.stdout.strip()
    if not out:
        raise RuntimeError("kiwoomcli 응답이 비어 있습니다.")

    starts = [i for i in (out.find("{"), out.find("[")) if i >= 0]
    if starts:
        out = out[min(starts):]

    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"kiwoomcli JSON 파싱 실패: {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeError("kiwoomcli 응답이 JSON object가 아닙니다.")
    return data


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


def to_int(value) -> int:
    number = to_number(value)
    return int(number) if number is not None else 0


def now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def parse_datetime(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value or ""))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(KST)


def is_fresh(value: str, max_age_hours: float) -> bool:
    checked = parse_datetime(value)
    if checked is None:
        return False
    return datetime.now(KST) - checked <= timedelta(hours=max_age_hours)


def atomic_write_json(path: Path, payload: object) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(path)


def load_master(path: Path) -> dict:
    if not path.exists():
        return {
            "version": 1,
            "source": "kiwoom-ka90001+ka90002",
            "themes": {},
            "reverse_index": {},
        }

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "version": 1,
            "source": "kiwoom-ka90001+ka90002",
            "themes": {},
            "reverse_index": {},
        }

    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("themes"), dict):
        data["themes"] = {}
    if not isinstance(data.get("reverse_index"), dict):
        data["reverse_index"] = {}
    data["version"] = 1
    data["source"] = "kiwoom-ka90001+ka90002"
    return data


def fetch_all_themes(cli: str) -> list[dict]:
    mode = os.environ.get("KIWOOM_MODE", "real").strip() or "real"
    data = run_cli(
        cli,
        "domestic", "themes", "lookup",
        "--kind", "all",
        "--days", "1",
        "--sort", "change-top",
        "--exchange", "ALL",
        "--pages", "0",
        "--mode", mode,
    )

    if data.get("return_code") not in (0, "0", None):
        raise RuntimeError(
            f"ka90001 all 오류: {data.get('return_msg', 'unknown')}"
        )

    raw_rows = data.get("thema_grp") or []
    rows: list[dict] = []
    seen: set[str] = set()

    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("thema_grp_cd") or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        rows.append({
            "theme_code": code,
            "theme_name": str(row.get("thema_nm") or "").strip(),
            "reported_stock_count": to_int(row.get("stk_num")),
            "group_change_rate_pct": to_number(row.get("flu_rt")),
            "rising_stock_count": to_int(row.get("rising_stk_num")),
            "falling_stock_count": to_int(row.get("fall_stk_num")),
            "main_stocks": str(row.get("main_stk") or "").strip(),
        })

    return rows


def fetch_theme_members(cli: str, theme_code: str) -> list[dict]:
    mode = os.environ.get("KIWOOM_MODE", "real").strip() or "real"
    data = run_cli(
        cli,
        "domestic", "themes", "by-stock",
        "--code", theme_code,
        "--exchange", "ALL",
        "--days", "1",
        "--pages", "0",
        "--mode", mode,
    )

    if data.get("return_code") not in (0, "0", None):
        raise RuntimeError(
            f"ka90002 theme={theme_code} 오류: {data.get('return_msg', 'unknown')}"
        )

    members: list[dict] = []
    seen: set[str] = set()

    for row in data.get("thema_comp_stk") or []:
        if not isinstance(row, dict):
            continue
        raw_code = str(row.get("stk_cd") or "").strip()
        code = canonical_stock_code(raw_code)
        if not code or code in seen:
            continue
        seen.add(code)
        members.append({
            "stock_code": code,
            "raw_stock_code": raw_code or code,
            "stock_name": str(row.get("stk_nm") or "").strip(),
        })

    return members


def merge_catalog(master: dict, catalog: list[dict]) -> None:
    themes = master.setdefault("themes", {})
    active_codes: list[str] = []

    for meta in catalog:
        code = meta["theme_code"]
        active_codes.append(code)
        old = themes.get(code) if isinstance(themes.get(code), dict) else {}
        members = old.get("members") if isinstance(old.get("members"), list) else []
        checked_at = str(old.get("members_checked_at") or "")

        themes[code] = {
            **meta,
            "members_checked_at": checked_at,
            "member_count_actual": len(members) if checked_at else 0,
            "members": members,
        }

    master["active_theme_codes"] = active_codes
    master["theme_count"] = len(catalog)
    master["catalog_checked_at"] = now_iso()


def build_reverse_index(master: dict) -> dict[str, list[dict]]:
    reverse: dict[str, list[dict]] = {}

    for theme_code in master.get("active_theme_codes") or []:
        theme = master.get("themes", {}).get(theme_code, {})
        if not isinstance(theme, dict):
            continue
        theme_name = str(theme.get("theme_name") or "").strip()
        for member in theme.get("members") or []:
            if not isinstance(member, dict):
                continue
            code = canonical_stock_code(member.get("stock_code") or member.get("raw_stock_code") or "")
            if not code:
                continue
            reverse.setdefault(code, []).append({
                "theme_code": str(theme_code),
                "theme_name": theme_name,
            })

    for code, items in reverse.items():
        unique = {
            (str(x.get("theme_code") or ""), str(x.get("theme_name") or "")): x
            for x in items
        }
        reverse[code] = [
            unique[key]
            for key in sorted(unique.keys(), key=lambda x: (x[0], x[1]))
        ]

    return reverse


def load_enrichment_targets(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    rows = data.get("rows") or [] if isinstance(data, dict) else []
    targets: list[dict] = []

    for row in rows:
        if not isinstance(row, dict) or row.get("needs_theme_enrichment") is not True:
            continue
        code = canonical_stock_code(row.get("stock_code") or row.get("raw_stock_code") or "")
        if not code:
            continue
        targets.append({
            "rank": row.get("rank"),
            "stock_code": code,
            "stock_name": str(row.get("stock_name") or "").strip(),
            "trading_value_eok": row.get("trading_value_eok"),
        })

    targets.sort(key=lambda r: r.get("rank") if r.get("rank") is not None else 9999)
    return targets


def analyze_targets(master: dict, targets: list[dict]) -> dict:
    reverse = master.get("reverse_index") or {}
    rows: list[dict] = []
    recovered = 0

    for target in targets:
        code = target["stock_code"]
        themes = reverse.get(code) or []
        if themes:
            recovered += 1
        rows.append({
            **target,
            "themes": themes,
            "theme_count": len(themes),
            "status": "FOUND_IN_KA90002" if themes else "STILL_NO_THEME",
        })

    return {
        "target_count": len(targets),
        "recovered_count": recovered,
        "still_missing_count": len(targets) - recovered,
        "rows": rows,
    }


def save_progress(master_file: Path, master: dict, targets: list[dict]) -> None:
    reverse = build_reverse_index(master)
    master["reverse_index"] = reverse
    master["reverse_stock_count"] = len(reverse)
    master["target_analysis"] = analyze_targets(master, targets)
    master["updated_at"] = now_iso()
    atomic_write_json(master_file, master)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Kiwoom 142-theme master + member reverse index builder"
    )
    parser.add_argument("--auth-env", default=str(DEFAULT_AUTH_ENV))
    parser.add_argument("--master-file", default=str(DEFAULT_MASTER_FILE))
    parser.add_argument("--instrument-file", default=str(DEFAULT_INSTRUMENT_FILE))
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="이번 실행에서 새로 호출할 ka90002 최대 개수. 0=제한 없음",
    )
    parser.add_argument("--sleep", type=float, default=0.6)
    parser.add_argument("--max-age-hours", type=float, default=24.0)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    auth_env = Path(args.auth_env).expanduser()
    master_file = Path(args.master_file).expanduser()
    instrument_file = Path(args.instrument_file).expanduser()

    load_env_file(auth_env)
    cli = find_cli()

    master = load_master(master_file)
    targets = load_enrichment_targets(instrument_file)

    catalog = fetch_all_themes(cli)
    if not catalog:
        raise RuntimeError("ka90001 전체 테마 목록이 비어 있습니다.")

    merge_catalog(master, catalog)

    print(f"theme_count={len(catalog)}")
    print(f"enrichment_targets={len(targets)}")
    print(f"master_file={master_file}")

    queried = 0
    cache_reused = 0
    deferred = 0
    errors: list[dict] = []

    themes = master["themes"]

    for index, meta in enumerate(catalog, start=1):
        code = meta["theme_code"]
        name = meta["theme_name"]
        entry = themes[code]

        checked_at = str(entry.get("members_checked_at") or "")
        fresh = (
            not args.force
            and checked_at
            and is_fresh(checked_at, args.max_age_hours)
        )

        if fresh:
            cache_reused += 1
            print(
                f"[CACHE] {index:3}/{len(catalog)} {code} {name} "
                f"members={len(entry.get('members') or [])}"
            )
            continue

        if args.limit > 0 and queried >= args.limit:
            deferred += 1
            continue

        try:
            members = fetch_theme_members(cli, code)
            entry["members"] = members
            entry["member_count_actual"] = len(members)
            entry["members_checked_at"] = now_iso()
            entry.pop("last_error", None)
            queried += 1
            print(
                f"[QUERY] {index:3}/{len(catalog)} {code} {name} "
                f"members={len(members)}"
            )
        except Exception as exc:
            error = {
                "theme_code": code,
                "theme_name": name,
                "error": str(exc),
                "at": now_iso(),
            }
            entry["last_error"] = error
            errors.append(error)
            queried += 1
            print(f"[ERROR] {index:3}/{len(catalog)} {code} {name}: {exc}")

        save_progress(master_file, master, targets)

        if args.sleep > 0:
            time.sleep(args.sleep)

    save_progress(master_file, master, targets)

    fetched_themes = sum(
        1
        for code in master.get("active_theme_codes") or []
        if str(master.get("themes", {}).get(code, {}).get("members_checked_at") or "")
    )
    analysis = master.get("target_analysis") or {}

    print()
    print("===== THEME MASTER SUMMARY =====")
    print(f"themes_total={len(catalog)}")
    print(f"themes_with_members_snapshot={fetched_themes}")
    print(f"queried={queried}")
    print(f"cache_reused={cache_reused}")
    print(f"deferred={deferred}")
    print(f"errors={len(errors)}")
    print(f"reverse_stock_count={master.get('reverse_stock_count', 0)}")
    print(f"target_count={analysis.get('target_count', 0)}")
    print(f"recovered_in_ka90002={analysis.get('recovered_count', 0)}")
    print(f"still_missing={analysis.get('still_missing_count', 0)}")
    print(f"master_file={master_file}")

    if targets:
        print()
        print("===== NO_THEME TARGET RECHECK =====")
        for row in analysis.get("rows") or []:
            themes_found = row.get("themes") or []
            theme_text = ", ".join(
                f"{t.get('theme_code')}:{t.get('theme_name')}"
                for t in themes_found
            ) or "NO_MATCH"
            status = "FOUND" if themes_found else "MISS"
            rank = row.get("rank")
            rank_text = str(rank).rjust(3) if rank is not None else "  -"
            print(
                f"[{status}] {rank_text} {row.get('stock_code')} "
                f"{row.get('stock_name')} -> {theme_text}"
            )

    if errors:
        print()
        print("일부 테마 조회 오류가 있어 캐시에 성공분만 저장했습니다. 재실행하면 이어서 조회합니다.")
        return 2

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[STOP] 사용자 중단")
        raise SystemExit(130)
    except Exception as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        raise SystemExit(1)
