from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from broker_cli import KiwoomCliBroker

KST = ZoneInfo("Asia/Seoul")
PRICE_KEYS = (
    "현재가",
    "cur_prc",
    "current_price",
    "last_price",
    "stck_prpr",
)


class QuotePriceError(RuntimeError):
    pass


class QuoteTransportError(RuntimeError):
    pass


@dataclass(frozen=True)
class QuoteSnapshot:
    symbol: str
    last_price: int
    captured_at: datetime
    payload: Any
    exchange: str = "KRX"
    query_code: str = ""


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
    signless = text[1:] if text[0] in {"+", "-"} else text
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
        raise QuotePriceError("현재가 필드를 찾지 못했습니다.")
    unique = sorted({price for _, price in candidates})
    if len(unique) != 1:
        raise QuotePriceError(
            "현재가 후보 필드 값이 서로 다릅니다: "
            + ", ".join(f"{key}={price}" for key, price in candidates)
        )
    return unique[0]


def exchange_query_code(symbol: str, exchange: str) -> str:
    exchange = str(exchange or "").strip().upper()
    if exchange == "KRX":
        return symbol
    if exchange == "NXT":
        return f"{symbol}_NX"
    raise ValueError("exchange must be KRX|NXT")


def _kiwoomcli_path() -> Path:
    found = shutil.which("kiwoomcli")
    if found:
        return Path(found)
    fallback = Path.home() / ".local" / "bin" / "kiwoomcli"
    if fallback.exists():
        return fallback
    raise QuoteTransportError("kiwoomcli executable not found")


def _cli_python_command(cli_path: Path) -> list[str]:
    try:
        first = cli_path.read_text(encoding="utf-8", errors="ignore").splitlines()[0].strip()
    except Exception as exc:
        raise QuoteTransportError("cannot inspect kiwoomcli interpreter") from exc
    if first.startswith("#!"):
        parts = shlex.split(first[2:].strip())
        if parts:
            return parts
    # Last-resort fallback. If kwcli is installed in the same interpreter this
    # works; otherwise the bridge fails closed with a clear error.
    return [sys.executable]


def _read_nxt_with_current_python(symbol: str, mode: str) -> dict[str, Any] | None:
    try:
        from kiwoom import get_client  # type: ignore
    except Exception:
        return None

    try:
        client = get_client(mode=mode)
        response = client.fetch_page(
            api_id="ka10001",
            path="/api/dostk/stkinfo",
            body={"stk_cd": exchange_query_code(symbol, "NXT")},
        )
        payload = response.body
        return payload if isinstance(payload, dict) else None
    except Exception:
        # The project Python may not share kwcli's secret/token store. In that
        # case fall through to the exact interpreter that owns kiwoomcli.
        return None


def _read_nxt_with_cli_runtime(symbol: str, mode: str) -> dict[str, Any]:
    cli = _kiwoomcli_path()
    python_cmd = _cli_python_command(cli)
    bridge = Path(__file__).with_name("nxt_quote_sdk_bridge.py")
    if not bridge.exists():
        raise QuoteTransportError("NXT SDK bridge file missing")

    cmd = [*python_cmd, str(bridge), "--symbol", symbol, "--mode", mode]
    try:
        cp = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=25,
        )
    except subprocess.TimeoutExpired as exc:
        raise QuoteTransportError("Kiwoom official SDK NXT quote timeout") from exc
    except Exception as exc:
        raise QuoteTransportError(
            f"Kiwoom official SDK bridge launch failed: {type(exc).__name__}"
        ) from exc

    stdout = (cp.stdout or "").strip()
    stderr = (cp.stderr or "").strip()
    try:
        envelope = json.loads(stdout) if stdout else {}
    except json.JSONDecodeError as exc:
        detail = (stderr or stdout)[-500:]
        raise QuoteTransportError(
            "Kiwoom official SDK bridge returned non-JSON output: " + detail
        ) from exc

    if cp.returncode != 0 or not envelope.get("ok"):
        etype = str(envelope.get("error_type") or "SDK_ERROR")
        error = str(envelope.get("error") or stderr or "unknown")[:500]
        raise QuoteTransportError(f"Kiwoom official SDK NXT quote failed: {etype}: {error}")

    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        raise QuoteTransportError("Kiwoom official SDK bridge payload is not an object")
    return payload


def read_nxt_quote_official(
    *,
    symbol: str,
    mode: str = "real",
) -> QuoteSnapshot:
    """READ-only NXT quote using Kiwoom's official Python client/runtime.

    kwcli's `domestic quotes price --code` currently performs a local 6-char
    validation, so it cannot pass the documented NXT code `005930_NX` through.
    Instead this function calls the same official Kiwoom Python runtime that
    backs kwcli and sends ka10001 with the exchange-specific code. This reuses
    kwcli's existing credential/token store; no new App Key/Secret flow exists
    in this project.
    """
    symbol = str(symbol or "").strip().upper()
    if len(symbol) != 6 or not symbol.isalnum():
        raise ValueError("symbol must be a 6-character stock code")
    mode = str(mode or "real").strip().lower()
    if mode not in {"real", "demo"}:
        raise ValueError("mode must be real|demo")
    if mode == "demo":
        raise QuoteTransportError("Kiwoom demo does not support NXT")

    payload = _read_nxt_with_current_python(symbol, mode)
    if payload is None:
        payload = _read_nxt_with_cli_runtime(symbol, mode)

    code = payload.get("return_code")
    try:
        normalized = int(code) if code is not None else None
    except (TypeError, ValueError):
        normalized = None
    if normalized not in {None, 0}:
        msg = str(payload.get("return_msg") or "").strip()
        raise QuoteTransportError(
            f"Kiwoom ka10001 return_code={normalized}: {msg[:300]}"
        )

    query_code = exchange_query_code(symbol, "NXT")
    return QuoteSnapshot(
        symbol=symbol,
        last_price=extract_last_price(payload),
        captured_at=datetime.now(KST),
        payload=payload,
        exchange="NXT",
        query_code=query_code,
    )


def read_quote_snapshot(
    broker: KiwoomCliBroker,
    *,
    symbol: str,
    exchange: str = "KRX",
) -> QuoteSnapshot:
    symbol = str(symbol or "").strip().upper()
    if len(symbol) != 6 or not symbol.isalnum():
        raise ValueError("symbol must be a 6-character stock code")
    exchange = str(exchange or "").strip().upper()

    if exchange == "NXT":
        return read_nxt_quote_official(symbol=symbol, mode=broker.mode)
    if exchange != "KRX":
        raise ValueError("exchange must be KRX|NXT")

    # KRX keeps the already validated kwcli path.
    result = broker._run(  # noqa: SLF001
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
    broker._require_api_ok(result)  # noqa: SLF001
    price = extract_last_price(result.payload)
    return QuoteSnapshot(
        symbol=symbol,
        last_price=price,
        captured_at=datetime.now(KST),
        payload=result.payload,
        exchange="KRX",
        query_code=symbol,
    )
