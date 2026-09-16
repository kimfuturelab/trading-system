from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

KST = ZoneInfo("Asia/Seoul")
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CACHE_PATH = Path.home() / ".cache" / "trading-system" / "box_control.json"
CLIENT_VERSION = "box-control-sync-v1"


@dataclass(frozen=True)
class BoxControl:
    market: str
    effective_date: str
    box_high: float
    box_low: float
    box_signature: str
    source: str
    source_updated_at: str
    fetched_at: str
    status: str
    client_version: str = CLIENT_VERSION


def now_kst_string() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def env_first(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def load_runtime_env() -> tuple[str, str]:
    load_dotenv(BASE_DIR / ".env", override=False)
    for path in (
        Path.home() / "stage3-supply.env",
        Path.home() / "api-read-v2.env",
        Path.home() / "technical-position.env",
    ):
        if path.exists():
            load_dotenv(path, override=False)

    endpoint = env_first(
        "BOX_CONTROL_WEBHOOK_URL",
        "SHEETS_WEBHOOK_URL",
        "TECH_POSITION_WEBHOOK_URL",
        "WEBHOOK_URL",
    )
    secret = env_first(
        "BOX_CONTROL_INGEST_SECRET",
        "INGEST_SECRET",
        "TECH_POSITION_INGEST_SECRET",
        "WEBHOOK_SECRET",
    )

    if not endpoint:
        raise RuntimeError("missing BOX control Web App URL")
    if not secret:
        raise RuntimeError("missing BOX control secret")

    return endpoint, secret


def _number(value: Any, field: str) -> float:
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}: {value!r}") from exc
    if not (number == number):
        raise ValueError(f"invalid {field}: NaN")
    return number


def parse_box_control(payload: dict[str, Any], *, fetched_at: str | None = None) -> BoxControl:
    if not isinstance(payload, dict):
        raise ValueError("box control payload must be an object")
    if not payload.get("ok"):
        raise ValueError(f"box control not ok: {payload}")
    if payload.get("type") != "box_control":
        raise ValueError(f"unexpected type: {payload.get('type')!r}")

    market = str(payload.get("market") or "").strip().upper()
    if market != "KOSPI":
        raise ValueError(f"unsupported market: {market!r}")

    high = _number(payload.get("box_high"), "box_high")
    low = _number(payload.get("box_low"), "box_low")
    if high <= low:
        raise ValueError(f"invalid BOX range: high={high} low={low}")

    effective_date = str(payload.get("effective_date") or "").strip()
    if len(effective_date) != 10:
        raise ValueError(f"invalid effective_date: {effective_date!r}")

    expected_signature = f"{high:.12g}|{low:.12g}"
    supplied_signature = str(payload.get("box_signature") or "").strip()
    if supplied_signature and supplied_signature != expected_signature:
        raise ValueError(
            f"box_signature mismatch: supplied={supplied_signature!r} "
            f"expected={expected_signature!r}"
        )

    status = str(payload.get("status") or "READY").strip().upper()
    if status != "READY":
        raise ValueError(f"box control status not READY: {status!r}")

    return BoxControl(
        market=market,
        effective_date=effective_date,
        box_high=high,
        box_low=low,
        box_signature=expected_signature,
        source=str(payload.get("source") or "MASTER_00_수동입력").strip(),
        source_updated_at=str(payload.get("updated_at") or "").strip(),
        fetched_at=fetched_at or now_kst_string(),
        status=status,
    )


def fetch_box_control(endpoint: str, secret: str) -> BoxControl:
    response = requests.get(
        endpoint,
        params={"type": "box_control", "secret": secret},
        timeout=30,
    )
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Apps Script returned non-JSON: {response.text[:500]}"
        ) from exc
    return parse_box_control(payload)


def save_cache(path: Path, control: BoxControl) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(asdict(control), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, path)


def load_cache(path: Path = DEFAULT_CACHE_PATH) -> BoxControl | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("BOX control cache must contain an object")
    return BoxControl(**data)


def sync_once(
    endpoint: str,
    secret: str,
    *,
    cache_path: Path = DEFAULT_CACHE_PATH,
) -> BoxControl:
    control = fetch_box_control(endpoint, secret)
    save_cache(cache_path, control)
    return control


def self_test() -> None:
    payload = {
        "ok": True,
        "type": "box_control",
        "market": "KOSPI",
        "effective_date": "2026-09-07",
        "box_high": 7216,
        "box_low": 6400,
        "box_signature": "7216|6400",
        "source": "MASTER_00_수동입력",
        "updated_at": "2026-09-16 14:00:00",
        "status": "READY",
    }

    control = parse_box_control(
        payload,
        fetched_at="2026-09-16 14:54:00",
    )
    assert control.market == "KOSPI"
    assert control.box_high == 7216.0
    assert control.box_low == 6400.0
    assert control.box_signature == "7216|6400"

    tmpdir = Path(tempfile.mkdtemp(prefix="box_control_test_"))
    path = tmpdir / "box_control.json"
    save_cache(path, control)
    loaded = load_cache(path)
    assert loaded == control

    bad = dict(payload)
    bad["box_high"] = 6300
    try:
        parse_box_control(bad)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid BOX range was not rejected")

    bad_sig = dict(payload)
    bad_sig["box_signature"] = "WRONG"
    try:
        parse_box_control(bad_sig)
    except ValueError:
        pass
    else:
        raise AssertionError("signature mismatch was not rejected")

    print("BOX_CONTROL_PARSE = PASS")
    print("BOX_CONTROL_ATOMIC_CACHE = PASS")
    print("BOX_CONTROL_INVALID_RANGE_REJECT = PASS")
    print("BOX_CONTROL_SIGNATURE_REJECT = PASS")
    print("LIVE_NETWORK_CALLED = NO")
    print(f"TEMP_CACHE = {path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync MASTER KOSPI STRUCT BOX control-plane data into a local atomic cache."
    )
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--cache", default=str(DEFAULT_CACHE_PATH))
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0

    endpoint, secret = load_runtime_env()
    control = sync_once(
        endpoint,
        secret,
        cache_path=Path(args.cache).expanduser(),
    )
    print(
        "BOX_CONTROL_SYNC = PASS | "
        f"market={control.market} "
        f"box={control.box_low:.12g}~{control.box_high:.12g} "
        f"effective={control.effective_date} "
        f"signature={control.box_signature}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
