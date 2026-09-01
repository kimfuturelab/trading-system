#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from broker_cli import BrokerCliError, KiwoomCliBroker
from state_store import DuplicateIntentError, StateStore
from v0_engine import (
    holding_qty,
    open_order_rows,
    read_and_save_snapshot,
    snapshot_summary,
    sync_open_intents,
)

KST = ZoneInfo("Asia/Seoul")
DEMO_STATE_DB = Path.home() / ".trading-execution-v0-demo.sqlite3"


def _extract_order_no(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("주문번호") or payload.get("ord_no") or "").strip()


def _print_snapshot(title: str, snapshot: dict) -> None:
    print(f"\n===== {title} =====")
    print(json.dumps(snapshot_summary(snapshot), ensure_ascii=False, indent=2))


def _wait_for(
    *,
    broker: KiwoomCliBroker,
    store: StateStore,
    symbol: str,
    intent_id: str,
    expected_holding_qty: int,
    timeout_seconds: int,
    poll_seconds: float,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        snapshot = read_and_save_snapshot(broker, store)
        transitions = sync_open_intents(store, snapshot)
        intent = store.get_intent(intent_id) or {}
        held = holding_qty(snapshot, symbol)
        opens = len(open_order_rows(snapshot))

        print(
            f"[POLL] intent={intent_id} status={intent.get('status')} "
            f"holding={held} broker_open_orders={opens}"
        )
        if transitions:
            print(json.dumps(transitions, ensure_ascii=False, indent=2))

        if intent.get("status") == "FILLED" and held == expected_holding_qty and opens == 0:
            return True

        time.sleep(poll_seconds)
    return False


def main() -> int:
    p = argparse.ArgumentParser(
        description="V0 DEMO-only 1-share roundtrip. This program refuses real mode."
    )
    p.add_argument("--symbol", default="005930")
    p.add_argument("--qty", type=int, default=1)
    p.add_argument("--exchange", choices=["KRX"], default="KRX")
    p.add_argument("--timeout-seconds", type=int, default=30)
    p.add_argument("--poll-seconds", type=float, default=2.0)
    p.add_argument(
        "--send-demo-orders",
        action="store_true",
        help="Actually send orders to Kiwoom DEMO/mock. Without this flag only READ + preview runs.",
    )
    args = p.parse_args()

    symbol = args.symbol.strip().upper()
    if len(symbol) != 6 or not symbol.isdigit():
        raise SystemExit("[BLOCKED] symbol must be a 6-digit domestic code")
    if args.qty != 1:
        raise SystemExit("[BLOCKED] V0 demo roundtrip hard limit is exactly 1 share")
    if args.poll_seconds < 1.0:
        raise SystemExit("[BLOCKED] poll interval must be >= 1 second")

    # Hard safety boundary: this script never accepts or derives real mode.
    broker = KiwoomCliBroker(mode="demo")
    store = StateStore(DEMO_STATE_DB)

    print("===== V0 DEMO ROUNDTRIP PREFLIGHT =====")
    print("mode=demo (hard-coded; real mode is not supported by this script)")
    print(f"symbol={symbol} qty=1 exchange={args.exchange}")
    print(f"state_db={DEMO_STATE_DB}")

    snapshot = read_and_save_snapshot(broker, store)
    sync_open_intents(store, snapshot)
    _print_snapshot("DEMO PRE-STATE", snapshot)

    existing_local = store.open_intents()
    existing_broker_open = open_order_rows(snapshot)
    existing_holding = holding_qty(snapshot, symbol)

    block_reasons: list[str] = []
    if existing_local:
        block_reasons.append("LOCAL_OPEN_DEMO_INTENTS_PRESENT")
    if existing_broker_open:
        block_reasons.append("BROKER_OPEN_DEMO_ORDERS_PRESENT")
    if existing_holding != 0:
        block_reasons.append(f"PREEXISTING_DEMO_HOLDING_{existing_holding}")

    if block_reasons:
        print("[BLOCKED] " + ", ".join(block_reasons))
        print("Resolve the demo account state first; this script will not guess or stack positions.")
        return 2

    buy_preview = broker.preview_order(
        side="buy",
        symbol=symbol,
        qty=1,
        exchange=args.exchange,
        order_type="market",
    )
    print("\n===== DEMO BUY PREVIEW — NOT SENT =====")
    print(buy_preview.text)

    if not args.send_demo_orders:
        print("\n[SAFE] Preview only. No demo order was sent.")
        print("Re-run with --send-demo-orders only when the demo/mock account is ready.")
        return 0

    stamp = datetime.now(KST).strftime("%Y%m%d-%H%M%S")
    buy_intent = f"DEMO-{stamp}-{symbol}-BUY-1"
    sell_intent = f"DEMO-{stamp}-{symbol}-SELL-1"

    try:
        store.reserve_intent(
            intent_id=buy_intent,
            account_masked="DEMO",
            symbol=symbol,
            side="BUY",
            qty=1,
            requested_price=None,
            mode="DEMO",
        )
    except DuplicateIntentError as exc:
        print(f"[BLOCKED] {exc}")
        return 2

    try:
        buy = broker.submit_order(
            side="buy",
            symbol=symbol,
            qty=1,
            exchange=args.exchange,
            order_type="market",
            confirm_live_write=True,
        )
    except Exception as exc:
        store.update_intent(
            buy_intent,
            status="SUBMIT_ERROR_REVIEW",
            last_error=f"{type(exc).__name__}: {exc}",
        )
        print(f"[DEMO BUY ERROR] {type(exc).__name__}: {exc}")
        return 3

    buy_order_no = _extract_order_no(buy.payload)
    store.update_intent(
        buy_intent,
        status="SUBMITTED",
        broker_order_no=buy_order_no or None,
        raw_response=buy.payload,
    )
    print("\n===== DEMO BUY SUBMITTED =====")
    print(json.dumps(buy.payload, ensure_ascii=False, indent=2))

    if not buy_order_no:
        print("[BLOCKED] demo buy returned no order number; do not continue")
        return 3

    if not _wait_for(
        broker=broker,
        store=store,
        symbol=symbol,
        intent_id=buy_intent,
        expected_holding_qty=1,
        timeout_seconds=args.timeout_seconds,
        poll_seconds=args.poll_seconds,
    ):
        print("[STOP] demo buy did not reach FILLED + holding=1 within timeout.")
        print("Do not resend blindly. Inspect demo open orders/fills first.")
        return 4

    print("[PASS] DEMO BUY: FILLED + holdings=1")

    # Re-read immediately before the exit order.  Any open broker order or
    # unexpected holding count is a hard stop.
    pre_sell = read_and_save_snapshot(broker, store)
    sync_open_intents(store, pre_sell)
    if open_order_rows(pre_sell):
        print("[BLOCKED] broker open orders appeared before demo sell")
        return 4
    if holding_qty(pre_sell, symbol) != 1:
        print("[BLOCKED] expected exactly one demo share before sell")
        return 4

    try:
        store.reserve_intent(
            intent_id=sell_intent,
            account_masked="DEMO",
            symbol=symbol,
            side="SELL",
            qty=1,
            requested_price=None,
            mode="DEMO",
        )
    except DuplicateIntentError as exc:
        print(f"[BLOCKED] {exc}")
        return 2

    try:
        sell = broker.submit_order(
            side="sell",
            symbol=symbol,
            qty=1,
            exchange=args.exchange,
            order_type="market",
            confirm_live_write=True,
        )
    except Exception as exc:
        store.update_intent(
            sell_intent,
            status="SUBMIT_ERROR_REVIEW",
            last_error=f"{type(exc).__name__}: {exc}",
        )
        print(f"[DEMO SELL ERROR] {type(exc).__name__}: {exc}")
        return 5

    sell_order_no = _extract_order_no(sell.payload)
    store.update_intent(
        sell_intent,
        status="SUBMITTED",
        broker_order_no=sell_order_no or None,
        raw_response=sell.payload,
    )
    print("\n===== DEMO SELL SUBMITTED =====")
    print(json.dumps(sell.payload, ensure_ascii=False, indent=2))

    if not sell_order_no:
        print("[BLOCKED] demo sell returned no order number")
        return 5

    if not _wait_for(
        broker=broker,
        store=store,
        symbol=symbol,
        intent_id=sell_intent,
        expected_holding_qty=0,
        timeout_seconds=args.timeout_seconds,
        poll_seconds=args.poll_seconds,
    ):
        print("[STOP] demo sell did not reach FILLED + holding=0 within timeout.")
        print("Inspect demo open orders/fills; do not blindly resend.")
        return 6

    final_snapshot = read_and_save_snapshot(broker, store)
    _print_snapshot("DEMO FINAL STATE", final_snapshot)
    if open_order_rows(final_snapshot):
        print("[FAIL] final broker open orders are not zero")
        return 7
    if holding_qty(final_snapshot, symbol) != 0:
        print("[FAIL] final holding is not zero")
        return 7

    print("\n[PASS] V0 DEMO ROUNDTRIP COMPLETE")
    print(f"buy_intent={buy_intent} buy_order_no={buy_order_no}")
    print(f"sell_intent={sell_intent} sell_order_no={sell_order_no}")
    print("final_holding=0 broker_open_orders=0")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BrokerCliError as exc:
        print(f"[BROKER ERROR] {exc}")
        raise SystemExit(1)
