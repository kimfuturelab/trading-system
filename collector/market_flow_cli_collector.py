#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
STATE_FILE = Path.home() / ".market-flow-rank-state.json"
LATEST_TOP100_FILE = Path.home() / ".market-flow-top100-latest.json"
DEFAULT_AUTH_ENV = Path.home() / "api-read-v2.env"
DEFAULT_PROJECT_ENV = Path.home() / "market-flow.env"


def load_env_file(path: Path, *, required: bool = True) -> None:
    if not path.exists():
        if required:
            raise RuntimeError(f"환경파일 없음: {path}")
        return
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


def to_number(value, *, absolute: bool = False) -> float | None:
    if value is None or value == "":
        return None
    text = str(value).replace(",", "").strip()
    try:
        number = float(text)
    except ValueError:
        return None
    return abs(number) if absolute else number


def to_int(value, *, absolute: bool = False) -> int | None:
    number = to_number(value, absolute=absolute)
    return None if number is None else int(round(number))


def canonical_stock_code(raw_code: str) -> str:
    code = raw_code.strip()
    if "_" in code:
        code = code.split("_", 1)[0]
    return code


def load_previous_ranks() -> dict[str, int]:
    if not STATE_FILE.exists():
        return {}
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return {str(k): int(v) for k, v in raw.items()}
    except Exception:
        return {}


def atomic_write_json(path: Path, payload: object) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def save_current_ranks(rows: list[dict]) -> None:
    state = {
        str(row["stock_code"]): int(row["rank"])
        for row in rows
        if row.get("stock_code") and row.get("rank") is not None
    }
    atomic_write_json(STATE_FILE, state)


def save_latest_top100(rows: list[dict]) -> None:
    payload = {
        "version": 1,
        "captured_at": rows[0].get("captured_at", "") if rows else "",
        "row_count": len(rows),
        "rows": rows,
    }
    atomic_write_json(LATEST_TOP100_FILE, payload)


def fetch_top100(cli: str) -> list[dict]:
    mode = os.environ.get("KIWOOM_MODE", "real").strip() or "real"
    data = run_cli(
        cli,
        "domestic", "rankings", "amount",
        "--market", "all",
        "--include-managed", "no",
        "--exchange", "ALL",
        "--pages", "1",
        "--mode", mode,
    )
    if data.get("return_code") not in (0, "0", None):
        raise RuntimeError(f"ka10032 오류: {data.get('return_msg', 'unknown')}")
    rows = data.get("trde_prica_upper") or []
    if not isinstance(rows, list):
        raise RuntimeError("ka10032 응답 형식이 예상과 다릅니다.")
    return rows[:100]


def normalize_top100(raw_rows: list[dict]) -> list[dict]:
    previous = load_previous_ranks()
    captured_at = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    divisor = float(os.environ.get("TRADING_VALUE_DIVISOR", "100"))
    if divisor <= 0:
        raise RuntimeError("TRADING_VALUE_DIVISOR는 0보다 커야 합니다.")

    normalized: list[dict] = []
    for item in raw_rows:
        raw_code = str(item.get("stk_cd", "")).strip()
        code = canonical_stock_code(raw_code)
        if not code:
            continue
        rank = to_int(item.get("now_rank"))
        prev_rank = previous.get(code)
        rank_change = None if rank is None or prev_rank is None else prev_rank - rank
        amount_raw = to_number(item.get("trde_prica"), absolute=True)
        normalized.append({
            "captured_at": captured_at,
            "rank": rank,
            "raw_stock_code": raw_code,
            "stock_code": code,
            "stock_name": str(item.get("stk_nm", "")).strip(),
            "market": "ALL",
            "exchange": "ALL",
            "current_price": to_int(item.get("cur_prc"), absolute=True),
            "change_rate_pct": to_number(item.get("flu_rt")),
            "trading_volume": to_int(item.get("now_trde_qty"), absolute=True),
            "trading_value_raw": amount_raw,
            "trading_value_eok": None if amount_raw is None else amount_raw / divisor,
            "previous_snapshot_rank": prev_rank,
            "rank_change": rank_change,
            "previous_day_rank": to_int(item.get("pred_rank")),
            "status": "OK",
        })
    normalized.sort(key=lambda row: row.get("rank") or 9999)
    return normalized


def print_summary(rows: list[dict]) -> None:
    print(f"row_count={len(rows)}", flush=True)
    for row in rows[:10]:
        print(
            f"{row.get('rank'):>3} {row.get('raw_stock_code',''):>10} "
            f"{row.get('stock_name',''):<18} price={row.get('current_price')} "
            f"rate={row.get('change_rate_pct')} amount_raw={row.get('trading_value_raw')} "
            f"amount_eok={row.get('trading_value_eok')}",
            flush=True,
        )


def post_webhook(rows: list[dict]) -> dict:
    require_env(["WEBHOOK_URL", "WEBHOOK_SECRET"])
    payload = {
        "secret": os.environ["WEBHOOK_SECRET"].strip(),
        "type": "top100",
        "source": "kiwoomcli-ka10032",
        "captured_at": rows[0]["captured_at"] if rows else datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
        "row_count": len(rows),
        "rows": rows,
    }
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
    result = json.loads(text)
    if not result.get("ok"):
        raise RuntimeError(f"Webhook 오류: {result.get('error', 'unknown')}")
    return result


def hhmm_to_minutes(value: str) -> int:
    text = value.strip().replace(":", "")
    if len(text) != 4 or not text.isdigit():
        raise RuntimeError(f"잘못된 HHMM 값: {value}")
    hour, minute = int(text[:2]), int(text[2:])
    return hour * 60 + minute


def market_window_open(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    start = hhmm_to_minutes(os.environ.get("COLLECT_START_HHMM", "0855"))
    end = hhmm_to_minutes(os.environ.get("COLLECT_END_HHMM", "1535"))
    current = now.hour * 60 + now.minute
    return start <= current <= end


def collect_once(cli: str, *, dry_run: bool = False, bypass_market_guard: bool = False) -> bool:
    now = datetime.now(KST)
    if not bypass_market_guard and not market_window_open(now):
        print(f"[SKIP] 수집시간 아님 {now:%Y-%m-%d %H:%M:%S}", flush=True)
        return False

    raw_rows = fetch_top100(cli)
    rows = normalize_top100(raw_rows)
    if len(rows) != 100:
        print(f"[WARN] TOP100 응답이 100행이 아님: {len(rows)}", flush=True)
    if not rows:
        raise RuntimeError("사용 가능한 TOP100 데이터가 없습니다.")

    # theme_mapper가 TOP100 API를 다시 호출하지 않도록 최신 전체 스냅샷을 항상 로컬에 보존합니다.
    save_latest_top100(rows)

    print_summary(rows)
    if dry_run:
        print(f"[DRY-RUN] Webhook 전송 생략 latest_file={LATEST_TOP100_FILE}", flush=True)
        return True

    result = post_webhook(rows)
    save_current_ranks(rows)
    print(
        f"[OK] recorded={result.get('received', result.get('recorded'))} "
        f"captured_at={rows[0]['captured_at']} latest_file={LATEST_TOP100_FILE}",
        flush=True,
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="3.5단계 자금흐름 TOP100 collector")
    parser.add_argument("--auth-env", default=str(DEFAULT_AUTH_ENV))
    parser.add_argument("--project-env", default=str(DEFAULT_PROJECT_ENV))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--bypass-market-guard", action="store_true")
    args = parser.parse_args()

    try:
        load_env_file(Path(args.auth_env), required=True)
        load_env_file(Path(args.project_env), required=not args.dry_run)
        require_env(["KIWOOM_MODE", "APP_KEY", "APP_SECRET"])
        cli = find_cli()
    except Exception as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2

    poll_seconds = int(os.environ.get("POLL_SECONDS", "300"))
    if poll_seconds < 30:
        print("CONFIG ERROR: POLL_SECONDS must be >= 30", file=sys.stderr)
        return 2

    while True:
        try:
            collect_once(cli, dry_run=args.dry_run, bypass_market_guard=args.bypass_market_guard)
        except KeyboardInterrupt:
            print("[STOP] 사용자 중지", flush=True)
            return 0
        except Exception as exc:
            print(f"[ERROR] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            if not args.loop:
                return 1

        if not args.loop:
            return 0
        time.sleep(poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
