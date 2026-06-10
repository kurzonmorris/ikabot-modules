@echo off
setlocal

REM Launcher for ikabot-mod-install.py
REM Place this BAT next to ikabot-mod-install.py and double-click to run.

set "SCRIPT_DIR=%~dp0"
set "PY_SCRIPT=%SCRIPT_DIR%ikabot-mod-install.py"

if not exist "%PY_SCRIPT%" (
    echo ERROR: ikabot-mod-install.py not found next to this BAT file.
    echo Expected: "%PY_SCRIPT%"
    pause
    exit /b 1
)

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%PY_SCRIPT%"
    goto done
)

where python >nul 2>nul
if %errorlevel%==0 (
    python "%PY_SCRIPT%"
    goto done
)

echo ERROR: Python not found in PATH.
echo Install Python from https://www.python.org/downloads/
pause
exit /b 1

:done
echo.
echo Finished.
pause
