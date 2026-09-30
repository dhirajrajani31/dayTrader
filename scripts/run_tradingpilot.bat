@echo off
setlocal
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
  echo Missing .venv. Follow the setup steps in README.md.
  exit /b 1
)
".venv\Scripts\python.exe" -m app.main run
endlocal

