@echo off
setlocal
rem Raw2 two-finger test: scan, pick a ring, print what the model recognises.
rem Close the Mythlink app first; it holds the ring BLE connection.
cd /d "%~dp0.."
chcp 65001 >nul
if not exist ".runtime\venv\Scripts\python.exe" (
    echo Project Python is missing. Run scripts\setup.ps1 first.
    pause
    exit /b 1
)
".runtime\venv\Scripts\python.exe" -X utf8 -u tools\test_touchpad_two_finger.py %*
pause
