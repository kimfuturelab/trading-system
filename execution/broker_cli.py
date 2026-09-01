from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class BrokerCliError(RuntimeError):
    pass


@dataclass(frozen=True)
class BrokerResult:
    command: tuple[str, ...]
    exit_code: int
    payload: dict[str, Any] | list[Any] | None
    text: str


class KiwoomCliBroker:
    """Thin adapter around the already-validated `kiwoomcli` binary.

    V0 deliberately reuses the server CLI/auth path instead of introducing a
    second REST client. This keeps Python automation close to the manual path
    that already passed a live 1-share roundtrip.
    """

    def __init__(self, *, mode: str = "real", cli: str | None = None):
        self.mode = (mode or "real").strip().lower()
        if self.mode not in {"real", "demo"}:
            raise ValueError(f"unsupported mode: {mode}")
        self.cli = cli or self._find_cli()

    @staticmethod
    def _find_cli() -> str:
        found = shutil.which("kiwoomcli")
        if found:
            return found
        fallback = Path.home() / ".local" / "bin" / "kiwoomcli"
        if fallback.exists():
            return str(fallback)
        raise BrokerCliError("kiwoomcli를 찾을 수 없습니다.")

    def _run(
        self,
        args: list[str],
        *,
        timeout: int = 25,
        expect_json: bool = True,
    ) -> BrokerResult:
        cmd = [self.cli, *args]
        cp = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=os.environ.copy(),
        )
        stdout = (cp.stdout or "").strip()
        stderr = (cp.stderr or "").strip()
        text = stdout
        if stderr:
            text = (text + "\n" + stderr).strip()

        payload: dict[str, Any] | list[Any] | None = None
        if expect_json and stdout:
            try:
                payload = json.loads(stdout)
            except json.JSONDecodeError as exc:
                raise BrokerCliError(
                    "kiwoomcli JSON 파싱 실패. stdout/stderr 형식이 바뀌었을 수 있습니다.\n"
                    + text[-1200:]
                ) from exc

        if cp.returncode != 0:
            raise BrokerCliError(
                f"kiwoomcli exit={cp.returncode}: {' '.join(cmd[1:])}\n{text[-1200:]}"
            )

        return BrokerResult(tuple(cmd), cp.returncode, payload, text)

    @staticmethod
    def _return_code(payload: Any) -> int | None:
        if not isinstance(payload, dict):
            return None
        value = payload.get("return_code")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def _require_api_ok(
        cls,
        result: BrokerResult,
        *,
        allow_codes: set[int] | None = None,
    ) -> BrokerResult:
        allowed = {0} if allow_codes is None else set(allow_codes)
        code = cls._return_code(result.payload)
        if code not in allowed:
            raise BrokerCliError(
                f"Kiwoom API return_code={code}, allowed={sorted(allowed)}\n{result.text[-1200:]}"
            )
        return result

    # ------------------------- READ -------------------------

    def account_list(self) -> BrokerResult:
        result = self._run(
            [
                "domestic", "accounts", "list",
                "--mode", self.mode,
                "--format", "json",
                "--named",
            ]
        )
        return self._require_api_ok(result)

    def cash(self, *, basis: str = "estimated") -> BrokerResult:
        if basis not in {"estimated", "normal"}:
            raise ValueError("basis must be estimated|normal")
        result = self._run(
            [
                "domestic", "accounts", "cash",
                "--cash-basis", basis,
                "--mode", self.mode,
                "--format", "json",
                "--named",
            ]
        )
        return self._require_api_ok(result)

    def holdings(self, *, basis: str = "total", exchange: str = "KRX") -> BrokerResult:
        if basis not in {"total", "individual"}:
            raise ValueError("basis must be total|individual")
        exchange = exchange.upper()
        if exchange not in {"KRX", "NXT"}:
            raise ValueError("exchange must be KRX|NXT")
        result = self._run(
            [
                "domestic", "accounts", "holdings",
                "--basis", basis,
                "--exchange", exchange,
                "--mode", self.mode,
                "--format", "json",
                "--named",
            ]
        )
        return self._require_api_ok(result)

    def order_fill_status(self) -> BrokerResult:
        """Read today's account-level order/fill status.

        Kiwoom can return code 20 / '관련자료가없습니다' when there has been no
        order for the day. In V0 reconciliation that is a valid empty state.
        """
        result = self._run(
            [
                "domestic", "accounts", "order-fill-status",
                "--asset-kind", "all",
                "--market", "all",
                "--side", "all",
                "--fill-status", "all",
                "--exchange", "ALL",
                "--mode", self.mode,
                "--format", "json",
                "--named",
            ]
        )
        return self._require_api_ok(result, allow_codes={0, 20})

    def list_open_orders(self) -> BrokerResult:
        """Read current unfilled/open orders via ka10075."""
        result = self._run(
            [
                "domestic", "orders", "list-open",
                "--stock-scope", "all",
                "--side", "all",
                "--exchange", "ALL",
                "--mode", self.mode,
                "--format", "json",
                "--named",
            ]
        )
        return self._require_api_ok(result, allow_codes={0, 20})

    def list_fills(self) -> BrokerResult:
        """Read today's fills via ka10076."""
        result = self._run(
            [
                "domestic", "orders", "list-fills",
                "--stock-scope", "all",
                "--side", "all",
                "--exchange", "ALL",
                "--mode", self.mode,
                "--format", "json",
                "--named",
            ]
        )
        return self._require_api_ok(result, allow_codes={0, 20})

    def order_chance(
        self,
        *,
        symbol: str,
        side: str = "buy",
        price: int | str,
    ) -> BrokerResult:
        """Read Kiwoom order/withdrawal capacity simulation for one symbol.

        This is a READ-only call. V0 uses the returned margin-tier
        주문가능금액 values to derive 최대주문가능금액. It never uses
        주문가능현금 as the buy-capacity gate.
        """
        side = side.lower()
        if side not in {"buy", "sell"}:
            raise ValueError("side must be buy|sell")
        price_text = str(price).strip().replace(",", "")
        if not price_text.isdigit() or int(price_text) <= 0:
            raise ValueError("price must be a positive integer")
        result = self._run(
            [
                "domestic", "orders", "chance",
                "--code", symbol,
                "--side", side,
                "--price", price_text,
                "--mode", self.mode,
                "--format", "json",
                "--named",
            ]
        )
        return self._require_api_ok(result)

    def read_snapshot(self) -> dict[str, Any]:
        account = self.account_list().payload
        cash = self.cash(basis="estimated").payload
        holdings = self.holdings(basis="total", exchange="KRX").payload
        orders = self.order_fill_status().payload
        open_orders = self.list_open_orders().payload
        fills = self.list_fills().payload
        return {
            "account": account,
            "cash": cash,
            "holdings": holdings,
            "order_fill_status": orders,
            "open_orders": open_orders,
            "fills": fills,
        }

    # --------------------- ORDER PREVIEW --------------------

    @staticmethod
    def _order_args(
        *,
        side: str,
        exchange: str,
        symbol: str,
        qty: int,
        order_type: str,
        price: str | None,
        mode: str,
        confirm: bool,
    ) -> list[str]:
        side = side.lower()
        if side not in {"buy", "sell"}:
            raise ValueError("side must be buy|sell")
        exchange = exchange.upper()
        if exchange not in {"KRX", "NXT", "SOR"}:
            raise ValueError("exchange must be KRX|NXT|SOR")
        order_type = order_type.lower()
        if order_type not in {"limit", "market"}:
            raise ValueError("order_type must be limit|market")
        if order_type == "limit" and not price:
            raise ValueError("limit order requires price")

        args = [
            "domestic", "orders", side,
            "--exchange", exchange,
            "--code", symbol,
            "--qty", str(int(qty)),
            "--order-type", order_type,
            "--mode", mode,
            "--named",
        ]
        if price:
            args += ["--price", str(price)]
        if confirm:
            args += ["--confirm", "--format", "json"]
        return args

    def preview_order(
        self,
        *,
        side: str,
        symbol: str,
        qty: int,
        exchange: str = "KRX",
        order_type: str = "market",
        price: str | None = None,
    ) -> BrokerResult:
        args = self._order_args(
            side=side,
            exchange=exchange,
            symbol=symbol,
            qty=qty,
            order_type=order_type,
            price=price,
            mode=self.mode,
            confirm=False,
        )
        return self._run(args, expect_json=False)

    def submit_order(
        self,
        *,
        side: str,
        symbol: str,
        qty: int,
        exchange: str = "KRX",
        order_type: str = "market",
        price: str | None = None,
        confirm_live_write: bool = False,
    ) -> BrokerResult:
        """Actual broker WRITE with an independent explicit boolean guard."""
        if not confirm_live_write:
            raise BrokerCliError("실주문 차단: confirm_live_write=True가 필요합니다.")
        args = self._order_args(
            side=side,
            exchange=exchange,
            symbol=symbol,
            qty=qty,
            order_type=order_type,
            price=price,
            mode=self.mode,
            confirm=True,
        )
        result = self._run(args, expect_json=True)
        return self._require_api_ok(result)
