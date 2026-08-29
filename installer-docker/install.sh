#!/usr/bin/env bash
# ikabot Docker installer — Linux, Unraid, TrueNAS, macOS.
# Run from the folder this file was extracted into.
set -uo pipefail

INSTALLER_VERSION="1.0.2"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say()  { printf '%s\n' "$*"; }
ask()  { local p="$1" d="$2" a; read -r -p "$p [$d]: " a </dev/tty; printf '%s' "${a:-$d}"; }

say ""
say "=================================================="
say "  ikabot in Docker — installer v${INSTALLER_VERSION}"
say "=================================================="
say ""

if ! command -v docker >/dev/null 2>&1; then
    say "Docker is not installed."
    say ""
    say "  Unraid   : Settings -> Docker -> Enable = Yes"
    say "  TrueNAS  : Apps -> install Docker"
    say "  Linux PC : https://docs.docker.com/engine/install/"
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    say "Docker is installed but not running (or this user cannot reach it)."
    say "Start Docker, or re-run with sudo, then try again."
    exit 1
fi

if [ ! -f "$HERE/docker/Dockerfile" ]; then
    say "Cannot find docker/Dockerfile next to this script."
    say "Extract the whole zip and run install.sh from inside it."
    exit 1
fi

# Unraid keeps persistent app data on the array; elsewhere use the home folder.
if [ -f /etc/unraid-version ]; then
    DEFAULT_DIR=/mnt/user/appdata/ikabot
    say "Unraid detected."
else
    DEFAULT_DIR="$HOME/ikabot"
fi

INSTALL_DIR="${IKABOT_DIR:-$(ask "Where should ikabot keep its data?" "$DEFAULT_DIR")}"
INSTANCES="${IKABOT_INSTANCES:-$(ask "How many accounts will you run?" "4")}"

if ! [[ "$INSTANCES" =~ ^[0-9]+$ ]] || [ "$INSTANCES" -lt 1 ]; then
    say "That is not a whole number. Aborting."
    exit 1
fi

PANEL_PASS="${IKABOT_PASS:-}"
while [ -z "$PANEL_PASS" ]; do
    read -r -s -p "Choose a password for the web pages: " PANEL_PASS </dev/tty; echo
    if [ -z "$PANEL_PASS" ]; then say "  A password is required."; fi
done

say ""
say "Installing to : $INSTALL_DIR"
say "Instances     : $INSTANCES"
say ""

mkdir -p "$INSTALL_DIR/config" || { say "Cannot create $INSTALL_DIR"; exit 1; }

say "Building the image — this takes a few minutes the first time..."
if ! docker build -t ikabot-mod:latest "$HERE/docker"; then
    say ""
    say "The build failed. The output above says why."
    exit 1
fi

docker rm -f ikabot >/dev/null 2>&1

say ""
say "Starting..."
docker run -d \
  --name ikabot \
  --init \
  --restart unless-stopped \
  --network=host \
  -e TTYD_USER=ikabot \
  -e TTYD_PASS="$PANEL_PASS" \
  -e INSTANCES="$INSTANCES" \
  -e TZ="$(cat /etc/timezone 2>/dev/null || echo Europe/London)" \
  -v "$INSTALL_DIR/config":/config \
  ikabot-mod:latest >/dev/null || { say "Could not start the container."; exit 1; }

sleep 4
IP="$(docker exec ikabot ika-panel-host 2>/dev/null || echo localhost)"

say ""
say "=================================================="
say "  Done."
say ""
say "  Control panel : http://${IP}:7682"
say "  Terminal      : http://${IP}:7681"
say ""
say "  Username      : ikabot"
say "  Password      : the one you just chose"
say ""
say "  Open the control panel, then use the terminal to"
say "  log each account in for the first time."
say "=================================================="
say ""
docker exec ikabot ika panel 2>/dev/null | sed 's/^/  /'
