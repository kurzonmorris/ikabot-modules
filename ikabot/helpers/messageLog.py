#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""A local copy of every notification ikabot sends.

Telegram and Discord cannot be read back. A bot cannot list its own messages
and a webhook is write-only. So the copy is made here, as the message is sent,
and the control panel reads these files instead of any service. That also
covers an account with no backend set up at all.

One file per account, one JSON object per line. Nothing here raises: a
notification must not fail because its own log could not be written.
"""

import json
import os
import re
import sys
import time

from ikabot.config import IKABOT_DATA_DIR
from ikabot.helpers.logging import getLogger
from ikabot.helpers.taskWatchdog import account_id

logger = getLogger(__name__)

# Mirrors STATUS_DIR: a shared volume can hold every container's messages
# while each container keeps its own data directory, and therefore its own
# vault.
MESSAGE_DIR = os.getenv("IKABOT_MESSAGE_DIR") or os.path.join(
    IKABOT_DATA_DIR, "messages")

SCHEMA = 1
MAX_BYTES = 2 * 1024 * 1024
MAX_TITLE = 300
MAX_BODY = 4000

# A credential that reaches a message body by accident would otherwise sit in
# a file the panel serves to a browser.
_SECRETS = (
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}"),
    re.compile(r"https://\S*?discord(?:app)?\.com/api/webhooks/\S+", re.I),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-.]{12,}"),
)

# Frames from these files are the plumbing, not the caller worth naming.
_PLUMBING = ("messageLog.py", "botComm.py", "taskWatchdog.py")


def _scrub(text):
    """Replace anything shaped like a credential with a marker."""
    for pattern in _SECRETS:
        text = pattern.sub("[removed]", text)
    return text


def _caller():
    """The name of the module that asked for the notification."""
    try:
        frame = sys._getframe(1)
        for _ in range(12):
            if frame is None:
                break
            name = os.path.basename(frame.f_code.co_filename)
            if name not in _PLUMBING and name.endswith(".py"):
                return re.sub(r"_v[\d.]+$", "", name[:-3])[:64]
            frame = frame.f_back
    except Exception:
        pass
    return ""


def message_path(account):
    """Where one account's log lives."""
    return os.path.join(MESSAGE_DIR, "%s.jsonl" % account)


def _rotate(path):
    """Keep one old file, so the log cannot grow without limit."""
    try:
        if os.path.getsize(path) < MAX_BYTES:
            return
    except OSError:
        return
    try:
        os.replace(path, path + ".1")
    except OSError:
        logger.warning("Could not rotate %s", path, exc_info=True)


def log_message(session, msg, sent=(), module=""):
    """Append one record for a notification. Never raises.

    *sent* is a sequence of (kind, ok) pairs, one for each backend tried. An
    empty sequence means nothing was configured, which is still worth a record
    — the panel is then the only place the message exists.
    """
    try:
        text = _scrub(str(msg or "")).strip()
        title, _, body = text.partition("\n")
        record = {
            "schema": SCHEMA,
            "t": int(time.time()),
            "account": account_id(session),
            "module": str(module or "") or _caller(),
            "title": title[:MAX_TITLE],
            "body": body[:MAX_BODY],
            "sent": [{"kind": str(k)[:16], "ok": bool(o)} for k, o in sent],
        }
        os.makedirs(MESSAGE_DIR, exist_ok=True)
        path = message_path(record["account"])
        _rotate(path)
        # One short append under O_APPEND lands whole, so several instances
        # writing at once interleave lines rather than corrupt them. A rotation
        # during another process's write loses at most that process's next few
        # lines into the rotated file, which is why only one old file is kept.
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except Exception:
        logger.warning("Could not write the message log", exc_info=True)
        return False


def read_messages(account, limit=200):
    """The newest records for one account, newest first. Never raises."""
    out = []
    try:
        with open(message_path(account), encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return out
    for line in reversed(lines):
        if len(out) >= limit:
            break
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            out.append(record)
    return out
