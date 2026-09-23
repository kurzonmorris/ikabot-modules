#!/bin/bash
# The container's real entry point.
#
# It runs the copy of entrypoint.sh in /app when there is one, so a change to
# how the container starts arrives with `ika update` instead of a rebuild.
# Every recent rebuild was forced by entrypoint.sh alone; the base image and
# the package list have not changed since this setup was built.
set -u

BUILTIN=/usr/local/bin/entrypoint-builtin.sh
MOUNTED=/app/docker/entrypoint.sh
OVERRIDE=/config/.no-app-entrypoint

# A recovery switch. A broken entrypoint stops the container before the
# terminal or the panel exist, so there would be no way in to repair it.
# /config is on the host disk, so this file can be created from outside.
if [ -f "$OVERRIDE" ]; then
    echo "[entrypoint] $OVERRIDE exists — using the built-in startup"
    exec "$BUILTIN" "$@"
fi

if [ ! -f "$MOUNTED" ]; then
    exec "$BUILTIN" "$@"
fi

# Two checks, because they catch different damage. bash -n catches a file cut
# mid-command. The marker catches one cut on a clean line: that is still valid
# shell, so it would start, do nothing, exit, and stop the container.
if ! bash -n "$MOUNTED" 2>/dev/null; then
    echo "[entrypoint] $MOUNTED is not valid shell — using the built-in instead" >&2
    exec "$BUILTIN" "$@"
fi

if ! grep -q '^# ika-entrypoint-complete$' "$MOUNTED"; then
    echo "[entrypoint] $MOUNTED is not a complete entry point — using the built-in" >&2
    exec "$BUILTIN" "$@"
fi

echo "[entrypoint] using $MOUNTED"
exec bash "$MOUNTED" "$@"
