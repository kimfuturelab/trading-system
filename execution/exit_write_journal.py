from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


class DuplicateWriteReservation(RuntimeError):
    pass


class ExitWriteJournal:
    """Persistent write-ahead journal for V1 broker SELL.

    Safety goal: once an EXIT intent reaches the broker-write boundary, the
    logical write is reserved on disk *before* `submit_order` is called.
    Any existing row blocks another write for the same exit_intent_id until
    broker truth is reconciled explicitly.
    """

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
        return datetime.now(KST).isoformat(timespec="seconds")

    def _init_schema(self) -> None:
        with self.connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS exit_write_journal (
                    exit_intent_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    qty INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    reserved_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    broker_order_no TEXT,
                    last_error TEXT,
                    raw_response_json TEXT
                );

                CREATE TABLE IF NOT EXISTS exit_write_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exit_intent_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_exit_write_state
                    ON exit_write_journal(state);
                CREATE INDEX IF NOT EXISTS idx_exit_write_event_intent
                    ON exit_write_events(exit_intent_id, id);
                """
            )

    def _event(
        self,
        con: sqlite3.Connection,
        *,
        exit_intent_id: str,
        event_type: str,
        payload: Any,
    ) -> None:
        con.execute(
            """
            INSERT INTO exit_write_events(exit_intent_id, created_at, event_type, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                exit_intent_id,
                self.now_text(),
                event_type,
                json.dumps(payload, ensure_ascii=False, default=str),
            ),
        )

    def reserve_once(
        self,
        *,
        exit_intent_id: str,
        plan_id: str,
        symbol: str,
        qty: int,
    ) -> dict[str, Any]:
        exit_intent_id = str(exit_intent_id or "").strip()
        plan_id = str(plan_id or "").strip()
        symbol = str(symbol or "").strip().upper()
        qty = int(qty)
        if not exit_intent_id:
            raise ValueError("exit_intent_id required")
        if not plan_id:
            raise ValueError("plan_id required")
        if not symbol:
            raise ValueError("symbol required")
        if qty <= 0:
            raise ValueError("qty must be positive")

        now = self.now_text()
        try:
            with self.connect() as con:
                con.execute("BEGIN IMMEDIATE")
                con.execute(
                    """
                    INSERT INTO exit_write_journal(
                        exit_intent_id, plan_id, symbol, qty, state,
                        reserved_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'WRITE_RESERVED', ?, ?)
                    """,
                    (exit_intent_id, plan_id, symbol, qty, now, now),
                )
                self._event(
                    con,
                    exit_intent_id=exit_intent_id,
                    event_type="WRITE_RESERVED",
                    payload={"plan_id": plan_id, "symbol": symbol, "qty": qty},
                )
        except sqlite3.IntegrityError as exc:
            existing = self.get(exit_intent_id)
            state = existing.get("state") if existing else "UNKNOWN"
            raise DuplicateWriteReservation(
                f"existing write reservation: {exit_intent_id} state={state}"
            ) from exc
        row = self.get(exit_intent_id)
        if row is None:
            raise RuntimeError("write reservation missing after insert")
        return row

    def _mark(
        self,
        exit_intent_id: str,
        *,
        state: str,
        broker_order_no: str | None = None,
        last_error: str | None = None,
        raw_response: Any | None = None,
    ) -> dict[str, Any]:
        payload = None
        if raw_response is not None:
            payload = json.dumps(raw_response, ensure_ascii=False, default=str)
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            cur = con.execute(
                """
                UPDATE exit_write_journal
                SET state=?, updated_at=?,
                    broker_order_no=COALESCE(?, broker_order_no),
                    last_error=?,
                    raw_response_json=COALESCE(?, raw_response_json)
                WHERE exit_intent_id=?
                """,
                (
                    state,
                    self.now_text(),
                    str(broker_order_no or "").strip() or None,
                    last_error,
                    payload,
                    exit_intent_id,
                ),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"write reservation not found: {exit_intent_id}")
            self._event(
                con,
                exit_intent_id=exit_intent_id,
                event_type=state,
                payload={
                    "broker_order_no": broker_order_no,
                    "last_error": last_error,
                    "raw_response": raw_response,
                },
            )
        row = self.get(exit_intent_id)
        if row is None:
            raise RuntimeError("write reservation missing after update")
        return row

    def mark_acked(
        self,
        exit_intent_id: str,
        *,
        broker_order_no: str,
        raw_response: Any | None = None,
    ) -> dict[str, Any]:
        return self._mark(
            exit_intent_id,
            state="ACKED",
            broker_order_no=broker_order_no,
            last_error=None,
            raw_response=raw_response,
        )

    def mark_unknown_ack(self, exit_intent_id: str, *, error: str) -> dict[str, Any]:
        return self._mark(
            exit_intent_id,
            state="UNKNOWN_ACK",
            last_error=str(error),
        )

    def mark_review_required(self, exit_intent_id: str, *, error: str) -> dict[str, Any]:
        return self._mark(
            exit_intent_id,
            state="REVIEW_REQUIRED",
            last_error=str(error),
        )

    def get(self, exit_intent_id: str) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute(
                "SELECT * FROM exit_write_journal WHERE exit_intent_id=?",
                (exit_intent_id,),
            ).fetchone()
        return dict(row) if row else None

    def events(self, exit_intent_id: str) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute(
                """
                SELECT * FROM exit_write_events
                WHERE exit_intent_id=? ORDER BY id
                """,
                (exit_intent_id,),
            ).fetchall()
        return [dict(row) for row in rows]
