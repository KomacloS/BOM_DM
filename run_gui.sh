#!/usr/bin/env bash
set -euo pipefail

echo "=== Step 0: Move to project root ==="
cd "/c/Users/Michael/Documents/Python/BOM_DB_project/BOM_DB" || { echo "Project path not found"; exit 1; }

echo "=== Step 1: Ensure venv with Python 3.10+ ==="
# If an old venv exists with an older Python (e.g. 3.7), recreate it.
if [ -d .venv ]; then
  VENV_PY_W=".venv/Scripts/python.exe"
  if [ ! -x "$VENV_PY_W" ]; then
    echo "Existing .venv is invalid; removing..."
    rm -rf .venv
  else
    VSTR=$("$VENV_PY_W" -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')") || VSTR="0.0"
    case "$VSTR" in
      3.10|3.11|3.12|3.13) echo "Found venv Python $VSTR";;
      *) echo "Found venv Python $VSTR (too old); recreating..."; rm -rf .venv;;
    esac
  fi
fi

if [ ! -d .venv ]; then
  echo "Creating venv with Python 3.12"
  py -3.12 -m venv .venv
fi

echo "=== Step 2: Activate venv ==="
source .venv/Scripts/activate
VENV_PY=".venv/Scripts/python"

echo "=== Step 3: Upgrade pip and install project ==="
"$VENV_PY" -m pip install --upgrade pip
# Install our project and extras. Using the venv python ensures correct interpreter.
"$VENV_PY" -m pip install -e '.[full]'

echo "=== Step 4: Ensure __init__.py exists in gui/ ==="
if [ ! -f "gui/__init__.py" ]; then
  touch gui/__init__.py
  echo "Created gui/__init__.py"
else
  echo "gui/__init__.py already exists."
fi

echo "=== Step 5: Run Projects Terminal GUI ==="
"$VENV_PY" -m app.gui
