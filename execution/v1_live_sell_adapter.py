from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any, Callable

from exit_write_journal import DuplicateWriteReservation, ExitWriteJournal
from safety import ExecutionConfig, assert_live_write_allowed, validate_order_request
from sell_handoff import prepare_sell_handoff
from v0_engine import account_mask_matches, broker_account_mask


@dataclass(frozen=True)
class LiveSellResult:
    plan_id: str
    exit_intent_id: str
    symbol: str
    qty: int
    broker_order_no: str


def _extract_order_no(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("주문번호") or payload.get("ord_no") or "").strip()


def execute_live_sell_once(
    *,
    cfg: ExecutionConfig,
    plan_row: dict[str, Any],
    snapshot: dict[str, Any],
    local_open_intent_count: int,
    broker: Any,
    journal: ExitWriteJournal,
    mark_exit_submitted: Callable[..., Any],
) -> LiveSellResult:
    """One guarded V1 broker SELL write.

    IMPORTANT: this module is intentionally not exposed through a CLI yet.

    Ordering contract:
    1) broker/local exact-holding gates
    2) safety config/account/allowlist checks
    3) persistent WRITE_RESERVED journal
    4) independent final live-write assertion
    5) broker submit exactly once
    6) persist ACKED + EXIT_SUBMITTED

    Any existing journal row blocks a second write for that exit_intent_id.
    Timeout/ambiguous failure is never retried automatically.
    """
    handoff = prepare_sell_handoff(
        plan_row,
        snapshot,
        local_open_intent_count=int(local_open_intent_count),
    )

    broker_masked = broker_account_mask(snapshot)
    if not cfg.allowed_account:
        raise RuntimeError("LIVE_SELL_BLOCKED: ALLOWED_ACCOUNT_NOT_SET")
    if not account_mask_matches(cfg.allowed_account, broker_masked):
        raise RuntimeError("LIVE_SELL_BLOCKED: BROKER_ACCOUNT_SUFFIX_MISMATCH")

    reasons = validate_order_request(
        cfg,
        account=cfg.allowed_account,
        symbol=handoff.symbol,
        qty=int(handoff.qty),
        side="SELL",
        intent_id=handoff.exit_intent_id,
    )
    if reasons:
        raise RuntimeError("LIVE_SELL_BLOCKED: " + ", ".join(reasons))

    try:
        journal.reserve_once(
            exit_intent_id=handoff.exit_intent_id,
            plan_id=handoff.plan_id,
            symbol=handoff.symbol,
            qty=int(handoff.qty),
        )
    except DuplicateWriteReservation as exc:
        raise RuntimeError(
            "LIVE_SELL_DUPLICATE_BLOCKED: existing write journal requires broker reconciliation"
        ) from exc

    # Independent guard immediately before the broker write.
    assert_live_write_allowed(cfg)

    try:
        result = broker.submit_order(
            side="sell",
            symbol=handoff.symbol,
            qty=int(handoff.qty),
            exchange=handoff.exchange,
            order_type=handoff.order_type,
            price=None,
            confirm_live_write=True,
        )
    except subprocess.TimeoutExpired as exc:
        journal.mark_unknown_ack(
            handoff.exit_intent_id,
            error=f"broker submit timeout: {exc}",
        )
        raise RuntimeError(
            "LIVE_SELL_UNKNOWN_ACK: timeout after WRITE_RESERVED; DO NOT RESEND"
        ) from exc
    except Exception as exc:
        journal.mark_review_required(
            handoff.exit_intent_id,
            error=f"broker submit exception: {type(exc).__name__}: {exc}",
        )
        raise RuntimeError(
            "LIVE_SELL_REVIEW_REQUIRED: broker write outcome must be reconciled; DO NOT RESEND"
        ) from exc

    payload = getattr(result, "payload", None)
    order_no = _extract_order_no(payload)
    if not order_no:
        journal.mark_unknown_ack(
            handoff.exit_intent_id,
            error="broker response had no order number",
        )
        raise RuntimeError(
            "LIVE_SELL_UNKNOWN_ACK: broker response missing order number; DO NOT RESEND"
        )

    journal.mark_acked(
        handoff.exit_intent_id,
        broker_order_no=order_no,
        raw_response=payload,
    )

    # If this local state transition itself fails, the ACKED journal still
    # preserves broker_order_no and blocks any duplicate write on restart.
    mark_exit_submitted(handoff.plan_id, broker_order_no=order_no)

    return LiveSellResult(
        plan_id=handoff.plan_id,
        exit_intent_id=handoff.exit_intent_id,
        symbol=handoff.symbol,
        qty=int(handoff.qty),
        broker_order_no=order_no,
    )
