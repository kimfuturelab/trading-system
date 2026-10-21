"""No-broker, no-order deterministic 3211 EXIT audit checks."""
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from scalp3211_exit_readonly_audit_v1 import classify_exit

KST=ZoneInfo("Asia/Seoul")
now=datetime(2026,10,21,19,56,tzinfo=KST)
base={"plan_id":"SCALP3211-abc","symbol":"005930","qty":1,
      "remaining_qty":1,"timecut_at":"2026-10-21T19:55:00+09:00"}
for status,expected in (
    ("EXIT_SUBMITTED","UNFILLED_OR_UNRECONCILED_SELL_OVER_2_MIN"),
    ("PARTIAL_EXIT","PARTIAL_FILL_NO_AUTO_RESEND"),
    ("EXIT_TRIGGERED","EXIT_TRIGGER_RESERVED_WRITE_STATE_MUST_BE_RECONCILED"),
    ("EXIT_ARMED","EXIT_TIME_CUTOFF_UNEXECUTED_OR_UNKNOWN"),
    ("HALTED","EXIT_HALTED_OR_ACCOUNT_MISMATCH")
):
    r=classify_exit(plan={**base,"status":status},
                    submitted_at=(now-timedelta(minutes=4)).isoformat(),
                    now=now)
    assert r["requires_review"] and expected in r["reasons"],r
    assert r["automatic_resend"] is False and r["broker_write"] is False
flat=classify_exit(plan={**base,"status":"CLOSED"},submitted_at=None,now=now)
assert flat["requires_review"] is False
print("3211_EXIT_AUDIT_READ_ONLY_PASS")
