@echo off
setlocal
cd /d "%~dp0" || goto :fail
set "PYTHONUTF8=1"
if not exist ".venv\Scripts\python.exe" (
    py -3.11 -m venv .venv || goto :fail
)
".venv\Scripts\python.exe" -m pip install --only-binary=:all: -c "constraints-windows-x64.txt" ".\sdk\ring_windows_sdk-0.4.0-py3-none-win_amd64.whl[web]" || goto :fail
".venv\Scripts\python.exe" -m pip check || goto :fail
".venv\Scripts\python.exe" -m ring_mac_sdk.cli model-check || goto :fail
echo Ready. Open studio.cmd to start Ring Studio.
pause
exit /b 0
:fail
echo Installation failed. See docs\INSTALL.md and the error above.
pause
exit /b 1
