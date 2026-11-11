@echo off
setlocal
cd /d "%~dp0.."
if not exist ".runtime\venv\Scripts\python.exe" (
    echo Project Python is missing. Run scripts\setup.ps1 first.
    pause
    exit /b 1
)
".runtime\venv\Scripts\python.exe" -u tools\collect_stroke_layout.py %*
if errorlevel 1 pause
