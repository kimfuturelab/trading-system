from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from broker_cli import KiwoomCliBroker

KST = ZoneInfo("Asia/Seoul")

# `--named` normally returns Korean field names. English aliases are kept only
# as compatibility fallbacks because CLI/API versions can differ.
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
    # Kiwoom price fields can include a +/- sign used for direction. For the
    # executable price level we use the absolute numeric value.
    if text[0] in {"+", "-"}:
        signless = text[1:]
    else:
        signless = text
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
        raise QuotePriceError(
            "현재가 필드를 찾지 못했습니다. CLI/API 응답 계약을 다시 확인해야 합니다."
        )

    unique = sorted({price for _, price in candidates})
    if len(unique) != 1:
        raise QuotePriceError(
            "현재가 후보 필드 값이 서로 다릅니다: "
            + ", ".join(f"{key}={price}" for key, price in candidates)
        )
    return unique[0]


def read_quote_snapshot(
    broker: KiwoomCliBroker,
    *,
    symbol: str,
) -> QuoteSnapshot:
    symbol = str(symbol or "").strip().upper()
    if len(symbol) != 6 or not symbol.isalnum():
        raise ValueError("symbol must be a 6-character stock code")

    # READ ONLY. Intentionally no --confirm and no order command.
    result = broker._run(  # noqa: SLF001 - V1 adapter reuse; promote after validation
        [
            "domestic",
            "quotes",
            "price",
            "--code",
            symbol,
            "--mode",
            broker.mode,
            "--format",
            "json",
            "--named",
        ]
    )
    broker._require_api_ok(result)  # noqa: SLF001 - same validated CLI envelope
    price = extract_last_price(result.payload)
    return QuoteSnapshot(
        symbol=symbol,
        last_price=price,
        captured_at=datetime.now(KST),
        payload=result.payload,
    )
