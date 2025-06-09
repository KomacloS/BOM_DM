@echo off
set ROOT=%~dp0\..
pushd %ROOT%
if not exist .venv\Scripts\python.exe (
    python -m venv .venv || (
        echo Python 3.10+ required
        exit /b 1
    )
)
set PY=%ROOT%\.venv\Scripts\python.exe

%PY% -m pip install --upgrade pip
%PY% -m pip install ".[full]"
%PY% -m gui.control_center
popd
