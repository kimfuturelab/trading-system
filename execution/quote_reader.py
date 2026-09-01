from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator
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
    query_code = exchange_query_code(symbol, exchange)

    # Kiwoom REST exchange-specific stock code contract:
    # KRX=005930, NXT=005930_NX. READ ONLY; no --confirm/order command.
    result = broker._run(  # noqa: SLF001
        [
            "domestic", "quotes", "price",
            "--code", query_code,
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
        exchange=exchange,
        query_code=query_code,
    )
