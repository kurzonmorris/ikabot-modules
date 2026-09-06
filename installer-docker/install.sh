#!/usr/bin/env bash
# ikabot Docker installer — Linux, Unraid, TrueNAS, macOS.
# Run from the folder this file was extracted into.
set -uo pipefail

INSTALLER_VERSION="1.0.13"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

say()  { printf '%s\n' "$*"; }

# Only Debian-family systems have /etc/timezone. Arch, SteamOS, Fedora and
# macOS keep the zone as the tail of what /etc/localtime points at, and
# without this every one of them silently ran on the fallback zone.
host_tz() {
    if [ -s /etc/timezone ]; then
        tr -d '[:space:]' < /etc/timezone
        return
    fi
    local link
    link="$(readlink /etc/localtime 2>/dev/null || true)"
    case "$link" in
        */zoneinfo/*) printf '%s' "${link#*/zoneinfo/}" ;;
        *)            printf 'Etc/UTC' ;;
    esac
}
ask()  { local p="$1" d="$2" a; read -r -p "$p [$d]: " a </dev/tty; printf '%s' "${a:-$d}"; }

say ""
say "=================================================="
say "  ikabot in Docker — installer v${INSTALLER_VERSION}"
say "=================================================="
say ""

if ! command -v docker >/dev/null 2>&1; then
    say "Docker is not installed."
    say ""
    say "  Unraid     : Settings -> Docker -> Enable = Yes"
    say "  TrueNAS    : Apps -> install Docker"
    say "  Steam Deck : see docs/STEAMDECK_GUIDE.md"
    say "  Linux PC   : https://docs.docker.com/engine/install/"
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    say "Docker is installed but not running (or this user cannot reach it)."
    say ""
    say "  Start it   : sudo systemctl enable --now docker"
    say "  Then allow this user to reach it, and log out and back in:"
    say "               sudo usermod -aG docker \"$USER\""
    exit 1
fi

if [ ! -f "$HERE/docker/Dockerfile" ]; then
    say "Cannot find docker/Dockerfile next to this script."
    say "Extract the whole zip and run install.sh from inside it."
    exit 1
fi

if [ ! -d "$HERE/app/ikabot" ]; then
    say "Cannot find app/ikabot next to this script — the download is incomplete."
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

mkdir -p "$INSTALL_DIR/config" "$INSTALL_DIR/app" \
    || { say "Cannot create $INSTALL_DIR"; exit 1; }

# ikabot itself lives in a folder on the host, mounted at /app, so that
# `ika update` can replace it and the new version survives the container being
# rebuilt. Only ever populated when empty: re-running this installer must not
# throw away an ikabot that has since been updated.
if [ -d "$INSTALL_DIR/app/ikabot" ]; then
    say "ikabot is already in $INSTALL_DIR/app — left as it is."
    say "  (update it later with:  docker exec -it ikabot ika update)"
else
    say "Installing ikabot into $INSTALL_DIR/app ..."
    cp -r "$HERE/app/." "$INSTALL_DIR/app/" \
        || { say "Could not copy ikabot into $INSTALL_DIR/app"; exit 1; }
fi

if [ ! -f "$INSTALL_DIR/app/ikabot/__main__.py" ]; then
    say "ikabot did not end up in $INSTALL_DIR/app — stopping before it fails later."
    exit 1
fi

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
  -e TZ="$(host_tz)" \
  -v "$INSTALL_DIR/app":/app \
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
