@echo off
setlocal
set "FOLD_ROOT=%~dp0.."
set "FOLD_PYTHON=%FOLD_ROOT%\.runtime\venv\Scripts\pythonw.exe"
if not exist "%FOLD_PYTHON%" (
    echo Project Python is missing. Run scripts\setup.ps1 first.
    pause
    exit /b 1
)
start "" "%FOLD_PYTHON%" "%FOLD_ROOT%\tools\collect_clear_fold.py" %*
if %errorlevel%==0 exit /b 0
echo Could not start fold and clear gesture collection.
pause
exit /b 1
