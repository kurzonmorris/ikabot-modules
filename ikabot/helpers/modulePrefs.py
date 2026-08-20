#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""Per-account, per-module saved settings ("module memory").

Modules that opt in get their own small JSON file under
``IKABOT_DATA_DIR/module_prefs/``, named after the account (username +
server + world) and the module.  Settings therefore never leak between
accounts, worlds, or modules.

Typical use inside a module's interactive phase::

    from ikabot.helpers.modulePrefs import load_prefs, save_prefs, prompt_use_saved

    saved = load_prefs(session, "activateShrine")
    if saved and prompt_use_saved(session, "activateShrine", ["Gods: Pan, Tyche"]):
        ...   # replay the saved values, ask nothing
    else:
        ...   # ask the normal questions
        save_prefs(session, "activateShrine", {...})

Only plain JSON-serialisable values should be stored (ints, strings, bools,
lists, dicts).  Never store live game objects — save an id and re-fetch the
object on replay so the module always works from fresh data.
"""

import json
import os

from ikabot import config
from ikabot.config import IKABOT_DATA_DIR
from ikabot.helpers.gui import bcolors
from ikabot.helpers.logging import getLogger

logger = getLogger(__name__)

MODULE_PREFS_DIR = os.path.join(IKABOT_DATA_DIR, "module_prefs")


def _safe(s):
    """Reduce a string to characters that are safe in a filename."""
    return "".join(c for c in str(s) if c.isalnum() or c in "-_")


# Reserved key: auto-start metadata, not a module setting.  Keys starting with
# "_" belong to this layer and must be ignored by modules replaying prefs.
AUTOSTART_KEY = "_autostart"


def _account_prefix(session):
    """Return the filename prefix identifying this account's prefs files."""
    username = _safe(getattr(session, "username", "") or "unknown")
    servidor = _safe(getattr(session, "servidor", "") or "")
    mundo = _safe(str(getattr(session, "mundo", "") or ""))
    return f"{username}_{servidor}{mundo}_"


def prefs_path(session, module_name):
    """Return the settings file path for this (account, module) pair."""
    return os.path.join(
        MODULE_PREFS_DIR, f"{_account_prefix(session)}{_safe(module_name)}.json"
    )


def load_prefs(session, module_name):
    """Return the saved settings dict for this account+module, or None."""
    try:
        path = prefs_path(session, module_name)
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data:
                return data
    except Exception:
        logger.debug("Could not load module prefs for %s", module_name, exc_info=True)
    return None


def save_prefs(session, module_name, prefs):
    """Persist settings for this account+module.  Best-effort, never raises."""
    try:
        os.makedirs(MODULE_PREFS_DIR, exist_ok=True)
        path = prefs_path(session, module_name)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        logger.debug("Could not save module prefs for %s", module_name, exc_info=True)


def clear_prefs(session, module_name):
    """Delete the saved settings for this account+module."""
    try:
        os.unlink(prefs_path(session, module_name))
    except FileNotFoundError:
        pass
    except Exception:
        logger.debug("Could not clear module prefs for %s", module_name, exc_info=True)


def is_autostart(session, module_name):
    """True if this account has auto-start enabled for this module."""
    prefs = load_prefs(session, module_name)
    return bool(prefs and prefs.get(AUTOSTART_KEY))


def set_autostart(session, module_name, enabled):
    """Enable or disable auto-start for this account+module.

    No-op when there are no saved settings: auto-start replays saved settings,
    so enabling it without any is meaningless.  Returns True if the flag was
    written.
    """
    prefs = load_prefs(session, module_name)
    if not prefs:
        return False
    prefs[AUTOSTART_KEY] = bool(enabled)
    save_prefs(session, module_name, prefs)
    return True


def list_autostart_modules(session):
    """Return the module names this account has auto-start enabled for.

    Unreadable or malformed prefs files are skipped rather than raising — one
    bad file must never block login.
    """
    names = []
    prefix = _account_prefix(session)
    try:
        filenames = os.listdir(MODULE_PREFS_DIR)
    except OSError:
        return names
    for filename in sorted(filenames):
        if not filename.startswith(prefix) or not filename.endswith(".json"):
            continue
        module_name = filename[len(prefix):-len(".json")]
        if not module_name:
            continue
        try:
            if is_autostart(session, module_name):
                names.append(module_name)
        except Exception:
            logger.debug("Skipping unreadable prefs file %s", filename, exc_info=True)
    return names


def list_saved_modules(session):
    """Return every module name this account has saved settings for."""
    names = []
    prefix = _account_prefix(session)
    try:
        filenames = os.listdir(MODULE_PREFS_DIR)
    except OSError:
        return names
    for filename in sorted(filenames):
        if filename.startswith(prefix) and filename.endswith(".json"):
            module_name = filename[len(prefix):-len(".json")]
            if module_name:
                names.append(module_name)
    return names


def offer_autostart(session, module_name):
    """Offer to run this module automatically at login. Returns True if enabled.

    Call once, after save_prefs(), in the interactive path only. Silent and
    inert when there is nothing to offer, so callers need no extra guards.
    """
    from ikabot.helpers.pedirInfo import read

    # Never prompt under sequenceRunner (would swallow a recorded keystroke)
    # or under auto-start (no terminal). Nothing to enable without settings.
    try:
        if len(config.predetermined_input) != 0:
            return False
    except Exception:
        pass
    if getattr(config, "autostart_active", False):
        return False
    if not load_prefs(session, module_name) or is_autostart(session, module_name):
        return False

    print("")
    print("Run this automatically at login from now on?")
    print("It will start in the background using these settings.")
    choice = read(values=["y", "Y", "n", "N", ""], empty=True, default="n",
                  msg="[y/N]: ")
    if choice.lower() != "y":
        return False
    return set_autostart(session, module_name, True)


def prompt_use_saved(session, module_name, summary_lines=()):
    """Show the saved settings and ask whether to reuse them.

    Returns
    -------
    bool
        True  -> replay the saved settings (module should ask nothing else)
        False -> run the normal questions (settings were rejected or deleted)
    """
    # Late import: pedirInfo pulls in a lot of helpers, importing it at module
    # scope risks an import cycle for callers of this module.
    from ikabot.helpers.pedirInfo import read

    # Under sequenceRunner (predetermined_input populated) never insert an
    # extra prompt — it would swallow a recorded keystroke and desync the
    # whole sequence.  Fall through to the module's normal questions, which
    # is exactly what the recorded sequence expects.
    try:
        if len(config.predetermined_input) != 0:
            return False
    except Exception:
        pass

    # Auto-start: the module was launched at login with no terminal attached.
    # Replay the saved settings silently — printing or prompting here would
    # write into the parent's menu and block waiting for input nobody can give.
    if getattr(config, "autostart_active", False):
        return True

    print("")
    print(f"{bcolors.GREEN}Saved settings found for this module:{bcolors.ENDC}")
    for line in summary_lines:
        print(f"   {line}")
    print("")
    print("(1) Use these settings   [just press ENTER]")
    print("(2) Reconfigure")
    print("(3) Delete saved settings and reconfigure")
    choice = read(min=1, max=3, digit=True, default=1)
    if choice == 3:
        clear_prefs(session, module_name)
        return False
    return choice == 1
