@echo off
setlocal
cd /d "%~dp0.."
".runtime\venv\Scripts\python.exe" -u "..\ring-stroke-ime-demo\tools\collect_deletion.py" --test-model "..\ring-stroke-ime-demo\data\diagnostics\deletion\deletion-baseline-24bf34b0-db08-4720-ada5-92eae3d7e431.model.json" %*
if errorlevel 1 pause
