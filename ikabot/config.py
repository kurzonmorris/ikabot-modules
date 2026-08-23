#! /usr/bin/env python3
# -*- coding: utf-8 -*-

import locale
import os

# Version is changed automatically by the release pipeline
IKABOT_VERSION = "7.5.1"
IKABOT_VERSION_TAG = "v" + IKABOT_VERSION

IKABOT_MOD_VERSION = "1.8.4"
IKABOT_MOD_VERSION_TAG = "modded by kurzon v" + IKABOT_MOD_VERSION




update_msg = ""

isWindows = os.name == "nt"

# Multiprocessing configuration for pure-Python local decaptcha.
# Safe to leave on even with many accounts: the solver claims worker "seats"
# machine-wide, sizes itself to the free cores and RAM it finds at solve time
# (respecting container CPU/memory limits), and drops to a single-process
# solve when everything is busy. Set False if multiprocessing misbehaves here.
USE_MULTIPROCESSING_DECAPTCHA = True

# Log how long each local captcha solve took, to the ikabot log file. One line
# per solve tagged [decaptcha-timing], with worker count, free RAM and CPU
# topology. Off by default; turn on to compare machines or report a slow solve.
DECAPTCHA_TIMING_LOG = False

# Where the decaptcha worker "seats" live. Defaults to IKABOT_DATA_DIR, which
# is normally the shared volume in a multi-account Docker setup, so instances
# coordinate across containers. Set IKABOT_DECAPTCHA_SEAT_DIR to override —
# point it at a shared mount if the data dir is per-container, or at /tmp for
# per-machine behaviour. Must be on a filesystem where flock works.


# --- Regional context -------------------------------------------------------
# Gameforge rejects blackbox tokens whose regional context does not match the
# login request, so the locale, the Gameforge language, the timezone and the
# Accept-Language header must all agree with each other.  Override these via
# environment variables (or a .env file) to present a different region.
IKABOT_LOCALE = os.getenv("IKABOT_LOCALE", "en-GB")
IKABOT_GF_LANG = os.getenv("IKABOT_GF_LANG", IKABOT_LOCALE.split("-")[0])
IKABOT_TIMEZONE_ID = os.getenv("IKABOT_TIMEZONE_ID", "Europe/London")


# Curated locale/timezone pairs.  Regions are offered as whole presets rather
# than free text so a coherent fingerprint is the only thing representable —
# a locale and a timezone that disagree are worse than any single wrong value.
REGION_PRESETS = [
    ("United Kingdom", "en-GB", "Europe/London"),
    ("United States", "en-US", "America/New_York"),
    ("Germany", "de-DE", "Europe/Berlin"),
    ("France", "fr-FR", "Europe/Paris"),
    ("Spain", "es-ES", "Europe/Madrid"),
    ("Italy", "it-IT", "Europe/Rome"),
    ("Greece", "el-GR", "Europe/Athens"),
    ("Netherlands", "nl-NL", "Europe/Amsterdam"),
    ("Poland", "pl-PL", "Europe/Warsaw"),
    ("Portugal", "pt-PT", "Europe/Lisbon"),
    ("Russia", "ru-RU", "Europe/Moscow"),
    ("Turkey", "tr-TR", "Europe/Istanbul"),
    ("Brazil", "pt-BR", "America/Sao_Paulo"),
    ("Argentina", "es-AR", "America/Argentina/Buenos_Aires"),
    ("Mexico", "es-MX", "America/Mexico_City"),
]


def region_label(loc, timezone_id):
    """Return the friendly name of a locale/timezone pair, or a fallback."""
    for name, preset_locale, preset_tz in REGION_PRESETS:
        if preset_locale == loc and preset_tz == timezone_id:
            return name
    return "{} / {}".format(loc, timezone_id)


def build_accept_language(loc=None, gf_lang=None):
    """Return an Accept-Language header consistent with the configured locale.

    e.g. locale 'en-GB' + lang 'en' -> 'en-GB,en;q=0.9'
    """
    loc = loc or IKABOT_LOCALE
    gf_lang = gf_lang or IKABOT_GF_LANG
    return "{},{};q=0.9".format(loc, gf_lang)


IKABOT_DATA_DIR = os.getenv("APPDATA", os.path.expanduser("~")) + "\\.ikabot" if isWindows else os.path.expanduser("~/.ikabot")
IKABOT_SESSIONS_DIR = os.path.join(IKABOT_DATA_DIR, "sessions")
LOGS_DIRECTORY = os.path.join(IKABOT_DATA_DIR, "logs")
DEFAULT_LOG_LEVEL = 30  # Warning

publicAPIServerDomain = "ikagod.twilightparadox.com"
do_ssl_verify = True
ids_cache = None
cities_cache = None
has_params = False
menu_cities = ""
infoUser = ""
ikaFile = ".ikabot"  # legacy — no longer used; kept to avoid import errors in old code
city_url = "view=city&cityId="
island_url = "view=island&islandId="
prompt = " >>  "
materials_names = ["Wood", "Wine", "Marble", "Crystal", "Sulfur"]
materials_names_english = ["Wood", "Wine", "Marble", "Crystal", "Sulfur"]
miracle_names_english = [
    "",
    "Hephaestus' Forge",
    "Hades' Holy Grove",
    "Demeter's gardens",
    "Athena's Parthenon",
    "Temple of Hermes",
    "Ares' Stronghold",
    "Temple of Poseidon",
    "Colossus",
]
materials_names_tec = ["wood", "wine", "marble", "glass", "sulfur"]
material_img_hash = [
    "19c3527b2f694fb882563c04df6d8972",
    "c694ddfda045a8f5ced3397d791fd064",
    "bffc258b990c1a2a36c5aeb9872fc08a",
    "1e417b4059940b2ae2680c070a197d8c",
    "9b5578a7dfa3e98124439cca4a387a61",
]
tradegoods_names = [
    "Saw mill",
    "Vineyard",
    "Quarry",
    "Crystal Mine",
    "Sulfur Pit",
]
ConnectionError_wait = 5 * 60
actionRequest = "REQUESTID"
piracyMissionToBuildingLevel = {
    1: 1,
    2: 3,
    3: 5,
    4: 7,
    5: 9,
    6: 11,
    7: 13,
    8: 15,
    9: 17,
}
piracyMissionWaitingTime = {
    1: 150,
    2: 450,
    3: 900,
    4: 1800,
    5: 3600,
    6: 7200,
    7: 14400,
    8: 28800,
    9: 57600,
}
predetermined_input = []
sequence_input_delay = 0.0  # seconds to wait between each sequence input pop; increase (e.g. 0.3) if inputs land in the wrong place




debugON_alertAttacks = False
debugON_alertLowWine = False
debugON_donationBot = False
debugON_searchForIslandSpaces = False
debugON_loginDaily = False
debugON_enviarVino = False
debugON_sendResources = False
debugON_constructionList = False
debugON_buyResources = False
debugON_activateMiracle = False

MAXIMUM_CITY_NAME_LENGTH = 20
SECONDS_IN_HOUR = 60 * 60

# Default values for dynamic settings
enable_CustomPort = False

# Set True inside a child process launched by auto-start, so modules replay
# their saved settings instead of prompting.  Never set in the parent.
autostart_active = False

user_agents = [
    # Chromium-only, well-formed strings.  The public API dropped support for
    # non-Chromium user agents in July 2026, and a truncated/malformed UA is an
    # obvious bot signature, so every entry here must be a complete, real-world
    # Chromium UA.  A session picks one deterministically from the account mail.
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.2420.65",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.2365.92",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.2277.128",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.2210.144",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 OPR/108.0.5067.24",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]
