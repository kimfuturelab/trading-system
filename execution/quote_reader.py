from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterator
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from broker_cli import KiwoomCliBroker

KST = ZoneInfo("Asia/Seoul")
PRICE_KEYS = (
    "현재가",
    "cur_prc",
    "current_price",
    "last_price",
    "stck_prpr",
)


class QuotePriceError(RuntimeError):
    pass


class QuoteTransportError(RuntimeError):
    pass


@dataclass(frozen=True)
class QuoteSnapshot:
    symbol: str
    last_price: int
    captured_at: datetime
    payload: Any
    exchange: str = "KRX"
    query_code: str = ""


def _walk_dicts(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _parse_price_value(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    signless = text[1:] if text[0] in {"+", "-"} else text
    if not signless.isdigit():
        return None
    parsed = abs(int(text))
    return parsed if parsed > 0 else None


def extract_last_price(payload: Any) -> int:
    candidates: list[tuple[str, int]] = []
    for body in _walk_dicts(payload):
        for key in PRICE_KEYS:
            if key not in body:
                continue
            parsed = _parse_price_value(body.get(key))
            if parsed is not None:
                candidates.append((key, parsed))
    if not candidates:
        raise QuotePriceError("현재가 필드를 찾지 못했습니다.")
    unique = sorted({price for _, price in candidates})
    if len(unique) != 1:
        raise QuotePriceError(
            "현재가 후보 필드 값이 서로 다릅니다: "
            + ", ".join(f"{key}={price}" for key, price in candidates)
        )
    return unique[0]


def exchange_query_code(symbol: str, exchange: str) -> str:
    exchange = str(exchange or "").strip().upper()
    if exchange == "KRX":
        return symbol
    if exchange == "NXT":
        return f"{symbol}_NX"
    raise ValueError("exchange must be KRX|NXT")


def _first_env(*names: str) -> str:
    for name in names:
        value = str(os.getenv(name, "") or "").strip()
        if value:
            return value
    return ""


def _credentials_for_mode(mode: str) -> tuple[str, str]:
    mode = str(mode or "real").strip().lower()
    if mode == "real":
        key = _first_env("APP_KEY", "KIWOOM_APP_KEY", "KIWOOM_APPKEY")
        secret = _first_env("APP_SECRET", "KIWOOM_APP_SECRET", "KIWOOM_SECRETKEY")
    elif mode == "demo":
        key = _first_env("APP_KEY_MOCK", "KIWOOM_APP_KEY_MOCK", "KIWOOM_MOCK_APPKEY")
        secret = _first_env(
            "APP_SECRET_MOCK", "KIWOOM_APP_SECRET_MOCK", "KIWOOM_MOCK_SECRETKEY"
        )
    else:
        raise QuoteTransportError(f"unsupported mode: {mode}")
    if not key or not secret:
        raise QuoteTransportError(
            "Kiwoom REST credential env missing for NXT quote read; secrets are not logged"
        )
    return key, secret


def _base_url(mode: str) -> str:
    return "https://api.kiwoom.com" if mode == "real" else "https://mockapi.kiwoom.com"


def _post_json(
    url: str,
    *,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: int = 15,
    opener: Callable[..., Any] = urlopen,
) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with opener(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        raise QuoteTransportError(f"Kiwoom REST HTTP error: {exc.code}") from exc
    except (URLError, TimeoutError) as exc:
        raise QuoteTransportError(f"Kiwoom REST network error: {type(exc).__name__}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise QuoteTransportError("Kiwoom REST returned non-JSON payload") from exc
    if not isinstance(payload, dict):
        raise QuoteTransportError("Kiwoom REST payload is not an object")
    return payload


def _require_api_ok(payload: dict[str, Any], *, stage: str) -> dict[str, Any]:
    code = payload.get("return_code")
    try:
        normalized = int(code) if code is not None else None
    except (TypeError, ValueError):
        normalized = None
    if normalized not in {None, 0}:
        msg = str(payload.get("return_msg") or "").strip()
        raise QuoteTransportError(f"Kiwoom {stage} return_code={normalized}: {msg[:200]}")
    return payload


def read_nxt_quote_rest(
    *,
    symbol: str,
    mode: str = "real",
    opener: Callable[..., Any] = urlopen,
) -> QuoteSnapshot:
    """READ-only NXT quote via raw Kiwoom REST.

    The official kwcli `domestic quotes price` command currently rejects the
    documented NXT suffixed code at its local 6-character validator.  The
    underlying Kiwoom REST contract accepts `005930_NX`, so V1 uses ka10001
    directly for NXT only. No order endpoint is called here.
    """
    symbol = str(symbol or "").strip().upper()
    if len(symbol) != 6 or not symbol.isalnum():
        raise ValueError("symbol must be a 6-character stock code")
    mode = str(mode or "real").strip().lower()
    key, secret = _credentials_for_mode(mode)
    base = _base_url(mode)

    token_payload = _require_api_ok(
        _post_json(
            base + "/oauth2/token",
            headers={"Content-Type": "application/json;charset=UTF-8"},
            body={
                "grant_type": "client_credentials",
                "appkey": key,
                "secretkey": secret,
            },
            opener=opener,
        ),
        stage="token",
    )
    token = str(token_payload.get("token") or "").strip()
    if not token:
        raise QuoteTransportError("Kiwoom token response missing token")

    query_code = exchange_query_code(symbol, "NXT")
    payload = _require_api_ok(
        _post_json(
            base + "/api/dostk/stkinfo",
            headers={
                "Content-Type": "application/json;charset=UTF-8",
                "authorization": f"Bearer {token}",
                "api-id": "ka10001",
            },
            body={"stk_cd": query_code},
            opener=opener,
        ),
        stage="ka10001",
    )
    return QuoteSnapshot(
        symbol=symbol,
        last_price=extract_last_price(payload),
        captured_at=datetime.now(KST),
        payload=payload,
        exchange="NXT",
        query_code=query_code,
    )


def read_quote_snapshot(
    broker: KiwoomCliBroker,
    *,
    symbol: str,
    exchange: str = "KRX",
) -> QuoteSnapshot:
    symbol = str(symbol or "").strip().upper()
    if len(symbol) != 6 or not symbol.isalnum():
        raise ValueError("symbol must be a 6-character stock code")
    exchange = str(exchange or "").strip().upper()

    if exchange == "NXT":
        return read_nxt_quote_rest(symbol=symbol, mode=broker.mode)
    if exchange != "KRX":
        raise ValueError("exchange must be KRX|NXT")

    # KRX keeps the already validated kwcli path.
    result = broker._run(  # noqa: SLF001
        [
            "domestic", "quotes", "price",
            "--code", symbol,
            "--mode", broker.mode,
            "--format", "json",
            "--named",
        ]
    )
    broker._require_api_ok(result)  # noqa: SLF001
    price = extract_last_price(result.payload)
    return QuoteSnapshot(
        symbol=symbol,
        last_price=price,
        captured_at=datetime.now(KST),
        payload=result.payload,
        exchange="KRX",
        query_code=symbol,
    )
