@echo off
setlocal
cd /d "%~dp0"

set "VENV_MITMDUMP=%~dp0.venv\Scripts\mitmdump.exe"
set "MITM_CONFDIR=%~dp0mitmproxy"
if defined PROXY_PORT goto proxy_port_ready
set "PROXY_PORT=6769"
if exist "server_settings.json" for /f %%I in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=Get-Content 'server_settings.json' -Raw | ConvertFrom-Json; if ($s.proxy_port) { $s.proxy_port }"') do if not "%%I"=="" set "PROXY_PORT=%%I"
:proxy_port_ready
if not defined FGQFS_TRAFFIC_LOG set "FGQFS_TRAFFIC_LOG=%~dp0captures\live\traffic.jsonl"
if not exist "%~dp0captures\live" mkdir "%~dp0captures\live"
if not exist "%MITM_CONFDIR%" mkdir "%MITM_CONFDIR%"
if exist "%VENV_MITMDUMP%" (
    "%VENV_MITMDUMP%" --set confdir="%MITM_CONFDIR%" --set log_file="%FGQFS_TRAFFIC_LOG%" -p %PROXY_PORT% -s redirect/backdate_certs.py -s redirect/mitm_addon.py
) else (
    mitmdump --set confdir="%MITM_CONFDIR%" --set log_file="%FGQFS_TRAFFIC_LOG%" -p %PROXY_PORT% -s redirect/backdate_certs.py -s redirect/mitm_addon.py
)

endlocal
