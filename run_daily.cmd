@echo off
rem Run after market close (16:10+): screener -> signals/paper ledger -> dashboard
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PY=%LOCALAPPDATA%\Programs\Python\Python314\python.exe
if not exist logs mkdir logs
"%PY%" screener.py >> logs\run.log 2>&1
"%PY%" run_daily.py --top 75 >> logs\run.log 2>&1
