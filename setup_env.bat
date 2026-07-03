@echo off
setlocal
cd /d "%~dp0"

set "BOOTSTRAP_PY="
py --version >nul 2>&1 && set "BOOTSTRAP_PY=py"
if not defined BOOTSTRAP_PY (
    python --version >nul 2>&1 && set "BOOTSTRAP_PY=python"
)
if not defined BOOTSTRAP_PY (
    echo Could not find py or python on PATH.
    goto :fail
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating repo-local virtual environment...
    %BOOTSTRAP_PY% -m venv .venv || goto :fail
)

echo Installing Python packages into .venv...
".venv\Scripts\python.exe" -m pip install --upgrade pip || goto :fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail

echo.
echo Environment ready:
echo   Python  : %cd%\.venv\Scripts\python.exe
echo   mitmdump: %cd%\.venv\Scripts\mitmdump.exe
exit /b 0

:fail
echo Setup failed.
exit /b 1
