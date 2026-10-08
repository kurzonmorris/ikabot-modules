#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""How many requests this account sent to Ikariam, second by second.

Ikariam rate-limits by IP, and twenty-four instances behind one address is a
lot before anything unusual happens. The per-instance web server makes it
worse: it is a reverse proxy, so every page and every image a browser loads
through it is another request from the same address. Guessing which of those
caused a block is hopeless, so it is counted instead.

One bucket per second, written to a file the control panel reads. Per-minute
and per-thirty-second rates are not stored: the panel adds the seconds up, so
there is one set of numbers and they cannot disagree.

A file per process, not per account. A single account runs the menu, a web
server and a process for each background task, and they all send requests;
one file between them would mean a lock on the hot path. The panel sums the
files instead.

Nothing here raises, and nothing here blocks: a counter that can break a
request is worse than no counter.
"""

import json
import os
import tempfile
import time

from ikabot.config import IKABOT_DATA_DIR
from ikabot.helpers.logging import getLogger
from ikabot.helpers.taskWatchdog import account_id

logger = getLogger(__name__)

MONITOR_DIR = os.getenv("IKABOT_MONITOR_DIR") or os.path.join(
    IKABOT_DATA_DIR, "connections")

SCHEMA = 1
# Ten minutes of seconds. Enough to see a burst and what led up to it, and
# small enough that the whole window is one short list.
WINDOW = 600
# A write at most this often, and only when something was counted. An idle
# process writes nothing at all.
FLUSH_SECONDS = 2.0
# A file nobody has written to for this long is from a process that has gone.
STALE_SECONDS = 3600

_state = {
    "account": "",
    "path": "",
    "t0": 0,
    "counts": [],
    "errors": [],
    "total": 0,
    "peak": 0,
    "flushed": 0.0,
    "pruned": 0.0,
}


def _reset(account, now):
    _state["account"] = account
    _state["path"] = os.path.join(MONITOR_DIR, "%s.%d.json" % (account, os.getpid()))
    _state["t0"] = now
    _state["counts"] = [0]
    _state["errors"] = [0]


def _prune(now):
    """Delete the files of processes that have stopped."""
    if now - _state["pruned"] < STALE_SECONDS:
        return
    _state["pruned"] = now
    try:
        for name in os.listdir(MONITOR_DIR):
            path = os.path.join(MONITOR_DIR, name)
            if path == _state["path"] or not name.endswith(".json"):
                continue
            if now - os.path.getmtime(path) > STALE_SECONDS:
                os.unlink(path)
    except OSError:
        pass


def _flush(now):
    payload = {
        "schema": SCHEMA,
        "account": _state["account"],
        "pid": os.getpid(),
        "t0": _state["t0"],
        "updated": int(now),
        "counts": _state["counts"],
        "errors": _state["errors"],
        "total": _state["total"],
        "peak": _state["peak"],
    }
    directory = MONITOR_DIR
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".conn-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, _state["path"])
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _state["flushed"] = now


def record_request(session, status=None):
    """Count one request to Ikariam. Never raises, never blocks.

    *status* is the HTTP status, or None when the request never got an answer.
    Anything that is not a 2xx or 3xx counts as an error as well as a request,
    because a block arrives as a status rather than as silence.
    """
    try:
        now = time.time()
        second = int(now)
        account = account_id(session)

        if account != _state["account"] or not _state["counts"]:
            _reset(account, second)

        # Fill the silence. Seconds with no requests have to be present as
        # zeros, or a graph would draw a busy second next to one ten minutes
        # later as though they were neighbours.
        gap = second - (_state["t0"] + len(_state["counts"]) - 1)
        if gap > WINDOW:
            _reset(account, second)
        elif gap > 0:
            _state["counts"].extend([0] * gap)
            _state["errors"].extend([0] * gap)

        _state["counts"][-1] += 1
        if status is None or not 200 <= int(status) < 400:
            _state["errors"][-1] += 1
        _state["total"] += 1
        if _state["counts"][-1] > _state["peak"]:
            _state["peak"] = _state["counts"][-1]

        if len(_state["counts"]) > WINDOW:
            drop = len(_state["counts"]) - WINDOW
            _state["counts"] = _state["counts"][drop:]
            _state["errors"] = _state["errors"][drop:]
            _state["t0"] += drop

        if now - _state["flushed"] >= FLUSH_SECONDS:
            _flush(now)
            _prune(now)
        return True
    except Exception:
        logger.debug("Could not count a request", exc_info=True)
        return False


def flush_now():
    """Write the counts out without waiting for the next request."""
    try:
        if _state["counts"] and _state["account"]:
            _flush(time.time())
            return True
    except Exception:
        logger.debug("Could not write the request counts", exc_info=True)
    return False


def read_counts(account=None):
    """Every process's counts, as a list of payloads. Never raises."""
    out = []
    try:
        names = sorted(os.listdir(MONITOR_DIR))
    except OSError:
        return out
    for name in names:
        if not name.endswith(".json") or name.startswith("."):
            continue
        if account and not name.startswith(account + "."):
            continue
        try:
            with open(os.path.join(MONITOR_DIR, name), encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("schema") == SCHEMA:
            out.append(payload)
    return out
