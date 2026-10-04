@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-ffmpeg.ps1"
set "taskExitCode=%ERRORLEVEL%"
pause
exit /b %taskExitCode%
