#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p logs
export PYTHONIOENCODING=utf-8

if [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif [ -x "/opt/homebrew/bin/python3" ]; then
  PY="/opt/homebrew/bin/python3"
else
  PY="$(command -v python3)"
fi

"$PY" run_orders.py "$@" >> logs/orders.log 2>&1
