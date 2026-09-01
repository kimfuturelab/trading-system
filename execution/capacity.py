from __future__ import annotations

from typing import Any


def parse_intish(value: Any) -> int | None:
    text = str(value or "").strip().replace(",", "")
    if not text:
        return None
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def maximum_orderable_amount(payload: Any) -> int | None:
    """Return V0's canonical 최대주문가능금액 from a Kiwoom chance payload.

    Policy decided on 2026-09-01:
    - Do NOT use 주문가능현금/주문가능금액/예수금 as the BUY capacity gate.
    - Prefer an explicit 최대주문가능금액 field if Kiwoom ever returns one.
    - Otherwise derive it as the maximum numeric margin-tier orderable amount
      among keys containing both '증거금' and '주문가능금'.
    - Quantity fields are excluded.
    - If no trusted field exists, fail closed by returning None.
    """
    if not isinstance(payload, dict):
        return None

    direct = parse_intish(payload.get("최대주문가능금액"))
    if direct is not None:
        return max(0, direct)

    candidates: list[int] = []
    for key, raw in payload.items():
        key_text = str(key)
        if "증거금" not in key_text:
            continue
        if "주문가능금" not in key_text:
            continue
        if "수량" in key_text:
            continue
        value = parse_intish(raw)
        if value is not None:
            candidates.append(max(0, value))

    return max(candidates) if candidates else None


def capacity_summary(payload: Any) -> dict[str, int | None]:
    body = payload if isinstance(payload, dict) else {}
    return {
        "maximum_orderable_amount": maximum_orderable_amount(body),
        "orderable_cash_reference_only": parse_intish(body.get("주문가능현금")),
        "today_reusable_amount_reference_only": parse_intish(body.get("금일재사용가능금액")),
        "deposit_reference_only": parse_intish(body.get("예수금")),
    }


def buy_capacity_errors(
    payload: Any,
    *,
    qty: int,
    reference_price: int,
) -> list[str]:
    """Validate BUY notional using only 최대주문가능금액 as capacity truth."""
    if qty <= 0:
        return ["QTY_MUST_BE_POSITIVE"]
    if reference_price <= 0:
        return ["REFERENCE_PRICE_MUST_BE_POSITIVE"]

    maximum = maximum_orderable_amount(payload)
    if maximum is None:
        return ["MAX_ORDERABLE_AMOUNT_NOT_FOUND"]

    required = qty * reference_price
    if maximum < required:
        return [f"MAX_ORDERABLE_AMOUNT_INSUFFICIENT({maximum}<{required})"]
    return []
