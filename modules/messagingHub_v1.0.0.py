#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""Messaging Hub — forwards in-game Ikariam messages to Discord, Telegram and ntfy."""

MODULE_NAME = "Messaging Hub"
MODULE_ENTRY = "messagingHub"
MENU_LABEL = "Messaging Hub"
MENU_ORDER = 50

import copy
import html
import json
import os
import random
import re
import socket
import sys
import time
import traceback
import uuid

import ikabot.config as config
from ikabot.config import IKABOT_DATA_DIR
from ikabot.helpers.botComm import sendToBot
from ikabot.helpers.gui import banner, bcolors, enter
from ikabot.helpers.pedirInfo import read
from ikabot.helpers.process import set_child_mode
from ikabot.helpers.signals import setInfoSignal
from ikabot.helpers.varios import (
    addThousandSeparator,
    daysHoursMinutes,
    getDateTime,
    wait,
)

# Vanilla ikabot has neither of these — the hub must run on both trees.
try:
    from ikabot.helpers.botComm import notificationDataIsValid
except ImportError:
    from ikabot.helpers.botComm import telegramDataIsValid as notificationDataIsValid

try:
    from ikabot.helpers.modulePrefs import (
        load_prefs,
        save_prefs,
        is_autostart,
        set_autostart,
    )

    _HAS_MODULE_PREFS = True
except ImportError:
    _HAS_MODULE_PREFS = False

try:
    from ikabot.helpers.logging import getLogger

    logger = getLogger(__name__)
except ImportError:
    import logging

    logger = logging.getLogger(__name__)


CONFIG_VERSION = 1
HUB_DIR_NAME = "messaging_hub"
SESSION_DIR_KEY = "messagingHubDir"
PREFS_NAME = "messagingHub"

DEFAULT_MESSAGE_INTERVAL = 10
MIN_MESSAGE_INTERVAL = 1
DEFAULT_RESOURCE_INTERVAL = 30
MIN_RESOURCE_INTERVAL = 5
DEFAULT_COOLDOWN_MINUTES = 120
DEFAULT_REARM_MARGIN = 10
DEFAULT_BODY_MAX = 900

RESOURCES = ("wood", "wine", "marble", "crystal", "sulfur")
RESOURCE_LABELS = {
    "wood": "Wood",
    "wine": "Wine",
    "marble": "Marble",
    "crystal": "Crystal",
    "sulfur": "Sulfur",
}
RESOURCE_MODES = ("absolute", "percent", "hours_left")
MODE_LABELS = {
    "absolute": "amount",
    "percent": "% of warehouse",
    "hours_left": "hours left",
}
SEEN_HARD_CAP = 5000
FAILURE_REPORT_COOLDOWN = 3600

# The global config can live on a network share, so the lock must survive
# other machines, wrong clocks, dead holders and filesystems where O_EXCL
# is not truly atomic.  Critical sections are read-modify-write only: never
# hold the lock across a prompt or a network request.
LOCK_STALE_SECONDS = 45
LOCK_TIMEOUT = 20
LOCK_MAX_HOLD = 60
SHARED_SECTIONS = (
    "destinations",
    "routes",
    "type_enabled",
    "formatting",
    "resource_rules",
)
SNAPSHOT_KEY = "_snapshot"

DISCORD_WEBHOOK_PREFIXES = (
    "https://discord.com/api/webhooks/",
    "https://discordapp.com/api/webhooks/",
    "https://ptb.discord.com/api/webhooks/",
    "https://canary.discord.com/api/webhooks/",
)

# Order matters: the routing screen and the config file both follow this list.
EVENT_TYPES = [
    "player_message",
    "alliance_message",
    "combat",
    "espionage",
    "piracy",
    "construction",
    "shipment_internal",
    "shipment_external",
    "news",
    "treaty",
    "research",
    "other",
    "resource_alert",
    "hub_status",
]

TYPE_LABELS = {
    "player_message": "Player messages",
    "alliance_message": "Alliance messages",
    "combat": "Combat reports",
    "espionage": "Espionage reports",
    "piracy": "Piracy",
    "construction": "Construction",
    "shipment_internal": "Shipments (internal)",
    "shipment_external": "Shipments (from outside)",
    "news": "News / announcements",
    "treaty": "Treaties",
    "research": "Research",
    "other": "Other / unclassified",
    "resource_alert": "Resource alerts",
    "hub_status": "Hub status & errors",
}

TYPE_EMOJI = {
    "player_message": "✉",
    "alliance_message": "\U0001f91d",
    "combat": "⚔",
    "espionage": "\U0001f575",
    "piracy": "\U0001f3f4",
    "construction": "\U0001f3d7",
    "shipment_internal": "\U0001f6a2",
    "shipment_external": "\U0001f4e6",
    "news": "\U0001f4f0",
    "treaty": "\U0001f4dc",
    "research": "\U0001f52c",
    "other": "❓",
    "resource_alert": "\U0001f4ca",
    "hub_status": "⚙",
}

DISCORD_COLORS = {
    "player_message": 3447003,
    "alliance_message": 3066993,
    "combat": 15158332,
    "espionage": 10181046,
    "piracy": 2303786,
    "construction": 15844367,
    "shipment_internal": 1752220,
    "shipment_external": 3426654,
    "news": 9807270,
    "treaty": 12370112,
    "research": 5763719,
    "other": 9807270,
    "resource_alert": 15105570,
    "hub_status": 6323595,
}

NTFY_TAGS = {
    "player_message": ["email"],
    "alliance_message": ["handshake"],
    "combat": ["crossed_swords"],
    "espionage": ["detective"],
    "piracy": ["pirate_flag"],
    "construction": ["building_construction"],
    "shipment_internal": ["ship"],
    "shipment_external": ["package"],
    "news": ["newspaper"],
    "treaty": ["scroll"],
    "research": ["microscope"],
    "other": ["question"],
    "resource_alert": ["bar_chart"],
    "hub_status": ["gear"],
}

# Classification keywords, checked against subject + body.  English is always
# consulted as a fallback because many servers mix languages in system mail.
# Phase 2 fills the other languages in from real captures — see plan.md.
KEYWORDS = {
    "en": {
        "espionage": ["espionage", "spy report", "spied", "saboteur", "infiltrat"],
        "piracy": ["pirate", "piracy", "plunder", "raid", "booty", "crew points"],
        "combat": ["combat report", "battle", "fight", "attacked", "besieg", "occupied"],
        "treaty": ["cultural treaty", "trade agreement", "treaty", "alliance offer"],
        "research": ["research", "scientist", "discovered", "new technology"],
        "construction": [
            "has been completed",
            "construction",
            "building level",
            "expansion",
            "finished the",
            "upgraded to level",
        ],
        "shipment_external": ["has delivered", "trade", "market", "bought", "sold", "purchase"],
        "shipment_internal": ["transport", "freighter", "cargo ship", "shipment", "arrived"],
        "news": ["news", "announcement", "server", "maintenance", "event", "update"],
        "alliance_message": ["alliance", "circular", "ally"],
    },
    "de": {
        "espionage": ["spionage", "spionagebericht", "spion"],
        "piracy": ["pirat", "beute", "plünder"],
        "combat": ["kampfbericht", "kampf", "angriff", "belagerung"],
        "treaty": ["kulturabkommen", "handelsvertrag", "abkommen"],
        "research": ["forschung", "wissenschaftler"],
        "construction": ["fertiggestellt", "ausbau", "baustufe", "gebäude"],
        "shipment_external": ["geliefert", "handel", "markt", "gekauft", "verkauft"],
        "shipment_internal": ["transport", "frachtschiff", "lieferung", "angekommen"],
        "news": ["neuigkeiten", "ankündigung", "wartung"],
        "alliance_message": ["allianz", "rundschreiben"],
    },
    "es": {
        "espionage": ["espionaje", "informe de espionaje", "espía"],
        "piracy": ["pirata", "piratería", "botín", "saqueo"],
        "combat": ["informe de combate", "combate", "batalla", "ataque", "asedio"],
        "treaty": ["tratado cultural", "acuerdo comercial", "tratado"],
        "research": ["investigación", "científico"],
        "construction": ["ha sido completado", "construcción", "nivel", "ampliación"],
        "shipment_external": ["ha entregado", "comercio", "mercado", "comprado", "vendido"],
        "shipment_internal": ["transporte", "carguero", "envío", "llegado"],
        "news": ["noticias", "anuncio", "mantenimiento"],
        "alliance_message": ["alianza", "circular"],
    },
}

# The only types another player can actually send. Everything else in this
# taxonomy is generated by the game, so player mail is never tested against it.
PLAYER_CLASSIFY_ORDER = ["treaty", "alliance_message"]

# Checked in this order — most specific first.
CLASSIFY_ORDER = [
    "espionage",
    "piracy",
    "combat",
    "treaty",
    "research",
    "construction",
    "shipment_external",
    "shipment_internal",
    "alliance_message",
    "news",
]


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _safe(value):
    return "".join(c for c in str(value) if c.isalnum() or c in "-_")


def _account_key(session):
    """Filename prefix identifying this account — every hub file carries it."""
    username = _safe(getattr(session, "username", "") or "unknown")
    servidor = _safe(getattr(session, "servidor", "") or "")
    mundo = _safe(str(getattr(session, "mundo", "") or ""))
    return "{}_{}{}".format(username, servidor, mundo)


def _base_dir(session):
    try:
        stored = session.getSessionData().get("shared", {}).get(SESSION_DIR_KEY, "")
    except Exception:
        stored = ""
    return stored or IKABOT_DATA_DIR


def _set_base_dir(session, path):
    session.setSessionData({SESSION_DIR_KEY: path}, shared=True)


def _hub_dir(session):
    path = os.path.join(_base_dir(session), HUB_DIR_NAME)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        logger.error("Could not create hub directory %s", path, exc_info=True)
    return path


def _config_path(session):
    return os.path.join(_hub_dir(session), "{}_config.json".format(_account_key(session)))


def _state_path(session):
    return os.path.join(_hub_dir(session), "{}_state.json".format(_account_key(session)))


def _global_config_path(session):
    # Deliberately not account-named: this is the file every instance shares.
    return os.path.join(_hub_dir(session), "global_config.json")


def _capture_dir(session):
    path = os.path.join(_hub_dir(session), "capture")
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        logger.error("Could not create capture directory %s", path, exc_info=True)
    return path


def _read_json(path):
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
            logger.warning("%s does not contain a JSON object", path)
    except Exception:
        logger.warning("Could not read %s", path, exc_info=True)
    return None


def _read_json_resilient(path):
    """Read a file, falling back to the last known good copy if it is broken."""
    data = _read_json(path)
    if data is not None:
        return data
    if os.path.exists(path):
        backup = _read_json(path + ".bak")
        if backup is not None:
            logger.warning("Recovered %s from its backup copy", path)
            return backup
    return None


def _write_json(path, data, keep_backup=False):
    """Atomic write. A reader either sees the whole old file or the whole new one."""
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        if keep_backup and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as src:
                    previous = src.read()
                with open(path + ".bak.tmp", "w", encoding="utf-8") as dst:
                    dst.write(previous)
                os.replace(path + ".bak.tmp", path + ".bak")
            except OSError:
                logger.debug("Could not refresh backup of %s", path, exc_info=True)
        tmp = "{}.{}.tmp".format(path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception:
        logger.error("Could not write %s", path, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Cross-instance lock for the shared global config
# ---------------------------------------------------------------------------


class _LockBusy(Exception):
    """Another instance is holding the lock and still alive."""


class _LockUnavailable(Exception):
    """The lock cannot be used at all — read-only folder, missing share."""


class _LockLost(Exception):
    """Our lock was taken away mid-section. The write was abandoned, not applied."""


_lock_state = {"token": None, "depth": 0, "acquired_at": 0.0}


def _pid_alive(pid):
    """Never touches the process. On Windows os.kill would terminate it."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong(0)
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return exit_code.value == STILL_ACTIVE
                return True
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _lock_path(session):
    return _global_config_path(session) + ".lock"


def _lock_payload(session, token):
    return {
        "token": token,
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "account": _account_key(session),
        "timestamp": time.time(),
    }


def _read_lock(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _lock_age(path, data):
    """Age of the lock, taking whichever evidence says it is youngest.

    The file may sit on a share written by a machine whose clock is wrong, so
    a single timestamp cannot be trusted. Erring young means we wait for a
    lock we could have stolen — far better than stealing a live one.
    """
    ages = []
    try:
        ages.append(time.time() - float(data.get("timestamp", 0)))
    except (TypeError, ValueError):
        pass
    try:
        ages.append(time.time() - os.path.getmtime(path))
    except OSError:
        pass
    ages = [a for a in ages if a >= 0]
    return min(ages) if ages else 0.0


def _holder_is_dead(data):
    # A pid only means something on the machine that wrote it.
    if data.get("host") != socket.gethostname():
        return False
    pid = data.get("pid")
    return bool(pid) and not _pid_alive(pid)


def _describe_holder(data):
    if not data:
        return "unknown"
    return "{}@{} (pid {})".format(
        data.get("account", "?"), data.get("host", "?"), data.get("pid", "?")
    )


def _lock_write(path, payload, exclusive):
    """Create or steal the lock file. Returns True if the bytes were written."""
    if exclusive:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, json.dumps(payload).encode("utf-8"))
        finally:
            os.close(fd)
        return True

    tmp = "{}.{}.{}.tmp".format(path, os.getpid(), uuid.uuid4().hex[:8])
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, path)
        return True
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def _acquire_lock(session, timeout=LOCK_TIMEOUT):
    """Take the global-config lock. Raises _LockBusy or _LockUnavailable."""
    path = _lock_path(session)
    token = uuid.uuid4().hex
    payload = _lock_payload(session, token)
    deadline = time.time() + timeout
    holder = None

    while True:
        try:
            _lock_write(path, payload, exclusive=True)
            # O_EXCL is not atomic on every network filesystem, so confirm the
            # file really carries our token before believing we own it.
            current = _read_lock(path)
            if current and current.get("token") == token:
                return token
        except FileExistsError:
            current = _read_lock(path)
            age = _lock_age(path, current or {})
            unreadable = current is None
            if unreadable and age > LOCK_STALE_SECONDS:
                stale = True
            elif current is not None and (
                age > LOCK_STALE_SECONDS or _holder_is_dead(current)
            ):
                stale = True
            else:
                stale = False
                holder = current

            if stale:
                logger.warning(
                    "Reclaiming stale hub lock from %s (age %.0fs)",
                    _describe_holder(current),
                    age,
                )
                try:
                    payload["timestamp"] = time.time()
                    _lock_write(path, payload, exclusive=False)
                    current = _read_lock(path)
                    if current and current.get("token") == token:
                        return token
                except OSError:
                    logger.debug("Stale lock takeover failed", exc_info=True)
        except OSError as exc:
            # No such directory, read-only share, permission denied — the lock
            # can never be taken here, so say so instead of spinning.
            raise _LockUnavailable(str(exc))

        if time.time() >= deadline:
            raise _LockBusy(_describe_holder(holder))
        time.sleep(random.uniform(0.2, 0.7))


def _lock_is_ours(session, token):
    current = _read_lock(_lock_path(session))
    return bool(current) and current.get("token") == token


def _release_lock(session, token):
    """Only ever remove a lock file that is still ours."""
    path = _lock_path(session)
    try:
        current = _read_lock(path)
        if current is None:
            return
        if current.get("token") == token:
            os.remove(path)
        else:
            logger.warning(
                "Hub lock was taken over by %s before we released it",
                _describe_holder(current),
            )
    except FileNotFoundError:
        pass
    except OSError:
        logger.debug("Could not release hub lock", exc_info=True)


class _global_lock:
    """Re-entrant context manager around the shared global config lock."""

    def __init__(self, session, timeout=LOCK_TIMEOUT):
        self.session = session
        self.timeout = timeout
        self.outermost = False

    def __enter__(self):
        if _lock_state["depth"] > 0:
            _lock_state["depth"] += 1
            return self
        token = _acquire_lock(self.session, self.timeout)
        _lock_state["token"] = token
        _lock_state["depth"] = 1
        _lock_state["acquired_at"] = time.time()
        self.outermost = True
        return self

    def __exit__(self, exc_type, exc, tb):
        _lock_state["depth"] -= 1
        if _lock_state["depth"] > 0:
            return False
        held = time.time() - _lock_state["acquired_at"]
        if held > LOCK_MAX_HOLD:
            logger.warning(
                "Held the hub lock for %.0fs — sections must stay short", held
            )
        _release_lock(self.session, _lock_state["token"])
        _lock_state["token"] = None
        return False

    def still_ours(self):
        return _lock_is_ours(self.session, _lock_state["token"])


def _default_config():
    return {
        "config_version": CONFIG_VERSION,
        "use_global": {"routing": False, "formatting": False, "resources": False},
        "destinations": [],
        "routes": {t: [] for t in EVENT_TYPES},
        "type_enabled": {t: True for t in EVENT_TYPES},
        "watchers": {
            "messages": {
                "enabled": True,
                "interval_minutes": DEFAULT_MESSAGE_INTERVAL,
                "notify_existing": False,
            },
            "resources": {
                "enabled": False,
                "interval_minutes": DEFAULT_RESOURCE_INTERVAL,
            },
        },
        "resource_rules": [],
        "formatting": {
            "include_body": True,
            "body_max_chars": DEFAULT_BODY_MAX,
            "combat_full_report": False,
            "mutes": [],
            "quiet_hours": {"enabled": False, "from": "23:00", "to": "07:00", "types": []},
        },
        "classification_overrides": [],
        "seen_retention_days": 14,
    }


def _normalise_config(cfg):
    """Fill in anything a hand-edited or older config file is missing."""
    base = _default_config()
    if not isinstance(cfg, dict):
        return base

    merged = dict(base)
    for key, value in cfg.items():
        merged[key] = value

    for key in ("use_global", "formatting"):
        section = dict(base[key])
        if isinstance(merged.get(key), dict):
            section.update(merged[key])
        merged[key] = section

    quiet = dict(base["formatting"]["quiet_hours"])
    if isinstance(merged["formatting"].get("quiet_hours"), dict):
        quiet.update(merged["formatting"]["quiet_hours"])
    merged["formatting"]["quiet_hours"] = quiet

    watchers = dict(base["watchers"])
    if isinstance(merged.get("watchers"), dict):
        for name, values in merged["watchers"].items():
            if isinstance(values, dict):
                entry = dict(watchers.get(name, {}))
                entry.update(values)
                watchers[name] = entry
    merged["watchers"] = watchers

    routes = {t: [] for t in EVENT_TYPES}
    if isinstance(merged.get("routes"), dict):
        for t, ids in merged["routes"].items():
            if t in routes and isinstance(ids, list):
                routes[t] = [str(i) for i in ids]
    merged["routes"] = routes

    enabled = {t: True for t in EVENT_TYPES}
    if isinstance(merged.get("type_enabled"), dict):
        for t, value in merged["type_enabled"].items():
            if t in enabled:
                enabled[t] = bool(value)
    merged["type_enabled"] = enabled

    if not isinstance(merged.get("destinations"), list):
        merged["destinations"] = []
    if not isinstance(merged.get("classification_overrides"), list):
        merged["classification_overrides"] = []

    rules = []
    for rule in merged.get("resource_rules") or []:
        if not isinstance(rule, dict):
            continue
        if rule.get("resource") not in RESOURCES:
            continue
        if rule.get("mode") not in RESOURCE_MODES:
            rule["mode"] = "absolute"
        if rule.get("direction") not in ("below", "above"):
            rule["direction"] = "below"
        if rule.get("scope") not in ("city", "global"):
            rule["scope"] = "global"
        try:
            rule["threshold"] = float(rule.get("threshold", 0))
        except (TypeError, ValueError):
            continue
        if not isinstance(rule.get("destinations"), list):
            rule["destinations"] = []
        rules.append(rule)
    merged["resource_rules"] = rules

    return merged


def _load_account_config(session):
    return _normalise_config(_read_json_resilient(_config_path(session)))


def _save_account_config(session, cfg):
    cfg.pop(SNAPSHOT_KEY, None)
    cfg["config_version"] = CONFIG_VERSION
    ok = _write_json(_config_path(session), cfg, keep_backup=True)
    # Give the mod's auto-start screen a prefs file to flag; the real settings
    # stay in the hub's own config so they can be shared between accounts.
    if ok and _HAS_MODULE_PREFS:
        prefs = load_prefs(session, PREFS_NAME) or {}
        prefs["config_version"] = CONFIG_VERSION
        save_prefs(session, PREFS_NAME, prefs)
    return ok


def _load_global_config(session):
    """Read the shared config. Lock-free — writers replace the file atomically.

    The snapshot recorded here is what a later save diffs against, so two
    instances editing different sections do not overwrite each other.
    """
    data = _read_json_resilient(_global_config_path(session))
    if data is None:
        return None
    cfg = _normalise_config(data)
    cfg[SNAPSHOT_KEY] = copy.deepcopy({s: cfg.get(s) for s in SHARED_SECTIONS})
    return cfg


def _save_global_config(session, cfg, retries=3):
    """Merge this account's changed sections into the shared file, under lock.

    Returns (ok, message). Never raises: a locking problem must never take the
    hub down, it just means this edit was not saved.
    """
    snapshot = cfg.pop(SNAPSHOT_KEY, None)
    cfg["config_version"] = CONFIG_VERSION
    path = _global_config_path(session)

    for attempt in range(retries):
        try:
            with _global_lock(session) as lock:
                disk = _read_json_resilient(path)
                merged = _normalise_config(disk) if disk is not None else _default_config()

                if snapshot is None:
                    merged = dict(cfg)
                else:
                    for section in SHARED_SECTIONS:
                        if cfg.get(section) == snapshot.get(section):
                            continue
                        if merged.get(section) != snapshot.get(section):
                            logger.warning(
                                "Global '%s' changed on disk since it was read; "
                                "this account's version wins",
                                section,
                            )
                        merged[section] = cfg.get(section)

                merged.pop(SNAPSHOT_KEY, None)
                merged["config_version"] = CONFIG_VERSION
                merged["revision"] = int((disk or {}).get("revision", 0)) + 1
                merged["last_written_by"] = _account_key(session)

                # Re-check ownership immediately before replacing the file: if
                # somebody reclaimed our lock we must abandon the write rather
                # than clobber whatever they wrote.
                if not lock.still_ours():
                    raise _LockLost("lock taken over during the write")

                if _write_json(path, merged, keep_backup=True):
                    return True, ""
                return False, "could not write the global configuration file"
        except _LockLost:
            logger.warning("Hub lock lost mid-write, retrying (%d/%d)", attempt + 1, retries)
            time.sleep(random.uniform(0.3, 1.0))
        except _LockBusy as exc:
            return False, "another instance is editing it right now ({})".format(exc)
        except _LockUnavailable as exc:
            return False, "the folder cannot be locked ({})".format(exc)
        except Exception as exc:
            logger.error("Unexpected error saving global config", exc_info=True)
            return False, str(exc)[:120]

    return False, "another instance kept taking the lock — nothing was changed"


def _effective_config(session):
    """Account config with whole sections swapped for the global ones."""
    cfg = _load_account_config(session)
    use_global = cfg.get("use_global", {})
    if not any(
        use_global.get(k) for k in ("routing", "formatting", "resources")
    ):
        return cfg

    shared = _load_global_config(session)
    if shared is None:
        return cfg

    if use_global.get("routing"):
        # destinations, routes and type_enabled move together — routes hold
        # destination ids, so splitting them would leave dangling references.
        cfg["destinations"] = shared["destinations"]
        cfg["routes"] = shared["routes"]
        cfg["type_enabled"] = shared["type_enabled"]
    if use_global.get("formatting"):
        cfg["formatting"] = shared["formatting"]
    if use_global.get("resources"):
        cfg["resource_rules"] = shared["resource_rules"]
    return cfg


def _load_state(session):
    state = _read_json(_state_path(session))
    if not isinstance(state, dict):
        state = {}
    state.setdefault("seen_ids", {})
    state.setdefault("delivered", 0)
    state.setdefault("failed", 0)
    state.setdefault("last_error", "")
    state.setdefault("last_failure_report", 0)
    state.setdefault("first_run_done", False)
    return state


def _save_state(session, state):
    _write_json(_state_path(session), state)


def _prune_seen(state, retention_days):
    seen = state.get("seen_ids", {})
    cutoff = time.time() - max(1, int(retention_days)) * 86400
    seen = {k: v for k, v in seen.items() if float(v or 0) >= cutoff}
    if len(seen) > SEEN_HARD_CAP:
        ordered = sorted(seen.items(), key=lambda kv: float(kv[1] or 0), reverse=True)
        seen = dict(ordered[:SEEN_HARD_CAP])
    state["seen_ids"] = seen


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------


def _requests():
    import requests

    return requests


def _post_with_retry(fn):
    """Run a request callable up to 3 times. Returns (ok, detail)."""
    delay = 2
    detail = ""
    for attempt in range(3):
        try:
            resp = fn()
            if 200 <= resp.status_code < 300:
                return True, ""
            detail = "HTTP {}: {}".format(resp.status_code, str(resp.text)[:200])
            if resp.status_code == 429:
                try:
                    retry_after = float(resp.json().get("retry_after", delay))
                except Exception:
                    retry_after = delay
                time.sleep(min(30, max(1, retry_after)))
                continue
            if resp.status_code < 500:
                return False, detail
        except Exception as exc:
            detail = str(exc)[:200]
        if attempt < 2:
            time.sleep(delay)
            delay *= 2
    return False, detail


def _send_discord(dest, events, fmt, footer):
    cfg = dest.get("discord", {})
    url = cfg.get("webhook_url", "")
    if not url:
        return False, "no webhook url"

    requests = _requests()

    if cfg.get("use_embeds", True):
        embeds = [_discord_embed(e, fmt, footer) for e in events[:10]]
        payload = {"embeds": embeds}
        if cfg.get("username"):
            payload["username"] = cfg["username"]
        ok, detail = _post_with_retry(lambda: requests.post(url, json=payload, timeout=30))
        if ok:
            leftover = events[10:]
            if leftover:
                return _send_discord(dest, leftover, fmt, footer)
            return True, ""
        logger.warning("Discord embed send failed (%s), retrying as plain text", detail)

    text = "\n\n".join(_format_event_text(e, fmt) for e in events)
    for chunk in _chunk(text, 1900):
        payload = {"content": chunk}
        if cfg.get("username"):
            payload["username"] = cfg["username"]
        ok, detail = _post_with_retry(lambda: requests.post(url, json=payload, timeout=30))
        if not ok:
            return False, detail
    return True, ""


def _discord_embed(event, fmt, footer):
    embed = {
        "title": "{} {}".format(
            TYPE_EMOJI.get(event["type"], ""), event.get("title", "")
        )[:256],
        "color": DISCORD_COLORS.get(event["type"], 9807270),
        "footer": {"text": footer[:2048]},
    }
    body = _event_body(event, fmt)
    if body:
        embed["description"] = body[:4096]

    fields = []
    if event.get("sender"):
        fields.append({"name": "From", "value": str(event["sender"])[:1024], "inline": True})
    if event.get("city"):
        fields.append({"name": "City", "value": str(event["city"])[:1024], "inline": True})
    if event.get("date"):
        fields.append({"name": "Date", "value": str(event["date"])[:1024], "inline": True})
    if fields:
        embed["fields"] = fields
    return embed


def _send_telegram(dest, events, fmt, footer):
    cfg = dest.get("telegram", {})
    token = cfg.get("bot_token", "")
    chat_id = cfg.get("chat_id", "")
    if not token or not chat_id:
        return False, "no bot token or chat id"

    requests = _requests()
    url = "https://api.telegram.org/bot{}/sendMessage".format(token)
    text = "\n\n".join(_format_event_text(e, fmt) for e in events)
    text = "{}\n{}".format(footer, text)

    for chunk in _chunk(text, 4000):
        params = {"chat_id": chat_id, "text": chunk}
        if cfg.get("thread_id"):
            params["message_thread_id"] = cfg["thread_id"]
        ok, detail = _post_with_retry(lambda: requests.post(url, data=params, timeout=30))
        if not ok:
            return False, detail
    return True, ""


def _send_ntfy(dest, events, fmt, footer):
    cfg = dest.get("ntfy", {})
    topic = cfg.get("topic", "")
    if not topic:
        return False, "no topic"

    requests = _requests()
    server = (cfg.get("server") or "https://ntfy.sh").rstrip("/")
    url = "{}/{}".format(server, topic)

    for event in events:
        headers = {
            "Title": "{} {}".format(
                TYPE_EMOJI.get(event["type"], ""), event.get("title", "")
            )[:200],
            "Priority": str(cfg.get("priority", 3)),
        }
        tags = list(cfg.get("tags") or []) + NTFY_TAGS.get(event["type"], [])
        if tags:
            headers["Tags"] = ",".join(tags)
        if cfg.get("token"):
            headers["Authorization"] = "Bearer {}".format(cfg["token"])

        body = "{}\n{}".format(_format_event_text(event, fmt), footer)
        ok, detail = _post_with_retry(
            lambda: requests.post(
                url, data=body.encode("utf-8"), headers=headers, timeout=30
            )
        )
        if not ok:
            return False, detail
    return True, ""


def _send_ikabot(session, events, fmt, footer):
    text = "\n\n".join(_format_event_text(e, fmt) for e in events)
    try:
        sendToBot(session, text)
        return True, ""
    except Exception as exc:
        return False, str(exc)[:200]


def _deliver(session, dest, events, fmt, footer):
    kind = dest.get("kind")
    if kind == "discord":
        return _send_discord(dest, events, fmt, footer)
    if kind == "telegram":
        return _send_telegram(dest, events, fmt, footer)
    if kind == "ntfy":
        return _send_ntfy(dest, events, fmt, footer)
    if kind == "ikabot":
        return _send_ikabot(session, events, fmt, footer)
    return False, "unknown destination kind {}".format(kind)


def _chunk(text, size):
    if not text:
        return []
    return [text[i : i + size] for i in range(0, len(text), size)]


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _footer(session):
    return "Server:{}, World:{}, Player:{}".format(
        getattr(session, "servidor", "?"),
        getattr(session, "word", getattr(session, "mundo", "?")),
        getattr(session, "username", "?"),
    )


def _event_body(event, fmt):
    if not fmt.get("include_body", True):
        return ""
    body = str(event.get("body", "")).strip()
    if not body:
        return ""
    limit = int(fmt.get("body_max_chars", DEFAULT_BODY_MAX))
    if limit > 0 and len(body) > limit:
        body = body[:limit].rstrip() + " …"
    return body


def _format_event_text(event, fmt):
    lines = [
        "{} [{}] {}".format(
            TYPE_EMOJI.get(event["type"], ""),
            TYPE_LABELS.get(event["type"], event["type"]),
            event.get("title", ""),
        )
    ]
    if event.get("sender"):
        lines.append("From: {}".format(event["sender"]))
    if event.get("city"):
        lines.append("City: {}".format(event["city"]))
    if event.get("date"):
        lines.append("Date: {}".format(event["date"]))
    body = _event_body(event, fmt)
    if body:
        lines.append("")
        lines.append(body)
    return "\n".join(lines)


def _in_quiet_hours(fmt, event_type, now=None):
    quiet = fmt.get("quiet_hours", {})
    if not quiet.get("enabled"):
        return False
    types = quiet.get("types") or []
    if types and event_type not in types:
        return False
    try:
        start = _minutes_of_day(quiet.get("from", "23:00"))
        end = _minutes_of_day(quiet.get("to", "07:00"))
    except ValueError:
        return False
    local = time.localtime(now if now is not None else time.time())
    current = local.tm_hour * 60 + local.tm_min
    if start == end:
        return False
    if start < end:
        return start <= current < end
    return current >= start or current < end


def _minutes_of_day(value):
    parts = str(value).split(":")
    if len(parts) != 2:
        raise ValueError(value)
    return int(parts[0]) % 24 * 60 + int(parts[1]) % 60


def _is_muted(fmt, event):
    haystack = "{} {} {}".format(
        event.get("title", ""), event.get("sender", ""), event.get("body", "")
    ).lower()
    for pattern in fmt.get("mutes") or []:
        if str(pattern).strip() and str(pattern).strip().lower() in haystack:
            return True
    return False


# ---------------------------------------------------------------------------
# Scraping — adapted from ikabot/function/alertMessages.py so the hub works on
# vanilla ikabot, which does not ship that module.
# ---------------------------------------------------------------------------


def _clean_text(value):
    text = re.sub(r"<[^>]+>", " ", str(value))
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _decode_unicode_escapes(value):
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), value)


def _payload_variants(payload):
    text = str(payload)
    if not text:
        return []

    candidates = []
    for value in (text, html.unescape(text)):
        candidates.append(value)
        candidates.append(value.replace(r"\/", "/"))
        candidates.append(_decode_unicode_escapes(value))
        candidates.append(_decode_unicode_escapes(value).replace(r"\/", "/"))
        stripped = value.strip()
        if len(stripped) >= 2 and stripped[0] == '"' and stripped[-1] == '"':
            try:
                decoded = json.loads(stripped)
                if isinstance(decoded, str) and decoded:
                    candidates.append(decoded)
                    candidates.append(html.unescape(decoded))
            except Exception:
                pass

    variants = []
    seen = set()
    for value in candidates:
        candidate = str(value)
        if candidate and candidate not in seen:
            seen.add(candidate)
            variants.append(candidate)
    return variants


def _flatten_json_payload(raw):
    text = str(raw).strip()
    if not text or text[0] not in "[{":
        return []
    try:
        parsed = json.loads(text, strict=False)
    except Exception:
        return []

    fragments = []

    def walk(node):
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, str) and node.strip():
            fragments.append(node)

    walk(parsed)
    return fragments


def _current_city_id(session):
    try:
        home = session.get("view=city")
    except Exception:
        return None
    match = re.search(r"currentCityId:\s*(\d+),", str(home))
    return match.group(1) if match else None


def _fetch_message_payloads(session):
    payloads = []
    city_id = _current_city_id(session)

    urls = [
        "view=mail",
        "view=messages",
        "view=advisor&activeTab=tabMessages",
        "view=diplomacyAdvisor",
        "view=diplomacyAdvisor&activeTab=tab_diplomacyAdvisor",
    ]
    if city_id is not None:
        urls.append(
            "view=advisor&activeTab=tabMessages&backgroundView=city&currentCityId={}".format(
                city_id
            )
        )
        urls.append(
            "view=diplomacyAdvisor&activeTab=tab_diplomacyAdvisor&backgroundView=city"
            "&currentCityId={}".format(city_id)
        )

    for url in urls:
        try:
            data = session.get(url)
            if data:
                payloads.append(str(data))
        except Exception:
            continue

    if city_id is not None:
        ajax_urls = [
            (
                "view=advisor&oldView=city&oldBackgroundView=city&backgroundView=city"
                "&currentCityId={}&templateView=advisor&actionRequest={}"
                "&activeTab=tabMessages&ajax=1"
            ).format(city_id, config.actionRequest),
            (
                "view=diplomacyAdvisor&oldView=city&oldBackgroundView=city&backgroundView=city"
                "&currentCityId={}&templateView=diplomacyAdvisor&actionRequest={}"
                "&activeTab=tab_diplomacyAdvisor&ajax=1"
            ).format(city_id, config.actionRequest),
        ]
        for url in ajax_urls:
            raw = None
            try:
                raw = session.post(url)
            except Exception:
                try:
                    raw = session.get(url)
                except Exception:
                    raw = None
            if raw:
                payloads.append(str(raw))
                payloads.extend(_flatten_json_payload(raw))

    unique = []
    seen = set()
    for payload in payloads:
        text = str(payload)
        if text and text not in seen:
            seen.add(text)
            unique.append(text)
    return unique


def _canonical_message_id(raw_id):
    value = str(raw_id).strip().lower()
    if not value:
        return ""
    match = re.match(r"^gmessage(\d+)$", value)
    if match:
        return "g:{}".format(match.group(1))
    match = re.match(r"^message(\d+)$", value)
    if match:
        return "m:{}".format(match.group(1))
    if value.isdigit():
        return "m:{}".format(value)
    return value


def _extract_sender_from_row(row_html):
    avatar = re.search(
        r'<span[^>]*class=["\'][^"\']*avatarName[^"\']*["\'][^>]*>([\s\S]*?)</span>',
        row_html,
        flags=re.IGNORECASE,
    )
    if avatar:
        sender = _clean_text(avatar.group(1))
        if sender:
            return sender
    cells = re.findall(r"<td[^>]*>([\s\S]*?)</td>", row_html, flags=re.IGNORECASE)
    if len(cells) >= 3:
        sender = _clean_text(cells[2])
        if sender:
            return sender
    return ""


def _extract_subject_from_row(row_html):
    match = re.search(
        r'<td[^>]*class=["\'][^"\']*subject[^"\']*["\'][^>]*>([\s\S]*?)</td>',
        row_html,
        flags=re.IGNORECASE,
    )
    if match:
        subject = _clean_text(match.group(1))
        if subject:
            return subject
    cells = re.findall(r"<td[^>]*>([\s\S]*?)</td>", row_html, flags=re.IGNORECASE)
    if len(cells) >= 4:
        subject = _clean_text(cells[3])
        if subject:
            return subject
    return "No subject"


def _extract_town_and_date_from_row(row_html):
    cells = re.findall(r"<td[^>]*>([\s\S]*?)</td>", row_html, flags=re.IGNORECASE)
    if len(cells) >= 6:
        return _clean_text(cells[4]), _clean_text(cells[5])
    return "", ""


def _extract_row_icon(row_html):
    """The CSS class Ikariam puts on a system message row identifies its kind."""
    match = re.search(
        r'class=["\']([^"\']*(?:icon|messageType|msgtype)[^"\']*)["\']',
        row_html,
        flags=re.IGNORECASE,
    )
    return match.group(1).strip().lower() if match else ""


def _parse_message_bodies_by_suffix(payload):
    bodies = {}
    pattern = re.compile(
        r'<tr[^>]*id\s*=\s*["\']\s*tbl_(?:g?mail)(\d+)\s*["\'][^>]*>[\s\S]*?'
        r'<td[^>]*class=["\'][^"\']*msgText[^"\']*["\'][^>]*>([\s\S]*?)</td>[\s\S]*?</tr>',
        flags=re.IGNORECASE,
    )
    for match in pattern.finditer(payload):
        bodies[match.group(1)] = _clean_text(match.group(2))
    return bodies


def _parse_message_actions_by_suffix(payload):
    """Text of the hidden tbl_reply row.

    Diplomacy offers carry an empty msgText — everything that matters (the offer
    wording and the accept/decline links) lives here instead, so a forwarded
    treaty would otherwise be a bare subject line.
    """
    actions = {}
    start = re.compile(
        r'<tr[^>]*id\s*=\s*["\']\s*tbl_reply(\d+)\s*["\'][^>]*>', flags=re.IGNORECASE
    )
    # Scan to the next row that carries an id rather than to a closing tag: the
    # reply cell contains nested tables, so </tr> and </table> both appear
    # inside it and would cut the content short.
    next_row = re.compile(r"<tr[^>]*\sid\s*=", flags=re.IGNORECASE)

    for match in start.finditer(payload):
        begin = match.end()
        following = next_row.search(payload, begin)
        end = following.start() if following else min(len(payload), begin + 4000)
        text = _clean_text(payload[begin:end])
        if text:
            actions[match.group(1)] = text[:400]
    return actions


def _parse_messages_from_payload(payload):
    messages = {}
    bodies = _parse_message_bodies_by_suffix(payload)
    actions = _parse_message_actions_by_suffix(payload)
    pattern = re.compile(
        r'<tr[^>]*id\s*=\s*["\']\s*(g?message\d+)\s*["\']([^>]*)>([\s\S]*?)</tr>',
        flags=re.IGNORECASE,
    )

    for match in pattern.finditer(payload):
        row_id = match.group(1).strip()
        row_attrs = match.group(2)
        row_html = match.group(3)
        suffix_match = re.search(r"(\d+)$", row_id)
        suffix = suffix_match.group(1) if suffix_match else None

        action = actions.get(suffix, "") if suffix else ""
        body = bodies.get(suffix, "") if suffix else ""
        message = {
            "id": _canonical_message_id(row_id),
            "source": "game" if row_id.lower().startswith("gmessage") else "player",
            "sender": _extract_sender_from_row(row_html),
            "subject": _extract_subject_from_row(row_html),
            "body": body or action,
            "action": action,
            "city": "",
            "date": "",
            "icon": _extract_row_icon(row_attrs + row_html),
            "unread": "new" in row_attrs.lower(),
        }
        message["city"], message["date"] = _extract_town_and_date_from_row(row_html)
        if not message["id"]:
            continue
        if message["subject"] in ("", "No subject") and not message["body"]:
            continue
        messages[message["id"]] = message

    return list(messages.values())


def _extract_combat_id_from_row(row_html):
    for pattern in (
        r'name=["\']combatId\[(\d+)\]["\']',
        r"combatId=(\d+)",
        r"detailedCombatId=(\d+)",
        r"combatId%3D(\d+)",
    ):
        match = re.search(pattern, row_html, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _fetch_combat_reports(session):
    city_id = _current_city_id(session)
    urls = [
        "view=militaryAdvisorCombatList",
        "view=militaryAdvisorCombatList&activeTab=tab_militaryAdvisorCombatList",
    ]
    if city_id is not None:
        urls.append(
            "view=militaryAdvisorCombatList&activeTab=tab_militaryAdvisorCombatList"
            "&backgroundView=city&currentCityId={}".format(city_id)
        )

    reports = {}
    for url in urls:
        try:
            payload = str(session.get(url))
        except Exception:
            continue
        for report in _parse_combat_reports(payload):
            reports[report["id"]] = report
    return list(reports.values())


def _parse_combat_reports(payload):
    reports = []
    row_pattern = re.compile(
        r'<tr[^>]*class=["\']([^"\']*)["\'][^>]*>([\s\S]*?)</tr>', flags=re.IGNORECASE
    )

    for match in row_pattern.finditer(payload):
        row_class = match.group(1).lower()
        row_html = match.group(2)
        combat_id = _extract_combat_id_from_row(row_html)
        if combat_id is None:
            continue

        title_match = re.search(
            r'<img[^>]*title=["\']([^"\']+)["\']', row_html, flags=re.IGNORECASE
        )
        battle_type = _clean_text(title_match.group(1)) if title_match else "Combat"

        date_match = re.search(
            r'<td[^>]*class=["\'][^"\']*date[^"\']*["\'][^>]*>([\s\S]*?)</td>',
            row_html,
            flags=re.IGNORECASE,
        )
        date = _clean_text(date_match.group(1)) if date_match else ""

        right_cells = re.findall(
            r'<td[^>]*class=["\'][^"\']*right[^"\']*["\'][^>]*>([\s\S]*?)</td>',
            row_html,
            flags=re.IGNORECASE,
        )
        rounds = _clean_text(right_cells[0]) if right_cells else ""

        left_cells = re.findall(
            r'<td[^>]*class=["\'][^"\']*left[^"\']*["\'][^>]*>([\s\S]*?)</td>',
            row_html,
            flags=re.IGNORECASE,
        )
        town = _clean_text(left_cells[1]) if len(left_cells) >= 2 else ""
        owner = _clean_text(left_cells[2]) if len(left_cells) >= 3 else ""

        low = row_html.lower()
        if "running" in low and "bold" in low:
            outcome = "Ongoing"
        elif "green" in row_class:
            outcome = "Win"
        elif "red" in row_class:
            outcome = "Loss"
        else:
            outcome = "Unknown"

        body = ["Outcome: {}".format(outcome)]
        if rounds:
            body.append("Rounds: {}".format(rounds))
        if owner:
            body.append("Owner: {}".format(owner))

        reports.append(
            {
                # The id carries round and date so an ongoing battle re-notifies.
                "id": "c:{}:{}:{}".format(
                    combat_id,
                    re.sub(r"\s+", "", rounds.lower()) or "na",
                    re.sub(r"\s+", "", date.lower()) or "na",
                ),
                "source": "combat",
                "sender": owner or "Combat report",
                "subject": "{} | {}".format(battle_type, town) if town else battle_type,
                "body": ", ".join(body),
                "city": town,
                "date": date,
                "icon": "",
                "combat_id": combat_id,
            }
        )

    return reports


def _extract_export_text(payload):
    text = str(payload)
    bounded = re.search(
        r'"exportText"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,\s*"exportPreview"',
        text,
        flags=re.IGNORECASE,
    )
    match = bounded or re.search(
        r'"exportText"\s*:\s*"((?:[^"\\]|\\.)*)"', text, flags=re.IGNORECASE
    )
    if match is None:
        return ""
    try:
        return json.loads('"{}"'.format(match.group(1)))
    except Exception:
        return ""


def _clean_export_excerpt(export_text, max_lines=14):
    ignore_prefixes = ("battle for", "military", "damage percent")
    noise = ("town relocation", "triton engines", "great deals", "ajax.responder")

    cleaned = []
    for raw in str(export_text).splitlines():
        line = _clean_text(raw)
        if not line or set(line) == {"-"}:
            continue
        low = line.lower()
        if any(marker in low for marker in noise):
            continue
        if any(low.startswith(prefix) for prefix in ignore_prefixes):
            continue
        cleaned.append(line)
        if len(cleaned) >= max_lines:
            break
    return "\n".join(cleaned)


def _enrich_combat_reports(session, events):
    """Append the battle export excerpt when the full-report toggle is on."""
    for event in events:
        combat_id = event.get("combat_id")
        if not combat_id:
            continue
        excerpt = ""
        for combat_round in (0, 1):
            try:
                raw = session.get(
                    "view=militaryReportExport&combatId={}&combatRound={}".format(
                        combat_id, combat_round
                    )
                )
            except Exception:
                continue
            for candidate in _payload_variants(raw):
                export_text = _extract_export_text(candidate)
                if export_text:
                    excerpt = _clean_export_excerpt(export_text)
                    break
            if excerpt:
                break
        if excerpt:
            event["body"] = "{}\n\n{}".format(event.get("body", ""), excerpt).strip()


def _fetch_messages(session):
    """Return every message the account can see, best quality version of each."""

    def quality(message):
        score = 0
        if str(message.get("sender", "")).strip():
            score += 1
        if str(message.get("subject", "")).strip() not in ("", "No subject"):
            score += 1
        if str(message.get("body", "")).strip():
            score += 2
        return score

    merged = {}
    for payload in _fetch_message_payloads(session):
        for candidate in _payload_variants(payload):
            for message in _parse_messages_from_payload(candidate):
                previous = merged.get(message["id"])
                if previous is None or quality(message) > quality(previous):
                    merged[message["id"]] = message

    for report in _fetch_combat_reports(session):
        merged[report["id"]] = report

    return list(merged.values())


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _session_languages(session):
    langs = []
    for attr in ("gf_lang", "locale"):
        value = str(getattr(session, attr, "") or "").strip().lower()
        if len(value) >= 2 and value[:2] not in langs:
            langs.append(value[:2])
    if "en" not in langs:
        langs.append("en")
    return langs


def _own_city_names(session):
    try:
        from ikabot.helpers.pedirInfo import getIdsOfCities

        _, cities = getIdsOfCities(session)
        return {
            str(city.get("name", "")).strip().lower()
            for city in cities.values()
            if str(city.get("name", "")).strip()
        }
    except Exception:
        logger.debug("Could not read own city names", exc_info=True)
        return set()


def _apply_overrides(overrides, haystack):
    for rule in overrides or []:
        if not isinstance(rule, dict):
            continue
        value = str(rule.get("value", "")).strip().lower()
        event_type = rule.get("type")
        if not value or event_type not in EVENT_TYPES:
            continue
        if value in haystack:
            return event_type
    return None


def _classify(message, session_langs, overrides, own_cities):
    if message.get("source") == "combat":
        return "combat"

    from_player = message.get("source") == "player"
    haystack = "{} {} {} {}".format(
        message.get("subject", ""),
        message.get("body", ""),
        message.get("action", ""),
        message.get("icon", ""),
    ).lower()

    override = _apply_overrides(overrides, haystack)
    if override:
        return override

    # A row sent by a player can only be a few things. Running the full keyword
    # table over player mail misfiles it — a player advertising a "trading
    # community" is not a shipment.
    order = PLAYER_CLASSIFY_ORDER if from_player else CLASSIFY_ORDER
    fallback = "player_message" if from_player else "other"

    icon = str(message.get("icon", "")).lower()
    for event_type in order:
        if event_type.split("_")[0] in icon:
            return event_type

    for lang in session_langs:
        table = KEYWORDS.get(lang, {})
        for event_type in order:
            for keyword in table.get(event_type, []):
                if keyword in haystack:
                    if event_type in ("shipment_internal", "shipment_external"):
                        return _classify_shipment(haystack, own_cities, event_type)
                    return event_type

    return fallback


def _classify_shipment(haystack, own_cities, fallback):
    """A shipment naming one of your own cities came from inside the empire."""
    for name in own_cities:
        if name and name in haystack:
            return "shipment_internal"
    return fallback


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def _to_event(message, event_type):
    return {
        "id": message["id"],
        "type": event_type,
        "title": message.get("subject", "") or TYPE_LABELS.get(event_type, event_type),
        "sender": message.get("sender", ""),
        "body": message.get("body", ""),
        "city": message.get("city", ""),
        "date": message.get("date", ""),
        "combat_id": message.get("combat_id"),
    }


def _destinations_for(cfg, event_type, override=None):
    """Resolve destination ids for a type. A rule's own list wins over the route."""
    if not cfg["type_enabled"].get(event_type, True):
        return []
    wanted = override if override else (cfg["routes"].get(event_type) or [])
    by_id = {d.get("id"): d for d in cfg["destinations"] if d.get("enabled", True)}
    return [by_id[i] for i in wanted if i in by_id]


def _send_events(session, cfg, events, state):
    """Group events by destination and deliver. Returns (sent, failed)."""
    fmt = cfg["formatting"]
    footer = _footer(session)
    by_dest = {}

    for event in events:
        if _is_muted(fmt, event) or _in_quiet_hours(fmt, event["type"]):
            continue
        for dest in _destinations_for(cfg, event["type"], event.get("destinations")):
            by_dest.setdefault(dest["id"], (dest, []))[1].append(event)

    sent = 0
    failed = 0
    for dest, dest_events in by_dest.values():
        ok, detail = _deliver(session, dest, dest_events, fmt, footer)
        if ok:
            sent += len(dest_events)
        else:
            failed += len(dest_events)
            state["last_error"] = "{}: {}".format(dest.get("name", dest.get("id")), detail)
            logger.warning("Delivery to %s failed: %s", dest.get("name"), detail)

    state["delivered"] = int(state.get("delivered", 0)) + sent
    state["failed"] = int(state.get("failed", 0)) + failed
    return sent, failed


def _notify_status(session, cfg, text):
    """Send hub-status text through the hub's own routing, else via ikabot."""
    event = {
        "id": "status:{}".format(int(time.time())),
        "type": "hub_status",
        "title": "Messaging Hub",
        "sender": "",
        "body": text,
        "city": "",
        "date": "",
    }
    destinations = _destinations_for(cfg, "hub_status")
    if destinations:
        footer = _footer(session)
        for dest in destinations:
            _deliver(session, dest, [event], cfg["formatting"], footer)
        return
    try:
        if notificationDataIsValid(session):
            sendToBot(session, "Messaging Hub:\n{}".format(text))
    except Exception:
        logger.error("Could not send hub status", exc_info=True)


def _poll_messages(session, cfg, state, own_cities, session_langs):
    messages = _fetch_messages(session)
    seen = state["seen_ids"]
    now = time.time()

    first_run = not state.get("first_run_done")
    notify_existing = cfg["watchers"]["messages"].get("notify_existing", False)

    new_messages = [m for m in messages if m["id"] not in seen]

    if first_run and not notify_existing:
        for message in messages:
            seen[message["id"]] = now
        state["first_run_done"] = True
        _prune_seen(state, cfg.get("seen_retention_days", 14))
        _save_state(session, state)
        return 0, 0, len(messages)

    events = []
    for message in new_messages:
        event_type = _classify(
            message, session_langs, cfg.get("classification_overrides"), own_cities
        )
        events.append(_to_event(message, event_type))

    if cfg["formatting"].get("combat_full_report"):
        _enrich_combat_reports(
            session, [e for e in events if e["type"] == "combat" and e.get("combat_id")]
        )

    sent, failed = _send_events(session, cfg, events, state)

    # Mark as seen only after delivery was attempted — a crash mid-poll re-sends
    # rather than silently dropping a report.
    for message in new_messages:
        seen[message["id"]] = now
    state["first_run_done"] = True
    _prune_seen(state, cfg.get("seen_retention_days", 14))
    _save_state(session, state)

    return sent, failed, len(new_messages)


# ---------------------------------------------------------------------------
# Resource monitor
# ---------------------------------------------------------------------------


def _warehouse_capacity(page_html):
    match = re.search(
        r"maxResources:\s*JSON\.parse\('{\\\"resource\\\":(\d+),", str(page_html)
    )
    return int(match.group(1)) if match else 0


def _wine_consumption_per_hour(page_html):
    match = re.search(r"wineSpendings:\s(\d+)", str(page_html))
    return int(match.group(1)) if match else 0


def _wine_production_per_hour(page_html):
    """Wine produced here, or 0 — only wine islands have the counter."""
    match = re.search(
        r'<td id="js_GlobalMenu_production_wine"[^>]*>\s*([\d,.\s]+)\s*</td>',
        str(page_html),
    )
    if not match:
        return 0
    digits = re.sub(r"[^\d]", "", match.group(1))
    return int(digits) if digits else 0


def _rule_reading(rule, city, page_html):
    """Return (value, unit) for this rule against this city, or (None, '')."""
    index = RESOURCES.index(rule["resource"])
    try:
        amount = float(city.get("availableResources", [])[index])
    except (IndexError, TypeError, ValueError):
        return None, ""

    mode = rule.get("mode", "absolute")
    if mode == "absolute":
        return amount, ""

    if mode == "percent":
        capacity = _warehouse_capacity(page_html)
        if capacity <= 0:
            return None, ""
        return amount / capacity * 100.0, "%"

    # hours_left — wine only, and only meaningful while it is actually draining
    if rule["resource"] != "wine":
        return None, ""
    net_drain = _wine_consumption_per_hour(page_html) - _wine_production_per_hour(
        page_html
    )
    if net_drain <= 0:
        return None, ""
    return amount / net_drain, "h"


def _format_reading(value, unit):
    if unit == "%":
        return "{:.0f}%".format(value)
    if unit == "h":
        return daysHoursMinutes(int(value * 3600))
    return addThousandSeparator(int(value))


def _is_breach(rule, value):
    if rule["direction"] == "below":
        return value < rule["threshold"]
    return value > rule["threshold"]


def _has_recovered(rule, value):
    """Recovery needs a margin beyond the threshold, so a value sitting on the
    line cannot flip the rule back and forth every poll."""
    margin = float(rule.get("rearm_margin_percent", DEFAULT_REARM_MARGIN)) / 100.0
    threshold = float(rule["threshold"])
    slack = abs(threshold) * margin
    if slack == 0:
        slack = margin
    if rule["direction"] == "below":
        return value >= threshold + slack
    return value <= threshold - slack


def _rule_label(cfg, rule, city_name=None):
    where = city_name or (
        rule.get("city_name", "?") if rule.get("scope") == "city" else "every city"
    )
    threshold = rule["threshold"]
    if rule.get("mode") == "percent":
        limit = "{:.0f}%".format(threshold)
    elif rule.get("mode") == "hours_left":
        limit = "{:.0f}h".format(threshold)
    else:
        limit = addThousandSeparator(int(threshold))
    return "{} in {} {} {}".format(
        RESOURCE_LABELS.get(rule["resource"], rule["resource"]),
        where,
        rule["direction"],
        limit,
    )


def _resource_event(cfg, rule, city_name, value, unit, recovered):
    reading = _format_reading(value, unit)
    if rule.get("mode") == "percent":
        limit = "{:.0f}%".format(rule["threshold"])
    elif rule.get("mode") == "hours_left":
        limit = "{:.0f}h".format(rule["threshold"])
    else:
        limit = addThousandSeparator(int(rule["threshold"]))

    resource = RESOURCE_LABELS.get(rule["resource"], rule["resource"])
    if recovered:
        title = "{} recovered in {}".format(resource, city_name)
        body = "{} is back to {} (rule: {} {}).".format(
            resource, reading, rule["direction"], limit
        )
    else:
        title = "{} {} {} in {}".format(resource, rule["direction"], limit, city_name)
        body = "{} in {} is {} — the rule is {} {}.".format(
            resource, city_name, reading, rule["direction"], limit
        )

    return {
        "id": "res:{}:{}:{}".format(rule.get("id"), city_name, int(time.time())),
        "type": "resource_alert",
        "title": title,
        "sender": "",
        "body": body,
        "city": city_name,
        "date": getDateTime(),
        "destinations": list(rule.get("destinations") or []),
    }


def _check_resources(session, cfg, state):
    """Evaluate every enabled rule against every city. Returns events to send."""
    from ikabot.config import city_url
    from ikabot.helpers.getJson import getCity
    from ikabot.helpers.pedirInfo import getIdsOfCities

    rules = [r for r in cfg.get("resource_rules", []) if r.get("enabled", True)]
    if not rules:
        return [], 0

    city_ids, _ = getIdsOfCities(session)
    breaches = state.setdefault("resource_breaches", {})
    events = []
    checked = 0
    now = time.time()
    live_keys = set()

    for city_id in city_ids:
        applicable = [
            r
            for r in rules
            if r.get("scope") == "global" or str(r.get("city_id")) == str(city_id)
        ]
        if not applicable:
            continue

        try:
            page_html = session.get(city_url + str(city_id))
            city = getCity(page_html)
        except Exception:
            logger.warning("Could not read city %s for resource rules", city_id, exc_info=True)
            continue

        checked += 1
        city_name = city.get("name") or city.get("cityName") or str(city_id)

        for rule in applicable:
            value, unit = _rule_reading(rule, city, page_html)
            if value is None:
                continue

            key = "{}:{}".format(rule.get("id"), city_id)
            live_keys.add(key)
            record = breaches.get(key) or {"breached": False, "fired_at": 0}
            cooldown = float(rule.get("cooldown_minutes", DEFAULT_COOLDOWN_MINUTES)) * 60

            if _is_breach(rule, value):
                if not record["breached"]:
                    record["breached"] = True
                    record["fired_at"] = now
                    events.append(
                        _resource_event(cfg, rule, city_name, value, unit, False)
                    )
            elif record["breached"] and _has_recovered(rule, value):
                # Only re-arm once it is clear of the threshold by the margin
                # AND the cooldown has passed, so a value hovering on the line
                # cannot alert every single poll.
                if now - float(record.get("fired_at", 0)) >= cooldown:
                    record["breached"] = False
                    if rule.get("notify_on_recovery"):
                        events.append(
                            _resource_event(cfg, rule, city_name, value, unit, True)
                        )
            breaches[key] = record

    # Forget rules and cities that no longer exist so the file cannot grow
    # forever as cities are sold and rules deleted.
    for key in list(breaches):
        if key not in live_keys:
            del breaches[key]

    return events, checked


def _poll_resources(session, cfg, state):
    events, checked = _check_resources(session, cfg, state)
    sent, failed = _send_events(session, cfg, events, state)
    _save_state(session, state)
    return sent, failed, checked


def _do_it(session):
    state = _load_state(session)
    cfg = _effective_config(session)
    session_langs = _session_languages(session)
    own_cities = _own_city_names(session)

    _notify_status(
        session,
        cfg,
        "Hub started.\n{}".format(_config_summary(session, cfg)),
    )

    next_city_refresh = time.time() + 6 * 3600
    next_messages = 0.0
    next_resources = 0.0
    summary = "starting"

    while True:
        # Re-read both every pass so menu edits — including a counter or
        # seen-id reset done while the hub runs — take effect without a
        # restart, and without needing to lock the state file.
        cfg = _effective_config(session)
        state = _load_state(session)
        watchers = cfg["watchers"]
        now = time.time()

        messages_on = watchers["messages"].get("enabled", True)
        resources_on = watchers.get("resources", {}).get("enabled", False) and cfg.get(
            "resource_rules"
        )

        if messages_on and now >= next_messages:
            interval = max(
                MIN_MESSAGE_INTERVAL,
                int(watchers["messages"].get("interval_minutes", DEFAULT_MESSAGE_INTERVAL)),
            )
            try:
                sent, failed, scanned = _poll_messages(
                    session, cfg, state, own_cities, session_langs
                )
                summary = "{} new, {} forwarded, {} failed".format(scanned, sent, failed)
                if failed:
                    _report_failures(session, cfg, state)
            except Exception:
                logger.error("Message poll failed", exc_info=True)
                state["last_error"] = traceback.format_exc()[-400:]
                _save_state(session, state)
                _report_failures(session, cfg, state)
                summary = "message poll failed"
            next_messages = time.time() + interval * 60

        if resources_on and now >= next_resources:
            interval = max(
                MIN_RESOURCE_INTERVAL,
                int(
                    watchers["resources"].get(
                        "interval_minutes", DEFAULT_RESOURCE_INTERVAL
                    )
                ),
            )
            try:
                sent, failed, checked = _poll_resources(session, cfg, state)
                if sent or failed:
                    summary += " | resources: {} sent, {} failed".format(sent, failed)
                if failed:
                    _report_failures(session, cfg, state)
            except Exception:
                logger.error("Resource check failed", exc_info=True)
                state["last_error"] = traceback.format_exc()[-400:]
                _save_state(session, state)
                _report_failures(session, cfg, state)
            next_resources = time.time() + interval * 60

        if not messages_on and not resources_on:
            summary = "idle — no watcher enabled"

        session.setStatus(
            "Hub: {} (total {}/{})".format(
                summary, state.get("delivered", 0), state.get("failed", 0)
            )
        )

        if time.time() >= next_city_refresh:
            own_cities = _own_city_names(session)
            next_city_refresh = time.time() + 6 * 3600

        # Sleep until whichever watcher is due first.
        due = [t for t, on in ((next_messages, messages_on), (next_resources, resources_on)) if on]
        seconds = min(due) - time.time() if due else 300
        wait(max(30, int(seconds)), maxrandom=30)


def _report_failures(session, cfg, state):
    """Rate-limited — a dead webhook must not become a spam source itself."""
    now = time.time()
    if now - float(state.get("last_failure_report", 0)) < FAILURE_REPORT_COOLDOWN:
        return
    state["last_failure_report"] = now
    _save_state(session, state)
    _notify_status(
        session,
        cfg,
        "Delivery problems.\nFailed so far: {}\nLast error: {}".format(
            state.get("failed", 0), state.get("last_error", "")
        ),
    )


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------


class _BackToMenu(Exception):
    """Raised when ' is typed at any prompt — unwinds to the hub's main menu."""


class _LeaveHub(Exception):
    """(0) Back from the hub's main menu — leaves the module."""


def _read(**kwargs):
    """read() plus a universal ' shortcut back to the hub's main menu.

    additionalValues is checked before any digit or range validation, so the
    shortcut works even on menus that only accept numbers.
    """
    additional = list(kwargs.pop("additionalValues", None) or [])
    if "'" not in additional:
        additional.append("'")
    value = read(additionalValues=additional, **kwargs)
    if isinstance(value, str) and value.strip() == "'":
        raise _BackToMenu()
    return value


def _header(title, hint=True):
    banner()
    width = 58
    print("{}╔{}╗{}".format(bcolors.BLUE, "═" * width, bcolors.ENDC))
    print("{}║{}║{}".format(bcolors.BLUE, title.center(width), bcolors.ENDC))
    print("{}╚{}╝{}".format(bcolors.BLUE, "═" * width, bcolors.ENDC))
    if hint:
        print("{}( ' at any prompt returns to the hub menu ){}".format(
            bcolors.BLACK, bcolors.ENDC
        ))
    print("")


def _on_off(value):
    return "{}[ON] {}".format(bcolors.GREEN, bcolors.ENDC) if value else "[--] "


def _dest_label(dest):
    return "{} ({})".format(dest.get("name", "?"), dest.get("kind", "?"))


def _dest_names(cfg, ids):
    by_id = {d.get("id"): d for d in cfg["destinations"]}
    names = [by_id[i]["name"] for i in ids if i in by_id]
    missing = len([i for i in ids if i not in by_id])
    if missing:
        names.append("{}{} unknown{}".format(bcolors.RED, missing, bcolors.ENDC))
    return ", ".join(names) if names else "{}none{}".format(bcolors.WARNING, bcolors.ENDC)


def _config_summary(session, cfg):
    routed = sum(
        1
        for t in EVENT_TYPES
        if cfg["routes"].get(t) and cfg["type_enabled"].get(t, True)
    )
    watchers = cfg["watchers"]
    rules = cfg.get("resource_rules", [])
    active_rules = len([r for r in rules if r.get("enabled", True)])
    return (
        "Destinations: {}\n".format(len(cfg["destinations"]))
        + "Routed types: {}/{}\n".format(routed, len(EVENT_TYPES))
        + "Messages: {}\n".format(
            "every {} min".format(
                watchers["messages"].get("interval_minutes", DEFAULT_MESSAGE_INTERVAL)
            )
            if watchers["messages"].get("enabled", True)
            else "off"
        )
        + "Resources: {}\n".format(
            "{} rule(s), every {} min".format(
                active_rules,
                watchers.get("resources", {}).get(
                    "interval_minutes", DEFAULT_RESOURCE_INTERVAL
                ),
            )
            if watchers.get("resources", {}).get("enabled", False)
            else "off"
        )
        + "Storage: {}".format(_hub_dir(session))
    )


def _config_is_runnable(cfg):
    if cfg["watchers"]["messages"].get("enabled", True):
        for event_type in EVENT_TYPES:
            if event_type in ("resource_alert", "hub_status"):
                continue
            if cfg["type_enabled"].get(event_type, True) and _destinations_for(
                cfg, event_type
            ):
                return True

    if cfg["watchers"].get("resources", {}).get("enabled", False):
        for rule in cfg.get("resource_rules", []):
            if rule.get("enabled", True) and _destinations_for(
                cfg, "resource_alert", rule.get("destinations")
            ):
                return True
    return False


def _new_id(existing):
    used = {d.get("id") for d in existing}
    index = 1
    while "d{}".format(index) in used:
        index += 1
    return "d{}".format(index)


def _pick_config_to_edit(session):
    """Ask whether an edit targets this account or the shared global config."""
    account_cfg = _load_account_config(session)
    if not (
        account_cfg["use_global"].get("routing")
        or account_cfg["use_global"].get("formatting")
    ):
        return account_cfg, False

    _header("WHICH CONFIGURATION?")
    print("This account is set to use the global configuration.")
    print("(1) Edit the global configuration (affects every account using it)")
    print("(2) Edit this account's own configuration")
    if _read(min=1, max=2, digit=True, default=1) == 1:
        shared = _load_global_config(session) or _default_config()
        return shared, True
    return account_cfg, False


def _store(session, cfg, is_global):
    if is_global:
        ok, message = _save_global_config(session, cfg)
    else:
        ok = _save_account_config(session, cfg)
        message = "could not write the configuration file"
    if not ok:
        print("\n{}Not saved — {}{}".format(bcolors.RED, message, bcolors.ENDC))
        print("Your change was discarded, nothing on disk was damaged.")
        enter()
    return ok


def _menu_main(session):
    """Returns True when the user chose to start the hub."""
    while True:
        try:
            if _menu_main_once(session):
                return True
        except _BackToMenu:
            continue
        except _LeaveHub:
            return False


def _menu_main_once(session):
    while True:
        cfg = _effective_config(session)
        _header("MESSAGING HUB", hint=False)
        print(_config_summary(session, cfg))
        if not _config_is_runnable(cfg):
            print(
                "\n{}No message type is routed to a destination yet.{}".format(
                    bcolors.WARNING, bcolors.ENDC
                )
            )
        print("")
        print("(0) Back")
        print("(1) Start hub")
        print("(2) Destinations")
        print("(3) Message forwarding & routing")
        print("(4) Resource monitor")
        print("(5) Formatting & filters")
        print("(6) Diagnostics")
        print("(7) Storage & global configuration")
        print("(8) Import / export")

        choice = _read(min=0, max=8, digit=True)
        if choice == 0:
            raise _LeaveHub()
        if choice == 1:
            if not _config_is_runnable(cfg):
                print(
                    "\n{}Add a destination and route at least one message type first.{}".format(
                        bcolors.RED, bcolors.ENDC
                    )
                )
                enter()
                continue
            _offer_autostart(session)
            return True
        if choice == 2:
            _menu_destinations(session)
        elif choice == 3:
            _menu_forwarding(session)
        elif choice == 4:
            _menu_resources(session)
        elif choice == 5:
            _menu_formatting(session)
        elif choice == 6:
            _menu_diagnostics(session)
        elif choice == 7:
            _menu_storage(session)
        elif choice == 8:
            _menu_import_export(session)


def _offer_autostart(session):
    if not _HAS_MODULE_PREFS:
        return
    try:
        if len(config.predetermined_input) != 0:
            return
    except Exception:
        return
    if is_autostart(session, PREFS_NAME):
        return
    print("\nRun the hub automatically at login from now on?")
    answer = _read(
        values=["y", "Y", "n", "N", ""], empty=True, default="n", msg="[y/N]: "
    )
    if str(answer).lower() == "y":
        set_autostart(session, PREFS_NAME, True)


def _menu_destinations(session):
    while True:
        cfg, is_global = _pick_config_to_edit(session)
        _header("DESTINATIONS{}".format(" (GLOBAL)" if is_global else ""))

        if cfg["destinations"]:
            for index, dest in enumerate(cfg["destinations"], 1):
                print(
                    "  {}) {}{}".format(
                        index, _on_off(dest.get("enabled", True)), _dest_label(dest)
                    )
                )
        else:
            print("  No destinations configured yet.")
        print("")
        print("(0) Back")
        print("(1) Add a destination")
        print("(2) Rename / enable / disable")
        print("(3) Send a test message")
        print("(4) Delete a destination")

        choice = _read(min=0, max=4, digit=True)
        if choice == 0:
            return
        if choice == 1:
            _add_destination(session, cfg, is_global)
        elif choice in (2, 3, 4) and not cfg["destinations"]:
            print("\nNothing to do — add a destination first.")
            enter()
        elif choice == 2:
            _edit_destination(session, cfg, is_global)
        elif choice == 3:
            _test_destination(session, cfg)
        elif choice == 4:
            _delete_destination(session, cfg, is_global)


def _add_destination(session, cfg, is_global):
    _header("ADD DESTINATION")
    print("(0) Cancel")
    print("(1) Discord webhook")
    print("(2) Telegram bot")
    print("(3) ntfy")
    print("(4) ikabot's own notification setup")
    kind_choice = _read(min=0, max=4, digit=True)
    if kind_choice == 0:
        return

    name = str(_read(msg="Name for this destination: ")).strip()
    if not name:
        return

    dest = {
        "id": _new_id(cfg["destinations"]),
        "name": name,
        "enabled": True,
    }

    if kind_choice == 1:
        dest["kind"] = "discord"
        print("\nCreate a webhook in the Discord channel you want, then paste its URL.")
        url = str(_read(msg="Webhook URL: ")).strip()
        if not url.startswith(DISCORD_WEBHOOK_PREFIXES):
            print(
                "\n{}That does not look like a Discord webhook URL.{}".format(
                    bcolors.RED, bcolors.ENDC
                )
            )
            enter()
            return
        print("Override the webhook's display name? (blank keeps the Discord default)")
        username = str(_read(empty=True, default="")).strip()
        dest["discord"] = {
            "webhook_url": url,
            "username": username,
            "use_embeds": True,
        }
    elif kind_choice == 2:
        dest["kind"] = "telegram"
        print("\nThis is the hub's own bot — it does not have to be ikabot's bot.")
        token = str(_read(msg="Bot token: ")).strip()
        chat_id = str(_read(msg="Chat id: ")).strip()
        print("Forum topic id? (blank for none)")
        thread_id = str(_read(empty=True, default="")).strip()
        dest["telegram"] = {
            "bot_token": token,
            "chat_id": chat_id,
            "thread_id": thread_id or None,
        }
    elif kind_choice == 3:
        dest["kind"] = "ntfy"
        print("\nServer (blank for https://ntfy.sh)")
        server = str(_read(empty=True, default="")).strip() or "https://ntfy.sh"
        topic = str(_read(msg="Topic: ")).strip()
        print("Access token? (blank for a public topic)")
        token = str(_read(empty=True, default="")).strip()
        print("Priority 1-5 (default 3)")
        priority = int(_read(min=1, max=5, digit=True, default=3))
        dest["ntfy"] = {
            "server": server,
            "topic": topic,
            "token": token,
            "priority": priority,
            "tags": [],
        }
    else:
        dest["kind"] = "ikabot"

    cfg["destinations"].append(dest)
    _store(session, cfg, is_global)
    print("\n{}Destination added.{}".format(bcolors.GREEN, bcolors.ENDC))
    enter()


def _choose_destination(cfg, prompt_text):
    for index, dest in enumerate(cfg["destinations"], 1):
        print("  {}) {}".format(index, _dest_label(dest)))
    print("(0) Cancel")
    print(prompt_text)
    choice = _read(min=0, max=len(cfg["destinations"]), digit=True)
    if choice == 0:
        return None
    return cfg["destinations"][choice - 1]


def _edit_destination(session, cfg, is_global):
    _header("EDIT DESTINATION")
    dest = _choose_destination(cfg, "Which destination?")
    if dest is None:
        return

    _header("EDIT {}".format(dest.get("name", "")))
    print("(0) Back")
    print("(1) Rename")
    print("(2) {}".format("Disable" if dest.get("enabled", True) else "Enable"))
    choice = _read(min=0, max=2, digit=True)
    if choice == 1:
        name = str(_read(msg="New name: ")).strip()
        if name:
            dest["name"] = name
    elif choice == 2:
        dest["enabled"] = not dest.get("enabled", True)
    else:
        return

    _store(session, cfg, is_global)
    print("\n{}Saved.{}".format(bcolors.GREEN, bcolors.ENDC))
    enter()


def _test_destination(session, cfg):
    _header("TEST DESTINATION")
    dest = _choose_destination(cfg, "Which destination should receive a test?")
    if dest is None:
        return

    event = {
        "id": "test",
        "type": "hub_status",
        "title": "Test message",
        "sender": "",
        "body": "If you can read this, the Messaging Hub can reach this destination.",
        "city": "",
        "date": "",
    }
    print("\nSending…")
    ok, detail = _deliver(session, dest, [event], cfg["formatting"], _footer(session))
    if ok:
        print("{}Sent.{}".format(bcolors.GREEN, bcolors.ENDC))
    else:
        print("{}Failed: {}{}".format(bcolors.RED, detail, bcolors.ENDC))
    enter()


def _delete_destination(session, cfg, is_global):
    _header("DELETE DESTINATION")
    dest = _choose_destination(cfg, "Which destination should be deleted?")
    if dest is None:
        return

    routed = [t for t in EVENT_TYPES if dest["id"] in (cfg["routes"].get(t) or [])]
    if routed:
        print(
            "\n{}Still routed for: {}{}".format(
                bcolors.WARNING,
                ", ".join(TYPE_LABELS.get(t, t) for t in routed),
                bcolors.ENDC,
            )
        )
    print("Delete '{}'? [y/N]".format(dest.get("name", "")))
    if str(_read(values=["y", "Y", "n", "N", ""], empty=True, default="n")).lower() != "y":
        return

    cfg["destinations"] = [d for d in cfg["destinations"] if d["id"] != dest["id"]]
    for event_type in EVENT_TYPES:
        cfg["routes"][event_type] = [
            i for i in cfg["routes"].get(event_type, []) if i != dest["id"]
        ]
    _store(session, cfg, is_global)
    print("\n{}Deleted.{}".format(bcolors.GREEN, bcolors.ENDC))
    enter()


def _menu_forwarding(session):
    while True:
        cfg, is_global = _pick_config_to_edit(session)
        watcher = cfg["watchers"]["messages"]
        _header("MESSAGE FORWARDING{}".format(" (GLOBAL)" if is_global else ""))
        print("  Forwarding:        {}".format("enabled" if watcher.get("enabled", True) else "disabled"))
        print("  Check every:       {} minute(s)".format(watcher.get("interval_minutes", DEFAULT_MESSAGE_INTERVAL)))
        print(
            "  Existing messages: {}".format(
                "forwarded on first run" if watcher.get("notify_existing") else "marked as seen, not forwarded"
            )
        )
        print("")
        print("(0) Back")
        print("(1) {} forwarding".format("Disable" if watcher.get("enabled", True) else "Enable"))
        print("(2) Change interval")
        print("(3) Toggle first-run behaviour")
        print("(4) Per-type routing")

        choice = _read(min=0, max=4, digit=True)
        if choice == 0:
            return
        if choice == 1:
            watcher["enabled"] = not watcher.get("enabled", True)
        elif choice == 2:
            print("\nMinutes between checks (min 1, default 10)")
            watcher["interval_minutes"] = int(
                _read(min=MIN_MESSAGE_INTERVAL, digit=True, default=DEFAULT_MESSAGE_INTERVAL)
            )
        elif choice == 3:
            watcher["notify_existing"] = not watcher.get("notify_existing", False)
        elif choice == 4:
            _menu_routing(session, cfg, is_global)
            continue
        _store(session, cfg, is_global)


def _menu_routing(session, cfg, is_global):
    while True:
        _header("PER-TYPE ROUTING{}".format(" (GLOBAL)" if is_global else ""))
        for index, event_type in enumerate(EVENT_TYPES, 1):
            print(
                "  {:>2}) {}{:<26} {}".format(
                    index,
                    _on_off(cfg["type_enabled"].get(event_type, True)),
                    TYPE_LABELS.get(event_type, event_type),
                    _dest_names(cfg, cfg["routes"].get(event_type, [])),
                )
            )
        print("")
        print("(0) Back")
        print("Pick a type to change where it goes.")

        choice = _read(min=0, max=len(EVENT_TYPES), digit=True)
        if choice == 0:
            return
        _edit_route(session, cfg, is_global, EVENT_TYPES[choice - 1])


def _edit_route(session, cfg, is_global, event_type):
    _header(TYPE_LABELS.get(event_type, event_type).upper())
    print("  Currently: {}".format(_dest_names(cfg, cfg["routes"].get(event_type, []))))
    print("")
    print("(0) Back")
    print("(1) {} this type".format("Disable" if cfg["type_enabled"].get(event_type, True) else "Enable"))
    print("(2) Choose destinations")

    choice = _read(min=0, max=2, digit=True)
    if choice == 0:
        return
    if choice == 1:
        cfg["type_enabled"][event_type] = not cfg["type_enabled"].get(event_type, True)
        _store(session, cfg, is_global)
        return

    if not cfg["destinations"]:
        print("\nAdd a destination first.")
        enter()
        return

    _header("{} → WHERE?".format(TYPE_LABELS.get(event_type, event_type).upper()))
    for index, dest in enumerate(cfg["destinations"], 1):
        marker = "*" if dest["id"] in (cfg["routes"].get(event_type) or []) else " "
        print("  {} {}) {}".format(marker, index, _dest_label(dest)))
    print("")
    print("Enter the numbers to send this type to, separated by commas.")
    print("Enter 0 or leave blank to send it nowhere.")
    raw = str(_read(empty=True, default="")).strip()

    chosen = []
    for token in raw.split(","):
        token = token.strip()
        if token.isdigit() and 1 <= int(token) <= len(cfg["destinations"]):
            dest_id = cfg["destinations"][int(token) - 1]["id"]
            if dest_id not in chosen:
                chosen.append(dest_id)

    cfg["routes"][event_type] = chosen
    _store(session, cfg, is_global)
    print("\n{}Now going to: {}{}".format(bcolors.GREEN, _dest_names(cfg, chosen), bcolors.ENDC))
    enter()


def _menu_resources(session):
    while True:
        cfg, is_global = _pick_config_to_edit(session)
        watcher = cfg["watchers"].setdefault(
            "resources",
            {"enabled": False, "interval_minutes": DEFAULT_RESOURCE_INTERVAL},
        )
        rules = cfg.setdefault("resource_rules", [])

        _header("RESOURCE MONITOR{}".format(" (GLOBAL)" if is_global else ""))
        print("  Monitoring:  {}".format("on" if watcher.get("enabled") else "off"))
        print("  Check every: {} minute(s)".format(
            watcher.get("interval_minutes", DEFAULT_RESOURCE_INTERVAL)
        ))
        print("")
        if rules:
            for index, rule in enumerate(rules, 1):
                print("  {:>2}) {}{:<40} {}".format(
                    index,
                    _on_off(rule.get("enabled", True)),
                    _rule_label(cfg, rule),
                    _dest_names(cfg, rule.get("destinations") or cfg["routes"].get("resource_alert", [])),
                ))
        else:
            print("  No rules yet.")
        print("")
        print("(0) Back")
        print("(1) {} monitoring".format("Turn off" if watcher.get("enabled") else "Turn on"))
        print("(2) Change interval")
        print("(3) Add a rule")
        print("(4) Edit a rule")
        print("(5) Delete a rule")
        print("(6) Check every rule now (nothing is sent)")

        choice = _read(min=0, max=6, digit=True)
        if choice == 0:
            return
        if choice == 1:
            watcher["enabled"] = not watcher.get("enabled", False)
        elif choice == 2:
            print("\nMinutes between checks (min {}, default {})".format(
                MIN_RESOURCE_INTERVAL, DEFAULT_RESOURCE_INTERVAL
            ))
            watcher["interval_minutes"] = int(
                _read(min=MIN_RESOURCE_INTERVAL, digit=True, default=DEFAULT_RESOURCE_INTERVAL)
            )
        elif choice == 3:
            if not _add_resource_rule(session, cfg):
                continue
        elif choice == 4 and rules:
            if not _edit_resource_rule(session, cfg):
                continue
        elif choice == 5 and rules:
            print("\nWhich rule should be deleted? (0 to cancel)")
            index = int(_read(min=0, max=len(rules), digit=True))
            if index == 0:
                continue
            rules.pop(index - 1)
        elif choice == 6:
            _resource_dry_run(session, cfg)
            continue
        else:
            continue
        _store(session, cfg, is_global)


def _add_resource_rule(session, cfg):
    _header("ADD A RESOURCE RULE")
    print("(0) Cancel")
    print("(1) One city")
    print("(2) Every city  — one rule covering the whole account")
    scope_choice = _read(min=0, max=2, digit=True)
    if scope_choice == 0:
        return False

    city_id = None
    city_name = ""
    if scope_choice == 1:
        from ikabot.helpers.pedirInfo import getIdsOfCities

        try:
            city_ids, cities = getIdsOfCities(session)
        except Exception:
            print("\n{}Could not read your cities.{}".format(bcolors.RED, bcolors.ENDC))
            enter()
            return False
        listed = [(cid, cities[cid].get("name", str(cid))) for cid in city_ids]
        _header("WHICH CITY?")
        for index, (_, name) in enumerate(listed, 1):
            print("  {:>2}) {}".format(index, name))
        print("(0) Cancel")
        index = int(_read(min=0, max=len(listed), digit=True))
        if index == 0:
            return False
        city_id, city_name = listed[index - 1]

    _header("WHICH RESOURCE?")
    for index, key in enumerate(RESOURCES, 1):
        print("  {}) {}".format(index, RESOURCE_LABELS[key]))
    resource = RESOURCES[int(_read(min=1, max=len(RESOURCES), digit=True)) - 1]

    _header("WHAT SHOULD BE COMPARED?")
    print("(1) The amount in store")
    print("(2) Percent of warehouse capacity")
    if resource == "wine":
        print("(3) Hours of wine left at the current rate")
        mode_choice = _read(min=1, max=3, digit=True)
    else:
        mode_choice = _read(min=1, max=2, digit=True)
    mode = RESOURCE_MODES[int(mode_choice) - 1]

    _header("WHEN SHOULD IT ALERT?")
    print("(1) When it goes below the number")
    print("(2) When it goes above the number")
    direction = "below" if int(_read(min=1, max=2, digit=True)) == 1 else "above"

    if mode == "percent":
        print("\nPercent (1-100)")
        threshold = float(_read(min=1, max=100, digit=True))
    elif mode == "hours_left":
        print("\nHours")
        threshold = float(_read(min=1, max=1000, digit=True))
    else:
        print("\nAmount")
        threshold = float(_read(min=0, digit=True))

    destinations = _choose_destinations(
        cfg, "Where should this rule's alerts go?", cfg["routes"].get("resource_alert", [])
    )

    print("\nMinutes to wait before this rule can alert again (default {})".format(
        DEFAULT_COOLDOWN_MINUTES
    ))
    cooldown = int(_read(min=0, digit=True, default=DEFAULT_COOLDOWN_MINUTES))

    print("\nAlso tell me when it recovers? [y/N]")
    recovery = str(
        _read(values=["y", "Y", "n", "N", ""], empty=True, default="n")
    ).lower() == "y"

    rule = {
        "id": _new_rule_id(cfg["resource_rules"]),
        "enabled": True,
        "scope": "city" if scope_choice == 1 else "global",
        "city_id": city_id,
        "city_name": city_name,
        "resource": resource,
        "mode": mode,
        "direction": direction,
        "threshold": threshold,
        "destinations": destinations,
        "cooldown_minutes": cooldown,
        "rearm_margin_percent": DEFAULT_REARM_MARGIN,
        "notify_on_recovery": recovery,
    }
    cfg["resource_rules"].append(rule)
    print("\n{}Added: {}{}".format(bcolors.GREEN, _rule_label(cfg, rule), bcolors.ENDC))
    enter()
    return True


def _edit_resource_rule(session, cfg):
    rules = cfg["resource_rules"]
    _header("EDIT A RESOURCE RULE")
    for index, rule in enumerate(rules, 1):
        print("  {:>2}) {}".format(index, _rule_label(cfg, rule)))
    print("(0) Cancel")
    index = int(_read(min=0, max=len(rules), digit=True))
    if index == 0:
        return False
    rule = rules[index - 1]

    _header(_rule_label(cfg, rule).upper()[:56])
    print("  Alerts go to:  {}".format(_dest_names(cfg, rule.get("destinations") or [])))
    print("  Cooldown:      {} minute(s)".format(rule.get("cooldown_minutes", DEFAULT_COOLDOWN_MINUTES)))
    print("  Re-arm margin: {}%".format(rule.get("rearm_margin_percent", DEFAULT_REARM_MARGIN)))
    print("  Recovery note: {}".format("yes" if rule.get("notify_on_recovery") else "no"))
    print("")
    print("(0) Back")
    print("(1) {} this rule".format("Disable" if rule.get("enabled", True) else "Enable"))
    print("(2) Change the number")
    print("(3) Change where its alerts go")
    print("(4) Change the cooldown")
    print("(5) Change the re-arm margin")
    print("(6) Toggle the recovery message")

    choice = _read(min=0, max=6, digit=True)
    if choice == 0:
        return False
    if choice == 1:
        rule["enabled"] = not rule.get("enabled", True)
    elif choice == 2:
        if rule.get("mode") == "percent":
            print("\nPercent (1-100)")
            rule["threshold"] = float(_read(min=1, max=100, digit=True))
        elif rule.get("mode") == "hours_left":
            print("\nHours")
            rule["threshold"] = float(_read(min=1, max=1000, digit=True))
        else:
            print("\nAmount")
            rule["threshold"] = float(_read(min=0, digit=True))
    elif choice == 3:
        rule["destinations"] = _choose_destinations(
            cfg, "Where should this rule's alerts go?", rule.get("destinations") or []
        )
    elif choice == 4:
        print("\nMinutes before this rule can alert again")
        rule["cooldown_minutes"] = int(_read(min=0, digit=True, default=DEFAULT_COOLDOWN_MINUTES))
    elif choice == 5:
        print("\nHow far past the number it must recover before the rule re-arms,")
        print("as a percent of the number. Higher values mean less flapping.")
        rule["rearm_margin_percent"] = int(
            _read(min=0, max=100, digit=True, default=DEFAULT_REARM_MARGIN)
        )
    elif choice == 6:
        rule["notify_on_recovery"] = not rule.get("notify_on_recovery", False)
    return True


def _choose_destinations(cfg, prompt_text, current):
    """Multi-select destinations. Empty answer falls back to the type's route."""
    if not cfg["destinations"]:
        print("\n{}No destinations exist yet — this rule will use the".format(bcolors.WARNING))
        print("Resource alerts route once you add one.{}".format(bcolors.ENDC))
        enter()
        return []

    print("")
    print(prompt_text)
    for index, dest in enumerate(cfg["destinations"], 1):
        marker = "*" if dest["id"] in (current or []) else " "
        print("  {} {}) {}".format(marker, index, _dest_label(dest)))
    print("Numbers separated by commas, or blank to use the Resource alerts route.")
    raw = str(_read(empty=True, default="")).strip()

    chosen = []
    for token in raw.split(","):
        token = token.strip()
        if token.isdigit() and 1 <= int(token) <= len(cfg["destinations"]):
            dest_id = cfg["destinations"][int(token) - 1]["id"]
            if dest_id not in chosen:
                chosen.append(dest_id)
    return chosen


def _new_rule_id(rules):
    used = {r.get("id") for r in rules}
    index = 1
    while "r{}".format(index) in used:
        index += 1
    return "r{}".format(index)


def _resource_dry_run(session, cfg):
    _header("RESOURCE CHECK — NOTHING WILL BE SENT")
    rules = [r for r in cfg.get("resource_rules", []) if r.get("enabled", True)]
    if not rules:
        print("No enabled rules.")
        enter()
        return

    print("Reading your cities…\n")
    from ikabot.config import city_url
    from ikabot.helpers.getJson import getCity
    from ikabot.helpers.pedirInfo import getIdsOfCities

    try:
        city_ids, _ = getIdsOfCities(session)
    except Exception:
        print("{}Could not read your cities.{}".format(bcolors.RED, bcolors.ENDC))
        enter()
        return

    for city_id in city_ids:
        applicable = [
            r
            for r in rules
            if r.get("scope") == "global" or str(r.get("city_id")) == str(city_id)
        ]
        if not applicable:
            continue
        try:
            page_html = session.get(city_url + str(city_id))
            city = getCity(page_html)
        except Exception:
            print("  {}could not read city {}{}".format(bcolors.RED, city_id, bcolors.ENDC))
            continue

        city_name = city.get("name") or str(city_id)
        for rule in applicable:
            value, unit = _rule_reading(rule, city, page_html)
            if value is None:
                print("  {:<14} {:<34} {}not measurable here{}".format(
                    city_name, _rule_label(cfg, rule, city_name),
                    bcolors.BLACK, bcolors.ENDC
                ))
                continue
            breached = _is_breach(rule, value)
            print("  {:<14} {:<34} {} {}{}{}".format(
                city_name,
                _rule_label(cfg, rule, city_name),
                _format_reading(value, unit),
                bcolors.RED if breached else bcolors.GREEN,
                "ALERT" if breached else "ok",
                bcolors.ENDC,
            ))
    enter()


def _menu_formatting(session):
    while True:
        cfg, is_global = _pick_config_to_edit(session)
        fmt = cfg["formatting"]
        quiet = fmt["quiet_hours"]
        _header("FORMATTING & FILTERS{}".format(" (GLOBAL)" if is_global else ""))
        print("  Message body:      {}".format("included" if fmt.get("include_body", True) else "omitted"))
        print("  Body limit:        {} characters".format(fmt.get("body_max_chars", DEFAULT_BODY_MAX)))
        print("  Combat reports:    {}".format("full report" if fmt.get("combat_full_report") else "summary"))
        print(
            "  Quiet hours:       {}".format(
                "{} → {}".format(quiet.get("from"), quiet.get("to"))
                if quiet.get("enabled")
                else "off"
            )
        )
        print("  Muted phrases:     {}".format(len(fmt.get("mutes") or [])))
        print("")
        print("(0) Back")
        print("(1) Toggle message body")
        print("(2) Change body character limit")
        print("(3) Toggle combat summary / full report")
        print("(4) Quiet hours")
        print("(5) Muted phrases")

        choice = _read(min=0, max=5, digit=True)
        if choice == 0:
            return
        if choice == 1:
            fmt["include_body"] = not fmt.get("include_body", True)
        elif choice == 2:
            print("\nMaximum characters of message body to forward (0 for no limit)")
            fmt["body_max_chars"] = int(_read(min=0, max=4000, digit=True, default=DEFAULT_BODY_MAX))
        elif choice == 3:
            fmt["combat_full_report"] = not fmt.get("combat_full_report", False)
        elif choice == 4:
            _edit_quiet_hours(quiet)
        elif choice == 5:
            _edit_mutes(fmt)
        _store(session, cfg, is_global)


def _edit_quiet_hours(quiet):
    _header("QUIET HOURS")
    print("Nothing is forwarded during quiet hours — those messages are still")
    print("marked as seen, so they will not arrive later in a burst.")
    print("")
    print("(0) Back")
    print("(1) {} quiet hours".format("Disable" if quiet.get("enabled") else "Enable"))
    print("(2) Change the times")
    choice = _read(min=0, max=2, digit=True)
    if choice == 1:
        quiet["enabled"] = not quiet.get("enabled", False)
    elif choice == 2:
        print("\nStart hour (0-23)")
        start_h = int(_read(min=0, max=23, digit=True, default=23))
        print("End hour (0-23)")
        end_h = int(_read(min=0, max=23, digit=True, default=7))
        quiet["from"] = "{:02d}:00".format(start_h)
        quiet["to"] = "{:02d}:00".format(end_h)
        quiet["enabled"] = True


def _edit_mutes(fmt):
    while True:
        mutes = fmt.setdefault("mutes", [])
        _header("MUTED PHRASES")
        if mutes:
            for index, phrase in enumerate(mutes, 1):
                print("  {}) {}".format(index, phrase))
        else:
            print("  Nothing muted.")
        print("")
        print("A message containing a muted phrase is never forwarded.")
        print("(0) Back")
        print("(1) Add a phrase")
        print("(2) Remove a phrase")
        choice = _read(min=0, max=2, digit=True)
        if choice == 0:
            return
        if choice == 1:
            phrase = str(_read(msg="Phrase to mute: ")).strip()
            if phrase:
                mutes.append(phrase)
        elif choice == 2 and mutes:
            print("Which one?")
            index = int(_read(min=0, max=len(mutes), digit=True))
            if index:
                mutes.pop(index - 1)


def _menu_diagnostics(session):
    while True:
        cfg = _effective_config(session)
        state = _load_state(session)
        _header("DIAGNOSTICS")
        print("  Forwarded:   {}".format(state.get("delivered", 0)))
        print("  Failed:      {}".format(state.get("failed", 0)))
        print("  Known ids:   {}".format(len(state.get("seen_ids", {}))))
        print("  Last error:  {}".format(state.get("last_error") or "none"))
        print("")
        print("(0) Back")
        print("(1) Test every destination")
        print("(2) Scan the inbox now and show what would be sent")
        print("(3) Capture raw message data to a file")
        print("(4) Reset seen messages")
        print("(5) Reset counters")
        print("(6) Shared configuration lock")

        choice = _read(min=0, max=6, digit=True)
        if choice == 0:
            return
        if choice == 6:
            _menu_lock(session)
        elif choice == 1:
            _test_all_destinations(session, cfg)
        elif choice == 2:
            _dry_run(session, cfg)
        elif choice == 3:
            _capture(session, cfg)
        elif choice == 4:
            print("\nForget every message id? The next scan treats the inbox as new. [y/N]")
            if str(_read(values=["y", "Y", "n", "N", ""], empty=True, default="n")).lower() == "y":
                state["seen_ids"] = {}
                state["first_run_done"] = False
                _save_state(session, state)
                print("{}Done.{}".format(bcolors.GREEN, bcolors.ENDC))
                enter()
        elif choice == 5:
            state["delivered"] = 0
            state["failed"] = 0
            state["last_error"] = ""
            _save_state(session, state)
            print("\n{}Counters reset.{}".format(bcolors.GREEN, bcolors.ENDC))
            enter()


def _menu_lock(session):
    while True:
        path = _lock_path(session)
        data = _read_lock(path)
        _header("SHARED CONFIGURATION LOCK")
        print("  File:    {}".format(path))
        if data is None:
            print("  Held by: {}nobody{}".format(bcolors.GREEN, bcolors.ENDC))
        else:
            age = _lock_age(path, data)
            dead = _holder_is_dead(data)
            if dead:
                verdict = "{}holder is gone — will be reclaimed{}".format(
                    bcolors.WARNING, bcolors.ENDC
                )
            elif age > LOCK_STALE_SECONDS:
                verdict = "{}stale — will be reclaimed{}".format(
                    bcolors.WARNING, bcolors.ENDC
                )
            else:
                verdict = "{}live{}".format(bcolors.GREEN, bcolors.ENDC)
            print("  Held by: {}".format(_describe_holder(data)))
            print("  Age:     {:.0f}s ({})".format(age, verdict))
            print("  Stale after {}s".format(LOCK_STALE_SECONDS))

        shared = _read_json_resilient(_global_config_path(session))
        if shared is not None:
            print("")
            print("  Global config revision {} last written by {}".format(
                shared.get("revision", 0), shared.get("last_written_by", "?")
            ))
        print("")
        print("A stale lock is reclaimed automatically — you should not normally")
        print("need this screen. Forcing a release while another instance really")
        print("is writing can lose that instance's edit.")
        print("")
        print("(0) Back")
        print("(1) Force release the lock")

        if _read(min=0, max=1, digit=True) == 0:
            return
        if data is None:
            continue
        print("\nForce release the lock held by {}? [y/N]".format(_describe_holder(data)))
        if str(_read(values=["y", "Y", "n", "N", ""], empty=True, default="n")).lower() != "y":
            continue
        try:
            os.remove(path)
            print("{}Released.{}".format(bcolors.GREEN, bcolors.ENDC))
        except FileNotFoundError:
            print("{}It had already been released.{}".format(bcolors.GREEN, bcolors.ENDC))
        except OSError as exc:
            print("{}Could not remove it: {}{}".format(bcolors.RED, exc, bcolors.ENDC))
        enter()


def _test_all_destinations(session, cfg):
    _header("TEST EVERY DESTINATION")
    if not cfg["destinations"]:
        print("No destinations configured.")
        enter()
        return

    event = {
        "id": "test",
        "type": "hub_status",
        "title": "Test message",
        "sender": "",
        "body": "Messaging Hub test.",
        "city": "",
        "date": "",
    }
    footer = _footer(session)
    for dest in cfg["destinations"]:
        ok, detail = _deliver(session, dest, [event], cfg["formatting"], footer)
        if ok:
            print("  {}OK  {}{}".format(bcolors.GREEN, bcolors.ENDC, _dest_label(dest)))
        else:
            print(
                "  {}FAIL{} {} — {}".format(
                    bcolors.RED, bcolors.ENDC, _dest_label(dest), detail
                )
            )
    enter()


def _dry_run(session, cfg):
    _header("SCAN — NOTHING WILL BE SENT")
    print("Fetching…\n")
    messages = _fetch_messages(session)
    state = _load_state(session)
    seen = state.get("seen_ids", {})
    langs = _session_languages(session)
    own_cities = _own_city_names(session)

    if not messages:
        print("{}No messages found.{}".format(bcolors.WARNING, bcolors.ENDC))
        print("If your inbox is not empty, use (3) Capture raw message data.")
        enter()
        return

    counts = {}
    for message in messages:
        event_type = _classify(message, langs, cfg.get("classification_overrides"), own_cities)
        counts[event_type] = counts.get(event_type, 0) + 1
        if message["id"] not in seen:
            print(
                "  {} [{}] {} — {}".format(
                    TYPE_EMOJI.get(event_type, ""),
                    TYPE_LABELS.get(event_type, event_type),
                    message.get("subject", "")[:60],
                    _dest_names(cfg, cfg["routes"].get(event_type, [])),
                )
            )

    print("\n  {} message(s) total:".format(len(messages)))
    for event_type, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print("    {:<26} {}".format(TYPE_LABELS.get(event_type, event_type), count))
    enter()


def _redact(text):
    text = re.sub(r"https://\S*discord\S*/api/webhooks/\S+", "<webhook redacted>", str(text))
    return re.sub(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b", "<token redacted>", text)


def _capture(session, cfg):
    _header("CAPTURE RAW MESSAGE DATA")
    print("Writes what the game returned plus how the hub read it, so the")
    print("classification tables can be built from real data.\n")

    payloads = _fetch_message_payloads(session)
    messages = _fetch_messages(session)
    langs = _session_languages(session)
    own_cities = _own_city_names(session)

    path = os.path.join(
        _capture_dir(session),
        "{}_capture_{}.txt".format(_account_key(session), time.strftime("%Y%m%d_%H%M%S")),
    )

    lines = [
        "Messaging Hub capture",
        "Account: {}".format(_account_key(session)),
        "Languages tried: {}".format(", ".join(langs)),
        "Payloads fetched: {}".format(len(payloads)),
        "Messages parsed: {}".format(len(messages)),
        "",
        "=== PARSED ===",
    ]
    for message in messages:
        event_type = _classify(message, langs, cfg.get("classification_overrides"), own_cities)
        lines.append("")
        lines.append("id      : {}".format(message.get("id")))
        lines.append("source  : {}".format(message.get("source")))
        lines.append("icon    : {}".format(message.get("icon")))
        lines.append("sender  : {}".format(message.get("sender")))
        lines.append("subject : {}".format(message.get("subject")))
        lines.append("city    : {}".format(message.get("city")))
        lines.append("date    : {}".format(message.get("date")))
        lines.append("body    : {}".format(message.get("body")))
        lines.append("classified as: {}".format(event_type))

    lines.append("")
    lines.append("=== RAW MESSAGE ROWS ===")
    for payload in payloads:
        for row in re.findall(
            r'<tr[^>]*id\s*=\s*["\']\s*g?message\d+\s*["\'][\s\S]*?</tr>',
            payload,
            flags=re.IGNORECASE,
        ):
            lines.append("")
            lines.append(_redact(row))

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print("{}Written to:{}\n  {}".format(bcolors.GREEN, bcolors.ENDC, path))
    except OSError:
        print("{}Could not write the capture file.{}".format(bcolors.RED, bcolors.ENDC))
        logger.error("Capture write failed", exc_info=True)
    enter()


def _menu_storage(session):
    while True:
        account_cfg = _load_account_config(session)
        use_global = account_cfg["use_global"]
        shared_exists = _load_global_config(session) is not None
        _header("STORAGE & GLOBAL CONFIGURATION")
        print("  Folder:            {}".format(_hub_dir(session)))
        print("  Files for:         {}".format(_account_key(session)))
        print("  Global file:       {}".format("present" if shared_exists else "not created yet"))
        print("  Routing from:      {}".format("global" if use_global.get("routing") else "this account"))
        print("  Formatting from:   {}".format("global" if use_global.get("formatting") else "this account"))
        print("  Resource rules:    {}".format("global" if use_global.get("resources") else "this account"))
        print("")
        print("Point several accounts at the same folder to share one global")
        print("configuration between them.")
        print("")
        print("(0) Back")
        print("(1) Change the storage folder")
        print("(2) Reset the storage folder to the default")
        print("(3) Use global routing here: {}".format("on" if use_global.get("routing") else "off"))
        print("(4) Use global formatting here: {}".format("on" if use_global.get("formatting") else "off"))
        print("(5) Copy this account's settings into the global configuration")
        print("(6) Copy the global configuration into this account")
        print("(7) Use global resource rules here: {}".format(
            "on" if use_global.get("resources") else "off"
        ))

        choice = _read(min=0, max=7, digit=True)
        if choice == 0:
            return
        if choice == 7:
            if not use_global.get("resources") and not shared_exists:
                print("\n{}There is no global configuration yet — use (5) first.{}".format(
                    bcolors.WARNING, bcolors.ENDC
                ))
                enter()
                continue
            if not use_global.get("resources"):
                print("\n{}City-specific rules from the global file only apply to the".format(
                    bcolors.WARNING
                ))
                print("account that made them. Rules covering every city apply here.{}".format(
                    bcolors.ENDC
                ))
                enter()
            use_global["resources"] = not use_global.get("resources", False)
            _save_account_config(session, account_cfg)
            continue
        if choice == 1:
            print("\nFolder to keep hub data in — a '{}' folder is created inside it.".format(HUB_DIR_NAME))
            print("Current: {}".format(_base_dir(session)))
            path = str(_read(msg="New folder: ")).strip().strip('"')
            if not path:
                continue
            if not os.path.isdir(path):
                print("\n{}No such folder.{}".format(bcolors.RED, bcolors.ENDC))
                enter()
                continue
            _set_base_dir(session, path)
            print("\n{}Now storing in {}{}".format(bcolors.GREEN, _hub_dir(session), bcolors.ENDC))
            enter()
        elif choice == 2:
            _set_base_dir(session, "")
            print("\n{}Back to {}{}".format(bcolors.GREEN, _hub_dir(session), bcolors.ENDC))
            enter()
        elif choice in (3, 4):
            key = "routing" if choice == 3 else "formatting"
            if not use_global.get(key) and not shared_exists:
                print(
                    "\n{}There is no global configuration yet — use (5) first.{}".format(
                        bcolors.WARNING, bcolors.ENDC
                    )
                )
                enter()
                continue
            use_global[key] = not use_global.get(key, False)
            _save_account_config(session, account_cfg)
        elif choice == 5:
            print("\nOverwrite the global configuration with this account's settings? [y/N]")
            if str(_read(values=["y", "Y", "n", "N", ""], empty=True, default="n")).lower() == "y":
                shared = dict(account_cfg)
                shared["use_global"] = {"routing": False, "formatting": False}
                ok, message = _save_global_config(session, shared)
                if ok:
                    print("{}Written.{}".format(bcolors.GREEN, bcolors.ENDC))
                else:
                    print("{}Not written — {}{}".format(bcolors.RED, message, bcolors.ENDC))
                enter()
        elif choice == 6:
            shared = _load_global_config(session)
            if shared is None:
                print("\n{}No global configuration to copy.{}".format(bcolors.WARNING, bcolors.ENDC))
                enter()
                continue
            print("\nOverwrite this account's settings with the global ones? [y/N]")
            if str(_read(values=["y", "Y", "n", "N", ""], empty=True, default="n")).lower() == "y":
                shared["use_global"] = account_cfg["use_global"]
                _save_account_config(session, shared)
                print("{}Copied.{}".format(bcolors.GREEN, bcolors.ENDC))
                enter()


def _menu_import_export(session):
    _header("IMPORT / EXPORT")
    print("(0) Back")
    print("(1) Export this account's configuration to a file")
    print("(2) Import a configuration file into this account")
    choice = _read(min=0, max=2, digit=True)
    if choice == 0:
        return

    if choice == 1:
        default_path = os.path.join(
            _hub_dir(session), "{}_export.json".format(_account_key(session))
        )
        print("\nFile to write (blank for {})".format(default_path))
        path = str(_read(empty=True, default="")).strip().strip('"') or default_path
        if _write_json(path, _load_account_config(session)):
            print("\n{}Exported to {}{}".format(bcolors.GREEN, path, bcolors.ENDC))
        else:
            print("\n{}Could not write that file.{}".format(bcolors.RED, bcolors.ENDC))
    else:
        path = str(_read(msg="File to import: ")).strip().strip('"')
        data = _read_json(path)
        if data is None:
            print("\n{}Could not read that file.{}".format(bcolors.RED, bcolors.ENDC))
        else:
            print("\nReplace this account's configuration? [y/N]")
            if str(_read(values=["y", "Y", "n", "N", ""], empty=True, default="n")).lower() == "y":
                _save_account_config(session, _normalise_config(data))
                print("{}Imported.{}".format(bcolors.GREEN, bcolors.ENDC))
    enter()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def messagingHub(session, event, stdin_fd, predetermined_input):
    sys.stdin = os.fdopen(stdin_fd)
    config.predetermined_input = predetermined_input

    try:
        if getattr(config, "autostart_active", False):
            cfg = _effective_config(session)
            if not _config_is_runnable(cfg):
                try:
                    if notificationDataIsValid(session):
                        sendToBot(
                            session,
                            "messagingHub: no usable configuration, auto-start aborted.",
                        )
                except Exception:
                    logger.error("Auto-start abort notice failed", exc_info=True)
                event.set()
                return
        elif not _menu_main(session):
            event.set()
            return
    except KeyboardInterrupt:
        event.set()
        return

    set_child_mode(session)
    event.set()

    setInfoSignal(session, "Messaging hub is forwarding in-game messages")

    try:
        _do_it(session)
    except Exception:
        message = "Error in Messaging Hub:\n{}".format(traceback.format_exc())
        try:
            _notify_status(session, _effective_config(session), message)
        except Exception:
            logger.error("Could not report hub failure", exc_info=True)
    finally:
        session.logout()
