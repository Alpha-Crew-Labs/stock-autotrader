@echo off
rem Weekday 08:30-08:55: send pending orders to broker (paper mode = no-op)
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if not defined PY (
  where py >nul 2>&1 && (
    py -3 run_orders.py %* >> logs\orders.log 2>&1
    exit /b %ERRORLEVEL%
  )
  set "PY=python"
)
"%PY%" run_orders.py %* >> logs\orders.log 2>&1
