#!/usr/bin/env python3
from __future__ import annotations

from exit_write_reconcile import reconcile_exit_write


def snapshot(qty: int, *, open_orders: int = 0) -> dict:
    holdings = []
    if qty > 0:
        holdings.append({"종목번호": "A005930", "보유수량": str(qty)})
    opens = []
    for i in range(open_orders):
        opens.append({"주문번호": f"00{i+1}", "종목번호": "A005930", "미체결수량": "1"})
    return {
        "holdings": {"계좌평가잔고개별합산": holdings},
        "open_orders": {"미체결": opens},
    }


def plan(status: str = "EXIT_TRIGGERED", remaining: int = 1) -> dict:
    return {
        "plan_id": "V1-RECON-FIXTURE-001",
        "symbol": "005930",
        "qty": 1,
        "remaining_qty": remaining,
        "status": status,
        "trigger_reason": "TIMECUT",
        "exit_intent_id": "V1-RECON-FIXTURE-001-EXIT",
    }


def journal(state: str, order_no: str | None = None) -> dict:
    return {
        "exit_intent_id": "V1-RECON-FIXTURE-001-EXIT",
        "plan_id": "V1-RECON-FIXTURE-001",
        "symbol": "005930",
        "qty": 1,
        "state": state,
        "broker_order_no": order_no,
    }


def main() -> int:
    print("===== V1 EXIT WRITE RECONCILIATION FIXTURE =====")
    print("[SAFE] fixture-only: no Kiwoom API and no broker write")

    submitted: list[tuple[str, str]] = []
    reconciled: list[tuple[str, int]] = []

    def mark_submitted(plan_id: str, *, broker_order_no: str):
        submitted.append((plan_id, broker_order_no))

    def reconcile_holding(plan_id: str, *, broker_holding_qty: int):
        reconciled.append((plan_id, broker_holding_qty))

    # 1) ACK persisted, local EXIT_PLAN update crashed -> restore state, never resend.
    result = reconcile_exit_write(
        plan_row=plan("EXIT_TRIGGERED", 1),
        snapshot=snapshot(1),
        journal_row=journal("ACKED", "0012345"),
        mark_exit_submitted=mark_submitted,
        reconcile_holding=reconcile_holding,
    )
    assert result.action == "ACKED_RESTORED_NO_RESEND"
    assert result.safe_to_resend is False
    assert submitted[-1] == ("V1-RECON-FIXTURE-001", "0012345")
    assert reconciled[-1] == ("V1-RECON-FIXTURE-001", 1)
    print("[PASS] ACKED journal restores EXIT_SUBMITTED after crash; resend forbidden")

    # 2) Lost/ambiguous ACK with position still present -> sticky review, no resend.
    for state in ("WRITE_RESERVED", "UNKNOWN_ACK", "REVIEW_REQUIRED"):
        before_submit = len(submitted)
        before_recon = len(reconciled)
        result = reconcile_exit_write(
            plan_row=plan("EXIT_TRIGGERED", 1),
            snapshot=snapshot(1),
            journal_row=journal(state),
            mark_exit_submitted=mark_submitted,
            reconcile_holding=reconcile_holding,
        )
        assert "REQUIRES_REVIEW_NO_RESEND" in result.action
        assert result.safe_to_resend is False
        assert len(submitted) == before_submit
        assert len(reconciled) == before_recon
    print("[PASS] WRITE_RESERVED/UNKNOWN_ACK/REVIEW_REQUIRED never auto-resend")

    # 3) Even if a snapshot shows no open orders, UNKNOWN_ACK is still ambiguous.
    result = reconcile_exit_write(
        plan_row=plan(),
        snapshot=snapshot(1, open_orders=0),
        journal_row=journal("UNKNOWN_ACK"),
        mark_exit_submitted=mark_submitted,
        reconcile_holding=reconcile_holding,
    )
    assert result.safe_to_resend is False
    assert result.action == "UNKNOWN_ACK_REQUIRES_REVIEW_NO_RESEND"
    print("[PASS] one empty open-order snapshot is not treated as permission to resend")

    # 4) Broker holding=0 is authoritative for position closure and blocks any resend.
    before_submit = len(submitted)
    result = reconcile_exit_write(
        plan_row=plan(),
        snapshot=snapshot(0),
        journal_row=journal("UNKNOWN_ACK"),
        mark_exit_submitted=mark_submitted,
        reconcile_holding=reconcile_holding,
    )
    assert result.action == "CLOSED_BY_BROKER_HOLDING"
    assert result.safe_to_resend is False
    assert len(submitted) == before_submit
    assert reconciled[-1] == ("V1-RECON-FIXTURE-001", 0)
    print("[PASS] broker holding=0 -> CLOSED recovery; no second SELL")

    # 5) No journal does not itself authorize a write; live adapter gates decide later.
    result = reconcile_exit_write(
        plan_row=plan(),
        snapshot=snapshot(1),
        journal_row=None,
        mark_exit_submitted=mark_submitted,
        reconcile_holding=reconcile_holding,
    )
    assert result.action == "NO_WRITE_JOURNAL"
    assert result.safe_to_resend is False
    print("[PASS] missing journal is never interpreted by reconcile as safe-to-resend")

    print("[PASS] V1 exit write reconciliation fixture complete")
    print("[SAFE] no network or real order call occurred")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
