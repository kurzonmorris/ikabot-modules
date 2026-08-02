@echo off
rem ============================================================
rem  Build the ikabot installer exe and package it as a release zip
rem
rem  Run this from anywhere - it works out its own location.
rem
rem  Reads INSTALLER_VERSION from ikabot-mod-install.py and names
rem  the zip:  ikabot-mod-install_v{INSTALLER_VERSION}.zip
rem
rem  Output: releases\ikabot-mod-install_v2.0.1.zip
rem          (containing the single self-contained exe)
rem ============================================================
setlocal

rem Stay in the installer folder - ikabot-mod-install.spec refers to
rem RELEASE_NOTES.txt and open-all-instances.ps1 by relative path,
rem so they only resolve when PyInstaller runs from here.
pushd "%~dp0"

rem ── Read the version from the source file ─────────────────────
set VER=
for /f "tokens=2 delims==" %%V in ('findstr /B /C:"INSTALLER_VERSION = " ikabot-mod-install.py') do set VER=%%V

rem Strip spaces and quotes
set VER=%VER: =%
set VER=%VER:"=%

if "%VER%"=="" (
    echo ERROR: Could not read INSTALLER_VERSION from ikabot-mod-install.py
    popd
    exit /b 1
)

echo ============================================================
echo  Building ikabot-mod-install v%VER%
echo ============================================================
echo.

rem ── Build with PyInstaller ────────────────────────────────────
rem --noconfirm  auto-accepts wiping the previous dist output
rem --clean      clears the PyInstaller cache and build temp files
python -m PyInstaller --clean --noconfirm ikabot-mod-install.spec
if errorlevel 1 (
    echo.
    echo BUILD FAILED
    popd
    exit /b 1
)

rem ── Verify the build output before zipping ────────────────────
set OUTEXE=dist\ikabot-mod-install.exe
if not exist "%OUTEXE%" (
    echo.
    echo ERROR: %OUTEXE% not found - not creating a partial zip.
    popd
    exit /b 1
)

rem ── Zip into the repo-level releases folder ───────────────────
if not exist "..\releases" mkdir "..\releases"
set ZIPFILE=..\releases\ikabot-mod-install_v%VER%.zip

echo.
echo Zipping %OUTEXE% to releases\ikabot-mod-install_v%VER%.zip ...
rem /F /Q - force delete read-only files, no "are you sure?" prompt
if exist "%ZIPFILE%" del /F /Q "%ZIPFILE%"

powershell -NoProfile -Command "Compress-Archive -Path '%OUTEXE%' -DestinationPath '%ZIPFILE%' -Force"
if errorlevel 1 (
    echo.
    echo ZIP FAILED
    popd
    exit /b 1
)

echo.
echo ============================================================
echo  Done!  releases\ikabot-mod-install_v%VER%.zip
echo.
echo  Installer version : %VER%
echo ============================================================
popd
