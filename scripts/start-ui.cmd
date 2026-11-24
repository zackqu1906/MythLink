@echo off
setlocal
cd /d "%~dp0.."

set "PIP_CACHE_DIR=%CD%\.cache\pip"
set "MODELSCOPE_CACHE=%CD%\.cache\modelscope"
set "HF_HOME=%CD%\.cache\huggingface"
set "TORCH_HOME=%CD%\.cache\torch"
if not defined PROXIMIC_LLM_HOME set "PROXIMIC_LLM_HOME=%CD%\.runtime\local-llm"
set "PROXIMIC_OPUS_DIR=%CD%\.runtime\opus"
set "PATH=%PROXIMIC_OPUS_DIR%;%PATH%"

rem Enable the Windows input path on normal launches once the prototype is installed.
if not defined MYTHLINK_WINDOWS_TSF (
    reg query "HKLM\Software\Classes\CLSID\{40b2749b-c169-4a28-921c-c3367b96482b}\InprocServer32" /ve /reg:64 >nul 2>&1
    if not errorlevel 1 set "MYTHLINK_WINDOWS_TSF=1"
)

if not exist ".runtime\venv\Scripts\python.exe" (
    echo Mythlink is not installed yet.
    echo Run: powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
    pause
    exit /b 1
)

".runtime\venv\Scripts\python.exe" -m proximic_ring.ui
if errorlevel 1 (
    echo.
    echo Mythlink exited with an error.
    echo Reinstall with: powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -Recreate
    pause
    exit /b 1
)
endlocal
