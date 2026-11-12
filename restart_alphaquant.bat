@echo off
title Restarting AlphaQuant AI Pro
cd /d "%~dp0"

echo =====================================================================
echo    AlphaQuant AI Pro - Service Restart
echo =====================================================================

call stop_alphaquant.bat
ping 127.0.0.1 -n 3 >nul
call start_alphaquant.bat
