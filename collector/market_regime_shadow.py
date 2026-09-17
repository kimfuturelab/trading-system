from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from kiwoom_collector import KiwoomClient, Settings
import technical_position_collector as tp

KST = ZoneInfo("Asia/Seoul")
BASE_DIR = Path(__file__).resolve().parent
CACHE_PATH = Path.home() / ".cache" / "trading-system" / "market_regime_shadow.json"
VERSION = "market-regime-shadow-v1"

REGIME_KO = {
    "BIG BLUE": "초강세",
    "BLUE": "강세",
    "GREEN": "박스",
    "RED+": "약세",
    "RED": "초약세",
}

POLICY = {
    "BIG BLUE": {
        "allowed_time": "장중 · 종배",
        "allowed_strategies": "돌파 불타기 · 돌파 · 눌림 · 저점 · 저점이탈 · 사이드카 · 서킷브레이크",
        "max_hold_stage": "빅스윙까지",
    },
    "BLUE": {
        "allowed_time": "장중 · 종배",
        "allowed_strategies": "돌파 · 눌림 · 저점 · 저점이탈 · 사이드카 · 서킷브레이크",
        "max_hold_stage": "빅스윙까지",
    },
    "GREEN": {
        "allowed_time": "종배 · 장중예외(사이드카/서킷)",
        "allowed_strategies": "저점 · 저점이탈 · 사이드카 · 서킷브레이크",
        "max_hold_stage": "스윙까지",
    },
    "RED+": {
        "allowed_time": "종배 · 장중예외(사이드카/서킷)",
        "allowed_strategies": "저점이탈 · 사이드카 · 서킷브레이크",
        "max_hold_stage": "종배까지",
    },
    "RED": {
        "allowed_time": "장중예외(사이드카/서킷)",
        "allowed_strategies": "사이드카 · 서킷브레이크",
        "max_hold_stage": "기존 보유 청산만",
    },
}


@dataclass(frozen=True)
class RegimeResult:
    run_id: str
    captured_at: str
    target_date: str
    source_date: str
    source: str
    close: float
    return_5d: float
    efficiency_5d: float
    return_10d: float
    efficiency_10d: float
    return_20d: float
    efficiency_20d: float
    average_20d: float
    regime_code: str
    regime_ko: str
    allowed_time: str
    allowed_strategies: str
    max_hold_stage: str
    krx_close: float | None
    krx_verification: str
    operating_status: str
    warning: str
    code_version: str = VERSION


def now_kst() -> datetime:
    return datetime.now(KST)


def now_kst_string() -> str:
    return now_kst().strftime("%Y-%m-%d %H:%M:%S")


def _efficiency(closes: list[float], periods: int) -> float:
    numerator = abs(closes[0] - closes[periods])
    denominator = sum(abs(closes[i] - closes[i + 1]) for i in range(periods))
    return 0.0 if denominator == 0 else numerator / denominator


def calculate_regime(closes: list[float]) -> dict[str, Any]:
    if len(closes) < 21:
        raise ValueError(f"need at least 21 closes, got {len(closes)}")

    values = [float(value) for value in closes[:21]]
    if any(value <= 0 for value in values):
        raise ValueError("all closes must be positive")

    r5 = values[0] / values[5] - 1.0
    e5 = _efficiency(values, 5)
    r10 = values[0] / values[10] - 1.0
    e10 = _efficiency(values, 10)
    r20 = values[0] / values[20] - 1.0
    e20 = _efficiency(values, 20)
    avg20 = sum(values[:20]) / 20.0

    if r20 >= 0.08 and e20 >= 0.40:
        code = "BIG BLUE"
    elif r20 > 0 and e20 >= 0.20:
        code = "BLUE"
    elif (r5 >= 0.03 and e5 >= 0.40) or (r20 < 0 and r10 >= 0.10 and e10 >= 0.35):
        code = "GREEN"
    elif values[0] >= avg20:
        code = "RED+"
    else:
        code = "RED"

    return {
        "return_5d": r5,
        "efficiency_5d": e5,
        "return_10d": r10,
        "efficiency_10d": e10,
        "return_20d": r20,
        "efficiency_20d": e20,
        "average_20d": avg20,
        "regime_code": code,
    }


def verify_krx(
    operating_close: float,
    krx_close: float | None,
    tolerance: float = 1e-9,
) -> tuple[str, str]:
    if krx_close is None:
        return "PENDING", "KRX 미수신 - 운영 계속"
    if abs(float(operating_close) - float(krx_close)) <= tolerance:
        return "MATCH", ""
    return "WARN", (
        f"KRX 불일치: KIWOOM={operating_close} KRX={krx_close} - 운영 계속"
    )


def _load_settings() -> Settings:
    load_dotenv(BASE_DIR / ".env", override=False)

    env_path = Path.home() / "api-read-v2.env"
    if env_path.exists():
        load_dotenv(env_path, override=False)

    app_key = (os.getenv("KIWOOM_APP_KEY") or os.getenv("APP_KEY") or "").strip()
    app_secret = (
        os.getenv("KIWOOM_APP_SECRET") or os.getenv("APP_SECRET") or ""
    ).strip()
    base_url = (
        os.getenv("KIWOOM_BASE_URL") or "https://api.kiwoom.com"
    ).rstrip("/")

    if not app_key or not app_secret:
        raise RuntimeError("missing Kiwoom read credentials")

    return Settings(
        app_key=app_key,
        app_secret=app_secret,
        base_url=base_url,
        webhook_url="unused-market-regime-shadow",
        ingest_secret="unused-market-regime-shadow",
        market_type="0",
        exchange_type="1",
        exclude_managed="1",
        poll_seconds=300,
        trading_value_divisor=100.0,
    )


def _daily_closes_exact(
    client: KiwoomClient,
    target_date: str,
) -> tuple[list[str], list[float]]:
    rows = tp.fetch_daily(client, "001", target_date)
    by_date: dict[str, float] = {}

    for row in rows:
        date_key = tp._trade_date(row)
        close = tp._index_price(row.get("cur_prc"))
        if not date_key or close is None:
            continue
        by_date.setdefault(date_key, float(close))

    dates = sorted(by_date, reverse=True)
    if not dates or dates[0] != target_date:
        raise RuntimeError(
            f"exact Kiwoom KOSPI daily row unavailable for {target_date}"
        )

    dates = dates[:21]
    if len(dates) < 21:
        raise RuntimeError(f"Kiwoom KOSPI history short: {len(dates)} rows")

    return dates, [by_date[date] for date in dates]


def build_result(
    target_date: str,
    krx_close: float | None = None,
) -> RegimeResult:
    client = KiwoomClient(_load_settings())
    dates, closes = _daily_closes_exact(client, target_date)
    metrics = calculate_regime(closes)

    code = str(metrics["regime_code"])
    verification, warning = verify_krx(closes[0], krx_close)
    policy = POLICY[code]
    captured_at = now_kst_string()

    return RegimeResult(
        run_id="REGIME-" + now_kst().strftime("%Y%m%d-%H%M%S"),
        captured_at=captured_at,
        target_date=f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:8]}",
        source_date=f"{dates[0][:4]}-{dates[0][4:6]}-{dates[0][6:8]}",
        source="KIWOOM-ka20006-KOSPI-001",
        close=closes[0],
        return_5d=float(metrics["return_5d"]),
        efficiency_5d=float(metrics["efficiency_5d"]),
        return_10d=float(metrics["return_10d"]),
        efficiency_10d=float(metrics["efficiency_10d"]),
        return_20d=float(metrics["return_20d"]),
        efficiency_20d=float(metrics["efficiency_20d"]),
        average_20d=float(metrics["average_20d"]),
        regime_code=code,
        regime_ko=REGIME_KO[code],
        allowed_time=policy["allowed_time"],
        allowed_strategies=policy["allowed_strategies"],
        max_hold_stage=policy["max_hold_stage"],
        krx_close=krx_close,
        krx_verification=verification,
        operating_status="SHADOW",
        warning=warning,
    )


def save_result(
    result: RegimeResult,
    path: Path = CACHE_PATH,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temp.write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temp, path)


def self_test() -> None:
    closes = [
        6717.97, 6627.26, 6684.37, 6909.91, 7033.92, 7051.64, 6954.52,
        6995.39, 6687.21, 6579.48, 6562.72, 6835.80, 6820.02, 6788.88,
        6912.37, 6808.21, 6742.74, 6696.96, 6912.95, 6852.58, 6471.17,
    ]

    result = calculate_regime(closes)
    assert result["regime_code"] == "RED"
    assert abs(result["return_5d"] - (-0.04731807068993876)) < 1e-12
    assert abs(result["efficiency_5d"] - 0.6477897066532062) < 1e-12
    assert abs(result["return_10d"] - 0.023656349806177923) < 1e-12
    assert abs(result["efficiency_10d"] - 0.1429887174763988) < 1e-12
    assert abs(result["return_20d"] - 0.0381383891939171) < 1e-12
    assert abs(result["efficiency_20d"] - 0.10272974750459964) < 1e-12

    assert verify_krx(6717.97, 6717.97)[0] == "MATCH"

    status, warning = verify_krx(6717.97, 6718.00)
    assert status == "WARN"
    assert "운영 계속" in warning

    status, warning = verify_krx(6717.97, None)
    assert status == "PENDING"
    assert "운영 계속" in warning

    print("MARKET_REGIME_REPLAY = PASS")
    print("KRX_MATCH = PASS")
    print("KRX_MISMATCH_WARN_ONLY = PASS")
    print("KRX_MISSING_CONTINUE = PASS")
    print("LIVE_KIWOOM_CALLED = NO")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="KOSPI market regime Python SHADOW collector"
    )
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--date", help="YYYYMMDD, default today KST")
    parser.add_argument("--cache", default=str(CACHE_PATH))
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0

    target_date = args.date or now_kst().strftime("%Y%m%d")
    result = build_result(target_date)
    save_result(result, Path(args.cache).expanduser())
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
