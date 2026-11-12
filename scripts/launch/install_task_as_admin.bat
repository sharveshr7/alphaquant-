@echo off
title Installing AlphaQuant AI Pro as Windows Scheduled Task (Admin)
cd /d "%~dp0\..\.."

echo Requesting Administrator privileges to register AlphaQuant 24/7 Scheduled Task...
powershell -Command "Start-Process powershell -Verb RunAs -ArgumentList '-ExecutionPolicy Bypass -File \"\"%~dp0install_autostart.ps1\"\"'"

echo Done. Check the elevated PowerShell window.
pause
