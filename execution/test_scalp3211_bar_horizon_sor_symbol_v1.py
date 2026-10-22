"""3211 SOR 24 completed 5-minute bar timecut; no real broker/orders."""
from datetime import datetime,timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
from zoneinfo import ZoneInfo
from scalp3211_bar_horizon_v1 import (
    completed_bars_since, completed_24_bar_timecut, SOR_SAMSUNG_BAR_CODE
)
KST=ZoneInfo("Asia/Seoul")

def main():
    assert SOR_SAMSUNG_BAR_CODE == "005930_AL"
    anchor=datetime(2026,10,22,14,0,tzinfo=KST)
    with TemporaryDirectory(prefix="3211-sor-bar-") as tmp:
        db=Path(tmp)/"bars.sqlite3"
        with sqlite3.connect(db) as c:
            c.execute("CREATE TABLE stock_bars (symbol TEXT, timeframe TEXT, is_complete INTEGER, bar_time TEXT)")
            rows=[]
            for i in range(24):
                when=(anchor+timedelta(minutes=5*i)).strftime("%Y-%m-%d %H:%M:%S")
                rows.append((SOR_SAMSUNG_BAR_CODE,"5m",1,when))
            c.executemany("INSERT INTO stock_bars VALUES (?,?,?,?)",rows[:23])
            c.execute("INSERT INTO stock_bars VALUES (?,?,?,?)",("005930","5m",1,rows[23][3]))
            c.execute("INSERT INTO stock_bars VALUES (?,?,?,?)",(SOR_SAMSUNG_BAR_CODE,"5m",0,rows[23][3]))
            c.commit()
        assert completed_bars_since(anchor.isoformat(),db=db)==23
        assert not completed_24_bar_timecut(anchor.isoformat(),db=db)
        with sqlite3.connect(db) as c:
            c.execute("INSERT INTO stock_bars VALUES (?,?,?,?)",rows[23])
            c.commit()
        assert completed_bars_since(anchor.isoformat(),db=db)==24
        assert completed_24_bar_timecut(anchor.isoformat(),db=db)
        # Query must count only bars on/after this confirmed first-fill cutoff.
        assert completed_bars_since((anchor+timedelta(minutes=5)).isoformat(),db=db)==23
        assert not completed_24_bar_timecut((anchor+timedelta(minutes=5)).isoformat(),db=db)
        print("PASS 3211 SOR 005930_AL: 23 bars=HOLD / 24 bars=TIMECUT, ignores bare symbol/incomplete, no writes to live DB")

if __name__=="__main__":main()
