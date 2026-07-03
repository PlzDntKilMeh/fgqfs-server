@echo off
setlocal
cd /d "%~dp0"

set "PROMPTED="
set "APK_DIR=%~1"
if not defined APK_DIR (
    set "PROMPTED=1"
    echo Enter the decompiled APK folder path.
    echo Example: D:\APK Easy Tool\1-Decompiled APKs\Family Guy
    echo.
    set /p "APK_DIR=APK folder: "
)

if not defined APK_DIR (
    echo No APK folder path entered.
    if defined PROMPTED pause
    exit /b 1
)

set "APK_DIR=%APK_DIR:"=%"

if not exist "%APK_DIR%\AndroidManifest.xml" (
    echo Could not find AndroidManifest.xml in:
    echo   %APK_DIR%
    if defined PROMPTED pause
    exit /b 1
)

set "VENV_PY=%~dp0.venv\Scripts\python.exe"
if exist "%VENV_PY%" (
    "%VENV_PY%" tools\patch_apk_proxy.py "%APK_DIR%"
) else (
    py --version >nul 2>&1
    if not errorlevel 1 (
        py tools\patch_apk_proxy.py "%APK_DIR%"
    ) else (
        python tools\patch_apk_proxy.py "%APK_DIR%"
    )
)

if errorlevel 1 (
    echo.
    echo APK proxy patch failed.
    if defined PROMPTED pause
    exit /b 1
)

echo.
echo APK proxy patch succeeded. Recompile and sign the APK next.
if defined PROMPTED pause

endlocal
