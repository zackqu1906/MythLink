@echo off
setlocal
rem Prompted single/two-finger collection with ground-truth labels.
rem Close the main app and other test programs first; they hold the ring connection.
set "LABEL_ROOT=%~dp0.."
set "LABEL_PYTHON=%LABEL_ROOT%\.runtime\venv\Scripts\pythonw.exe"
if not exist "%LABEL_PYTHON%" (
    echo Project Python is missing. Run scripts\setup.ps1 first.
    pause
    exit /b 1
)
start "" "%LABEL_PYTHON%" "%LABEL_ROOT%\tools\collect_two_finger_labels.py" %*
if %errorlevel%==0 exit /b 0
echo Could not start the two-finger label collection window.
pause
exit /b 1
