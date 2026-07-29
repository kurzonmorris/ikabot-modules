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


def prefs_path(session, module_name):
    """Return the settings file path for this (account, module) pair."""
    username = _safe(getattr(session, "username", "") or "unknown")
    servidor = _safe(getattr(session, "servidor", "") or "")
    mundo = _safe(str(getattr(session, "mundo", "") or ""))
    filename = f"{username}_{servidor}{mundo}_{_safe(module_name)}.json"
    return os.path.join(MODULE_PREFS_DIR, filename)


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
