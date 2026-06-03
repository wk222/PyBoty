@echo off
title PyBot Launcher
setlocal enabledelayedexpansion

echo ===================================================
echo             PyBot One-Click Launcher               
echo ===================================================

:: 1. Check for Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in your PATH.
    echo Please install Python 3.10 or higher from python.org
    pause
    exit /b 1
)

:: 2. Check for modern fast packager 'uv'
where uv >nul 2>&1
if errorlevel 1 (
    echo [INFO] 'uv' packager not detected. Installing dependencies via standard pip.
    echo        (For 10x faster startup, install 'uv' from: https://github.com/astral-sh/uv)
    set USE_UV=0
) else (
    echo [INFO] 'uv' packager detected. Using 'uv' for instant setup.
    set USE_UV=1
)

:: 3. Setup Virtual Environment
if not exist .venv (
    echo [INFO] Creating local virtual environment in .venv...
    if !USE_UV! == 1 (
        uv venv .venv
    ) else (
        python -m venv .venv
    )
)

:: 4. Activate Virtual Environment
call .venv\Scripts\activate.bat

:: 5. Install Dependencies
echo [INFO] Upgrading and syncing dependencies...
if !USE_UV! == 1 (
    uv pip install -e .[all-llm]
) else (
    python -m pip install --upgrade pip
    pip install -e .[all-llm]
)

:: Local dev authentication (override in production)
if not defined PYBOT_API_KEYS set PYBOT_API_KEYS=dev-key:*
if not defined PYBOT_ALLOW_DEV_KEY set PYBOT_ALLOW_DEV_KEY=1

:: 6. Launch Default Web Browser after service has time to start
echo [INFO] Starting PyBot Web service...
start "" cmd /c "timeout /t 3 /nobreak >nul && start http://localhost:8000"

:: 7. Start Web Service (foreground)
python service_mode.py

pause
