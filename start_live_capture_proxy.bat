@echo off
setlocal
cd /d "%~dp0"

set "VENV_DIR=%~dp0.venv"
set "VENV_MITMDUMP=%VENV_DIR%\Scripts\mitmdump.exe"
set "MITM_CONFDIR=%~dp0mitmproxy"
set "CAPTURE_DIR=%~dp0captures\live"
set "SAVE_DIR=%CAPTURE_DIR%\saves"
set "FGQFS_TRAFFIC_LOG=%CAPTURE_DIR%\traffic.jsonl"
if not exist "%CAPTURE_DIR%" mkdir "%CAPTURE_DIR%"
if not exist "%SAVE_DIR%" mkdir "%SAVE_DIR%"
if not exist "%MITM_CONFDIR%" mkdir "%MITM_CONFDIR%"

set "SERVER_PORT=6767"
set "PROXY_PORT=6769"
if exist "server_settings.json" for /f %%I in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=Get-Content 'server_settings.json' -Raw | ConvertFrom-Json; if ($s.server_port) { $s.server_port } elseif ($s.port) { $s.port }"') do if not "%%I"=="" set "SERVER_PORT=%%I"
if exist "server_settings.json" for /f %%I in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=Get-Content 'server_settings.json' -Raw | ConvertFrom-Json; if ($s.proxy_port) { $s.proxy_port }"') do if not "%%I"=="" set "PROXY_PORT=%%I"
set "FGQFS_REDIRECT=0"
set "FGQFS_OFFLINE=0"
set "FGQFS_LOG_ALL=0"
for /f %%I in ('powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 ^| Where-Object { $_.IPAddress -ne ''127.0.0.1'' -and $_.IPAddress -notlike ''169.254*'' } ^| Select-Object -First 1 -ExpandProperty IPAddress)"') do set "FGQFS_EXTERNAL_HOST=%%I"
if not defined FGQFS_EXTERNAL_HOST set "FGQFS_EXTERNAL_HOST=127.0.0.1"

echo Starting capture-only proxy on :%PROXY_PORT%...
echo Device manual proxy should point to this PC on port %PROXY_PORT%.
echo Live game traffic will pass through and be logged to:
echo   %FGQFS_TRAFFIC_LOG%
echo Exported saves will be written under:
echo   %SAVE_DIR%
echo Cert: http://%FGQFS_EXTERNAL_HOST%:%SERVER_PORT%/cert
echo.

if exist "%VENV_MITMDUMP%" (
    "%VENV_MITMDUMP%" --set confdir="%MITM_CONFDIR%" --set log_file="%FGQFS_TRAFFIC_LOG%" -p %PROXY_PORT% -s redirect/backdate_certs.py -s redirect/mitm_addon.py
) else (
    mitmdump --set confdir="%MITM_CONFDIR%" --set log_file="%FGQFS_TRAFFIC_LOG%" -p %PROXY_PORT% -s redirect/backdate_certs.py -s redirect/mitm_addon.py
)

endlocal
