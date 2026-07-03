@echo off
setlocal

cd /d "%~dp0"

set "VENV_DIR=%~dp0.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "VENV_MITMDUMP=%VENV_DIR%\Scripts\mitmdump.exe"

set "SERVER_HOST=0.0.0.0"
set "SERVER_PORT=6767"
set "PROXY_PORT=6769"
if exist "server_settings.json" for /f %%I in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=Get-Content 'server_settings.json' -Raw | ConvertFrom-Json; if ($s.server_port) { $s.server_port } elseif ($s.port) { $s.port }"') do if not "%%I"=="" set "SERVER_PORT=%%I"
if exist "server_settings.json" for /f %%I in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=Get-Content 'server_settings.json' -Raw | ConvertFrom-Json; if ($s.proxy_port) { $s.proxy_port }"') do if not "%%I"=="" set "PROXY_PORT=%%I"

for /f %%I in ('powershell -NoProfile -Command "(Get-NetIPAddress -AddressFamily IPv4 ^| Where-Object { $_.IPAddress -ne ''127.0.0.1'' -and $_.IPAddress -notlike ''169.254*'' } ^| Select-Object -First 1 -ExpandProperty IPAddress)"') do set "FGQFS_EXTERNAL_HOST=%%I"
if not defined FGQFS_EXTERNAL_HOST set "FGQFS_EXTERNAL_HOST=127.0.0.1"

set "FGQFS_HOST=127.0.0.1"
set "FGQFS_PORT=%SERVER_PORT%"
set "FGQFS_REDIRECT=1"
set "FGQFS_OFFLINE=0"
set "FGQFS_PUBLIC_HOST=%FGQFS_EXTERNAL_HOST%:%SERVER_PORT%"

echo Starting FGQFS private server on %SERVER_HOST%:%SERVER_PORT%...
start "FGQFS Server" cmd /k "set \"FGQFS_PUBLIC_HOST=%FGQFS_PUBLIC_HOST%\" && set \"SERVER_HOST=%SERVER_HOST%\" && set \"SERVER_PORT=%SERVER_PORT%\" && call run_server.bat"

echo Starting mitmdump proxy on :%PROXY_PORT% forwarding to %FGQFS_HOST%:%FGQFS_PORT%...
start "FGQFS Proxy" cmd /k "set \"FGQFS_HOST=%FGQFS_HOST%\" && set \"FGQFS_PORT=%FGQFS_PORT%\" && set \"FGQFS_REDIRECT=%FGQFS_REDIRECT%\" && set \"FGQFS_OFFLINE=%FGQFS_OFFLINE%\" && set \"FGQFS_EXTERNAL_HOST=%FGQFS_EXTERNAL_HOST%\" && set \"PROXY_PORT=%PROXY_PORT%\" && call run_proxy.bat"

echo.
echo Started:
echo   Server: http://%SERVER_HOST%:%SERVER_PORT%
echo   Proxy : localhost:%PROXY_PORT%
echo   Device ConfigURL host: %FGQFS_PUBLIC_HOST%
echo   Cert  : http://%FGQFS_EXTERNAL_HOST%:%SERVER_PORT%/cert
echo.
echo Set the device manual proxy to this PC's LAN IP on port %PROXY_PORT%.

endlocal
