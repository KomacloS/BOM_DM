@echo off
py -3 -m venv .venv
if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
) else (
    echo Python 3.10+ required
    exit /b 1
)
py -3 -m pip install --upgrade pip
py -3 -m pip install ".[full]"
py -3 -m gui.control_center
