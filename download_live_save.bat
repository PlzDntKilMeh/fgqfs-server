@echo off
setlocal
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
if exist "%VENV_PY%" (
    "%VENV_PY%" tools\download_live_save.py %*
) else (
    py --version >nul 2>&1
    if not errorlevel 1 (
        py tools\download_live_save.py %*
    ) else (
        python tools\download_live_save.py %*
    )
)

endlocal
