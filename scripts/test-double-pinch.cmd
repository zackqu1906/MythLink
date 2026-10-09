@echo off
call "%~dp0test-firmware-gestures.cmd" --double-pinch %*
exit /b %errorlevel%
