from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
PLAN_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,96}$")
SYMBOL_RE = re.compile(r"^[0-9A-Z]{6}$")

EXIT_OPEN_STATUSES = (
    "EXIT_ARMED",
    "EXIT_TRIGGERED",
    "EXIT_SUBMITTED",
    "PARTIAL_EXIT",
    "HALTED",
)
EXIT_TERMINAL_STATUSES = ("CLOSED", "CANCELLED")


class DuplicateExitPlanError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExitPlan:
    plan_id: str
    symbol: str
    qty: int
    entry_price: int
    stop_price: int
    take_profit_price: int
    timecut_at: datetime


@dataclass(frozen=True)
class ExitTrigger:
    reason: str
    price: int
    triggered_at: datetime


def now_kst() -> datetime:
    return datetime.now(KST)


def _as_kst(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timecut_at/now must be timezone-aware")
    return value.astimezone(KST)


def parse_iso_datetime(value: str) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("datetime is empty")
    parsed = datetime.fromisoformat(text)
    return _as_kst(parsed)


def exit_plan_errors(plan: ExitPlan) -> list[str]:
    errors: list[str] = []
    plan_id = str(plan.plan_id or "").strip()
    symbol = str(plan.symbol or "").strip().upper()

    if not PLAN_ID_RE.fullmatch(plan_id):
        errors.append("INVALID_PLAN_ID")
    if not SYMBOL_RE.fullmatch(symbol):
        errors.append("INVALID_SYMBOL")
    if int(plan.qty) <= 0:
        errors.append("QTY_MUST_BE_POSITIVE")
    if int(plan.entry_price) <= 0:
        errors.append("ENTRY_PRICE_MUST_BE_POSITIVE")
    if int(plan.stop_price) <= 0:
        errors.append("STOP_PRICE_MUST_BE_POSITIVE")
    if int(plan.take_profit_price) <= 0:
        errors.append("TAKE_PROFIT_PRICE_MUST_BE_POSITIVE")

    if (
        int(plan.stop_price) > 0
        and int(plan.entry_price) > 0
        and int(plan.stop_price) >= int(plan.entry_price)
    ):
        errors.append("STOP_MUST_BE_BELOW_ENTRY_FOR_LONG")
    if (
        int(plan.take_profit_price) > 0
        and int(plan.entry_price) > 0
        and int(plan.take_profit_price) <= int(plan.entry_price)
    ):
        errors.append("TAKE_PROFIT_MUST_BE_ABOVE_ENTRY_FOR_LONG")

    try:
        _as_kst(plan.timecut_at)
    except Exception:
        errors.append("TIMECUT_MUST_BE_TIMEZONE_AWARE")

    return errors


def evaluate_exit_trigger(
    plan: ExitPlan,
    *,
    last_price: int,
    now: datetime,
) -> ExitTrigger | None:
    errors = exit_plan_errors(plan)
    if errors:
        raise ValueError(", ".join(errors))
    if int(last_price) <= 0:
        raise ValueError("LAST_PRICE_MUST_BE_POSITIVE")

    current = _as_kst(now)
    timecut = _as_kst(plan.timecut_at)

    # Safety precedence: STOP > TAKE_PROFIT > TIMECUT.
    if int(last_price) <= int(plan.stop_price):
        return ExitTrigger("STOP", int(last_price), current)
    if int(last_price) >= int(plan.take_profit_price):
        return ExitTrigger("TAKE_PROFIT", int(last_price), current)
    if current >= timecut:
        return ExitTrigger("TIMECUT", int(last_price), current)
    return None


def pre_exit_gate_errors(
    plan_row: dict[str, Any],
    *,
    broker_holding_qty: int,
    broker_open_order_count: int,
    local_open_intent_count: int,
) -> list[str]:
    errors: list[str] = []
    status = str(plan_row.get("status") or "")
    remaining = int(plan_row.get("remaining_qty") or 0)
    exit_intent_id = str(plan_row.get("exit_intent_id") or "").strip()

    if status != "EXIT_TRIGGERED":
        errors.append("EXIT_PLAN_NOT_TRIGGERED")
    if not exit_intent_id:
        errors.append("EXIT_INTENT_ID_NOT_RESERVED")
    if broker_open_order_count > 0:
        errors.append("BROKER_OPEN_ORDERS_PRESENT")
    if local_open_intent_count > 0:
        errors.append("LOCAL_OPEN_INTENTS_PRESENT")
    if broker_holding_qty <= 0:
        errors.append("NO_BROKER_HOLDING_TO_EXIT")
    elif broker_holding_qty != remaining:
        errors.append(f"BROKER_HOLDING_MISMATCH({broker_holding_qty}!={remaining})")
    return errors


class ExitPlanStore:
    def __init__(self, path: Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=10000")
        return con

    @staticmethod
    def now_text() -> str:
        return now_kst().isoformat(timespec="seconds")

    def _init_schema(self) -> None:
        with self.connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS exit_plans (
                    plan_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    qty INTEGER NOT NULL,
                    entry_price INTEGER NOT NULL,
                    stop_price INTEGER NOT NULL,
                    take_profit_price INTEGER NOT NULL,
                    timecut_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    trigger_reason TEXT,
                    trigger_price INTEGER,
                    triggered_at TEXT,
                    exit_intent_id TEXT,
                    broker_order_no TEXT,
                    remaining_qty INTEGER NOT NULL,
                    last_error TEXT
                );

                CREATE TABLE IF NOT EXISTS exit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_exit_plan_status
                    ON exit_plans(status);
                CREATE INDEX IF NOT EXISTS idx_exit_event_plan_time
                    ON exit_events(plan_id, created_at);
                """
            )

    def _event(
        self,
        con: sqlite3.Connection,
        *,
        plan_id: str,
        event_type: str,
        payload: Any,
    ) -> None:
        con.execute(
            """
            INSERT INTO exit_events(plan_id, created_at, event_type, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                plan_id,
                self.now_text(),
                event_type,
                json.dumps(payload, ensure_ascii=False, default=str),
            ),
        )

    def reserve_plan(self, plan: ExitPlan) -> dict[str, Any]:
        errors = exit_plan_errors(plan)
        if errors:
            raise ValueError(", ".join(errors))

        normalized_symbol = plan.symbol.strip().upper()
        timecut_text = _as_kst(plan.timecut_at).isoformat(timespec="seconds")
        try:
            with self.connect() as con:
                con.execute(
                    """
                    INSERT INTO exit_plans(
                        plan_id, created_at, symbol, qty, entry_price, stop_price,
                        take_profit_price, timecut_at, status, remaining_qty
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'EXIT_ARMED', ?)
                    """,
                    (
                        plan.plan_id.strip(),
                        self.now_text(),
                        normalized_symbol,
                        int(plan.qty),
                        int(plan.entry_price),
                        int(plan.stop_price),
                        int(plan.take_profit_price),
                        timecut_text,
                        int(plan.qty),
                    ),
                )
                self._event(
                    con,
                    plan_id=plan.plan_id.strip(),
                    event_type="PLAN_RESERVED",
                    payload={
                        "symbol": normalized_symbol,
                        "qty": int(plan.qty),
                        "entry_price": int(plan.entry_price),
                        "stop_price": int(plan.stop_price),
                        "take_profit_price": int(plan.take_profit_price),
                        "timecut_at": timecut_text,
                    },
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateExitPlanError(
                f"이미 존재하는 plan_id: {plan.plan_id}"
            ) from exc
        row = self.get_plan(plan.plan_id)
        if row is None:
            raise RuntimeError("reserved plan not found")
        return row

    def get_plan(self, plan_id: str) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute(
                "SELECT * FROM exit_plans WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_plans(self) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute(
                "SELECT * FROM exit_plans ORDER BY created_at, plan_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def list_events(self, plan_id: str) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute(
                """
                SELECT * FROM exit_events
                WHERE plan_id = ?
                ORDER BY id
                """,
                (plan_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def trigger_once(
        self,
        plan_id: str,
        trigger: ExitTrigger,
    ) -> tuple[bool, dict[str, Any]]:
        if trigger.reason not in {"STOP", "TAKE_PROFIT", "TIMECUT"}:
            raise ValueError(f"unsupported trigger reason: {trigger.reason}")
        if int(trigger.price) <= 0:
            raise ValueError("trigger price must be positive")
        triggered_at = _as_kst(trigger.triggered_at).isoformat(timespec="seconds")

        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT * FROM exit_plans WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"plan_id 없음: {plan_id}")

            current = dict(row)
            if current.get("trigger_reason"):
                return False, current
            if current.get("status") not in {"EXIT_ARMED", "POSITION"}:
                raise RuntimeError(
                    f"trigger 불가 상태: {current.get('status')}"
                )

            exit_intent_id = f"{plan_id}-EXIT"
            con.execute(
                """
                UPDATE exit_plans
                SET status='EXIT_TRIGGERED',
                    trigger_reason=?,
                    trigger_price=?,
                    triggered_at=?,
                    exit_intent_id=?,
                    last_error=NULL
                WHERE plan_id=?
                """,
                (
                    trigger.reason,
                    int(trigger.price),
                    triggered_at,
                    exit_intent_id,
                    plan_id,
                ),
            )
            self._event(
                con,
                plan_id=plan_id,
                event_type="TRIGGER_RESERVED",
                payload={
                    "reason": trigger.reason,
                    "price": int(trigger.price),
                    "triggered_at": triggered_at,
                    "exit_intent_id": exit_intent_id,
                },
            )
            updated = con.execute(
                "SELECT * FROM exit_plans WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
            if updated is None:
                raise RuntimeError("updated plan not found")
            return True, dict(updated)

    def mark_exit_submitted(
        self,
        plan_id: str,
        *,
        broker_order_no: str | None = None,
    ) -> dict[str, Any]:
        with self.connect() as con:
            row = con.execute(
                "SELECT * FROM exit_plans WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"plan_id 없음: {plan_id}")
            current = dict(row)
            if current.get("status") != "EXIT_TRIGGERED":
                raise RuntimeError(
                    f"EXIT_SUBMITTED 전이 불가 상태: {current.get('status')}"
                )
            con.execute(
                """
                UPDATE exit_plans
                SET status='EXIT_SUBMITTED',
                    broker_order_no=COALESCE(?, broker_order_no),
                    last_error=NULL
                WHERE plan_id=?
                """,
                (str(broker_order_no or "").strip() or None, plan_id),
            )
            self._event(
                con,
                plan_id=plan_id,
                event_type="EXIT_SUBMITTED",
                payload={"broker_order_no": broker_order_no},
            )
        result = self.get_plan(plan_id)
        if result is None:
            raise RuntimeError("submitted plan not found")
        return result

    def reconcile_holding(
        self,
        plan_id: str,
        *,
        broker_holding_qty: int,
    ) -> dict[str, Any]:
        if int(broker_holding_qty) < 0:
            raise ValueError("broker_holding_qty must be >= 0")

        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT * FROM exit_plans WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"plan_id 없음: {plan_id}")
            current = dict(row)
            remaining = int(current.get("remaining_qty") or 0)
            broker_qty = int(broker_holding_qty)

            if broker_qty == 0:
                con.execute(
                    """
                    UPDATE exit_plans
                    SET status='CLOSED',
                        remaining_qty=0,
                        last_error=NULL
                    WHERE plan_id=?
                    """,
                    (plan_id,),
                )
                self._event(
                    con,
                    plan_id=plan_id,
                    event_type="CLOSED_BY_BROKER_HOLDING",
                    payload={"broker_holding_qty": 0},
                )
            elif broker_qty < remaining:
                con.execute(
                    """
                    UPDATE exit_plans
                    SET status='PARTIAL_EXIT',
                        remaining_qty=?,
                        last_error=NULL
                    WHERE plan_id=?
                    """,
                    (broker_qty, plan_id),
                )
                self._event(
                    con,
                    plan_id=plan_id,
                    event_type="PARTIAL_EXIT_BY_BROKER_HOLDING",
                    payload={
                        "before_remaining_qty": remaining,
                        "broker_holding_qty": broker_qty,
                    },
                )
            elif broker_qty == remaining:
                self._event(
                    con,
                    plan_id=plan_id,
                    event_type="HOLDING_RECONCILED_NO_CHANGE",
                    payload={"broker_holding_qty": broker_qty},
                )
            else:
                message = f"BROKER_HOLDING_GREATER_THAN_REMAINING({broker_qty}>{remaining})"
                con.execute(
                    """
                    UPDATE exit_plans
                    SET status='HALTED',
                        last_error=?
                    WHERE plan_id=?
                    """,
                    (message, plan_id),
                )
                self._event(
                    con,
                    plan_id=plan_id,
                    event_type="HALTED_HOLDING_MISMATCH",
                    payload={
                        "remaining_qty": remaining,
                        "broker_holding_qty": broker_qty,
                    },
                )

        result = self.get_plan(plan_id)
        if result is None:
            raise RuntimeError("reconciled plan not found")
        return result

    def halt(self, plan_id: str, reason: str) -> dict[str, Any]:
        text = str(reason or "").strip() or "UNSPECIFIED"
        with self.connect() as con:
            cur = con.execute(
                """
                UPDATE exit_plans
                SET status='HALTED', last_error=?
                WHERE plan_id=?
                """,
                (text, plan_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"plan_id 없음: {plan_id}")
            self._event(
                con,
                plan_id=plan_id,
                event_type="HALTED",
                payload={"reason": text},
            )
        result = self.get_plan(plan_id)
        if result is None:
            raise RuntimeError("halted plan not found")
        return result
