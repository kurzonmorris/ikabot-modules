@echo off
setlocal

REM Runs setup_ikabot_instances.py from the same folder as this BAT file.
REM Put both files together, then double-click this BAT.

set "SCRIPT_DIR=%~dp0"
set "PY_SCRIPT=%SCRIPT_DIR%setup_ikabot_instances.py"

if not exist "%PY_SCRIPT%" (
    echo ERROR: Could not find setup_ikabot_instances.py next to this BAT file.
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

echo ERROR: Python was not found in PATH.
echo Install Python from https://www.python.org/downloads/
pause
exit /b 1

:done
echo.
echo Finished.
pause
