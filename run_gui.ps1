# PowerShell launcher for the Projects Terminal GUI
# - Ensures a Python 3.10+ virtualenv exists (recreates if older)
# - Installs project in editable mode with the `full` extra
# - Starts the Qt GUI

$ErrorActionPreference = 'Stop'

Write-Host '=== Step 0: Move to project root ==='
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Write-Host '=== Step 1: Ensure venv with Python 3.10+ ==='
$venvPath = Join-Path $Root '.venv'
$venvPython = Join-Path $venvPath 'Scripts\python.exe'

if (Test-Path $venvPath) {
  if (-not (Test-Path $venvPython)) {
    Write-Host 'Existing .venv is invalid; removing...'
    Remove-Item -Recurse -Force $venvPath
  } else {
    $vstr = & $venvPython -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"
    if ($vstr -notin @('3.10','3.11','3.12','3.13')) {
      Write-Host "Found venv Python $vstr (too old); recreating..."
      Remove-Item -Recurse -Force $venvPath
    } else {
      Write-Host "Found venv Python $vstr"
    }
  }
}

if (-not (Test-Path $venvPath)) {
  Write-Host 'Creating venv with Python 3.12'
  py -3.12 -m venv .venv
}

Write-Host '=== Step 2: Activate venv ==='
& (Join-Path $venvPath 'Scripts\Activate.ps1')

Write-Host '=== Step 3: Upgrade pip and install project ==='
python -m pip install --upgrade pip
python -m pip install -e '.[full]'

Write-Host '=== Step 4: Run Projects Terminal GUI ==='
python -m app.gui

