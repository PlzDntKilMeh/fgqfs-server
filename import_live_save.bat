@echo off
setlocal
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"
set "TRAFFIC_LOG=captures\live\traffic.jsonl"
set "SAVE_DIR=captures\live\saves"
set "LATEST_SAVE=%SAVE_DIR%\latest_live_save.pbuf"

if not exist "%TRAFFIC_LOG%" (
    echo %TRAFFIC_LOG% not found.
    echo Run start_live_capture_proxy.bat first and capture a town load or save.
    exit /b 1
)

if not exist "%SAVE_DIR%" mkdir "%SAVE_DIR%"

echo Exporting latest live save from %TRAFFIC_LOG%...
if exist "%VENV_PY%" (
    "%VENV_PY%" tools\export_live_save.py --in "%TRAFFIC_LOG%" --out-dir "%SAVE_DIR%" || exit /b 1
) else (
    py --version >nul 2>&1
    if not errorlevel 1 (
        py tools\export_live_save.py --in "%TRAFFIC_LOG%" --out-dir "%SAVE_DIR%" || exit /b 1
    ) else (
        python tools\export_live_save.py --in "%TRAFFIC_LOG%" --out-dir "%SAVE_DIR%" || exit /b 1
    )
)

if not exist "%LATEST_SAVE%" (
    echo Export did not produce %LATEST_SAVE%.
    exit /b 1
)

echo Importing %LATEST_SAVE% into shared player 0...
if exist "%VENV_PY%" (
    "%VENV_PY%" tools\create_account.py --player-id 0 --save "%LATEST_SAVE%" || exit /b 1
) else (
    py --version >nul 2>&1
    if not errorlevel 1 (
        py tools\create_account.py --player-id 0 --save "%LATEST_SAVE%" || exit /b 1
    ) else (
        python tools\create_account.py --player-id 0 --save "%LATEST_SAVE%" || exit /b 1
    )
)

echo.
echo Done. Shared private save slot updated from %TRAFFIC_LOG%.
echo Exported save files are in %SAVE_DIR%.
endlocal
