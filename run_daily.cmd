@echo off
rem Run after market close (16:10+): screener -> signals/paper ledger -> dashboard
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if not defined PY (
  where py >nul 2>&1 && (
    py -3 screener.py >> logs\run.log 2>&1
    py -3 run_daily.py --top 75 >> logs\run.log 2>&1
    exit /b %ERRORLEVEL%
  )
  set "PY=python"
)
"%PY%" screener.py >> logs\run.log 2>&1
"%PY%" run_daily.py --top 75 >> logs\run.log 2>&1
