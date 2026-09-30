#!/usr/bin/env bash
# AI Data Operations Platform - provision, verify, launch (macOS / Linux).
#   ./launch.sh            -> create venv if missing, install, run tests, start the app
#   ./launch.sh --test     -> provision + run tests only
#   ./launch.sh --no-test  -> provision + start the app without running tests
set -euo pipefail
cd "$(dirname "$0")"

PY=venv/bin/python
[ -x "$PY" ] || PY=venv/Scripts/python.exe   # Git Bash on Windows
if [ ! -x "$PY" ]; then
  echo "[launch] creating virtual environment..."
  python3 -m venv venv 2>/dev/null || python -m venv venv
  PY=venv/bin/python; [ -x "$PY" ] || PY=venv/Scripts/python.exe
fi
echo "[launch] installing dependencies..."
"$PY" -m pip install -q -r requirements.txt

if [ "${1:-}" != "--no-test" ]; then
  echo "[launch] running test suite..."
  "$PY" -m pytest tests/ -q
  [ "${1:-}" = "--test" ] && exit 0
fi

if [ ! -f .env ]; then
  echo "[launch] NOTE: no .env found. Copy .env.example to .env and set ANTHROPIC_API_KEY"
  echo "         to enable plan generation. Upload, profiling and cleaning work without it."
fi
echo "[launch] starting Streamlit..."
exec "$PY" -m streamlit run app.py
