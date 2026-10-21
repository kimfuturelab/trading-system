from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account

HOME = Path.home()
KST = ZoneInfo("Asia/Seoul")
ROOT = HOME / "trading-system-stage3"
FILL_SHADOW_DIR = ROOT / "fill-shadow-v1"
if str(FILL_SHADOW_DIR) not in sys.path:
    sys.path.insert(0, str(FILL_SHADOW_DIR))
from trade_plan_link_v1 import save_plan

DECISION_JSON = ROOT / "runtime" / "decision_packet_v1_latest.json"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
CREDENTIALS = HOME / ".config" / "trading-system" / "google-sheets-writer.json"

STAGE4_ID = "1zhv_PEVqTG2SfYqNe-kdNEK6WpOrpe8qxOFL3Gez6mE"
MASTER_ID = "1D2Dl0AtVCWvwn9R8xa-EnovERQVsUpQaKP4Z2B1Y21s"

SUPPORTED = {
    "000660": "SK하이닉스",
    "005930": "삼성전자",
}


class HandoffError(RuntimeError):
    pass


def session() -> AuthorizedSession:
    cred = service_account.Credentials.from_service_account_file(
        str(CREDENTIALS), scopes=SCOPES
    )
    return AuthorizedSession(cred)


def qrange(sheet: str, cell_range: str) -> str:
    return f"'{sheet}'!{cell_range}"


def get_values(s: AuthorizedSession, spreadsheet_id: str, sheet: str, cell_range: str):
    rng = qrange(sheet, cell_range)
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{rng}"
    r = s.get(url, timeout=20)
    if r.status_code != 200:
        raise HandoffError(f"READ_HTTP_{r.status_code}:{r.text[:300]}")
    return r.json().get("values") or []


def update_values(s: AuthorizedSession, spreadsheet_id: str, updates: list[dict]):
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values:batchUpdate"
    payload = {
        "valueInputOption": "USER_ENTERED",
        "data": [
            {
                "range": qrange(x["sheet"], x["range"]),
                "majorDimension": "ROWS",
                "values": x["values"],
            }
            for x in updates
        ],
    }
    r = s.post(url, json=payload, timeout=25)
    if r.status_code not in (200, 201):
        raise HandoffError(f"WRITE_HTTP_{r.status_code}:{r.text[:500]}")
    return r.json()


def entry_map(rows):
    out = {}
    for row in rows:
        if row and str(row[0] or "").strip():
            out[str(row[0]).strip()] = row[1] if len(row) > 1 else None
    return out


def parse_number(value):
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return int(round(float(value)))
    s = str(value).replace(",", "").replace("₩", "").strip()
    m = re.search(r"-?\d+(?:\.\d+)?", s)
    return int(round(float(m.group(0)))) if m else None


def account_alias_from_label(value):
    s = str(value or "").strip().lower()
    # "단기계좌/장기계좌" 같은 구형 고정 라벨은 실제 DAY/SWING 선택값이 아니므로 사용 금지.
    if s in {"day", "day 계좌", "단타", "단타계좌"}:
        return "day"
    if s in {"swing", "swing 계좌", "스윙", "스윙계좌"}:
        return "swing"
    if s in {"3211", "3211 시험계좌"}:
        return "test"
    return None


def current_operation_mode():
    try:
        d = json.loads(DECISION_JSON.read_text(encoding="utf-8"))
        stages = d.get("stages") or {}
        for value in (
            ((stages.get("stage0") or {}).get("value") or {}).get("next_operation_mode"),
            ((stages.get("stage5") or {}).get("value") or {}).get("operation_mode"),
        ):
            if value in {"생활비모드", "복리모드"}:
                return value
    except Exception:
        pass
    return None


def resolve_entry_class(s: AuthorizedSession, symbol: str, name: str) -> str:
    rows = get_values(s, MASTER_ID, "진입결정_제2판", "A97:I126")
    # Active MEGA boundary rows: code/name/active...
    for row in rows:
        if len(row) >= 3 and str(row[0]).strip() == symbol and str(row[2]).strip().upper() == "YES":
            return "메가"
    # A/B candidate rows: rank, name, ..., grade at H
    for row in rows:
        if len(row) >= 8 and str(row[1] or "").strip() == name:
            grade = str(row[7] or "").strip()
            if grade in {"당일주도주 A", "당일주도주 B"}:
                return grade
    raise HandoffError(f"ENTRY_CLASS_NOT_FOUND:{symbol}:{name}")


def read_result(s: AuthorizedSession, daily_sheet: str) -> dict:
    entry = entry_map(get_values(s, STAGE4_ID, "ENTRY_연동", "A1:C50"))
    fast = get_values(s, STAGE4_ID, daily_sheet, "D4:J10")
    approval = get_values(s, STAGE4_ID, daily_sheet, "A18:C19")
    account_label = None
    try:
        account_label = approval[1][2]
    except Exception:
        pass
    # D4:J10 -> E column is index1, H index4, J index6
    def cell(r, c):
        try:
            return fast[r][c]
        except Exception:
            return None

    return {
        "symbol_code": entry.get("P047_SYMBOL_CODE"),
        "symbol_name": entry.get("P047_SYMBOL_NAME"),
        "entry_class": entry.get("P047_ENTRY_CLASS"),
        "entry_price": entry.get("ENTRY_PRICE"),
        "stop_price": entry.get("STOP_PRICE"),
        "input_at": entry.get("P047_INPUT_AT"),
        "handoff_id": entry.get("P047_HANDOFF_ID"),
        "handoff_status": entry.get("P047_HANDOFF_STATUS"),
        "entry_input_status": entry.get("ENTRY_INPUT_STATUS"),
        "sync_status": entry.get("SYNC_STATUS"),
        "final_r": entry.get("FINAL_R"),
        "invest_amount": entry.get("INVEST_AMOUNT"),
        "qty": entry.get("QTY"),
        "stage_decision": entry.get("STAGE_DECISION"),
        "quick_entry_price": cell(2, 1),
        "quick_stop_price": cell(3, 1),
        "quick_invest_amount": cell(4, 4),
        "quick_expected_loss": cell(4, 6),
        "quick_stage_decision": cell(5, 6),
        "quick_qty": cell(5, 1),
        "quick_account_label": account_label,
        "quick_account_alias": account_alias_from_label(account_label),
    }


def handoff(*, symbol: str, entry: int, stop: int, handoff_id: str, dry_run: bool = False) -> dict:
    if symbol not in SUPPORTED:
        raise HandoffError(f"UNSUPPORTED_SYMBOL:{symbol}")
    if entry <= 0 or stop <= 0 or stop >= entry:
        raise HandoffError("INVALID_ENTRY_STOP")
    if not handoff_id.strip():
        raise HandoffError("HANDOFF_ID_REQUIRED")

    name = SUPPORTED[symbol]
    s = session()
    rows = get_values(s, STAGE4_ID, "ENTRY_연동", "A1:C50")
    values = entry_map(rows)
    daily_sheet = str(values.get("DAILY_SHEET") or "").strip()
    today = datetime.now(KST).strftime("%Y-%m-%d")
    expected = f"{today}_빠른계산"
    if daily_sheet != expected:
        raise HandoffError(f"DAILY_SHEET_MISMATCH:{daily_sheet}:{expected}")

    entry_class = resolve_entry_class(s, symbol, name)
    input_at = datetime.now(KST).isoformat(timespec="seconds")

    planned = {
        "symbol": symbol,
        "name": name,
        "entry_class": entry_class,
        "entry": entry,
        "stop": stop,
        "input_at": input_at,
        "handoff_id": handoff_id,
        "daily_sheet": daily_sheet,
    }
    if dry_run:
        return {"status": "DRY_RUN_PASS", **planned}

    # Fail closed while writing. Formulas ignore P047 values until READY.
    update_values(s, STAGE4_ID, [
        {"sheet": "ENTRY_연동", "range": "B50", "values": [["WRITING"]]},
    ])

    update_values(s, STAGE4_ID, [
        {"sheet": "ENTRY_연동", "range": "B43:B49", "values": [
            [symbol], [name], [entry_class], [entry], [stop], [input_at], [handoff_id],
        ]},
        {"sheet": daily_sheet, "range": "E7", "values": [[stop]]},
    ])

    check = entry_map(get_values(s, STAGE4_ID, "ENTRY_연동", "A42:C50"))
    if (
        str(check.get("P047_SYMBOL_CODE") or "") != symbol
        or str(check.get("P047_SYMBOL_NAME") or "") != name
        or str(check.get("P047_ENTRY_CLASS") or "") != entry_class
        or int(float(check.get("P047_ENTRY_PRICE") or 0)) != entry
        or int(float(check.get("P047_STOP_PRICE") or 0)) != stop
        or str(check.get("P047_HANDOFF_ID") or "") != handoff_id
    ):
        raise HandoffError("WRITE_VERIFY_FAILED")

    update_values(s, STAGE4_ID, [
        {"sheet": "ENTRY_연동", "range": "B50", "values": [["READY"]]},
    ])

    result = None
    for _ in range(5):
        time.sleep(0.8)
        result = read_result(s, daily_sheet)
        if str(result.get("handoff_status") or "") == "READY":
            break

    result = result or {}
    planned_qty = parse_number(result.get("qty") or result.get("quick_qty"))
    planned_invest = parse_number(result.get("invest_amount") or result.get("quick_invest_amount"))
    planned_loss = parse_number(result.get("quick_expected_loss"))
    stage_decision = str(result.get("stage_decision") or result.get("quick_stage_decision") or "").strip()
    account_alias = result.get("quick_account_alias")
    checks = {
        "HANDOFF_READY": str(result.get("handoff_status") or "") == "READY",
        "QTY_VALID": planned_qty is not None and planned_qty>0,
        "INVEST_VALID": planned_invest is not None and planned_invest>0,
        "LOSS_VALID": planned_loss is not None and planned_loss>0,
        "ACCOUNT_VALID": account_alias in ("day","swing","test"),
        "3211_SYMBOL_AND_ONE_SHARE": account_alias != "test" or (symbol == "005930" and planned_qty == 1),
        "STAGE_APPROVED": stage_decision in ("진입가능","축소진입"),
    }
    missing = [name for name, passed in checks.items() if not passed]
    if missing:
        # No registry entry if 4-stage is not actually approved and fully evaluated.
        update_values(s, STAGE4_ID, [
            {"sheet": "ENTRY_연동", "range": "B50", "values": [["PENDING"]]},
        ])
        raise HandoffError("PLAN_NOT_REGISTERED:"+"|".join(missing))
    if account_alias == "test":
        broker_dir = str(HOME / "trading-system" / "collector")
        execution_dir = str(HOME / "trading-system" / "execution")
        for directory in (broker_dir, execution_dir):
            if directory not in sys.path:
                sys.path.insert(0, directory)
        from account_snapshot_readonly import collect
        from scalp3211_capital_owner_v1 import capture_3211_capital_owner
        owner = capture_3211_capital_owner(collect("test"))
        if owner["status"] != "BROKER_CAPITAL_VERIFIED_ONLY":
            raise HandoffError("3211_REAL_BROKER_CAPITAL_NOT_VERIFIED")
        if (planned_invest > owner["orderable_cash_krw"] or
                abs(planned_loss) > owner["risk_reference_krw_1p5pct"]):
            raise HandoffError("3211_INDEPENDENT_CASH_OR_RISK_LIMIT")
    plan_record = save_plan({
        "handoff_id": handoff_id,
        "created_at": input_at,
        "trade_date": today,
        "symbol": symbol,
        "stock_name": name,
        "account_alias": account_alias,
        "planned_entry_price": entry,
        "stop_price": stop,
        "planned_qty": planned_qty,
        "planned_invest_amount": planned_invest,
        "expected_loss": abs(planned_loss),
        "final_r": result.get("final_r"),
        "stage_decision": stage_decision,
        "operation_mode": current_operation_mode(),
    })
    return {
        "status": "PASS", **planned, "stage4": result,
        "plan_registry": {
            "handoff_id": plan_record.get("handoff_id"),
            "account_alias": plan_record.get("account_alias"),
            "planned_qty": plan_record.get("planned_qty"),
            "stop_price": plan_record.get("stop_price"),
            "expected_loss": plan_record.get("expected_loss"),
            "stage_decision": plan_record.get("stage_decision"),
            "operation_mode": plan_record.get("operation_mode"),
            "status": plan_record.get("status"),
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--entry", required=True, type=int)
    ap.add_argument("--stop", required=True, type=int)
    ap.add_argument("--handoff-id", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        out = handoff(
            symbol=args.symbol,
            entry=args.entry,
            stop=args.stop,
            handoff_id=args.handoff_id,
            dry_run=args.dry_run,
        )
        print(json.dumps(out, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({
            "status": "ERROR",
            "error": f"{type(exc).__name__}:{exc}",
        }, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())