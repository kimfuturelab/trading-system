#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
HOME = Path.home()
DEFAULT_LATEST = HOME / ".market-flow-top100-latest.json"
DEFAULT_INSTRUMENT_CACHE = HOME / ".market-flow-instrument-cache.json"
DEFAULT_STATE = HOME / ".market-flow-theme-pipeline-state.json"
DEFAULT_AUTH_ENV = HOME / "api-read-v2.env"
DEFAULT_PROJECT_ENV = HOME / "market-flow.env"
DEFAULT_WORKDIR = HOME / "market-flow"


def load_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def atomic_write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def latest_codes(latest: dict) -> set[str]:
    rows = latest.get("rows") or []
    return {
        str(r.get("stock_code") or "").strip()
        for r in rows
        if isinstance(r, dict) and str(r.get("stock_code") or "").strip()
    }


def instrument_cache_covers(latest: dict, cache_path: Path) -> bool:
    cache = load_json(cache_path, {})
    rows = cache.get("rows") or [] if isinstance(cache, dict) else []
    by_code = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(row.get("stock_code") or "").strip()
        if code:
            by_code[code] = row

    targets = latest_codes(latest)
    if len(targets) != 100:
        return False
    if not targets.issubset(by_code.keys()):
        return False

    for code in targets:
        row = by_code[code]
        if str(row.get("instrument_type") or "UNKNOWN") == "UNKNOWN":
            return False
        if str(row.get("aggregation_policy") or "REVIEW") == "REVIEW":
            return False
    return True


def run_capture(cmd: list[str], timeout: int = 240) -> str:
    cp = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    text = ((cp.stdout or "") + ("\n" + cp.stderr if cp.stderr else "")).strip()
    if cp.returncode != 0:
        raise RuntimeError(f"command failed({cp.returncode}): {' '.join(cmd)}\n{text[-2000:]}")
    return text


def summary_line(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("rows="):
            return line.strip()
    return ""


def last_nonempty_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        if line.strip():
            return line.strip()
    return ""


def gate_passed(summary: str) -> bool:
    required = (
        "cache_missing=0",
        "review=0",
        "theme_enrichment=0",
        "unknown_type=0",
    )
    return bool(summary) and all(token in summary for token in required)


def process_once(
    latest_path: Path,
    instrument_cache_path: Path,
    state_path: Path,
    auth_env: Path,
    project_env: Path,
    workdir: Path,
    *,
    force: bool = False,
) -> bool:
    latest = load_json(latest_path)
    if not isinstance(latest, dict):
        print(f"[THEME-SKIP] latest file missing/invalid: {latest_path}", flush=True)
        return False

    captured_at = str(latest.get("captured_at") or "").strip()
    rows = latest.get("rows") or []
    if not captured_at or not isinstance(rows, list) or not rows:
        print("[THEME-SKIP] latest snapshot empty", flush=True)
        return False

    state = load_json(state_path, {}) or {}
    if not force and state.get("captured_at") == captured_at:
        return False

    status = "ERROR"
    detail = ""
    try:
        mapper_cmd = [
            sys.executable,
            str(workdir / "theme_mapper.py"),
            "--auth-env", str(auth_env),
            "--latest-top100-file", str(latest_path),
            "--sleep", "0.4",
        ]
        mapper_out = run_capture(mapper_cmd)
        print(f"[THEME-MAPPER] captured_at={captured_at} {last_nonempty_line(mapper_out)}", flush=True)

        if instrument_cache_covers(latest, instrument_cache_path):
            print("[THEME-CLASSIFIER] cache coverage=100/100 -> skip API refresh", flush=True)
        else:
            classifier_cmd = [
                sys.executable,
                str(workdir / "instrument_classifier.py"),
                "--auth-env", str(auth_env),
                "--latest-top100-file", str(latest_path),
                "--sleep", "0.3",
            ]
            classifier_out = run_capture(classifier_cmd)
            print(f"[THEME-CLASSIFIER] refreshed {last_nonempty_line(classifier_out)}", flush=True)

        dry_cmd = [
            sys.executable,
            str(workdir / "theme_map_exporter.py"),
            "--project-env", str(project_env),
            "--latest-top100-file", str(latest_path),
            "--dry-run",
        ]
        dry_out = run_capture(dry_cmd)
        summary = summary_line(dry_out)
        print(f"[THEME-GATE] {summary or 'summary missing'}", flush=True)

        if not gate_passed(summary):
            status = "GATE_FAILED"
            detail = summary or "summary missing"
            print(f"[THEME-STOP] gate failed captured_at={captured_at}", flush=True)
            return False

        map_cmd = [
            sys.executable,
            str(workdir / "theme_map_exporter.py"),
            "--project-env", str(project_env),
            "--latest-top100-file", str(latest_path),
        ]
        map_out = run_capture(map_cmd)
        print(f"[THEME-MAP] {last_nonempty_line(map_out)}", flush=True)

        agg_cmd = [
            sys.executable,
            str(workdir / "theme_aggregator.py"),
            "--project-env", str(project_env),
            "--latest-top100-file", str(latest_path),
        ]
        agg_out = run_capture(agg_cmd)
        print(f"[THEME-AGG] {last_nonempty_line(agg_out)}", flush=True)

        status = "OK"
        detail = summary
        print(f"[THEME-OK] captured_at={captured_at}", flush=True)
        return True

    except Exception as exc:
        detail = str(exc)[:1500]
        print(f"[THEME-ERROR] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return False

    finally:
        atomic_write_json(state_path, {
            "captured_at": captured_at,
            "status": status,
            "detail": detail,
            "checked_at": datetime.now(KST).isoformat(timespec="seconds"),
        })


def main() -> int:
    parser = argparse.ArgumentParser(description="3.5단계 테마 자동 파이프라인 감시기")
    parser.add_argument("--latest-top100-file", default=str(DEFAULT_LATEST))
    parser.add_argument("--instrument-cache-file", default=str(DEFAULT_INSTRUMENT_CACHE))
    parser.add_argument("--state-file", default=str(DEFAULT_STATE))
    parser.add_argument("--auth-env", default=str(DEFAULT_AUTH_ENV))
    parser.add_argument("--project-env", default=str(DEFAULT_PROJECT_ENV))
    parser.add_argument("--workdir", default=str(DEFAULT_WORKDIR))
    parser.add_argument("--poll-seconds", type=int, default=20)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.poll_seconds < 10:
        print("CONFIG ERROR: --poll-seconds must be >= 10", file=sys.stderr)
        return 2

    latest_path = Path(args.latest_top100_file).expanduser()
    instrument_cache_path = Path(args.instrument_cache_file).expanduser()
    state_path = Path(args.state_file).expanduser()
    auth_env = Path(args.auth_env).expanduser()
    project_env = Path(args.project_env).expanduser()
    workdir = Path(args.workdir).expanduser()

    required_files = [
        workdir / "theme_mapper.py",
        workdir / "instrument_classifier.py",
        workdir / "theme_map_exporter.py",
        workdir / "theme_aggregator.py",
        workdir / "theme_overrides.json",
        auth_env,
        project_env,
    ]
    missing = [str(p) for p in required_files if not p.exists()]
    if missing:
        print("CONFIG ERROR: missing files: " + ", ".join(missing), file=sys.stderr)
        return 2

    while True:
        process_once(
            latest_path,
            instrument_cache_path,
            state_path,
            auth_env,
            project_env,
            workdir,
            force=args.force,
        )

        if not args.loop:
            return 0
        args.force = False
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
