"""One connection at a time: next-day 3211 EXIT never depends on new-BUY arm.
All tests use fake broker and temporary journal; no real orders.
"""
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from exit_write_journal import ExitWriteJournal
from safety import ExecutionConfig, assert_live_write_allowed, validate_order_request
from test_v1_live_sell_adapter_fixture import SpyBroker, plan, snapshot
from v1_live_sell_adapter import execute_live_sell_once

KST = ZoneInfo("Asia/Seoul")


def main():
    yesterday = (datetime.now(KST) - timedelta(days=1)).date().isoformat()
    with TemporaryDirectory(prefix="3211-nextday-fixture-") as td:
        root = Path(td)
        cfg = ExecutionConfig(
            auto_mode="LIVE_SMALL", kill_switch=False,
            allow_live_orders=True, daily_arm_date=yesterday,
            allowed_account="1234563211",
            allowed_symbols=frozenset({"005930"}), max_order_qty=1,
            state_db=root / "v0.sqlite3", auth_env=root / "auth.env")
        data = dict(account=cfg.allowed_account, symbol="005930",
                    qty=1, intent_id="TEST-NEXTDAY-3211",
                    now=datetime.now(KST))
        assert "DAILY_ARM_DATE_MISMATCH" in validate_order_request(
            cfg, side="BUY", **data)
        assert validate_order_request(cfg, side="SELL", **data) == []
        try:
            assert_live_write_allowed(cfg)
            raise AssertionError("Fresh BUY passed expired daily arm")
        except RuntimeError as exc:
            assert "DAILY_ARM_DATE" in str(exc)
        assert_live_write_allowed(cfg, side="SELL")
        for changed, expected in (
            (replace(cfg, kill_switch=True), "KILL_SWITCH_ON"),
            (replace(cfg, allow_live_orders=False),
             "LIVE_ORDERS_NOT_EXPLICITLY_ALLOWED"),
            (replace(cfg, allowed_symbols=frozenset({"000660"})),
             "SYMBOL_NOT_ALLOWED"),
            (replace(cfg, allowed_account="1234564311"),
             "ACCOUNT_NOT_ALLOWED"),
        ):
            assert expected in validate_order_request(
                changed, side="SELL", **data)

        journal = ExitWriteJournal(root / "write.sqlite3")
        broker = SpyBroker(journal)
        doc = plan("V1-LIVE-FIXTURE-001")
        result = execute_live_sell_once(
            cfg=cfg, plan_row=doc, snapshot=snapshot(1),
            local_open_intent_count=0, broker=broker, journal=journal,
            mark_exit_submitted=lambda *_args, **_kwargs: None)
        assert result.broker_order_no == "0099999"
        assert len(broker.submit_calls) == 1
        assert journal.get(doc["exit_intent_id"])["state"] == "ACKED"
        try:
            execute_live_sell_once(
                cfg=cfg, plan_row=doc, snapshot=snapshot(1),
                local_open_intent_count=0, broker=broker, journal=journal,
                mark_exit_submitted=lambda *_args, **_kwargs: None)
            raise AssertionError("Duplicate SELL escaped journal")
        except RuntimeError as exc:
            assert "DUPLICATE" in str(exc)
        assert len(broker.submit_calls) == 1
    print("PASS next-day V1 SELL; expired-arm BUY blocked; account/kill/duplicate guards intact")


if __name__ == "__main__":
    main()
