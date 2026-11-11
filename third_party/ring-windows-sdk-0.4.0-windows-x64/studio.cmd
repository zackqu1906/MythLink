@echo off
setlocal
cd /d "%~dp0" || goto :fail
if not exist ".venv\Scripts\ring-win.exe" (
    echo Run install.cmd first.
    goto :fail
)
set "PYTHONUTF8=1"
if "%~1"=="" (
    ".venv\Scripts\ring-win.exe" ui --open
) else if /i "%~1"=="--demo" (
    ".venv\Scripts\ring-win.exe" ui --demo --open
) else (
    echo Usage: studio.cmd [--demo]
    goto :fail
)
if errorlevel 1 goto :fail
exit /b 0
:fail
pause
exit /b 1
