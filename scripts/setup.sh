#!/bin/bash
set -e
# Determine project root (one directory up from this script)
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -x ".venv/bin/python" ]; then
  if command -v python3 >/dev/null; then
    python3 -m venv .venv
  else
    echo "Python 3.10+ required" >&2
    exit 1
  fi
fi

PY="$(pwd)/.venv/bin/python"
[ -x "$PY" ] || PY=$(command -v python3)

"$PY" -m pip install --upgrade pip
"$PY" -m pip install ".[full]"
"$PY" -m gui.control_center
