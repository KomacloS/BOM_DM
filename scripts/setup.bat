@echo off
python -m venv .venv
if exist .venv\Scripts\activate.bat (
    call .venv\Scripts\activate.bat
) else (
    echo Python 3.10+ required
    exit /b 1
)
python -m pip install --upgrade pip
python -m pip install ".[full]"
python -m gui.control_center
