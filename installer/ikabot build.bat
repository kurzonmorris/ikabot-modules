@echo off
setlocal

rem Read IKABOT_MOD_VERSION from ..\ikabot\config.py
rem (/C: matches the literal "IKABOT_MOD_VERSION =" so it does NOT also
rem  match the IKABOT_MOD_VERSION_TAG line on the following line)
for /f "tokens=2 delims==" %%V in ('findstr /C:"IKABOT_MOD_VERSION =" "..\ikabot\config.py"') do (
    set VER=%%V
    goto :found
)
echo ERROR: Could not find IKABOT_MOD_VERSION in ..\ikabot\config.py
exit /b 1
:found

rem Strip spaces and quotes
set VER=%VER: =%
set VER=%VER:"=%

echo ============================================================
echo  Building ikabot v%VER%
echo ============================================================
echo.

rem Run PyInstaller via python -m so it works regardless of PATH.
rem ikabot.spec resolves the repo root from SPECPATH, so it can be run
rem from this installer folder; output lands in .\dist\ikabot\
python -m PyInstaller --clean ikabot.spec
if errorlevel 1 (
    echo.
    echo BUILD FAILED
    exit /b 1
)

rem Zip the output folder (contains ikabot.exe and the _internal folder)
set OUTDIR=dist\ikabot
set ZIPFILE=dist\ikabot_v%VER%.zip

if not exist "%OUTDIR%" (
    echo.
    echo ERROR: expected build output "%OUTDIR%" was not found
    exit /b 1
)

echo.
echo Zipping %OUTDIR% to %ZIPFILE% ...
if exist "%ZIPFILE%" del "%ZIPFILE%"
powershell -NoProfile -Command "Compress-Archive -Path '%OUTDIR%' -DestinationPath '%ZIPFILE%' -Force"
if errorlevel 1 (
    echo.
    echo ZIP FAILED
    exit /b 1
)

echo.
echo ============================================================
echo  Done!  %ZIPFILE%
echo ============================================================
