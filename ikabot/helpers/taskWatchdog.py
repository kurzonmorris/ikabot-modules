#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""Task watchdog and machine-readable status export.

Two jobs:

1. **Watchdog.** A background task whose process dies without finishing used to
   just vanish from the menu's task table with nothing said. Unattended (Docker,
   24/7) that means silent failure. `check_for_dead_tasks()` compares the tasks
   the menu last saw against what is alive now and notifies via the normal
   `sendToBot()` fan-out for any that disappeared without completing.

2. **Status export.** Writes a small JSON file per account describing the
   running tasks, so an external monitor (the Docker maintenance page) can read
   instance health without HTTP, auth or port discovery. See
   `docs/DOCKER_STATUS_API.md`.

The file is written atomically (temp + os.replace), so a reader never sees a
half-written file and does not need a lock.
"""

import json
import os
import time

from ikabot.config import IKABOT_DATA_DIR, IKABOT_MOD_VERSION, IKABOT_VERSION
from ikabot.helpers.logging import getLogger

logger = getLogger(__name__)

# Where the per-account status files go. The monitor page wants one directory
# it can read for every instance, but the vault lives in IKABOT_DATA_DIR too —
# and sharing that whole directory across containers puts several ikabots on
# one vault file. Point IKABOT_STATUS_DIR at the shared volume instead and keep
# each container's data directory (and therefore its vault) private.
STATUS_DIR = os.getenv("IKABOT_STATUS_DIR") or os.path.join(IKABOT_DATA_DIR, "status")

# Tasks that end on their own are not failures. A task missing from the process
# list having never reported completion is what we care about.
_SEEN_KEY = "watchdogSeen"


def _safe(s):
    return "".join(c for c in str(s) if c.isalnum() or c in "-_")


def account_id(session):
    """Stable identifier for this account, matching the module-prefs scheme."""
    username = _safe(getattr(session, "username", "") or "unknown")
    servidor = _safe(getattr(session, "servidor", "") or "")
    mundo = _safe(str(getattr(session, "mundo", "") or ""))
    return f"{username}_{servidor}{mundo}"


def status_path(session):
    return os.path.join(STATUS_DIR, f"{account_id(session)}.json")


def write_status(session, process_list):
    """Export current task state for external monitors. Never raises."""
    try:
        os.makedirs(STATUS_DIR, exist_ok=True)
        payload = {
            "schema": 1,
            "account": account_id(session),
            "username": getattr(session, "username", None),
            "server": getattr(session, "servidor", None),
            "world": getattr(session, "mundo", None),
            "ikabot_version": IKABOT_VERSION,
            "mod_version": IKABOT_MOD_VERSION,
            "pid": os.getpid(),
            "updated": int(time.time()),
            "task_count": len(process_list),
            "tasks": [
                {
                    "pid": p.get("pid"),
                    "action": p.get("action"),
                    "status": p.get("status", "running"),
                    "started": int(p.get("date", 0)),
                }
                for p in process_list
            ],
        }
        path = status_path(session)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        logger.debug("Could not write status file", exc_info=True)


def check_for_dead_tasks(session, process_list):
    """Notify about tasks that vanished without completing.

    Compares the tasks recorded on the previous pass against those alive now.
    Anything that was running and is gone is reported once, then forgotten so
    it is not reported again.

    Returns the list of dead task descriptions (also used by the tests).
    """
    alive = {p["pid"]: p for p in process_list if p.get("pid") is not None}
    dead = []

    def _compare(data):
        previous = data.get(_SEEN_KEY) or {}
        for pid_str, info in previous.items():
            try:
                pid = int(pid_str)
            except (TypeError, ValueError):
                continue
            if pid not in alive:
                dead.append(info)
        data[_SEEN_KEY] = {
            str(pid): {"action": p.get("action"), "status": p.get("status", "running")}
            for pid, p in alive.items()
        }
        return data

    try:
        session.mutateSessionData(_compare)
    except Exception:
        logger.debug("Watchdog comparison failed", exc_info=True)
        return []

    if dead:
        # Import here: botComm imports gui/pedirInfo, and importing it at module
        # scope would create a cycle for callers of this helper.
        from ikabot.helpers.botComm import sendToBot

        names = ", ".join(
            "{} (was: {})".format(d.get("action") or "unknown", d.get("status") or "running")
            for d in dead
        )
        try:
            sendToBot(
                session,
                "Task stopped unexpectedly on {}: {}".format(account_id(session), names),
            )
        except Exception:
            logger.debug("Could not send dead-task notification", exc_info=True)

    return dead
