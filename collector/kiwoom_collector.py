from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
KST = ZoneInfo("Asia/Seoul")
TOKEN_CACHE = Path(
    os.getenv(
        "KIWOOM_TOKEN_CACHE",
        str(Path.home() / ".cache" / "trading-system" / "kiwoom_token.json"),
    )
).expanduser()
STATE_CACHE = BASE_DIR / ".rank_state.json"


def _signed_number(value: Any, *, absolute: bool = False) -> float:
    if value is None:
        return 0.0
    text = str(value).strip().replace(",", "")
    if not text:
        return 0.0
    try:
        number = float(text)
    except ValueError:
        return 0.0
    return abs(number) if absolute else number


def _int_number(value: Any, *, absolute: bool = False) -> int:
    return int(round(_signed_number(value, absolute=absolute)))


def now_kst_string() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class Settings:
    app_key: str
    app_secret: str
    base_url: str
    webhook_url: str
    ingest_secret: str
    market_type: str
    exchange_type: str
    exclude_managed: str
    poll_seconds: int
    trading_value_divisor: float

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(BASE_DIR / ".env")
        settings = cls(
            app_key=os.getenv("KIWOOM_APP_KEY", "").strip(),
            app_secret=os.getenv("KIWOOM_APP_SECRET", "").strip(),
            base_url=os.getenv("KIWOOM_BASE_URL", "https://api.kiwoom.com").rstrip("/"),
            webhook_url=os.getenv("SHEETS_WEBHOOK_URL", "").strip(),
            ingest_secret=os.getenv("INGEST_SECRET", "").strip(),
            market_type=os.getenv("MARKET_TYPE", "000").strip(),
            exchange_type=os.getenv("EXCHANGE_TYPE", "3").strip(),
            exclude_managed=os.getenv("EXCLUDE_MANAGED", "1").strip(),
            poll_seconds=int(os.getenv("POLL_SECONDS", "300")),
            trading_value_divisor=float(os.getenv("TRADING_VALUE_DIVISOR", "100")),
        )
        missing = []
        if not settings.app_key:
            missing.append("KIWOOM_APP_KEY")
        if not settings.app_secret:
            missing.append("KIWOOM_APP_SECRET")
        if not settings.webhook_url:
            missing.append("SHEETS_WEBHOOK_URL")
        if not settings.ingest_secret:
            missing.append("INGEST_SECRET")
        if missing:
            raise RuntimeError("Missing required .env values: " + ", ".join(missing))
        if settings.trading_value_divisor <= 0:
            raise RuntimeError("TRADING_VALUE_DIVISOR must be > 0")
        return settings


class KiwoomClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json;charset=UTF-8"})

    def _load_cached_token(self) -> str | None:
        if not TOKEN_CACHE.exists():
            return None
        try:
            data = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
            token = data.get("token")
            expires_at = data.get("expires_at")
            if not token or not expires_at:
                return None
            expiry = datetime.strptime(str(expires_at), "%Y%m%d%H%M%S").replace(tzinfo=KST)
            if datetime.now(KST) >= expiry - timedelta(minutes=10):
                return None
            return str(token)
        except Exception:
            return None

    def invalidate_token(self, token: str | None = None) -> None:
        """Remove only the token that actually failed.

        If another collector already refreshed the shared cache, do not delete the
        newer token just because this process received an 8005 on an older token.
        """
        try:
            if not TOKEN_CACHE.exists():
                return
            if token:
                try:
                    current = json.loads(TOKEN_CACHE.read_text(encoding="utf-8")).get("token")
                except Exception:
                    current = None
                if current and str(current) != str(token):
                    return
            TOKEN_CACHE.unlink(missing_ok=True)
        except OSError:
            pass

    def issue_token(self, *, force_refresh: bool = False) -> str:
        if not force_refresh:
            cached = self._load_cached_token()
            if cached:
                return cached

        url = f"{self.s.base_url}/oauth2/token"
        body = {
            "grant_type": "client_credentials",
            "appkey": self.s.app_key,
            "secretkey": self.s.app_secret,
        }
        response = self.session.post(url, json=body, timeout=15)
        response.raise_for_status()
        data = response.json()
        if int(data.get("return_code", -1)) != 0:
            raise RuntimeError(f"Kiwoom token error: {data}")

        token = data.get("token")
        expires_at = data.get("expires_dt")
        if not token or not expires_at:
            raise RuntimeError(f"Unexpected token response: {data}")

        TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        temp_path = TOKEN_CACHE.with_suffix(TOKEN_CACHE.suffix + f".{os.getpid()}.tmp")
        temp_path.write_text(
            json.dumps({"token": token, "expires_at": expires_at}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp_path, TOKEN_CACHE)
        return str(token)

    @staticmethod
    def _is_invalid_token_response(data: dict[str, Any]) -> bool:
        if int(data.get("return_code", -1)) == 0:
            return False
        message = str(data.get("return_msg", ""))
        return "8005" in message or "Token이 유효하지 않습니다" in message

    def post_authorized(
        self,
        api_id: str,
        path: str,
        body: dict[str, Any],
        *,
        timeout: int = 20,
    ) -> dict[str, Any]:
        """POST an authenticated Kiwoom request with one 8005 recovery retry."""
        last_data: dict[str, Any] | None = None
        for attempt in range(2):
            token = self.issue_token()
            response = self.session.post(
                f"{self.s.base_url}{path}",
                headers={
                    "authorization": f"Bearer {token}",
                    "api-id": api_id,
                },
                json=body,
                timeout=timeout,
            )
            response.raise_for_status()
            data = response.json()
            last_data = data
            if int(data.get("return_code", -1)) == 0:
                return data
            if attempt == 0 and self._is_invalid_token_response(data):
                self.invalidate_token(token)
                continue
            raise RuntimeError(f"{api_id} error: {data}")
        raise RuntimeError(f"{api_id} error after token refresh: {last_data}")

    def top_trading_value(self) -> list[dict[str, Any]]:
        data = self.post_authorized(
            "ka10032",
            "/api/dostk/rkinfo",
            {
                "mrkt_tp": self.s.market_type,
                "mang_stk_incls": self.s.exclude_managed,
                "stex_tp": self.s.exchange_type,
            },
        )

        rows = data.get("trde_prica_upper") or []
        if not isinstance(rows, list):
            raise RuntimeError(f"Unexpected ka10032 payload: {data}")
        return rows


def load_previous_ranks() -> dict[str, int]:
    if not STATE_CACHE.exists():
        return {}
    try:
        raw = json.loads(STATE_CACHE.read_text(encoding="utf-8"))
        return {str(k): int(v) for k, v in raw.items()}
    except Exception:
        return {}


def save_current_ranks(rows: list[dict[str, Any]]) -> None:
    state = {str(r["stock_code"]): int(r["rank"]) for r in rows if r.get("stock_code")}
    STATE_CACHE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_top100(raw_rows: list[dict[str, Any]], settings: Settings) -> list[dict[str, Any]]:
    previous = load_previous_ranks()
    captured_at = now_kst_string()
    normalized: list[dict[str, Any]] = []

    for item in raw_rows[:100]:
        code = str(item.get("stk_cd", "")).strip()
        if not code:
            continue
        rank = _int_number(item.get("now_rank"))
        prev_snapshot_rank = previous.get(code)
        rank_change = None if prev_snapshot_rank is None else prev_snapshot_rank - rank
        trading_value_raw = _signed_number(item.get("trde_prica"), absolute=True)

        normalized.append(
            {
                "captured_at": captured_at,
                "rank": rank,
                "stock_code": code,
                "stock_name": str(item.get("stk_nm", "")).strip(),
                "market": "ALL",
                "exchange": {"1": "KRX", "2": "NXT", "3": "SOR"}.get(settings.exchange_type, settings.exchange_type),
                "current_price": _int_number(item.get("cur_prc"), absolute=True),
                "change_rate_pct": _signed_number(item.get("flu_rt")),
                "trading_volume": _int_number(item.get("now_trde_qty"), absolute=True),
                "trading_value_raw": trading_value_raw,
                "trading_value_eok": trading_value_raw / settings.trading_value_divisor,
                "previous_snapshot_rank": prev_snapshot_rank,
                "rank_change": rank_change,
                "previous_day_rank": _int_number(item.get("pred_rank")),
                "status": "OK",
            }
        )

    normalized.sort(key=lambda x: x["rank"] or 9999)
    return normalized


def send_to_apps_script(rows: list[dict[str, Any]], settings: Settings) -> dict[str, Any]:
    payload = {
        "secret": settings.ingest_secret,
        "type": "top100",
        "source": "kiwoom-rest-ka10032",
        "captured_at": rows[0]["captured_at"] if rows else now_kst_string(),
        "row_count": len(rows),
        "rows": rows,
    }
    response = requests.post(settings.webhook_url, json=payload, timeout=30)
    response.raise_for_status()
    try:
        result = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Apps Script returned non-JSON: {response.text[:500]}") from exc
    if not result.get("ok"):
        raise RuntimeError(f"Apps Script ingestion failed: {result}")
    return result


def run_once(settings: Settings) -> None:
    client = KiwoomClient(settings)
    raw = client.top_trading_value()
    rows = normalize_top100(raw, settings)
    if not rows:
        raise RuntimeError("ka10032 returned no usable rows")

    result = send_to_apps_script(rows, settings)
    save_current_ranks(rows)
    print(
        f"[{rows[0]['captured_at']}] sent {len(rows)} rows | "
        f"sheet={result.get('sheet')} | top={rows[0]['stock_name']}"
    )


def main() -> int:
    try:
        settings = Settings.from_env()
    except Exception as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2

    loop = "--loop" in sys.argv
    while True:
        try:
            run_once(settings)
        except KeyboardInterrupt:
            print("Stopped by user")
            return 0
        except Exception as exc:
            print(f"RUN ERROR: {exc}", file=sys.stderr)
            if not loop:
                return 1

        if not loop:
            return 0
        time.sleep(settings.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
