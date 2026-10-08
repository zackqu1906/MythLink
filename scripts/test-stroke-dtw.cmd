@echo off
setlocal
call "%~dp0collect-stroke-samples.cmd" --recognizer dtw %*
