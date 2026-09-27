#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""Named places to send a notification to, and which accounts use each one.

ikabot's original settings hold one Telegram, one Discord and one ntfy, shared
by every account on the machine. There is nowhere to put a second Telegram
account. The Messaging Hub solved that for itself with a list of destinations
and a route per event type; this is the same idea, lifted out so core
notifications and the control panel share one list.

Nothing changes until the first destination is added. With an empty list
sendToBot() uses the original three settings exactly as before, so an existing
install keeps working without being touched.

The file is plain JSON, as the hub's own config already is. It holds bot
tokens, so it is written with owner-only permissions.
"""

import json
import os
import re
import tempfile
import uuid

from ikabot.config import IKABOT_DATA_DIR
from ikabot.helpers.logging import getLogger

logger = getLogger(__name__)

TARGETS_FILE = os.getenv("IKABOT_NOTIFY_FILE") or os.path.join(
    IKABOT_DATA_DIR, "notify_targets.json")

SCHEMA = 1
KINDS = ("telegram", "discord", "ntfy")
MAX_TARGETS = 40
NAME_OK = re.compile(r"^[\w \-/&().+#]{1,32}$")
ID_OK = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
ACCOUNT_OK = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
WEBHOOK_PREFIXES = (
    "https://discord.com/api/webhooks/",
    "https://discordapp.com/api/webhooks/",
    "https://ptb.discord.com/api/webhooks/",
    "https://canary.discord.com/api/webhooks/",
)

# The fields that hold a credential, by kind. Everything else about a target
# is safe to show.
SECRET_FIELDS = {
    "telegram": ("botToken",),
    "discord": ("webhookUrl",),
    # On the public server the topic is the only thing protecting the feed,
    # so it counts as a credential.
    "ntfy": ("topic", "token"),
}


def _new_id():
    return uuid.uuid4().hex[:12]


# ------------------------------------------------------------------ store ---

def _empty():
    return {"schema": SCHEMA, "targets": []}


def normalise(data):
    """A stored file turned into something the rest of this can rely on."""
    out = _empty()
    if not isinstance(data, dict):
        return out
    for raw in data.get("targets") or []:
        target, _ = clean_target(raw)
        if target is not None:
            out["targets"].append(target)
        if len(out["targets"]) >= MAX_TARGETS:
            break
    return out


def load():
    """The stored destinations. Never raises; a broken file reads as empty."""
    try:
        with open(TARGETS_FILE, encoding="utf-8") as f:
            return normalise(json.load(f))
    except (OSError, ValueError):
        return _empty()


def save(data):
    """Write the destinations atomically, readable by their owner only."""
    data = normalise(data)
    directory = os.path.dirname(TARGETS_FILE) or "."
    try:
        os.makedirs(directory, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".notify-")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, TARGETS_FILE)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    except OSError:
        logger.error("Could not write %s", TARGETS_FILE, exc_info=True)
        return False


# ------------------------------------------------------------ validation ---

def clean_target(raw, previous=None):
    """Validate one destination. Returns (target, error).

    *previous* is the stored version of the same destination, if there is one.
    A secret left blank on an edit keeps the one already stored, so the panel
    never has to send a token to a browser to get it back.
    """
    if not isinstance(raw, dict):
        return None, "not a destination"

    name = str(raw.get("name", "")).strip()
    if not NAME_OK.match(name):
        return None, ("Name must be 1-32 characters, using letters, numbers, "
                      "spaces or - / & ( ) . + #")

    kind = str(raw.get("kind", "")).strip().lower()
    if kind not in KINDS:
        return None, "Kind must be one of: %s." % ", ".join(KINDS)

    tid = str(raw.get("id") or "").strip()
    if tid and not ID_OK.match(tid):
        return None, "Bad destination id."

    accounts = raw.get("accounts") or []
    if isinstance(accounts, str):
        accounts = [a for a in accounts.replace(" ", ",").split(",") if a]
    for account in accounts:
        if not ACCOUNT_OK.match(str(account)):
            return None, "Bad account name in the list of accounts."
    accounts = sorted({str(a) for a in accounts})

    kept = (previous or {}).get(kind, {}) if previous else {}
    cfg = raw.get(kind) if isinstance(raw.get(kind), dict) else {}

    def field(key):
        value = str(cfg.get(key, "") or "").strip()
        return value or str(kept.get(key, "") or "").strip()

    if kind == "telegram":
        token, chat = field("botToken"), field("chatId")
        if not token or not chat:
            return None, "Telegram needs a bot token and a chat id."
        if ":" not in token:
            return None, "That does not look like a Telegram bot token."
        settings = {"botToken": token, "chatId": chat}
    elif kind == "discord":
        url = field("webhookUrl")
        if not url.startswith(WEBHOOK_PREFIXES):
            return None, ("A Discord webhook URL starts with "
                          "https://discord.com/api/webhooks/")
        settings = {"webhookUrl": url}
    else:
        topic = field("topic")
        if not topic:
            return None, "ntfy needs a topic."
        server = field("server") or "https://ntfy.sh"
        if not server.startswith(("http://", "https://")):
            return None, "The ntfy server must start with http:// or https://"
        settings = {"server": server.rstrip("/"), "topic": topic,
                    "token": field("token")}

    enabled = raw.get("enabled")
    return {
        "id": tid or _new_id(),
        "name": name,
        "kind": kind,
        "enabled": True if enabled is None else bool(enabled),
        # Empty means every account, the same as an empty instance list on a
        # panel button.
        "accounts": accounts,
        kind: settings,
    }, ""


def public(target):
    """One destination with its credentials replaced by a hint."""
    kind = target.get("kind", "")
    settings = dict(target.get(kind, {}) or {})
    for key in SECRET_FIELDS.get(kind, ()):
        value = str(settings.get(key, "") or "")
        settings[key] = ("…" + value[-4:]) if len(value) > 4 else ("set" if value else "")
    return {"id": target.get("id", ""), "name": target.get("name", ""),
            "kind": kind, "enabled": bool(target.get("enabled", True)),
            "accounts": list(target.get("accounts") or []), kind: settings}


# ------------------------------------------------------------- selecting ---

def targets_for(account, data=None):
    """The enabled destinations this account sends to."""
    data = load() if data is None else normalise(data)
    return [t for t in data["targets"]
            if t.get("enabled", True)
            and (not t.get("accounts") or account in t["accounts"])]


def have_targets(data=None):
    """True once at least one destination has been added."""
    data = load() if data is None else normalise(data)
    return bool(data["targets"])


# --------------------------------------------------------------- sending ---

def _post(url, **kwargs):
    from requests import post
    kwargs.setdefault("timeout", 30)
    return post(url, **kwargs)


def _send_telegram(cfg, msg, photo=None):
    url = "https://api.telegram.org/bot%s/%s" % (
        cfg["botToken"], "sendDocument" if photo else "sendMessage")
    if photo:
        resp = _post(url, files={"document": ("captcha.png", photo)},
                     data={"chat_id": cfg["chatId"], "caption": msg})
    else:
        resp = _post(url, data={"chat_id": cfg["chatId"], "text": msg})
    return 200 <= resp.status_code < 300


def _send_discord(cfg, msg, photo=None):
    resp = _post(cfg["webhookUrl"], json={"content": msg[:2000]})
    return 200 <= resp.status_code < 300


def _send_ntfy(cfg, msg, photo=None):
    lines = msg.strip().split("\n", 1)
    headers = {"Title": lines[0][:200]}
    if cfg.get("token"):
        headers["Authorization"] = "Bearer %s" % cfg["token"]
    body = lines[1] if len(lines) > 1 else ""
    resp = _post("%s/%s" % (cfg.get("server", "https://ntfy.sh").rstrip("/"),
                            cfg["topic"]),
                 data=body.encode("utf-8"), headers=headers)
    return 200 <= resp.status_code < 300


_SENDERS = {"telegram": _send_telegram, "discord": _send_discord,
            "ntfy": _send_ntfy}


def send_one(target, msg, photo=None):
    """Deliver to one destination. Returns (ok, detail)."""
    kind = target.get("kind", "")
    sender = _SENDERS.get(kind)
    if sender is None:
        return False, "unknown kind %s" % kind
    try:
        if sender(target.get(kind, {}), msg, photo):
            return True, ""
        return False, "the service refused it"
    except Exception as exc:
        logger.error("Delivery to %s failed", target.get("name"), exc_info=True)
        return False, "%s: %s" % (type(exc).__name__, exc)


def send_all(account, msg, photo=None, data=None):
    """Deliver to every destination this account uses.

    Returns a list of (kind, ok) pairs, in the order they were tried, which is
    what the message log records.
    """
    out = []
    for target in targets_for(account, data):
        ok, _ = send_one(target, msg, photo)
        out.append((target.get("kind", "?"), ok))
    return out
