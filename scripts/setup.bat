@echo off
set "ROOT=%~dp0\.."
set "PY=%ROOT%\.venv\Scripts\python.exe"
pushd "%ROOT%"
if not exist "%PY%" (
    python -m venv .venv || (
        echo Python 3.10+ required
        exit /b 1
    )
)

%PY% -m pip install -q --upgrade pip
%PY% -m pip install -q ".[full]"
%PY% -m gui.control_center
popd

echo If PowerShell blocks this script, run:
echo Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
