@echo off
setlocal
cd /d "%~dp0"
"runtime\python\python.exe" -I "app\tools\deployment_preflight.py" %*
pause
endlocal
