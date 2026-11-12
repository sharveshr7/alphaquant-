@echo off
title AlphaQuant AI Pro Status
cd /d "%~dp0"

set PYTHON_BIN=.\.venv\Scripts\python.exe
if not exist "%PYTHON_BIN%" (
    set PYTHON_BIN=python
)

"%PYTHON_BIN%" scripts\launch\alphaquant_supervisor.py --status
