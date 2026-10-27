@echo off
setlocal
set "TRIAL_ROOT=%~dp0.."
set "TRIAL_PYTHON=%TRIAL_ROOT%\.runtime\venv\Scripts\pythonw.exe"
if not exist "%TRIAL_PYTHON%" (
    echo Project Python is missing. Run scripts\setup.ps1 first.
    pause
    exit /b 1
)
start "" "%TRIAL_PYTHON%" "%TRIAL_ROOT%\tools\test_complete_stroke_input.py" %*
if %errorlevel%==0 exit /b 0
echo Could not start the complete stroke input trial.
pause
exit /b 1
