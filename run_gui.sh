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
  echo "Creating venv (trying Python 3.12 -> 3.10)"
  # 1) Try Windows py launcher with preferred versions
  if command -v py >/dev/null 2>&1; then
    for ver in 3.12 3.11 3.10; do
      echo " -> py -$ver -m venv .venv"
      if py -$ver -m venv .venv 2>/dev/null; then
        break
      fi
    done
  fi
  # 2) Try typical per-user / program files installs directly
  if [ ! -d .venv ]; then
    CANDS=(
      "$LOCALAPPDATA/Programs/Python/Python312/python.exe"
      "$LOCALAPPDATA/Programs/Python/Python311/python.exe"
      "$LOCALAPPDATA/Programs/Python/Python310/python.exe"
      "/c/Program Files/Python312/python.exe"
      "/c/Program Files/Python311/python.exe"
      "/c/Program Files/Python310/python.exe"
    )
    for exe in "${CANDS[@]}"; do
      if [ -x "$exe" ]; then
        echo " -> $exe -m venv .venv"
        "$exe" -m venv .venv && break
      fi
    done
  fi
  # 3) Fallback to python3/python on PATH
  if [ ! -d .venv ]; then
    if command -v python3 >/dev/null 2>&1; then
      echo " -> python3 -m venv .venv"
      python3 -m venv .venv || true
    elif command -v python >/dev/null 2>&1; then
      echo " -> python -m venv .venv"
      python -m venv .venv || true
    fi
  fi
  if [ ! -d .venv ]; then
    echo "Unable to create venv. Install Python 3.10+ (preferably 3.12) or ensure 'py' launcher is installed."
    exit 1
  fi
fi
echo "=== Step 2: Activate venv ==="
source .venv/Scripts/activate
VENV_PY=".venv/Scripts/python"

echo "=== Step 3: Upgrade pip and install project ==="
"$VENV_PY" -m pip install --upgrade pip
# Install our project and extras. Using the venv python ensures correct interpreter.
"$VENV_PY" -m pip install -e '.[full]'

echo "=== Step 4: Ensure __init__.py exists in app/gui/ ==="
if [ ! -f "app/gui/__init__.py" ]; then
  touch app/gui/__init__.py
  echo "Created app/gui/__init__.py"
else
  echo "app/gui/__init__.py already exists."
fi

echo "=== Step 5: Run Projects Terminal GUI ==="
"$VENV_PY" -m app.gui


