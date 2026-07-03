@echo off
setlocal
cd /d "%~dp0"

if not defined SERVER_HOST set "SERVER_HOST=0.0.0.0"
if defined SERVER_PORT goto server_port_ready
set "SERVER_PORT=6767"
if exist "server_settings.json" for /f %%I in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=Get-Content 'server_settings.json' -Raw | ConvertFrom-Json; if ($s.server_port) { $s.server_port } elseif ($s.port) { $s.port }"') do if not "%%I"=="" set "SERVER_PORT=%%I"
:server_port_ready

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
if exist "%VENV_PY%" (
    "%VENV_PY%" server.py --host %SERVER_HOST% --port %SERVER_PORT% --no-checksum-verify
) else (
    py --version >nul 2>&1
    if not errorlevel 1 (
        py server.py --host %SERVER_HOST% --port %SERVER_PORT% --no-checksum-verify
    ) else (
        python server.py --host %SERVER_HOST% --port %SERVER_PORT% --no-checksum-verify
    )
)

endlocal
