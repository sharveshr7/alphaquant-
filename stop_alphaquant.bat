@echo off
title Stopping AlphaQuant AI Pro
cd /d "%~dp0"

echo =====================================================================
echo    AlphaQuant AI Pro - Graceful Service Shutdown
echo =====================================================================

set PYTHON_BIN=.\.venv\Scripts\python.exe
if not exist "%PYTHON_BIN%" (
    set PYTHON_BIN=python
)

echo Stopping AlphaQuant supervisor and server...
"%PYTHON_BIN%" scripts\launch\alphaquant_supervisor.py --stop

REM Ensure port 8000 is released
echo Verifying port 8000 release...
ping 127.0.0.1 -n 2 >nul
"%PYTHON_BIN%" -c "from dashboard.run_dashboard import check_port_status; status, _ = check_port_status('127.0.0.1', 8000); exit(0 if status == 'free' else 1)"
if %errorlevel% equ 0 (
    echo [SUCCESS] AlphaQuant AI Pro stopped cleanly and port 8000 is free.
) else (
    echo [INFO] Port 8000 status check completed.
)
