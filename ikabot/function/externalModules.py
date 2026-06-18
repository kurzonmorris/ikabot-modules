#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""External Modules — main menu section 30.

Discovers .py files from a configured global directory and/or a per-account
directory and presents them as numbered menu entries starting at 31.
Option 99 configures the directories.
"""

import importlib.util
import json
import os
import re
import sys

import ikabot.config as config
from ikabot.config import IKABOT_DATA_DIR
from ikabot.helpers.gui import banner, bcolors, enter
from ikabot.helpers.logging import getLogger
from ikabot.helpers.pedirInfo import read

logger = getLogger(__name__)

_GLOBAL_CONFIG_FILE = os.path.join(IKABOT_DATA_DIR, "external_modules.json")
_SESSION_KEY = "externalModulesDir"


# ---------------------------------------------------------------------------
# Directory storage
# ---------------------------------------------------------------------------

def _load_global_config():
    try:
        with open(_GLOBAL_CONFIG_FILE, "r") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_global_config(data):
    os.makedirs(IKABOT_DATA_DIR, exist_ok=True)
    try:
        with open(_GLOBAL_CONFIG_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except OSError as e:
        logger.error("Failed to save external modules config: %s", e)


def get_global_dir():
    return _load_global_config().get("global_dir", "")


def set_global_dir(path):
    cfg = _load_global_config()
    cfg["global_dir"] = path
    _save_global_config(cfg)


def get_account_dir(session):
    try:
        sd = session.getSessionData()
        return sd.get("shared", {}).get(_SESSION_KEY, "")
    except Exception:
        return ""


def set_account_dir(session, path):
    session.setSessionData({_SESSION_KEY: path}, shared=True)


def get_dirs(session):
    """Return (global_dir, account_dir); either may be an empty string."""
    return get_global_dir(), get_account_dir(session)


# ---------------------------------------------------------------------------
# Module discovery
# ---------------------------------------------------------------------------

def _get_module_display_name(path):
    """Return MODULE_NAME / __module_name__ from the file, or the filename stem."""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(4096)
        match = re.search(
            r'(?:MODULE_NAME|__module_name__)\s*=\s*["\']([^"\']+)["\']',
            content,
        )
        if match:
            return match.group(1)
    except Exception:
        pass
    return os.path.basename(path).replace(".py", "")


def get_external_modules(session):
    """Return alphabetically sorted list of (display_name, path) tuples."""
    global_dir, account_dir = get_dirs(session)
    seen = set()
    modules = []

    for directory in (global_dir, account_dir):
        if not directory or not os.path.isdir(directory):
            continue
        try:
            for fname in sorted(os.listdir(directory)):
                if not fname.endswith(".py"):
                    continue
                path = os.path.normpath(os.path.join(directory, fname))
                if path in seen:
                    continue
                seen.add(path)
                modules.append((_get_module_display_name(path), path))
        except OSError as e:
            logger.warning("Cannot read external modules dir %s: %s", directory, e)

    modules.sort(key=lambda x: x[0].lower())
    return modules


# ---------------------------------------------------------------------------
# Child-process entry point (module-level so it is picklable on Windows)
# ---------------------------------------------------------------------------

class _SilencingEvent:
    """Wraps a multiprocessing.Event so that stdout/stderr are silenced
    (via _silence_child_terminal) the moment the module calls event.set().
    This guarantees the terminal is quiet before the parent wakes up,
    even if the module never calls set_child_mode() itself.
    """
    __slots__ = ("_ev",)

    def __init__(self, ev):
        self._ev = ev

    def set(self):
        try:
            from ikabot.helpers.process import _silence_child_terminal
            _silence_child_terminal()
        except Exception:
            pass
        self._ev.set()

    def wait(self, timeout=None):
        return self._ev.wait(timeout)

    def is_set(self):
        return self._ev.is_set()

    def clear(self):
        self._ev.clear()


def _run_external_module_child(path, session, event, stdin_fd, predetermined_input):
    """Spawned child: load and execute an external module from disk."""
    if sys.platform != "win32":
        try:
            sys.stdin = open("/dev/tty", "r")
        except Exception:
            sys.stdin = os.fdopen(stdin_fd)
    else:
        sys.stdin = os.fdopen(stdin_fd)

    config.predetermined_input = predetermined_input

    name = os.path.basename(path).replace(".py", "")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Prefer an explicit MODULE_ENTRY attribute, fall back to filename stem
    fn_name = getattr(module, "MODULE_ENTRY", None) or name
    fn = getattr(module, fn_name)
    # Wrap the event so terminal is silenced the instant the module signals ready
    fn(session, _SilencingEvent(event), stdin_fd, predetermined_input)


# ---------------------------------------------------------------------------
# Directory configuration UI  (option 99)
# ---------------------------------------------------------------------------

def configure_directories(session):
    while True:
        banner()
        global_dir, account_dir = get_dirs(session)

        print("  Configure External Module Directories")
        print("  =====================================\n")
        gd_str = global_dir if global_dir else bcolors.WARNING + "not set" + bcolors.ENDC
        ad_str = account_dir if account_dir else bcolors.WARNING + "not set" + bcolors.ENDC
        print(f"  Global dir  (all accounts): {gd_str}")
        print(f"  Account dir (this account): {ad_str}\n")

        print("  (0) Back")
        print("  (1) Set global directory")
        print("  (2) Set account directory")
        print("  (3) Clear global directory")
        print("  (4) Clear account directory")

        choice = read(min=0, max=4, digit=True)

        if choice == 0:
            return
        elif choice == 1:
            banner()
            print("Enter the full path to the global external modules directory:")
            path = read(msg="Path: ").strip()
            if not path:
                continue
            if os.path.isdir(path):
                set_global_dir(path)
                print(f"\n{bcolors.GREEN}Global directory set.{bcolors.ENDC}")
            else:
                print(f"\n{bcolors.RED}Directory not found: {path}{bcolors.ENDC}")
            enter()
        elif choice == 2:
            banner()
            print("Enter the full path to the account external modules directory:")
            path = read(msg="Path: ").strip()
            if not path:
                continue
            if os.path.isdir(path):
                set_account_dir(session, path)
                print(f"\n{bcolors.GREEN}Account directory set.{bcolors.ENDC}")
            else:
                print(f"\n{bcolors.RED}Directory not found: {path}{bcolors.ENDC}")
            enter()
        elif choice == 3:
            set_global_dir("")
            print(f"\n{bcolors.GREEN}Global directory cleared.{bcolors.ENDC}")
            enter()
        elif choice == 4:
            set_account_dir(session, "")
            print(f"\n{bcolors.GREEN}Account directory cleared.{bcolors.ENDC}")
            enter()
