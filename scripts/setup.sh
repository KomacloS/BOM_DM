#!/bin/bash
set -e
if ! command -v python3 >/dev/null; then
  echo "Python 3.10+ required" >&2
  exit 1
fi
python3 -m venv .venv
. .venv/bin/activate
pip install --upgrade pip
pip install ".[full]"
python -m gui.control_center
