@echo off
rem Weekday 08:30-08:55: send pending orders to broker (paper mode = no-op)
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PY=%LOCALAPPDATA%\Programs\Python\Python314\python.exe
if not exist logs mkdir logs
"%PY%" run_orders.py >> logs\orders.log 2>&1
