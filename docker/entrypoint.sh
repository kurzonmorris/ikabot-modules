#!/bin/bash
# Starts one tmux window per ikabot instance and keeps the container alive
# for as long as that tmux session exists.
set -uo pipefail

INSTANCES="${INSTANCES:-24}"
SESSION="${TMUX_SESSION:-ikabot}"
AUTO_ACCOUNT="${AUTO_ACCOUNT:-1}"
MODULES_DIR="${MODULES_DIR:-/config/modules}"
DATA_DIR=/config/.ikabot

export HOME=/config

mkdir -p "$DATA_DIR" "$MODULES_DIR" "$DATA_DIR/sessions" "$DATA_DIR/logs"

# Point ikabot's External Modules screen at the modules folder on first run.
# Never rewritten afterwards, so a directory set from inside ikabot survives.
if [ ! -f "$DATA_DIR/external_modules.json" ]; then
    printf '{\n  "global_dir": "%s"\n}\n' "$MODULES_DIR" > "$DATA_DIR/external_modules.json"
    echo "[entrypoint] external modules directory set to $MODULES_DIR"
fi

# First run with an empty modules folder: populate it from the copy that
# ships in /app, so the container is usable without network access.
if [ -z "$(ls -A "$MODULES_DIR" 2>/dev/null)" ]; then
    echo "[entrypoint] modules folder is empty — installing modules from /app"
    ika-modules sync --local || echo "[entrypoint] local module sync failed (continuing)"
fi

if [ ! -f /config/.tmux.conf ]; then
    cat > /config/.tmux.conf <<'TMUXCONF'
set -g mouse on
set -g history-limit 50000
set -g default-terminal "screen-256color"
set -g base-index 1
setw -g remain-on-exit on
set -g status-interval 5
set -g status-left "[ikabot] "
set -g status-left-length 20
set -g status-right "%H:%M"
setw -g window-status-current-style "bg=colour24,fg=colour231,bold"
TMUXCONF
fi

if ! [[ "$INSTANCES" =~ ^[0-9]+$ ]] || [ "$INSTANCES" -lt 1 ]; then
    echo "[entrypoint] INSTANCES must be a positive whole number, got '$INSTANCES'" >&2
    exit 1
fi

tmux kill-session -t "$SESSION" 2>/dev/null

# Passing the instance number as an argument pre-answers ikabot's "Stored
# accounts" prompt, so window ika03 lands on vault account 3. Without a vault
# there is no account list and the number would be eaten by the Mail prompt
# instead, so only do this once a vault actually exists.
if [ ! -f "$DATA_DIR/vault" ] && [ ! -f /config/.ikabot_vault ]; then
    if [ "$AUTO_ACCOUNT" = "1" ]; then
        echo "[entrypoint] no vault yet — instances will ask for the account by hand"
    fi
    AUTO_ACCOUNT=0
fi
export AUTO_ACCOUNT

echo "[entrypoint] starting $INSTANCES ikabot instance(s)"
for i in $(seq 1 "$INSTANCES"); do
    win=$(printf "ika%02d" "$i")

    if [ "$AUTO_ACCOUNT" = "1" ]; then
        cmd="python3 -m ikabot $i"
    else
        cmd="python3 -m ikabot"
    fi

    if [ "$i" -eq 1 ]; then
        tmux new-session -d -s "$SESSION" -n "$win" -c /app "$cmd"
    else
        tmux new-window -t "$SESSION" -n "$win" -c /app "$cmd"
    fi
done

tmux select-window -t "$SESSION:ika01" 2>/dev/null

# --- web terminal ----------------------------------------------------------
# Deliberately refuses to start without a password: this serves a shell, and
# an unauthenticated one must never be reachable just because a variable was
# left unset.
TTYD_PORT="${TTYD_PORT:-7681}"
TTYD_USER="${TTYD_USER:-ikabot}"
TTYD_PASS="${TTYD_PASS:-}"

if [ -n "$TTYD_PASS" ]; then
    ttyd --port "$TTYD_PORT" \
         --credential "${TTYD_USER}:${TTYD_PASS}" \
         --writable \
         --client-option fontSize=15 \
         --client-option 'theme={"background":"#0A1119"}' \
         /usr/local/bin/ika-web-attach \
         >> "$DATA_DIR/logs/ttyd.log" 2>&1 &
    echo "[entrypoint] web terminal listening on port $TTYD_PORT (user: $TTYD_USER)"
else
    echo "[entrypoint] web terminal off — set TTYD_PASS to turn it on"
fi

echo "[entrypoint] ready — attach with:  docker exec -it ikabot ika attach"

while tmux has-session -t "$SESSION" 2>/dev/null; do
    sleep 10
done

echo "[entrypoint] tmux session '$SESSION' ended — stopping container"
