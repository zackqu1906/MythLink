@echo off
setlocal
set "EIGHT_ROOT=%~dp0.."
set "EIGHT_PYTHON=%EIGHT_ROOT%\.runtime\venv\Scripts\pythonw.exe"
if not exist "%EIGHT_PYTHON%" (
    echo Project Python is missing. Run scripts\setup.ps1 first.
    pause
    exit /b 1
)
start "" "%EIGHT_PYTHON%" "%EIGHT_ROOT%\tools\collect_eight_direction_selection.py" %*
if %errorlevel%==0 exit /b 0
echo Could not start the eight-direction selection collection window.
pause
exit /b 1
