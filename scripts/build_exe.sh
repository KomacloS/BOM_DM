#!/usr/bin/env bash
set -euo pipefail

# Build a Windows GUI executable (one-folder) with PyInstaller
# - Uses icon.ico as the app icon
# - Keeps config and database files editable by placing them next to the .exe

# Change these if you want a different name or entry
APP_NAME="BOM_DB"
ENTRY="app/gui/__main__.py"
ICON="icon.ico"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "[build] Root: $ROOT_DIR"
echo "[build] Entry: $ENTRY"
echo "[build] Icon : $ICON"

# Ensure PyInstaller is available
if ! command -v pyinstaller >/dev/null 2>&1; then
  echo "[build] PyInstaller not found; installing..."
  python -m pip install --upgrade pip >/dev/null
  python -m pip install pyinstaller >/dev/null
fi

# Clean previous build artifacts
rm -rf build "dist/${APP_NAME}" "${APP_NAME}.spec" || true

# Platform-specific separator for --add-data
if [[ "${OS:-}" == "Windows_NT" ]]; then
  DATA_SEP=';'
else
  DATA_SEP=':'
fi

echo "[build] Running PyInstaller..."
pyinstaller \
  --name "$APP_NAME" \
  --icon "$ICON" \
  --noconfirm \
  --clean \
  --windowed \
  --collect-all PyQt6 \
  --add-data "app/gui/icons${DATA_SEP}app/gui/icons" \
  "$ENTRY"

DIST_DIR="dist/${APP_NAME}"
echo "[build] Dist dir: ${DIST_DIR}"

# Copy editable config/database files next to the exe (if they exist)
copy_if_exists() {
  local src="$1"
  local dst_dir="$2"
  if [[ -f "$src" ]]; then
    echo "[build] Copying $src -> $dst_dir/"
    cp -f "$src" "$dst_dir/"
  fi
}

# Agents configuration (project-local)
copy_if_exists "agents.local.toml" "$DIST_DIR"
copy_if_exists "agents.example.toml" "$DIST_DIR"

# Optional database artifacts (if you keep one in the repo)
copy_if_exists "app.db" "$DIST_DIR"
copy_if_exists "app/bom_dev.db" "$DIST_DIR"

# Helpful templates/assets (optional but handy)
copy_if_exists "bom_template.csv" "$DIST_DIR"

# Add a short README with runtime config guidance
cat >"${DIST_DIR}/README_DIST.txt" <<'EOF'
BOM_DB packaged application
===========================

Editable configuration:
 - Project-local agents file: agents.local.toml (next to the EXE)
   Copy/rename agents.example.toml -> agents.local.toml and edit values.

Database configuration:
 - Settings & the default SQLite path live beside the EXE when that folder is writable.
   If Windows blocks writes (e.g. Program Files), we fall back to %USERPROFILE%\.bom_platform\settings.toml.
   Edit that file or set the DATABASE_URL environment variable to override.
 - If app.db or bom_dev.db is present next to the EXE, you can point to it with:
     set DATABASE_URL=sqlite:///app.db
   (On PowerShell: $env:DATABASE_URL = 'sqlite:///app.db')

Paths and logs:
 - Data root and logs default under the same writable location chosen for settings.
 - Logs are written under the configured LOG_DIR.

Run:
 - Launch BOM_DB.exe inside this folder.
EOF

echo "[build] Done. Launch: ${DIST_DIR}/${APP_NAME}.exe"
