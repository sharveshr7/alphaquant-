@echo off
title AlphaQuant AI Dashboard
cd /d "%~dp0\.."

echo ========================================================
echo    Starting AlphaQuant AI Browser Dashboard...
echo ========================================================

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" dashboard\run_dashboard.py
) else (
    python dashboard\run_dashboard.py
)

pause
