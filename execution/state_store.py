from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


class DuplicateIntentError(RuntimeError):
    pass


class StateStore:
    """V0 로컬 영속 저장소.

    핵심 목적은 broker 호출 전에 intent_id를 선점해 동일 주문의 재전송을 막는 것이다.
    broker/키움이 최종 진실이며, 이 DB만으로 포지션을 확정하지 않는다.
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

    def _init_schema(self) -> None:
        with self.connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS order_intents (
                    intent_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    account_masked TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    qty INTEGER NOT NULL,
                    requested_price TEXT,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    broker_order_no TEXT,
                    last_error TEXT,
                    raw_response_json TEXT
                );

                CREATE TABLE IF NOT EXISTS reconciliation_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    captured_at TEXT NOT NULL,
                    snapshot_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_intents_status
                    ON order_intents(status);
                CREATE INDEX IF NOT EXISTS idx_recon_type_time
                    ON reconciliation_snapshots(snapshot_type, captured_at);
                """
            )

    @staticmethod
    def now_text() -> str:
        return datetime.now(KST).isoformat(timespec="seconds")

    def reserve_intent(
        self,
        *,
        intent_id: str,
        account_masked: str,
        symbol: str,
        side: str,
        qty: int,
        requested_price: str | None,
        mode: str,
    ) -> None:
        try:
            with self.connect() as con:
                con.execute(
                    """
                    INSERT INTO order_intents (
                        intent_id, created_at, account_masked, symbol, side, qty,
                        requested_price, mode, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'RESERVED')
                    """,
                    (
                        intent_id,
                        self.now_text(),
                        account_masked,
                        symbol,
                        side,
                        int(qty),
                        requested_price,
                        mode,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateIntentError(f"이미 존재하는 intent_id: {intent_id}") from exc

    def update_intent(
        self,
        intent_id: str,
        *,
        status: str,
        broker_order_no: str | None = None,
        last_error: str | None = None,
        raw_response: Any | None = None,
    ) -> None:
        payload = None
        if raw_response is not None:
            payload = json.dumps(raw_response, ensure_ascii=False, default=str)
        with self.connect() as con:
            cur = con.execute(
                """
                UPDATE order_intents
                SET status = ?,
                    broker_order_no = COALESCE(?, broker_order_no),
                    last_error = ?,
                    raw_response_json = COALESCE(?, raw_response_json)
                WHERE intent_id = ?
                """,
                (status, broker_order_no, last_error, payload, intent_id),
            )
            if cur.rowcount != 1:
                raise RuntimeError(f"intent_id 없음: {intent_id}")

    def get_intent(self, intent_id: str) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute(
                "SELECT * FROM order_intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
        return dict(row) if row else None

    def open_intents(self) -> list[dict[str, Any]]:
        terminal = ("CLOSED", "CANCELLED", "REJECTED")
        placeholders = ",".join("?" for _ in terminal)
        with self.connect() as con:
            rows = con.execute(
                f"SELECT * FROM order_intents WHERE status NOT IN ({placeholders}) ORDER BY created_at",
                terminal,
            ).fetchall()
        return [dict(row) for row in rows]

    def save_snapshot(self, snapshot_type: str, payload: Any) -> int:
        text = json.dumps(payload, ensure_ascii=False, default=str)
        with self.connect() as con:
            cur = con.execute(
                """
                INSERT INTO reconciliation_snapshots(captured_at, snapshot_type, payload_json)
                VALUES (?, ?, ?)
                """,
                (self.now_text(), snapshot_type, text),
            )
            return int(cur.lastrowid)
