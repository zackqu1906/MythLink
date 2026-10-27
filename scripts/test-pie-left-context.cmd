@echo off
setlocal
cd /d "%~dp0.."
".runtime\venv\Scripts\python.exe" -u "..\ring-stroke-ime-demo\tools\collect_pie_left_context.py" %*
if errorlevel 1 pause
