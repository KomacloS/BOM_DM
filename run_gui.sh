#!/usr/bin/env bash
set -e  # exit on error
set -o pipefail

echo "=== Step 0: Move to project root ==="
cd "/c/Users/Michael/Documents/Python/BOM_DB_project/BOM_DB" || { echo "Project path not found"; exit 1; }

echo "=== Step 1: Create venv (if not exists) ==="
if [ ! -d ".venv" ]; then
    py -3.12 -m venv .venv
else
    echo "Venv already exists, skipping creation."
fi

echo "=== Step 2: Activate venv ==="
source .venv/Scripts/activate

echo "=== Step 3: Upgrade pip and install project ==="
python -m pip install --upgrade pip
python -m pip install -e '.[full]'

echo "=== Step 4: Ensure __init__.py exists in gui/ ==="
if [ ! -f "gui/__init__.py" ]; then
    touch gui/__init__.py
    echo "Created gui/__init__.py"
else
    echo "gui/__init__.py already exists."
fi

echo "=== Step 5: Run control_center GUI ==="
python -m app.gui 
