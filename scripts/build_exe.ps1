Param(
    [string]$Name = "BOM_DB_GUI",
    [switch]$Clean
)

# Build a standalone Windows .exe for the Qt GUI using PyInstaller.
# Requirements: run from the project root in an activated virtualenv
# that has all dependencies installed (including PyInstaller), e.g.:
#   .\.venv\Scripts\Activate.ps1
#   python -m pip install -e .[full]
#   python -m pip install pyinstaller

if ($Clean) {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue dist, build
}

$dataPairs = @(
    # Copy function list used by Test Method detail dropdown
    "data\function_list.txt;data",
    # Include GUI icon assets so the frozen app can load them
    "app\gui\icons;app/gui/icons"
)

# Convert --add-data for Windows (src;dest)
$addDataArgs = @()
foreach ($pair in $dataPairs) {
    $addDataArgs += @("--add-data", $pair)
}

# Prefer launching module -m app.gui (__main__.py exists)
$args = @(
    "--noconfirm",
    "--clean",
    "--windowed",
    "--hidden-import", "sqlmodel",
    "--name", $Name
) + $addDataArgs + @(
    "-m", "app.gui"
)

Write-Host "Running: pyinstaller $($args -join ' ')"
python -m PyInstaller @args

Write-Host "Build complete. See dist\$Name\$Name.exe"
