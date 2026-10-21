from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from v0_engine import holding_qty, open_order_rows


@dataclass(frozen=True)
class ExitWriteReconcileResult:
    action: str
    plan_id: str
    exit_intent_id: str
    broker_holding_qty: int
    broker_open_order_count: int
    broker_order_no: str | None
    safe_to_resend: bool = False


def reconcile_exit_write(
    *,
    plan_row: dict[str, Any],
    snapshot: dict[str, Any],
    journal_row: dict[str, Any] | None,
    mark_exit_submitted: Callable[..., Any],
    reconcile_holding: Callable[..., Any],
) -> ExitWriteReconcileResult:
    """Recover V1 SELL state from broker truth without ever resending.

    Safety contract:
    - This function NEVER calls broker.submit_order.
    - Any existing write journal row means safe_to_resend is always False.
    - Holding=0 is authoritative for position closure.
    - ACKED + broker_order_no can restore EXIT_SUBMITTED after a local crash.
    - WRITE_RESERVED / UNKNOWN_ACK / REVIEW_REQUIRED remain review-only while
      holding remains >0; absence of an order in one snapshot is NOT proof that
      the broker write never happened.
    """
    plan_id = str(plan_row.get("plan_id") or "").strip()
    exit_intent_id = str(plan_row.get("exit_intent_id") or "").strip()
    symbol = str(plan_row.get("symbol") or "").strip().upper()
    if not plan_id or not exit_intent_id or not symbol:
        raise RuntimeError("EXIT_WRITE_RECONCILE_BLOCKED: invalid plan identity")

    # A missing or malformed broker holdings payload must NEVER be treated as
    # zero shares. Otherwise the local EXIT plan can be falsely CLOSED.
    holdings = snapshot.get("holdings")
    if not isinstance(holdings, dict):
        raise RuntimeError("EXIT_RECONCILE_BROKER_HOLDINGS_MISSING")
    rows = holdings.get("계좌평가잔고개별합산")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise RuntimeError("EXIT_RECONCILE_BROKER_HOLDINGS_UNVERIFIED")
    if str(holdings.get("return_code", 0)) != "0":
        raise RuntimeError("EXIT_RECONCILE_BROKER_HOLDINGS_ERROR")
    for row in rows:
        raw = next((row[k] for k in ("보유수량", "rmnd_qty", "보유량")
                    if k in row), None)
        if raw is None:
            raise RuntimeError("EXIT_RECONCILE_HOLDING_QTY_MISSING")
        from v0_engine import parse_intish
        qty_check = parse_intish(raw)
        if qty_check is None or qty_check < 0:
            raise RuntimeError("EXIT_RECONCILE_HOLDING_QTY_INVALID")
    broker_qty = holding_qty(snapshot, symbol)
    broker_opens = len(open_order_rows(snapshot))

    # A proved broker-flat position is CLOSED even if its local journal was
    # lost across a restart; never resubmit a SELL to infer closure.
    if broker_qty == 0:
        reconcile_holding(plan_id, broker_holding_qty=0)
        return ExitWriteReconcileResult(
            action="CLOSED_BY_BROKER_HOLDING",
            plan_id=plan_id,
            exit_intent_id=exit_intent_id,
            broker_holding_qty=0,
            broker_open_order_count=broker_opens,
            broker_order_no=(str(journal_row.get("broker_order_no") or "").strip() or None)
                if journal_row else None,
            safe_to_resend=False,
        )

    if journal_row is None:
        return ExitWriteReconcileResult(
            action="NO_WRITE_JOURNAL",
            plan_id=plan_id,
            exit_intent_id=exit_intent_id,
            broker_holding_qty=broker_qty,
            broker_open_order_count=broker_opens,
            broker_order_no=None,
            safe_to_resend=False,
        )

    state = str(journal_row.get("state") or "").strip().upper()
    order_no = str(journal_row.get("broker_order_no") or "").strip() or None

    # If broker ACK/order number was persisted but the EXIT_PLAN state update
    # was interrupted, restore that local transition, then reconcile remaining
    # holding (same qty -> no change, lower qty -> PARTIAL_EXIT).
    if state == "ACKED" and order_no:
        if str(plan_row.get("status") or "") == "EXIT_TRIGGERED":
            mark_exit_submitted(plan_id, broker_order_no=order_no)
        reconcile_holding(plan_id, broker_holding_qty=broker_qty)
        return ExitWriteReconcileResult(
            action="ACKED_RESTORED_NO_RESEND",
            plan_id=plan_id,
            exit_intent_id=exit_intent_id,
            broker_holding_qty=broker_qty,
            broker_open_order_count=broker_opens,
            broker_order_no=order_no,
            safe_to_resend=False,
        )

    # Ambiguous states are deliberately sticky. Even with zero open orders in
    # one read, there is no automatic resend: broker visibility can lag and an
    # ACK can have been lost locally.
    if state in {"WRITE_RESERVED", "UNKNOWN_ACK", "REVIEW_REQUIRED"}:
        return ExitWriteReconcileResult(
            action=f"{state}_REQUIRES_REVIEW_NO_RESEND",
            plan_id=plan_id,
            exit_intent_id=exit_intent_id,
            broker_holding_qty=broker_qty,
            broker_open_order_count=broker_opens,
            broker_order_no=order_no,
            safe_to_resend=False,
        )

    return ExitWriteReconcileResult(
        action="UNKNOWN_JOURNAL_STATE_REQUIRES_REVIEW",
        plan_id=plan_id,
        exit_intent_id=exit_intent_id,
        broker_holding_qty=broker_qty,
        broker_open_order_count=broker_opens,
        broker_order_no=order_no,
        safe_to_resend=False,
    )
