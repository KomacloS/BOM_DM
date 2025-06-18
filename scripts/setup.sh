#!/bin/bash
set -e
# Determine project root (one directory up from this script)
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="$ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then
  if command -v python3 >/dev/null; then
    python3 -m venv .venv
  else
    echo "Python 3.10+ required" >&2
    exit 1
  fi
fi

"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q ".[full]"
exec "$PY" -m gui.control_center
