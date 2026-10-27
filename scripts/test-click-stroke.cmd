@echo off
setlocal
cd /d "%~dp0.."
if not exist ".runtime\venv\Scripts\python.exe" (
    echo Project Python is missing. Run scripts\setup.ps1 first.
    pause
    exit /b 1
)
set QT_QUICK_CONTROLS_STYLE=Basic
".runtime\venv\Scripts\python.exe" -u tools\collect_tap_diagnostics.py %*
if errorlevel 1 pause
