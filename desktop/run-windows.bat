@echo off
setlocal
cd /d "%~dp0.."
if not exist ".venv-desktop\Scripts\python.exe" (
    echo Creating Windows Python environment...
    py -3 -m venv .venv-desktop
    if errorlevel 1 (
        echo Python 3 not found. Install Python from python.org and retry.
        exit /b 1
    )
)
".venv-desktop\Scripts\python.exe" -m pip install -r desktop\requirements.txt
if errorlevel 1 exit /b 1
".venv-desktop\Scripts\python.exe" -m desktop
