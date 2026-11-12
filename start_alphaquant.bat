@echo off
title Starting AlphaQuant AI Pro
cd /d "%~dp0"

echo =====================================================================
echo    AlphaQuant AI Pro - 24/7 Background Service Launcher
echo =====================================================================

set PYTHON_BIN=.\.venv\Scripts\python.exe
if not exist "%PYTHON_BIN%" (
    set PYTHON_BIN=python
)
set PYTHONW_BIN=.\.venv\Scripts\pythonw.exe
if not exist "%PYTHONW_BIN%" (
    set PYTHONW_BIN=%PYTHON_BIN%
)

REM 1. Check if an active supervisor is already running
for /f "tokens=1,2 delims=:" %%a in ('"%PYTHON_BIN%" scripts\launch\alphaquant_supervisor.py --check-active 2^>nul') do (
    if "%%a"=="ACTIVE_SUPERVISOR_PID" (
        echo [INFO] AlphaQuant Supervisor is already active (PID %%b^).
        "%PYTHON_BIN%" scripts\launch\alphaquant_supervisor.py --status
        exit /b 0
    )
)

REM 2. Check for port collision with foreign applications
set PRECHECK=
for /f "tokens=*" %%i in ('"%PYTHON_BIN%" dashboard\run_dashboard.py --check-only') do (
    if not defined PRECHECK set PRECHECK=%%i
)

if "%PRECHECK%"=="PORT_STATUS:foreign" (
    echo [ERROR] Port 8000 is occupied by an unrelated application. Cannot start AlphaQuant.
    exit /b 1
)

REM 3. Launch supervisor with headless pythonw.exe in background
echo Starting AlphaQuant supervisor in background...
start "" "%PYTHONW_BIN%" scripts\launch\alphaquant_supervisor.py >nul 2>&1

REM 4. Wait for /api/health to respond
echo Waiting for backend to initialize and report ready...
set RETRY_COUNT=0

:WAIT_LOOP
ping 127.0.0.1 -n 2 >nul
set /a RETRY_COUNT+=1

for /f "tokens=*" %%i in ('"%PYTHON_BIN%" dashboard\run_dashboard.py --check-only 2^>nul') do (
    if "%%i"=="PORT_STATUS:alphaquant" (
        echo.
        echo =====================================================================
        echo    SUCCESS: AlphaQuant AI Pro is active and operational!
        echo =====================================================================
        "%PYTHON_BIN%" scripts\launch\alphaquant_supervisor.py --status
        exit /b 0
    )
)

if %RETRY_COUNT% geq 25 (
    echo.
    echo [WARNING] Server startup took longer than expected. Check runtime\logs\server.log for details.
    "%PYTHON_BIN%" scripts\launch\alphaquant_supervisor.py --status
    exit /b 1
)

goto WAIT_LOOP
