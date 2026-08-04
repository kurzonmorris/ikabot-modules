#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""Messaging Hub — forwards in-game Ikariam messages to Discord, Telegram and ntfy."""

MODULE_NAME = "Messaging Hub"
MODULE_ENTRY = "messagingHub"
MENU_LABEL = "Messaging Hub"
MENU_ORDER = 50

import html
import json
import os
import re
import sys
import time
import traceback

import ikabot.config as config
from ikabot.config import IKABOT_DATA_DIR
from ikabot.helpers.botComm import sendToBot
from ikabot.helpers.gui import banner, bcolors, enter
from ikabot.helpers.pedirInfo import read
from ikabot.helpers.process import set_child_mode
from ikabot.helpers.signals import setInfoSignal
from ikabot.helpers.varios import wait

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
DEFAULT_BODY_MAX = 900
SEEN_HARD_CAP = 5000
FAILURE_REPORT_COOLDOWN = 3600

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
    except Exception:
        logger.debug("Could not read %s", path, exc_info=True)
    return None


def _write_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception:
        logger.error("Could not write %s", path, exc_info=True)
        return False


def _default_config():
    return {
        "config_version": CONFIG_VERSION,
        "use_global": {"routing": False, "formatting": False},
        "destinations": [],
        "routes": {t: [] for t in EVENT_TYPES},
        "type_enabled": {t: True for t in EVENT_TYPES},
        "watchers": {
            "messages": {
                "enabled": True,
                "interval_minutes": DEFAULT_MESSAGE_INTERVAL,
                "notify_existing": False,
            }
        },
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

    return merged


def _load_account_config(session):
    return _normalise_config(_read_json(_config_path(session)))


def _save_account_config(session, cfg):
    cfg["config_version"] = CONFIG_VERSION
    ok = _write_json(_config_path(session), cfg)
    # Give the mod's auto-start screen a prefs file to flag; the real settings
    # stay in the hub's own config so they can be shared between accounts.
    if ok and _HAS_MODULE_PREFS:
        prefs = load_prefs(session, PREFS_NAME) or {}
        prefs["config_version"] = CONFIG_VERSION
        save_prefs(session, PREFS_NAME, prefs)
    return ok


def _load_global_config(session):
    data = _read_json(_global_config_path(session))
    if data is None:
        return None
    return _normalise_config(data)


def _save_global_config(session, cfg):
    cfg["config_version"] = CONFIG_VERSION
    return _write_json(_global_config_path(session), cfg)


def _effective_config(session):
    """Account config with whole sections swapped for the global ones."""
    cfg = _load_account_config(session)
    use_global = cfg.get("use_global", {})
    if not (use_global.get("routing") or use_global.get("formatting")):
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


def _parse_messages_from_payload(payload):
    messages = {}
    bodies = _parse_message_bodies_by_suffix(payload)
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

        message = {
            "id": _canonical_message_id(row_id),
            "source": "game" if row_id.lower().startswith("gmessage") else "player",
            "sender": _extract_sender_from_row(row_html),
            "subject": _extract_subject_from_row(row_html),
            "body": bodies.get(suffix, "") if suffix else "",
            "city": "",
            "date": "",
            "icon": _extract_row_icon(row_attrs + row_html),
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

    haystack = "{} {} {}".format(
        message.get("subject", ""), message.get("body", ""), message.get("icon", "")
    ).lower()

    override = _apply_overrides(overrides, haystack)
    if override:
        return override

    if message.get("source") == "player":
        return "player_message"

    icon = str(message.get("icon", "")).lower()
    for event_type in CLASSIFY_ORDER:
        if event_type.split("_")[0] in icon:
            return event_type

    for lang in session_langs:
        table = KEYWORDS.get(lang, {})
        for event_type in CLASSIFY_ORDER:
            for keyword in table.get(event_type, []):
                if keyword in haystack:
                    if event_type in ("shipment_internal", "shipment_external"):
                        return _classify_shipment(haystack, own_cities, event_type)
                    return event_type

    return "other"


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


def _destinations_for(cfg, event_type):
    if not cfg["type_enabled"].get(event_type, True):
        return []
    wanted = cfg["routes"].get(event_type) or []
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
        for dest in _destinations_for(cfg, event["type"]):
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

    while True:
        # Re-read every pass so menu edits take effect without a restart.
        cfg = _effective_config(session)
        interval = max(
            MIN_MESSAGE_INTERVAL,
            int(cfg["watchers"]["messages"].get("interval_minutes", DEFAULT_MESSAGE_INTERVAL)),
        )

        if cfg["watchers"]["messages"].get("enabled", True):
            try:
                sent, failed, scanned = _poll_messages(
                    session, cfg, state, own_cities, session_langs
                )
                session.setStatus(
                    "Hub: {} new, {} forwarded, {} failed (total {}/{})".format(
                        scanned,
                        sent,
                        failed,
                        state.get("delivered", 0),
                        state.get("failed", 0),
                    )
                )
                if failed:
                    _report_failures(session, cfg, state)
            except Exception:
                logger.error("Message poll failed", exc_info=True)
                state["last_error"] = traceback.format_exc()[-400:]
                _save_state(session, state)
                _report_failures(session, cfg, state)
        else:
            session.setStatus("Hub: message forwarding disabled")

        if time.time() >= next_city_refresh:
            own_cities = _own_city_names(session)
            next_city_refresh = time.time() + 6 * 3600

        wait(interval * 60, maxrandom=30)


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


def _header(title):
    banner()
    width = 58
    print("{}╔{}╗{}".format(bcolors.BLUE, "═" * width, bcolors.ENDC))
    print("{}║{}║{}".format(bcolors.BLUE, title.center(width), bcolors.ENDC))
    print("{}╚{}╝{}".format(bcolors.BLUE, "═" * width, bcolors.ENDC))
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
    return (
        "Destinations: {}\n".format(len(cfg["destinations"]))
        + "Routed types: {}/{}\n".format(routed, len(EVENT_TYPES))
        + "Interval: {} minute(s)\n".format(
            cfg["watchers"]["messages"].get("interval_minutes", DEFAULT_MESSAGE_INTERVAL)
        )
        + "Storage: {}".format(_hub_dir(session))
    )


def _config_is_runnable(cfg):
    if not cfg["watchers"]["messages"].get("enabled", True):
        return False
    for event_type in EVENT_TYPES:
        if cfg["type_enabled"].get(event_type, True) and _destinations_for(cfg, event_type):
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
    if read(min=1, max=2, digit=True, default=1) == 1:
        shared = _load_global_config(session) or _default_config()
        return shared, True
    return account_cfg, False


def _store(session, cfg, is_global):
    if is_global:
        _save_global_config(session, cfg)
    else:
        _save_account_config(session, cfg)


def _menu_main(session):
    """Returns True when the user chose to start the hub."""
    while True:
        cfg = _effective_config(session)
        _header("MESSAGING HUB")
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

        choice = read(min=0, max=8, digit=True)
        if choice == 0:
            return False
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
            _header("RESOURCE MONITOR")
            print("Resource monitoring arrives in v1.2.0 — see plan.md, phase 3.")
            enter()
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
    answer = read(
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

        choice = read(min=0, max=4, digit=True)
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
    kind_choice = read(min=0, max=4, digit=True)
    if kind_choice == 0:
        return

    name = str(read(msg="Name for this destination: ")).strip()
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
        url = str(read(msg="Webhook URL: ")).strip()
        if not url.startswith(DISCORD_WEBHOOK_PREFIXES):
            print(
                "\n{}That does not look like a Discord webhook URL.{}".format(
                    bcolors.RED, bcolors.ENDC
                )
            )
            enter()
            return
        print("Override the webhook's display name? (blank keeps the Discord default)")
        username = str(read(empty=True, default="")).strip()
        dest["discord"] = {
            "webhook_url": url,
            "username": username,
            "use_embeds": True,
        }
    elif kind_choice == 2:
        dest["kind"] = "telegram"
        print("\nThis is the hub's own bot — it does not have to be ikabot's bot.")
        token = str(read(msg="Bot token: ")).strip()
        chat_id = str(read(msg="Chat id: ")).strip()
        print("Forum topic id? (blank for none)")
        thread_id = str(read(empty=True, default="")).strip()
        dest["telegram"] = {
            "bot_token": token,
            "chat_id": chat_id,
            "thread_id": thread_id or None,
        }
    elif kind_choice == 3:
        dest["kind"] = "ntfy"
        print("\nServer (blank for https://ntfy.sh)")
        server = str(read(empty=True, default="")).strip() or "https://ntfy.sh"
        topic = str(read(msg="Topic: ")).strip()
        print("Access token? (blank for a public topic)")
        token = str(read(empty=True, default="")).strip()
        print("Priority 1-5 (default 3)")
        priority = int(read(min=1, max=5, digit=True, default=3))
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
    choice = read(min=0, max=len(cfg["destinations"]), digit=True)
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
    choice = read(min=0, max=2, digit=True)
    if choice == 1:
        name = str(read(msg="New name: ")).strip()
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
    if str(read(values=["y", "Y", "n", "N", ""], empty=True, default="n")).lower() != "y":
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

        choice = read(min=0, max=4, digit=True)
        if choice == 0:
            return
        if choice == 1:
            watcher["enabled"] = not watcher.get("enabled", True)
        elif choice == 2:
            print("\nMinutes between checks (min 1, default 10)")
            watcher["interval_minutes"] = int(
                read(min=MIN_MESSAGE_INTERVAL, digit=True, default=DEFAULT_MESSAGE_INTERVAL)
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

        choice = read(min=0, max=len(EVENT_TYPES), digit=True)
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

    choice = read(min=0, max=2, digit=True)
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
    raw = str(read(empty=True, default="")).strip()

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

        choice = read(min=0, max=5, digit=True)
        if choice == 0:
            return
        if choice == 1:
            fmt["include_body"] = not fmt.get("include_body", True)
        elif choice == 2:
            print("\nMaximum characters of message body to forward (0 for no limit)")
            fmt["body_max_chars"] = int(read(min=0, max=4000, digit=True, default=DEFAULT_BODY_MAX))
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
    choice = read(min=0, max=2, digit=True)
    if choice == 1:
        quiet["enabled"] = not quiet.get("enabled", False)
    elif choice == 2:
        print("\nStart hour (0-23)")
        start_h = int(read(min=0, max=23, digit=True, default=23))
        print("End hour (0-23)")
        end_h = int(read(min=0, max=23, digit=True, default=7))
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
        choice = read(min=0, max=2, digit=True)
        if choice == 0:
            return
        if choice == 1:
            phrase = str(read(msg="Phrase to mute: ")).strip()
            if phrase:
                mutes.append(phrase)
        elif choice == 2 and mutes:
            print("Which one?")
            index = int(read(min=0, max=len(mutes), digit=True))
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

        choice = read(min=0, max=5, digit=True)
        if choice == 0:
            return
        if choice == 1:
            _test_all_destinations(session, cfg)
        elif choice == 2:
            _dry_run(session, cfg)
        elif choice == 3:
            _capture(session, cfg)
        elif choice == 4:
            print("\nForget every message id? The next scan treats the inbox as new. [y/N]")
            if str(read(values=["y", "Y", "n", "N", ""], empty=True, default="n")).lower() == "y":
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

        choice = read(min=0, max=6, digit=True)
        if choice == 0:
            return
        if choice == 1:
            print("\nFolder to keep hub data in — a '{}' folder is created inside it.".format(HUB_DIR_NAME))
            print("Current: {}".format(_base_dir(session)))
            path = str(read(msg="New folder: ")).strip().strip('"')
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
            if str(read(values=["y", "Y", "n", "N", ""], empty=True, default="n")).lower() == "y":
                shared = dict(account_cfg)
                shared["use_global"] = {"routing": False, "formatting": False}
                _save_global_config(session, shared)
                print("{}Written.{}".format(bcolors.GREEN, bcolors.ENDC))
                enter()
        elif choice == 6:
            shared = _load_global_config(session)
            if shared is None:
                print("\n{}No global configuration to copy.{}".format(bcolors.WARNING, bcolors.ENDC))
                enter()
                continue
            print("\nOverwrite this account's settings with the global ones? [y/N]")
            if str(read(values=["y", "Y", "n", "N", ""], empty=True, default="n")).lower() == "y":
                shared["use_global"] = account_cfg["use_global"]
                _save_account_config(session, shared)
                print("{}Copied.{}".format(bcolors.GREEN, bcolors.ENDC))
                enter()


def _menu_import_export(session):
    _header("IMPORT / EXPORT")
    print("(0) Back")
    print("(1) Export this account's configuration to a file")
    print("(2) Import a configuration file into this account")
    choice = read(min=0, max=2, digit=True)
    if choice == 0:
        return

    if choice == 1:
        default_path = os.path.join(
            _hub_dir(session), "{}_export.json".format(_account_key(session))
        )
        print("\nFile to write (blank for {})".format(default_path))
        path = str(read(empty=True, default="")).strip().strip('"') or default_path
        if _write_json(path, _load_account_config(session)):
            print("\n{}Exported to {}{}".format(bcolors.GREEN, path, bcolors.ENDC))
        else:
            print("\n{}Could not write that file.{}".format(bcolors.RED, bcolors.ENDC))
    else:
        path = str(read(msg="File to import: ")).strip().strip('"')
        data = _read_json(path)
        if data is None:
            print("\n{}Could not read that file.{}".format(bcolors.RED, bcolors.ENDC))
        else:
            print("\nReplace this account's configuration? [y/N]")
            if str(read(values=["y", "Y", "n", "N", ""], empty=True, default="n")).lower() == "y":
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
