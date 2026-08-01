@echo off
setlocal

rem Read INSTALLER_VERSION from the source file
for /f "tokens=2 delims==" %%V in ('findstr /I "INSTALLER_VERSION" ikabot-mod-install.py') do (
    set VER=%%V
    goto :found
)
echo ERROR: Could not find INSTALLER_VERSION in ikabot-mod-install.py
exit /b 1
:found

rem Strip spaces and quotes
set VER=%VER: =%
set VER=%VER:"=%

echo ============================================================
echo  Building ikabot-mod-install v%VER%
echo ============================================================
echo.

rem Run PyInstaller via python -m so it works regardless of PATH
python -m PyInstaller --clean ikabot-mod-install.spec
if errorlevel 1 (
    echo.
    echo BUILD FAILED
    exit /b 1
)

rem Zip the single-file exe (onefile build)
set OUTEXE=dist\ikabot-mod-install.exe
set ZIPFILE=dist\ikabot-mod-install_v%VER%.zip

echo.
echo Zipping %OUTEXE% to %ZIPFILE% ...
if exist "%ZIPFILE%" del "%ZIPFILE%"
powershell -NoProfile -Command "Compress-Archive -Path '%OUTEXE%' -DestinationPath '%ZIPFILE%' -Force"
if errorlevel 1 (
    echo.
    echo ZIP FAILED
    exit /b 1
)

echo.
echo ============================================================
echo  Done!  %ZIPFILE%
echo ============================================================
