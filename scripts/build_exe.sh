#!/usr/bin/env bash
set -euo pipefail
export PYTHONUTF8=1

# Foldered build for BOM_DB: libs unpacked, assets beside the EXE, console hidden.

# ---- pick Python ≥3.10 (set PYTHON_EXE=... to force a path) ----
pick_python_array() {
  if [[ -n "${PYTHON_EXE:-}" ]]; then
    if "$PYTHON_EXE" -c 'import sys; exit(0 if sys.version_info>=(3,10) else 1)' 2>/dev/null; then
      printf '%s\0' "$PYTHON_EXE"; return 0
    else
      echo "ERROR: PYTHON_EXE is not Python ≥3.10" >&2; return 1
    fi
  fi
  if command -v py >/dev/null 2>&1; then
    for v in 3.12 3.11 3.10; do
      if py -$v -c 'import sys; exit(0 if sys.version_info>=(3,10) else 1)' >/dev/null 2>&1; then
        printf '%s\0%s\0' "py" "-$v"; return 0
      fi
    done
  fi
  for exe in python3.12 python3.11 python3.10 python; do
    if command -v "$exe" >/dev/null 2>&1 && "$exe" -c 'import sys; exit(0 if sys.version_info>=(3,10) else 1)'; then
      printf '%s\0' "$exe"; return 0
    fi
  done
  return 1
}
mapfile -d '' -t PYBIN < <(pick_python_array) || { echo "ERROR: Need Python ≥3.10"; exit 1; }
echo "Using interpreter: $("${PYBIN[@]}" -c 'import sys; print(sys.executable)')"
"${PYBIN[@]}" -c 'import sys; print("Python version:", sys.version)'

# ---- venv ----
"${PYBIN[@]}" -m venv .venv
# shellcheck disable=SC1091
source .venv/Scripts/activate

# ---- clean ----
rm -rf build dist .pytest_cache || true
rm -f ./*.spec || true

# ---- tools ----
python -m pip install -U pip wheel setuptools
python -m pip install -U "pyinstaller>=6.6"

# ---- deps (ensure 'requests' is installed) ----
if [[ -f requirements.txt ]]; then
  python -m pip install -r requirements.txt
else
  python -m pip install -e .
fi
python - <<'PY' || python -m pip install requests
import importlib; importlib.import_module("requests")
PY

# ---- collect external data next to the EXE (adjust these as needed) ----
shopt -s nullglob
declare -a ADD_DATA_ARGS=()
add_data_dir_if_exists() {
  local d="$1"
  [[ -d "$d" ]] && ADD_DATA_ARGS+=( "--add-data" "$d;$d" )
}
# Common folders to keep outside the EXE:
add_data_dir_if_exists "data"
add_data_dir_if_exists "static"
add_data_dir_if_exists "app/gui/icons"
add_data_dir_if_exists "migrations"

# ---- build (array avoids quoting bugs) ----
args=(
  -n BOM_DB
  --onedir
  --noconsole        # hide terminal window
  --debug noarchive  # keep pure-Python modules unpacked
  --clean
  --paths .
  --collect-all PyQt6
  --collect-submodules requests
  --collect-submodules sqlmodel
  app/gui/__main__.py
)
# optional icon:
[[ -f icon.ico ]] && args+=( --icon icon.ico )
args+=( "${ADD_DATA_ARGS[@]}" )

echo "== pyinstaller ${args[*]}"
pyinstaller "${args[@]}"

echo
echo "Build complete → ./dist/BOM_DB/"
echo "Verify data folders exist under dist/BOM_DB/ (e.g., data/, static/, app/gui/icons/, migrations/)."
