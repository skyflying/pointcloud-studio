@echo off
REM Environment check: tests Qt from source and from the packaged exe, lists DLL versions.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run build_windows.bat first ^(it creates .venv^).
  pause & exit /b 1
)
".venv\Scripts\python.exe" tools\check_env.py > "check_env_report.txt" 2>&1
type "check_env_report.txt"
echo.
echo Report saved to: %~dp0check_env_report.txt
pause
