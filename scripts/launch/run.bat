@echo off
title AlphaQuant AI Pro - Advanced S&D Trading Terminal
cd /d "%~dp0\..\.."
echo =====================================================================
echo    AlphaQuant AI Pro - Real-Time Futures Trading & Analytics
echo =====================================================================
echo Starting backend server on http://127.0.0.1:8000 ...

if exist ".\.venv\Scripts\python.exe" (
    .\.venv\Scripts\python.exe dashboard\run_dashboard.py %*
) else (
    python dashboard\run_dashboard.py %*
)

pause
