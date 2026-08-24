@echo off
cd /d %~dp0
python kiwoom_collector.py --loop
pause
