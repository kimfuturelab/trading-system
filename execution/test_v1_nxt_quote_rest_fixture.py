#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from quote_reader import read_nxt_quote_rest


@dataclass
class FakeResponse:
    payload: dict

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class FakeOpener:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, request, *, timeout: int):
        body = json.loads(request.data.decode("utf-8"))
        headers = {k.lower(): v for k, v in request.header_items()}
        call = {
            "url": request.full_url,
            "body": body,
            "headers": headers,
            "timeout": timeout,
        }
        self.calls.append(call)
        if request.full_url.endswith("/oauth2/token"):
            assert body["grant_type"] == "client_credentials"
            assert body["appkey"] == "fixture-key"
            assert body["secretkey"] == "fixture-secret"
            return FakeResponse(
                {
                    "return_code": 0,
                    "return_msg": "ok",
                    "token_type": "bearer",
                    "token": "fixture-token",
                }
            )
        if request.full_url.endswith("/api/dostk/stkinfo"):
            assert body == {"stk_cd": "005930_NX"}
            assert headers.get("api-id") == "ka10001"
            assert headers.get("authorization") == "Bearer fixture-token"
            return FakeResponse(
                {
                    "return_code": 0,
                    "return_msg": "ok",
                    "stk_cd": "005930_NX",
                    "stk_nm": "삼성전자",
                    "cur_prc": "+00260500",
                }
            )
        raise AssertionError(request.full_url)


def main() -> int:
    print("===== V1 NXT RAW REST QUOTE FIXTURE =====")
    print("[SAFE] fake HTTP only; no Kiwoom network and no order")
    old_key = os.environ.get("APP_KEY")
    old_secret = os.environ.get("APP_SECRET")
    try:
        os.environ["APP_KEY"] = "fixture-key"
        os.environ["APP_SECRET"] = "fixture-secret"
        opener = FakeOpener()
        quote = read_nxt_quote_rest(symbol="005930", mode="real", opener=opener)
        assert quote.symbol == "005930"
        assert quote.exchange == "NXT"
        assert quote.query_code == "005930_NX"
        assert quote.last_price == 260500
        assert len(opener.calls) == 2
        assert opener.calls[0]["url"] == "https://api.kiwoom.com/oauth2/token"
        assert opener.calls[1]["url"] == "https://api.kiwoom.com/api/dostk/stkinfo"
        print("[PASS] token -> ka10001 stk_cd=005930_NX -> cur_prc parsed")
        print("[PASS] CLI 6-char validator is not used on NXT REST quote path")
        print("[SAFE] no network or broker write occurred")
        return 0
    finally:
        if old_key is None:
            os.environ.pop("APP_KEY", None)
        else:
            os.environ["APP_KEY"] = old_key
        if old_secret is None:
            os.environ.pop("APP_SECRET", None)
        else:
            os.environ["APP_SECRET"] = old_secret


if __name__ == "__main__":
    raise SystemExit(main())
