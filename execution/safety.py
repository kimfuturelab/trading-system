from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
SYMBOL_RE = re.compile(r"^[0-9A-Z]{6}$")


def _truthy(value: str) -> bool:
    return str(value or "").strip().upper() in {"1", "Y", "YES", "TRUE", "ON"}


def load_env_file(path: Path, *, required: bool = True) -> None:
    path = path.expanduser()
    if not path.exists():
        if required:
            raise RuntimeError(f"환경파일 없음: {path}")
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip()


@dataclass(frozen=True)
class ExecutionConfig:
    auto_mode: str
    kill_switch: bool
    allow_live_orders: bool
    daily_arm_date: str
    allowed_account: str
    allowed_symbols: frozenset[str]
    max_order_qty: int
    state_db: Path
    auth_env: Path

    @classmethod
    def from_env(cls) -> "ExecutionConfig":
        auto_mode = os.getenv("AUTO_MODE", "OFF").strip().upper() or "OFF"
        if auto_mode not in {"OFF", "PAPER", "LIVE_SMALL", "LIVE"}:
            raise RuntimeError(f"지원하지 않는 AUTO_MODE: {auto_mode}")

        raw_symbols = os.getenv("ALLOWED_SYMBOLS", "")
        allowed_symbols = frozenset(
            x.strip().upper() for x in raw_symbols.split(",") if x.strip()
        )
        max_qty = int(os.getenv("MAX_ORDER_QTY", "1"))
        if max_qty <= 0:
            raise RuntimeError("MAX_ORDER_QTY는 1 이상이어야 합니다.")

        return cls(
            auto_mode=auto_mode,
            kill_switch=_truthy(os.getenv("KILL_SWITCH", "ON")),
            allow_live_orders=_truthy(os.getenv("ALLOW_LIVE_ORDERS", "NO")),
            daily_arm_date=os.getenv("DAILY_ARM_DATE", "").strip(),
            allowed_account=os.getenv("ALLOWED_ACCOUNT", "").strip(),
            allowed_symbols=allowed_symbols,
            max_order_qty=max_qty,
            state_db=Path(os.getenv("STATE_DB", "~/.trading-execution-v0.sqlite3")).expanduser(),
            auth_env=Path(os.getenv("AUTH_ENV", "~/api-read-v2.env")).expanduser(),
        )


def mask_account(account: str) -> str:
    text = str(account or "").strip()
    if len(text) <= 4:
        return "****" if text else "(unset)"
    return "*" * (len(text) - 4) + text[-4:]


def validate_order_request(
    cfg: ExecutionConfig,
    *,
    account: str,
    symbol: str,
    qty: int,
    side: str,
    intent_id: str,
    now: datetime | None = None,
) -> list[str]:
    """주문을 허용하지 못하는 이유 목록. 비어 있어야만 다음 단계로 진행 가능."""
    reasons: list[str] = []
    current = now or datetime.now(KST)
    side = str(side or "").strip().upper()
    symbol = str(symbol or "").strip().upper()
    account = str(account or "").strip()
    intent_id = str(intent_id or "").strip()

    if cfg.auto_mode == "OFF":
        reasons.append("AUTO_MODE_OFF")
    if cfg.kill_switch:
        reasons.append("KILL_SWITCH_ON")

    if side not in {"BUY", "SELL"}:
        reasons.append("INVALID_SIDE")
    if not SYMBOL_RE.fullmatch(symbol):
        reasons.append("INVALID_SYMBOL_FORMAT")
    if not cfg.allowed_symbols or symbol not in cfg.allowed_symbols:
        reasons.append("SYMBOL_NOT_ALLOWED")

    if qty <= 0:
        reasons.append("QTY_MUST_BE_POSITIVE")
    if qty > cfg.max_order_qty:
        reasons.append("QTY_EXCEEDS_V0_LIMIT")

    if not cfg.allowed_account:
        reasons.append("ALLOWED_ACCOUNT_NOT_SET")
    elif account != cfg.allowed_account:
        reasons.append("ACCOUNT_NOT_ALLOWED")

    if not intent_id:
        reasons.append("INTENT_ID_REQUIRED")

    if cfg.auto_mode in {"LIVE_SMALL", "LIVE"}:
        if not cfg.allow_live_orders:
            reasons.append("LIVE_ORDERS_NOT_EXPLICITLY_ALLOWED")
        today = current.strftime("%Y-%m-%d")
        if cfg.daily_arm_date != today:
            reasons.append("DAILY_ARM_DATE_MISMATCH")

    # PAPER는 실제 broker write를 호출하면 안 되고 상위 레이어에서 시뮬레이션해야 함.
    return reasons


def assert_live_write_allowed(cfg: ExecutionConfig) -> None:
    """broker의 실제 주문 메서드 직전에 한 번 더 호출하는 독립 write guard."""
    if cfg.auto_mode not in {"LIVE_SMALL", "LIVE"}:
        raise RuntimeError(f"실주문 차단: AUTO_MODE={cfg.auto_mode}")
    if cfg.kill_switch:
        raise RuntimeError("실주문 차단: KILL_SWITCH=ON")
    if not cfg.allow_live_orders:
        raise RuntimeError("실주문 차단: ALLOW_LIVE_ORDERS!=YES")
    today = datetime.now(KST).strftime("%Y-%m-%d")
    if cfg.daily_arm_date != today:
        raise RuntimeError("실주문 차단: DAILY_ARM_DATE가 오늘과 다름")
