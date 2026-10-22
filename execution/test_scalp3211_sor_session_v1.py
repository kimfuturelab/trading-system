"""3211 BUY session boundary, no live trades/network/DB writes."""
from datetime import datetime
from zoneinfo import ZoneInfo
from scalp3211_switch_v1 import entry_session_open
KST=ZoneInfo("Asia/Seoul")


def main():
    for time,expected in [
        ("07:59",False),("08:00",True),("08:30",True),
        ("19:54",True),("19:55",True),("19:59",True),
        ("20:00",False),("20:01",False)
    ]:
        h,m=map(int,time.split(":"))
        at=datetime(2026,10,22,h,m,tzinfo=KST)
        got=entry_session_open(at)
        assert got is expected,(time,got,expected)
    assert not entry_session_open(datetime(2026,10,24,17,0,tzinfo=KST))
    assert not entry_session_open(datetime(2026,10,25,17,0,tzinfo=KST))
    assert entry_session_open(datetime(2026,10,22,19,59,tzinfo=KST))
    print("PASS 3211 SOR entry 08:00 through 19:59 KST; 19:55 allowed; 20:00 cutoff; weekends blocked")


if __name__=="__main__":
    main()
