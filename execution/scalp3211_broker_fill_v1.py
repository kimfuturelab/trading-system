"""Derive the single real 3211 BUY fill from Kiwoom + existing V0 intent.

No dependency on the DAY/SWING-only fill-shadow recovery job.
Does not send or revise orders; returns nothing on partial/ambiguous evidence.
"""
from datetime import datetime
from hashlib import sha256
from zoneinfo import ZoneInfo

from v0_engine import holding_qty, open_order_rows, order_status_rows, normalize_symbol

KST = ZoneInfo("Asia/Seoul")


def intent_id_from_stage4(handoff_id: str) -> str:
    key = sha256(handoff_id.encode("utf-8")).hexdigest()[:20]
    return "SCALP3211-BUY-" + key


def verified_first_fill(*, handoff_id: str, intents: list, snapshot: dict,
                        trade_date: str) -> dict | None:
    """Reconcile broker reported BUY fill, local intent and exactly one holding."""
    intent_id = intent_id_from_stage4(handoff_id)
    candidates = [i for i in intents if i.get("intent_id") == intent_id
                  and i.get("side") == "BUY" and i.get("symbol") == "005930"
                  and i.get("status") == "FILLED" and int(i.get("qty") or 0) == 1
                  and str(i.get("account_masked") or "").endswith("3211")]
    if len(candidates) != 1 or holding_qty(snapshot, "005930") != 1:
        return None
    if open_order_rows(snapshot):
        return None
    order_no = str(candidates[0].get("broker_order_no") or "").strip()
    if not order_no:
        return None
    matches = []
    for row in order_status_rows(snapshot):
        if (str(row.get("주문번호") or row.get("ord_no") or "").strip() != order_no
                or normalize_symbol(row.get("종목번호") or row.get("종목코드")) != "005930"
                or "매수" not in str(row.get("주문유형구분") or "")):
            continue
        try:
            qty = int(str(row.get("체결수량") or 0).replace(",", ""))
            price = int(str(row.get("체결단가") or 0).replace(",", ""))
        except ValueError:
            continue
        time_part = str(row.get("체결시간") or "").replace(":", "").strip()
        if qty != 1 or price <= 0 or len(time_part) != 6 or not time_part.isdigit():
            continue
        try:
            when = datetime.strptime(trade_date.replace("-", "") + time_part,
                                     "%Y%m%d%H%M%S").replace(tzinfo=KST)
        except ValueError:
            continue
        if when.date().isoformat() != trade_date:
            continue
        matches.append((price, when.isoformat(timespec="seconds")))
    if len(matches) != 1:
        return None
    price, first_fill_at = matches[0]
    return {
        "handoff_id": handoff_id, "order_no": order_no,
        "fill_no": "ORDER_FILL_STATUS", "price": price, "qty": 1,
        "first_fill_at": first_fill_at,
        "broker_source": "KIWOOM_REAL_ORDER_FILL_STATUS",
    }
