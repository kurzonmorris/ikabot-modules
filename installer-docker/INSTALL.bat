@echo off
setlocal enabledelayedexpansion
set "INSTALLER_VERSION=1.0.12"
title ikabot Docker installer v%INSTALLER_VERSION%
color 0F

echo.
echo  ==================================================
echo    ikabot in Docker - installer v%INSTALLER_VERSION%
echo  ==================================================
echo.

docker --version >nul 2>&1
if errorlevel 1 (
    echo  Docker Desktop is not installed.
    echo.
    echo  Download it from:  https://www.docker.com/products/docker-desktop/
    echo  Install it, start it, then run this file again.
    echo.
    pause
    exit /b 1
)

docker info >nul 2>&1
if errorlevel 1 (
    echo  Docker Desktop is installed but not running.
    echo  Start Docker Desktop, wait for it to say "Engine running",
    echo  then run this file again.
    echo.
    pause
    exit /b 1
)

if not exist "%~dp0docker\Dockerfile" (
    echo  Cannot find the docker folder next to this file.
    echo  Extract the whole zip first, then run INSTALL.bat from inside it.
    echo.
    pause
    exit /b 1
)

set "DEFAULT_DIR=%USERPROFILE%\ikabot"
set /p "INSTALL_DIR=  Where should ikabot keep its data? [%DEFAULT_DIR%]: "
if "!INSTALL_DIR!"=="" set "INSTALL_DIR=%DEFAULT_DIR%"

set /p "INSTANCES=  How many accounts will you run? [4]: "
if "!INSTANCES!"=="" set "INSTANCES=4"

echo.
echo  Choose a password for the web pages.
set "PANEL_PASS="
set /p "PANEL_PASS=  Password: "
if "!PANEL_PASS!"=="" (
    echo.
    echo  A password is required. Nothing was installed.
    echo.
    pause
    exit /b 1
)

echo.
echo  Installing to : !INSTALL_DIR!
echo  Instances     : !INSTANCES!
echo.

if not exist "!INSTALL_DIR!\config" mkdir "!INSTALL_DIR!\config"
if not exist "!INSTALL_DIR!\app" mkdir "!INSTALL_DIR!\app"

rem ikabot itself lives on the host and is mounted at /app, so that a later
rem "ika update" survives the container being rebuilt. Only copied in when the
rem folder is empty, so re-running this never discards an updated ikabot.
if exist "!INSTALL_DIR!\app\ikabot" (
    echo  ikabot is already in !INSTALL_DIR!\app - left as it is.
    echo    ^(update it later with:  docker exec -it ikabot ika update^)
) else (
    echo  Installing ikabot into !INSTALL_DIR!\app ...
    xcopy /E /I /Q /Y "%~dp0app\*" "!INSTALL_DIR!\app\" >nul
)

if not exist "!INSTALL_DIR!\app\ikabot\__main__.py" (
    echo.
    echo  ikabot did not end up in !INSTALL_DIR!\app.
    echo  Extract the whole zip and run INSTALL.bat from inside it.
    echo.
    pause
    exit /b 1
)

echo  Building the image - this takes a few minutes the first time...
echo.
docker build -t ikabot-mod:latest "%~dp0docker"
if errorlevel 1 (
    echo.
    echo  The build failed. The output above says why.
    echo.
    pause
    exit /b 1
)

docker rm -f ikabot >nul 2>&1

echo.
echo  Starting...

rem Host networking is Linux-only, so on Windows the ports are published
rem individually. 7681 is the terminal, 7682 the control panel.
docker run -d ^
  --name ikabot ^
  --init ^
  --restart unless-stopped ^
  -p 7681:7681 ^
  -p 7682:7682 ^
  -e TTYD_USER=ikabot ^
  -e TTYD_PASS="!PANEL_PASS!" ^
  -e INSTANCES=!INSTANCES! ^
  -v "!INSTALL_DIR!\app:/app" ^
  -v "!INSTALL_DIR!\config:/config" ^
  ikabot-mod:latest
if errorlevel 1 (
    echo.
    echo  Could not start the container. The output above says why.
    echo.
    pause
    exit /b 1
)

timeout /t 5 /nobreak >nul

echo.
echo  ==================================================
echo    Done.
echo.
echo    Control panel : http://localhost:7682
echo    Terminal      : http://localhost:7681
echo.
echo    Username      : ikabot
echo    Password      : the one you just chose
echo.
echo    Open the control panel, then use the terminal to
echo    log each account in for the first time.
echo  ==================================================
echo.
echo  Opening the control panel...
start "" "http://localhost:7682"
echo.
pause
