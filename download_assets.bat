@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Missing .venv\Scripts\python.exe
  echo Run setup_env.bat first.
  exit /b 1
)

.venv\Scripts\python.exe tools\run_asset_refresh.py ^
  --workers 16 ^
  --discovery hybrid ^
  --scale-probe all
pause
