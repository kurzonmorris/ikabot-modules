@echo off
rem ============================================================
rem  Build ikabot.exe and package it as a release zip
rem
rem  Run this from anywhere - it works out the repo root itself.
rem
rem  Reads IKABOT_VERSION and IKABOT_MOD_VERSION from
rem  ikabot\config.py and names the zip:
rem      ikabot-v{IKABOT_VERSION}-mod-v{IKABOT_MOD_VERSION}.zip
rem
rem  Output: releases\ikabot-v7.4.0-mod-v1.6.9.zip
rem          (containing ikabot.exe and _internal\ at the zip root)
rem ============================================================
setlocal

rem This script lives in installer\ - step up to the repo root
pushd "%~dp0.."

rem ── Read version numbers from ikabot\config.py ────────────────
set IKVER=
set MODVER=

for /f "tokens=2 delims==" %%V in ('findstr /B /C:"IKABOT_VERSION = " ikabot\config.py') do set IKVER=%%V
for /f "tokens=2 delims==" %%V in ('findstr /B /C:"IKABOT_MOD_VERSION = " ikabot\config.py') do set MODVER=%%V

rem Strip spaces and quotes
set IKVER=%IKVER: =%
set IKVER=%IKVER:"=%
set MODVER=%MODVER: =%
set MODVER=%MODVER:"=%

if "%IKVER%"=="" (
    echo ERROR: Could not read IKABOT_VERSION from ikabot\config.py
    popd
    exit /b 1
)
if "%MODVER%"=="" (
    echo ERROR: Could not read IKABOT_MOD_VERSION from ikabot\config.py
    popd
    exit /b 1
)

echo ============================================================
echo  Building ikabot v%IKVER%  mod v%MODVER%
echo ============================================================
echo.

rem ── Build with PyInstaller ────────────────────────────────────
rem --noconfirm  auto-accepts wiping the existing dist\ikabot folder
rem --clean      clears the PyInstaller cache and build temp files
python -m PyInstaller --clean --noconfirm installer\ikabot.spec
if errorlevel 1 (
    echo.
    echo BUILD FAILED
    popd
    exit /b 1
)

rem ── Verify the build output before zipping ────────────────────
if not exist "dist\ikabot\ikabot.exe" (
    echo.
    echo ERROR: dist\ikabot\ikabot.exe not found - not creating a partial zip.
    popd
    exit /b 1
)
if not exist "dist\ikabot\_internal" (
    echo.
    echo ERROR: dist\ikabot\_internal not found - not creating a partial zip.
    popd
    exit /b 1
)

rem ── Zip it ────────────────────────────────────────────────────
if not exist "releases" mkdir "releases"
set ZIPFILE=releases\ikabot-v%IKVER%-mod-v%MODVER%.zip

echo.
echo Zipping dist\ikabot to %ZIPFILE% ...
rem /F /Q - force delete read-only files, no "are you sure?" prompt
if exist "%ZIPFILE%" del /F /Q "%ZIPFILE%"

rem 'dist\ikabot\*' puts ikabot.exe and _internal at the ZIP ROOT,
rem which is what the installer expects when it extracts the release.
powershell -NoProfile -Command "Compress-Archive -Path 'dist\ikabot\*' -DestinationPath '%ZIPFILE%' -Force"
if errorlevel 1 (
    echo.
    echo ZIP FAILED
    popd
    exit /b 1
)

echo.
echo ============================================================
echo  Done!  %ZIPFILE%
echo.
echo  ikabot version : %IKVER%
echo  mod version    : %MODVER%
echo ============================================================
popd
