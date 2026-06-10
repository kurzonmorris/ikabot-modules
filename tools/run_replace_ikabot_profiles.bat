@echo off
setlocal

REM Runs the Python script from the same folder as this BAT file.
REM Put both files in the same folder, then double-click this BAT.

set "SCRIPT_DIR=%~dp0"
set "PY_SCRIPT=%SCRIPT_DIR%replace_ikabot_profiles.py"

if not exist "%PY_SCRIPT%" (
    echo ERROR: Could not find replace_ikabot_profiles.py next to this BAT file.
    echo Expected path: "%PY_SCRIPT%"
    pause
    exit /b 1
)

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%PY_SCRIPT%"
) else (
    where python >nul 2>nul
    if %errorlevel%==0 (
        python "%PY_SCRIPT%"
    ) else (
        echo ERROR: Python was not found in PATH.
        echo Install Python from https://www.python.org/downloads/
        pause
        exit /b 1
    )
)

echo.
echo Finished.
pause
