#!/usr/bin/env bash
set -euo pipefail
export PYTHONUTF8=1

# Foldered build for BOM_DB: libs unpacked, assets beside the EXE, console hidden.

pick_python_array() {
  if [[ -n "${PYTHON_EXE:-}" ]]; then
    if "$PYTHON_EXE" -c 'import sys; exit(0 if sys.version_info>=(3,10) else 1)' 2>/dev/null; then
      printf '%s\0' "$PYTHON_EXE"
      return 0
    else
      echo "ERROR: PYTHON_EXE is not Python >=3.10" >&2
      return 1
    fi
  fi
  if command -v py >/dev/null 2>&1; then
    for v in 3.12 3.11 3.10; do
      if py "-$v" -c 'import sys; exit(0 if sys.version_info>=(3,10) else 1)' >/dev/null 2>&1; then
        printf '%s\0%s\0' "py" "-$v"
        return 0
      fi
    done
  fi
  for exe in python3.12 python3.11 python3.10 python python.exe; do
    if command -v "$exe" >/dev/null 2>&1 && "$exe" -c 'import sys; exit(0 if sys.version_info>=(3,10) else 1)' 2>/dev/null; then
      printf '%s\0' "$exe"
      return 0
    fi
  done
  local username="${USERNAME:-${USER:-}}"
  if [[ -n "$username" ]]; then
    local win_paths=(
      "/c/Users/${username}/AppData/Local/Programs/Python/Python312/python.exe"
      "/c/Users/${username}/AppData/Local/Programs/Python/Python311/python.exe"
      "/c/Users/${username}/AppData/Local/Programs/Python/Python310/python.exe"
    )
    for exe in "${win_paths[@]}"; do
      if [[ -x "$exe" ]] && "$exe" -c 'import sys; exit(0 if sys.version_info>=(3,10) else 1)' 2>/dev/null; then
        printf '%s\0' "$exe"
        return 0
      fi
    done
  fi
  return 1
}

mapfile -d '' -t PYBIN < <(pick_python_array) || { echo "ERROR: Need Python >=3.10" >&2; exit 1; }
echo "Using interpreter: $("${PYBIN[@]}" -c 'import sys; print(sys.executable)')"
"${PYBIN[@]}" -c 'import sys; print("Python version:", sys.version)'

"${PYBIN[@]}" -m venv .venv
# shellcheck disable=SC1091
source .venv/Scripts/activate

if command -v tasklist >/dev/null 2>&1; then
  if tasklist /FI "IMAGENAME eq BOM_DB.exe" 2>/dev/null | grep -qi "BOM_DB.exe"; then
    echo "Detected running BOM_DB.exe; attempting to terminate before cleaning dist/."
    taskkill //IM "BOM_DB.exe" //F >/dev/null 2>&1 || echo "Warning: could not terminate BOM_DB.exe"
    sleep 1
  fi
fi

if command -v powershell.exe >/dev/null 2>&1; then
  echo "Stopping any process with open handles into dist/BOM_DB/."
  powershell.exe -NoProfile -Command "Get-Process | Where-Object { $_.Modules.FileName -like '*\\dist\\BOM_DB\\*' } | Stop-Process -Force" >/dev/null 2>&1 || true
fi

rm -rf build .pytest_cache || true
rm -f ./*.spec || true

python -m pip install -U pip wheel setuptools
python -m pip install -U "pyinstaller>=6.6"

if [[ -f requirements.txt ]]; then
  python -m pip install -r requirements.txt
else
  python -m pip install -e .
fi
python - <<'PY' || python -m pip install requests
import importlib; importlib.import_module("requests")
PY

shopt -s nullglob
declare -a ADD_DATA_ARGS=()
add_data_dir_if_exists() {
  local d="$1"
  [[ -d "$d" ]] && ADD_DATA_ARGS+=( "--add-data" "$d;$d" )
}
add_data_dir_if_exists "data"
add_data_dir_if_exists "static"
add_data_dir_if_exists "app/gui/icons"
add_data_dir_if_exists "migrations"

DIST_ROOT="dist"
mkdir -p "$DIST_ROOT"
if [[ -d "${DIST_ROOT}/BOM_DB" ]]; then
  echo "Removing previous build at ${DIST_ROOT}/BOM_DB/"
  if ! rm -rf "${DIST_ROOT}/BOM_DB"; then
    echo "Warning: could not remove existing ${DIST_ROOT}/BOM_DB/ (likely still in use); leaving it untouched."
  fi
fi

args=(
  -n BOM_DB
  --distpath "$DIST_ROOT"
  --onedir
  --noconsole
  --debug noarchive
  --clean
  --paths .
  --collect-all PyQt6
  --collect-submodules requests
  --collect-submodules sqlmodel
  --collect-submodules app.integration
  --hidden-import app.integration.ce_bridge_manager
  app/gui/__main__.py
)
[[ -f icon.ico ]] && args+=( --icon icon.ico )
args+=( "${ADD_DATA_ARGS[@]}" )

echo "== pyinstaller ${args[*]}"
pyinstaller --noconfirm "${args[@]}"

NEW_DIST="${DIST_ROOT}/BOM_DB"
if [[ -d "$NEW_DIST" ]]; then
  echo
  echo "Build complete. Primary output: ${NEW_DIST}/"
  echo "Verify data folders exist under dist/BOM_DB/ (e.g., data/, static/, app/gui/icons/, migrations/)."
else
  echo "Warning: expected build output ${NEW_DIST}/ not found."
fi
