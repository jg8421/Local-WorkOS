@echo off
setlocal
cd /d "%~dp0"
if not exist "runtime\python\python.exe" (
  echo Portable Python is missing. Extract the whole ZIP and try again.
  pause
  exit /b 1
)
"runtime\python\python.exe" -I "app\tools\deployment_preflight.py" --launch %*
if errorlevel 1 pause
endlocal
