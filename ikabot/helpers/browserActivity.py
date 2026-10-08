#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""Records when the player last used the browser, so background tasks can
stand aside.

ikabot and the browser share one game session, and that session has a single
current city. When a background task loads `view=city&cityId=N` the player's
own view moves with it. During a manual action that needs several steps - the
shipping flow, for example - that interruption loses the action.

The web server is the only place that sees the player's clicks, so it stamps a
file here. A background task reads the stamp and waits.

Both sides must agree on the path, which is why this lives in helpers rather
than in either one.
"""

import os
import re
import time

from ikabot.config import IKABOT_DATA_DIR

# Writing on every request would hammer the disk: a single game page pulls
# many assets. One stamp every few seconds is enough to tell "active" from
# "idle".
STAMP_INTERVAL_SECONDS = 3

_last_write = [0.0]


def _safe(value):
    return re.sub(r"[^\w.-]", "_", str(value))


def activity_path(session):
    """Per-account stamp file. Includes the world: a player name is only
    unique within a world."""
    world = _safe(getattr(session, "mundo", "") or "")
    suffix = f"{_safe(session.servidor)}{world}_{_safe(session.username)}"
    return os.path.join(IKABOT_DATA_DIR, f"browser_activity_{suffix}")


def record_activity(session):
    """Stamp the file. Best effort: never raise into a request handler."""
    now = time.time()
    if now - _last_write[0] < STAMP_INTERVAL_SECONDS:
        return
    _last_write[0] = now
    try:
        os.makedirs(IKABOT_DATA_DIR, exist_ok=True)
        path = activity_path(session)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w") as f:
            f.write(str(int(now)))
        os.replace(tmp, path)
    except OSError:
        pass


def seconds_since_activity(session):
    """Seconds since the player's last browser request, or None if unknown.

    None means no stamp exists: either the web server is not running or the
    player has not used it. Callers must treat that as "cannot tell", not as
    "idle".
    """
    try:
        with open(activity_path(session), "r") as f:
            stamp = int(f.read().strip())
    except (OSError, ValueError):
        return None
    delta = time.time() - stamp
    # A stamp from the future means a clock change. Treat it as right now.
    return max(0.0, delta) if delta >= 0 else 0.0
