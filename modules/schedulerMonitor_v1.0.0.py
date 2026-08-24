#! /usr/bin/env python3
# -*- coding: utf-8 -*-

MODULE_NAME  = "Scheduler Monitor"
MODULE_ENTRY = "schedulerMonitor"

import glob
import importlib.util
import json
import multiprocessing
import os
import re
import sys
import threading
import time
import traceback

import ikabot.config as config
from ikabot.helpers.botComm import notificationDataIsValid, sendToBot
from ikabot.helpers.gui import banner, bcolors, enter
from ikabot.helpers.pedirInfo import read
from ikabot.helpers.process import set_child_mode, updateProcessList
from ikabot.helpers.signals import setInfoSignal
from ikabot.helpers.varios import getDateTime, wait

try:
    from ikabot.helpers.modulePrefs import (
        load_prefs as _mp_load_prefs,
        offer_autostart as _mp_offer_autostart,
        save_prefs as _mp_save_prefs,
    )
    _HAS_MODULE_PREFS = True
except ImportError:
    _HAS_MODULE_PREFS = False

try:
    import psutil
except ImportError:
    psutil = None

_VERSION = "1.0.0"

# Tunables ------------------------------------------------------------------
_DEFAULT_INTERVAL_MINUTES = 15
_MIN_INTERVAL_MINUTES     = 1
_MAX_INTERVAL_MINUTES     = 1440
_START_CONFIRM_SECONDS    = 30    # how long to wait for a started worker's lock
_RESTART_COOLDOWN_SECONDS = 300   # never re-launch the same worker faster
_MONITOR_LOCK_STALE_SECONDS = 300
_HEARTBEAT_SECONDS        = 60    # sleep chunk between lock heartbeats

# Marker handed to the spawned child through predetermined_input.  It is the
# only channel the external-module launcher gives us, and going through that
# launcher is what keeps the spawn picklable on Windows.
_HANDOFF_PREFIX = "schedulerMonitor.worker:"


# ---------------------------------------------------------------------------
# Scheduler registry — add a dict here to monitor another module
# ---------------------------------------------------------------------------

_TARGETS = [
    {
        "key":       "construction",
        "label":     "Construction Manager — build worker",
        "pattern":   "constructionManager*.py",
        "alias":     "_schedmon_construction",
        "entry":     "constructionManager",
        "proc_name": "constructionManager",
    },
    {
        "key":       "transport",
        "label":     "Resource Transport Manager — scheduler",
        "pattern":   "resourceTransportManager*.py",
        "alias":     "_schedmon_transport",
        "entry":     "resourceTransportManager",
        "proc_name": "resourceTransportManager",
    },
    {
        "key":       "recruit_units",
        "label":     "Auto Recruitment Manager — units worker",
        "pattern":   "autoRecruitmentManager*.py",
        "alias":     "_schedmon_recruit",
        "entry":     "autoRecruitmentManager",
        "proc_name": "autoRecruitmentManager (units)",
    },
    {
        "key":       "recruit_ships",
        "label":     "Auto Recruitment Manager — ships worker",
        "pattern":   "autoRecruitmentManager*.py",
        "alias":     "_schedmon_recruit",
        "entry":     "autoRecruitmentManager",
        "proc_name": "autoRecruitmentManager (ships)",
    },
]

_TARGETS_BY_KEY = {t["key"]: t for t in _TARGETS}
_DEFAULT_ENABLED = [t["key"] for t in _TARGETS]

_STDIN_FD = 0


def _target(key):
    return _TARGETS_BY_KEY[key]


# ---------------------------------------------------------------------------
# Sibling module loading — same preference order as constructionManager's
# load_rtm(): unversioned first, then the highest version suffix.
# ---------------------------------------------------------------------------

_MODULE_CACHE = {}
_MODULE_CACHE_LOCK = threading.Lock()


def _version_key(path):
    m = re.search(r"_v(\d+)\.(\d+)(?:\.(\d+))?\.py$", os.path.basename(path))
    if not m:
        return (0, 0, 0)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def _load_sibling(pattern, alias):
    """Import a module file sitting next to this one, or return None."""
    with _MODULE_CACHE_LOCK:
        if alias in _MODULE_CACHE:
            return _MODULE_CACHE[alias]
        here = os.path.dirname(os.path.abspath(__file__))
        candidates = glob.glob(os.path.join(here, pattern))
        if not candidates:
            return None
        base = pattern.replace("*.py", ".py")
        unversioned = [c for c in candidates
                       if os.path.basename(c) == base]
        if unversioned:
            path = unversioned[0]
        else:
            path = max(candidates,
                       key=lambda c: (_version_key(c), os.path.getmtime(c)))
        spec = importlib.util.spec_from_file_location(alias, path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MODULE_CACHE[alias] = mod
        return mod


def _module_for(key):
    tgt = _target(key)
    return _load_sibling(tgt["pattern"], tgt["alias"])


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

def _load_settings(session):
    settings = {"enabled": list(_DEFAULT_ENABLED),
                "interval_minutes": _DEFAULT_INTERVAL_MINUTES,
                "notify": True}
    if not _HAS_MODULE_PREFS:
        return settings
    try:
        saved = _mp_load_prefs(session, MODULE_NAME) or {}
        enabled = saved.get("enabled")
        if isinstance(enabled, list):
            settings["enabled"] = [k for k in enabled if k in _TARGETS_BY_KEY]
        interval = int(saved.get("interval_minutes",
                                 _DEFAULT_INTERVAL_MINUTES))
        if _MIN_INTERVAL_MINUTES <= interval <= _MAX_INTERVAL_MINUTES:
            settings["interval_minutes"] = interval
        settings["notify"] = bool(saved.get("notify", True))
    except Exception:
        pass
    return settings


def _save_settings(session, settings):
    if not _HAS_MODULE_PREFS:
        return
    try:
        _mp_save_prefs(session, MODULE_NAME, {
            "enabled": list(settings["enabled"]),
            "interval_minutes": int(settings["interval_minutes"]),
            "notify": bool(settings["notify"]),
        })
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Monitor's own lock — two monitors would race to start the same worker
# ---------------------------------------------------------------------------

def _safe(value):
    return re.sub(r"[^\w.-]", "_", str(value))


def _account_suffix(session):
    world = _safe(getattr(session, "mundo", "") or "")
    servidor = _safe(getattr(session, "servidor", "") or "")
    username = _safe(getattr(session, "username", "") or "")
    return f"{servidor}{world}_{username}"


def _monitor_lock_path(session):
    return os.path.join(
        os.path.expanduser("~"),
        f".ikabot_schedmon_{_account_suffix(session)}.lock",
    )


def _monitor_lock_is_fresh(session):
    try:
        with open(_monitor_lock_path(session), "r") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    try:
        held_at = float(data.get("timestamp", 0) or 0)
    except (TypeError, ValueError):
        return False
    if time.time() - held_at > _MONITOR_LOCK_STALE_SECONDS:
        return False
    try:
        pid = int(data.get("pid"))
    except (TypeError, ValueError):
        return True
    if psutil is None:
        return True
    try:
        return psutil.pid_exists(pid)
    except Exception:
        return True


def _monitor_heartbeat(session):
    path = _monitor_lock_path(session)
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w") as f:
            f.write(json.dumps({"pid": os.getpid(), "timestamp": time.time()}))
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _monitor_lock_release(session):
    try:
        with open(_monitor_lock_path(session), "r") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    if data.get("pid") != os.getpid():
        return
    try:
        os.remove(_monitor_lock_path(session))
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Probing — is this scheduler alive, and does it have anything to do?
# ---------------------------------------------------------------------------

def _is_running(session, key):
    mod = _module_for(key)
    if mod is None:
        return False
    if key == "construction":
        return bool(mod._is_worker_running(session))
    if key == "transport":
        return bool(mod._is_transport_worker_running(session))
    return bool(mod._is_worker_running(session, key == "recruit_units"))


def _probe(session, key):
    """Return the live state of one scheduler.

    installed / running / work  drive the decision; note and stopping are for
    the dashboard and the restart message.
    """
    state = {"installed": False, "running": False, "work": False,
             "note": "not installed", "stopping": False, "error": ""}
    try:
        mod = _module_for(key)
    except Exception as exc:
        state["error"] = str(exc)
        state["note"] = "could not be loaded"
        return state
    if mod is None:
        return state

    state["installed"] = True
    try:
        if key == "construction":
            state["running"] = bool(mod._is_worker_running(session))
            pending = mod.csv_count_pending(session)
            state["work"] = pending > 0
            state["note"] = (f"{pending} queued build(s)" if pending
                             else "queue empty")
            state["stopping"] = os.path.exists(mod.stop_flag_path(session))
        elif key == "transport":
            state["running"] = bool(mod._is_transport_worker_running(session))
            counts = mod.transport_csv_count_by_status(session)
            waiting = counts.get("active", 0) + counts.get("pending", 0)
            state["work"] = waiting > 0
            state["note"] = (f"{waiting} schedule(s)" if waiting
                             else "no schedules")
            state["stopping"] = os.path.exists(
                mod.transport_stop_flag_path(session))
        else:
            is_units = key == "recruit_units"
            state["running"] = bool(mod._is_worker_running(session, is_units))
            state["work"] = bool(mod.csv_has_active_orders(session, is_units))
            state["note"] = ("goals active" if state["work"]
                             else "no active goals")
            state["stopping"] = os.path.exists(mod._stop_flag_path(session))
    except Exception as exc:
        state["error"] = str(exc)
        state["note"] = "could not be read"
    return state


# ---------------------------------------------------------------------------
# Activation — spawn the module's worker without any prompting
# ---------------------------------------------------------------------------

class _Once:
    """event.set() must happen exactly once, whichever path the child takes."""

    def __init__(self, event):
        self._event = event
        self._done = False

    def set(self):
        if not self._done:
            self._done = True
            self._event.set()

    def wait(self, timeout=None):
        return self._event.wait(timeout)

    def is_set(self):
        return self._event.is_set()

    def clear(self):
        self._event.clear()


def _worker_child_entry(path, session, event, stdin_fd, predetermined_input):
    """Fallback child bootstrap when ikabot's external launcher is missing."""
    config.predetermined_input = predetermined_input
    schedulerMonitor(session, event, stdin_fd, predetermined_input)


def _child_launch(session, event, key):
    """Return (target, args) for the worker process.

    ikabot's own external-module launcher is preferred: it is importable by
    name, so multiprocessing can pickle it on Windows, and it detaches the
    child from the shared console the moment the worker signals it is ready.
    """
    handoff = [_HANDOFF_PREFIX + json.dumps({"target": key})]
    path = os.path.abspath(__file__)
    try:
        from ikabot.function.externalModules import _run_external_module_child
        return (_run_external_module_child,
                (path, session, event, _STDIN_FD, handoff))
    except Exception:
        return (_worker_child_entry,
                (path, session, event, _STDIN_FD, handoff))


def _activate(session, key):
    """Launch the worker for *key* and wait until its lock proves it is up."""
    tgt = _target(key)
    event = multiprocessing.Event()
    target, args = _child_launch(session, event, key)
    process = multiprocessing.Process(target=target, args=args,
                                      name=tgt["proc_name"])
    process.start()
    try:
        updateProcessList(session, programprocesslist=[{
            "pid": process.pid,
            "action": tgt["proc_name"],
            "date": time.time(),
            "status": "started",
        }])
    except Exception:
        pass

    deadline = time.time() + _START_CONFIRM_SECONDS
    while time.time() < deadline:
        wait(2)
        try:
            if _is_running(session, key):
                return True
        except Exception:
            pass
        if not process.is_alive():
            return False
    return False


# ---------------------------------------------------------------------------
# Worker child — runs one module's scheduler loop, asking nothing
# ---------------------------------------------------------------------------

def _default_notif_config(session):
    try:
        telegram = bool(notificationDataIsValid(session))
    except Exception:
        telegram = False
    return {"level": "partial" if telegram else "none", "telegram": telegram}


def _rtm_log_path(session):
    """The transport log path RTM already remembers — never prompt for it."""
    fallback = os.path.join(os.path.expanduser("~"), "shipment_log.csv")
    try:
        mod = _module_for("transport")
        if mod is None:
            return fallback
        return mod.load_prefs().get("log_path", "") or fallback
    except Exception:
        return fallback


def _worker_construction(session, event):
    mod = _module_for("construction")
    if mod is None or mod._is_worker_running(session):
        event.set()
        return
    if not mod.migrate_legacy_account_files(session):
        event.set()
        return
    if not mod.enforce_schema_or_abort(session):
        event.set()
        return
    if mod.csv_count_pending(session) == 0:
        event.set()
        return

    wlock = mod.worker_lock_path(session)
    token = mod._new_lock_token()
    if not mod._lock_acquire(wlock, timeout=5,
                             stale_after=mod.WORKER_LOCK_STALE_SECONDS,
                             token=token):
        event.set()
        return

    mod.WORKER_PREFS["notif_config"] = _default_notif_config(session)
    mod.WORKER_PREFS["log_path"] = _rtm_log_path(session)
    try:
        os.remove(mod.stop_flag_path(session))
    except OSError:
        pass

    set_child_mode(session)
    event.set()
    setInfoSignal(session, "\nConstruction worker (Scheduler Monitor)\n")
    try:
        mod.scheduler_loop(session, threading.Event(), worker_token=token)
    except Exception:
        try:
            sendToBot(session, "Construction worker crashed:\n{}".format(
                traceback.format_exc()))
        except Exception:
            pass
    finally:
        mod._lock_release(wlock, token)
        try:
            session.logout()
        except Exception:
            pass


def _worker_recruitment(session, event, is_units):
    mod = _module_for("recruit_units" if is_units else "recruit_ships")
    if mod is None or mod._is_worker_running(session, is_units):
        event.set()
        return
    if not mod.csv_has_active_orders(session, is_units):
        event.set()
        return

    try:
        with open(mod._worker_lock_path(session, is_units), "w") as f:
            json.dump({"pid": os.getpid(), "timestamp": time.time()}, f)
    except OSError:
        event.set()
        return

    try:
        os.remove(mod._stop_flag_path(session))
    except OSError:
        pass
    mod._consume_wake_flag(session, is_units)
    cfg = mod.load_config(session)

    set_child_mode(session)
    event.set()
    kind = "units" if is_units else "ships"
    setInfoSignal(session,
                  f"\nAuto Recruitment ({kind}) (Scheduler Monitor)\n")
    try:
        mod.execute_recruitment_loop(session, is_units, cfg,
                                     stop_event=threading.Event())
    except Exception:
        try:
            sendToBot(session, "Auto Recruitment Manager crashed:\n{}".format(
                traceback.format_exc()))
        except Exception:
            pass
    finally:
        mod._release_worker_lock(session, is_units)
        mod._consume_wake_flag(session, is_units)
        try:
            os.remove(mod._stop_flag_path(session))
        except OSError:
            pass
        if getattr(mod, "RRS_AVAILABLE", False):
            try:
                mod.release_all_for_module(session, mod.MODULE_NAME)
            except Exception:
                pass
        try:
            session.logout()
        except Exception:
            pass


def _worker_autostart(session, event, stdin_fd, key):
    """Start a module through its own headless auto-start path."""
    mod = _module_for(key)
    if mod is None:
        event.set()
        return
    entry = getattr(mod, "MODULE_ENTRY", None) or _target(key)["entry"]
    fn = getattr(mod, entry, None)
    if fn is None:
        event.set()
        return
    config.autostart_active = True
    fn(session, event, stdin_fd, [])


def _take_handoff(predetermined_input):
    try:
        items = list(predetermined_input or [])
    except Exception:
        return None
    if not items or not isinstance(items[0], str):
        return None
    if not items[0].startswith(_HANDOFF_PREFIX):
        return None
    try:
        return json.loads(items[0][len(_HANDOFF_PREFIX):])
    except ValueError:
        return None


def _run_worker_child(session, event, stdin_fd, handoff):
    # The monitor is already in the background, so nothing this child prints
    # belongs on the user's terminal — detach before touching the modules.
    try:
        from ikabot.helpers.process import detach_console
        detach_console()
    except Exception:
        pass

    once = _Once(event)
    key = handoff.get("target")
    try:
        if key == "construction":
            _worker_construction(session, once)
        elif key == "transport":
            _worker_autostart(session, once, stdin_fd, key)
        elif key in ("recruit_units", "recruit_ships"):
            _worker_recruitment(session, once, key == "recruit_units")
        else:
            once.set()
    except Exception:
        try:
            sendToBot(session, "Scheduler Monitor could not start {}:\n{}".format(
                key, traceback.format_exc()))
        except Exception:
            pass
        once.set()


# ---------------------------------------------------------------------------
# The monitoring pass
# ---------------------------------------------------------------------------

def _check_target(session, key, last_start, notify):
    """Check one scheduler, restarting it if it is down. Returns a report line."""
    tgt = _target(key)
    state = _probe(session, key)

    if not state["installed"]:
        return f"{tgt['label']}: {state['note']}"
    if state["running"]:
        return f"{tgt['label']}: running"
    if state["stopping"]:
        return f"{tgt['label']}: stopping — left alone"
    if state["error"]:
        return f"{tgt['label']}: {state['note']} ({state['error']})"
    if not state["work"]:
        return f"{tgt['label']}: idle, {state['note']}"

    since = time.time() - last_start.get(key, 0)
    if since < _RESTART_COOLDOWN_SECONDS:
        return (f"{tgt['label']}: down, waiting "
                f"{int(_RESTART_COOLDOWN_SECONDS - since)}s before retrying")

    last_start[key] = time.time()
    started = _activate(session, key)
    if started:
        line = f"{tgt['label']}: was stopped — restarted ({state['note']})"
    else:
        line = f"{tgt['label']}: was stopped — restart FAILED ({state['note']})"
    if notify:
        try:
            sendToBot(session, f"Scheduler Monitor\n{line}")
        except Exception:
            pass
    return line


def _run_pass(session, settings, last_start):
    lines = []
    for key in [t["key"] for t in _TARGETS if t["key"] in settings["enabled"]]:
        try:
            lines.append(_check_target(session, key, last_start,
                                       settings["notify"]))
        except Exception:
            lines.append(f"{_target(key)['label']}: check failed")
            try:
                sendToBot(session, "Scheduler Monitor check failed for {}:\n{}".format(
                    key, traceback.format_exc()))
            except Exception:
                pass
    return lines


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

def _status_cell(state):
    if not state["installed"]:
        return f"{bcolors.WARNING}not installed{bcolors.ENDC}"
    if state["error"]:
        return f"{bcolors.RED}error{bcolors.ENDC}"
    if state["running"]:
        return f"{bcolors.GREEN}RUNNING{bcolors.ENDC}"
    if state["work"]:
        return f"{bcolors.RED}STOPPED{bcolors.ENDC}"
    return f"{bcolors.WARNING}stopped{bcolors.ENDC}"


def _print_dashboard(session, settings):
    print("╔══════════════════════════════════════════════════╗")
    print("║              SCHEDULER MONITOR                   ║")
    print("╚══════════════════════════════════════════════════╝")
    print(f"  v{_VERSION}\n")
    print(f"  Check every : {settings['interval_minutes']} minute(s)")
    notify = (f"{bcolors.GREEN}on{bcolors.ENDC}" if settings["notify"]
              else f"{bcolors.WARNING}off{bcolors.ENDC}")
    print(f"  Notify      : {notify}\n")

    for pos, tgt in enumerate(_TARGETS, start=1):
        state = _probe(session, tgt["key"])
        mark = ("ON " if tgt["key"] in settings["enabled"] else "off")
        colour = bcolors.GREEN if mark == "ON " else bcolors.WARNING
        note = ("" if not state["installed"]
                else f"  {bcolors.WARNING}{state['note']}{bcolors.ENDC}")
        print(f"  ({pos}) [{colour}{mark}{bcolors.ENDC}] {tgt['label']:<44}"
              f"{_status_cell(state)}{note}")

    print("")
    print(f"  {bcolors.BOLD}(1-{len(_TARGETS)}){bcolors.ENDC} Toggle monitoring for that scheduler")
    print(f"  {bcolors.BOLD}(a){bcolors.ENDC} Monitor all      "
          f"{bcolors.BOLD}(n){bcolors.ENDC} Monitor none")
    print(f"  {bcolors.BOLD}(t){bcolors.ENDC} Change check interval")
    print(f"  {bcolors.BOLD}(m){bcolors.ENDC} Toggle notifications")
    print(f"  {bcolors.BOLD}(r){bcolors.ENDC} Check now (one pass, stay here)")
    print(f"  {bcolors.BOLD}(s){bcolors.ENDC} Start monitoring in the background")
    print(f"  {bcolors.BOLD}('){bcolors.ENDC} Back")


def _menu(session, settings):
    """Returns True to start monitoring, False to leave."""
    last_start = {}
    while True:
        banner()
        _print_dashboard(session, settings)
        choice = read(min=1, max=len(_TARGETS),
                      additionalValues=["'", "a", "A", "n", "N", "t", "T",
                                        "m", "M", "r", "R", "s", "S"])

        if isinstance(choice, int):
            key = _TARGETS[choice - 1]["key"]
            if key in settings["enabled"]:
                settings["enabled"].remove(key)
            else:
                settings["enabled"].append(key)
            _save_settings(session, settings)
            continue

        letter = choice.lower()
        if letter == "'":
            return False
        if letter == "a":
            settings["enabled"] = list(_DEFAULT_ENABLED)
            _save_settings(session, settings)
        elif letter == "n":
            settings["enabled"] = []
            _save_settings(session, settings)
        elif letter == "t":
            banner()
            print(f"How often should the schedulers be checked?")
            print(f"  ({_MIN_INTERVAL_MINUTES}-{_MAX_INTERVAL_MINUTES} minutes)")
            settings["interval_minutes"] = read(min=_MIN_INTERVAL_MINUTES,
                                                max=_MAX_INTERVAL_MINUTES,
                                                digit=True,
                                                msg="Minutes: ")
            _save_settings(session, settings)
        elif letter == "m":
            settings["notify"] = not settings["notify"]
            _save_settings(session, settings)
        elif letter == "r":
            banner()
            if not settings["enabled"]:
                print("Nothing is being monitored — turn a scheduler on first.")
            else:
                print("Checking...\n")
                for line in _run_pass(session, settings, last_start):
                    print(f"  {line}")
            enter()
        elif letter == "s":
            if not settings["enabled"]:
                banner()
                print(f"{bcolors.RED}Nothing is being monitored.{bcolors.ENDC}")
                print("Turn at least one scheduler on first.")
                enter()
                continue
            _save_settings(session, settings)
            return True


# ---------------------------------------------------------------------------
# Background loop
# ---------------------------------------------------------------------------

def _sleep_until_next_check(session, seconds):
    """Sleep in chunks so the monitor lock never looks stale while alive."""
    remaining = seconds
    while remaining > 0:
        _monitor_heartbeat(session)
        chunk = min(_HEARTBEAT_SECONDS, remaining)
        wait(chunk)
        remaining -= chunk


def _do_it(session, settings):
    last_start = {}
    interval = settings["interval_minutes"] * 60
    while True:
        # The whole point of this module is to still be here when something
        # else has died, so a bad pass must never take the monitor down.
        try:
            _monitor_heartbeat(session)
            lines = _run_pass(session, settings, last_start)
            restarted = sum(1 for line in lines if "restarted" in line)
            next_check = getDateTime(
                int(time.time()) + interval)[11:16].replace("-", ":")
            session.setStatus(
                f"Scheduler Monitor: {len(settings['enabled'])} watched, "
                f"{restarted} restarted this pass, next check {next_check}")
        except Exception:
            try:
                sendToBot(session, "Scheduler Monitor pass failed:\n{}".format(
                    traceback.format_exc()))
            except Exception:
                pass
        _sleep_until_next_check(session, interval)


def _start_monitor(session, event, settings):
    if _monitor_lock_is_fresh(session):
        event.set()
        return

    set_child_mode(session)
    event.set()
    _monitor_heartbeat(session)

    watched = ", ".join(_target(k)["label"] for k in settings["enabled"])
    setInfoSignal(session, f"\nScheduler Monitor — every "
                           f"{settings['interval_minutes']} min\n  {watched}\n")
    try:
        _do_it(session, settings)
    except Exception:
        try:
            sendToBot(session, "Scheduler Monitor stopped:\n{}".format(
                traceback.format_exc()))
        except Exception:
            pass
    finally:
        _monitor_lock_release(session)
        try:
            session.logout()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def schedulerMonitor(session, event, stdin_fd, predetermined_input):
    global _STDIN_FD
    _STDIN_FD = stdin_fd

    handoff = _take_handoff(predetermined_input)
    if handoff is not None:
        config.predetermined_input = []
        _run_worker_child(session, event, stdin_fd, handoff)
        return

    sys.stdin = os.fdopen(stdin_fd)
    config.predetermined_input = predetermined_input

    settings = _load_settings(session)

    if getattr(config, "autostart_active", False):
        if not settings["enabled"]:
            event.set()
            return
        _start_monitor(session, event, settings)
        return

    try:
        if not _menu(session, settings):
            event.set()
            return
        if _monitor_lock_is_fresh(session):
            banner()
            print(f"{bcolors.WARNING}A Scheduler Monitor is already running "
                  f"for this account.{bcolors.ENDC}")
            enter()
            event.set()
            return
        if _HAS_MODULE_PREFS:
            _mp_offer_autostart(session, MODULE_NAME)
        enter()
    except KeyboardInterrupt:
        event.set()
        return

    _start_monitor(session, event, settings)
