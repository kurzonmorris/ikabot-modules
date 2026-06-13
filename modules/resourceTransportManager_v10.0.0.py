#! /usr/bin/env python3
# -*- coding: utf-8 -*-

import traceback
import time
import datetime
import json
import math
import os
import random
import re
import csv
import tempfile
import threading
import pathlib
import sys

from ikabot.config import *
from ikabot.helpers.botComm import *
from ikabot.helpers.getJson import getCity, getIsland
from ikabot.helpers.gui import *
from ikabot.helpers.pedirInfo import *
from ikabot.helpers.planRoutes import executeRoutes
from ikabot.helpers.process import set_child_mode
from ikabot.helpers.signals import setInfoSignal
from ikabot.helpers.naval import getAvailableShips, getAvailableFreighters
from ikabot.helpers.varios import addThousandSeparator, getDateTime

# ---------------------------------------------------------------------------
#  Resource Reservation System — optional dependency
# ---------------------------------------------------------------------------
try:
    from resourceReservationSystem import (
        reserve as rrs_reserve,
        release as rrs_release,
        release_all_for_module as rrs_release_all_for_module,
        update_reservation as rrs_update_reservation,
        get_summary as rrs_get_summary,
        get_reservation_snapshot as rrs_get_reservation_snapshot,
        is_city_excluded as rrs_is_city_excluded,
        get_excluded_cities as rrs_get_excluded_cities,
        get_config as rrs_get_config,
    )
    RRS_AVAILABLE = True
except ImportError:
    RRS_AVAILABLE = False

MODULE_NAME = "resourceTransportManager"


# ============================================================================
#  ANSI COLOUR  (with Windows cmd.exe fallback)
# ============================================================================

def _ansi_supported():
    if os.name == "nt":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            handle = k32.GetStdHandle(-11)
            mode = ctypes.c_ulong()
            if k32.GetConsoleMode(handle, ctypes.byref(mode)):
                k32.SetConsoleMode(handle, mode.value | 0x0004)
                return True
        except Exception:
            pass
        return os.environ.get("WT_SESSION") is not None
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

_USE_COLOUR = _ansi_supported()

class C:
    """Colour codes. All resolve to empty string when ANSI is off."""
    RESET  = "\033[0m"  if _USE_COLOUR else ""
    BOLD   = "\033[1m"  if _USE_COLOUR else ""
    DIM    = "\033[2m"  if _USE_COLOUR else ""
    CYAN   = "\033[36m" if _USE_COLOUR else ""
    GREEN  = "\033[32m" if _USE_COLOUR else ""
    YELLOW = "\033[33m" if _USE_COLOUR else ""
    RED    = "\033[31m" if _USE_COLOUR else ""
    WHITE  = "\033[97m" if _USE_COLOUR else ""
    BLUE   = "\033[34m" if _USE_COLOUR else ""
    HEADER = "\033[1;36m" if _USE_COLOUR else ""  # bold cyan
    OK     = "\033[1;32m" if _USE_COLOUR else ""   # bold green
    WARN   = "\033[1;33m" if _USE_COLOUR else ""   # bold yellow
    ERR    = "\033[1;31m" if _USE_COLOUR else ""    # bold red
    HINT   = "\033[2;37m" if _USE_COLOUR else ""    # dim white


# ============================================================================
#  RRS HELPERS  (no-op when RRS is not installed)
# ============================================================================

def _rrs_usable_amount(session, city, resource_index, amount_needed):
    """How much of resource_index can we take from city, respecting reservations?
    Falls back to raw availableResources when RRS is not installed."""
    actual = city["availableResources"][resource_index]
    if not RRS_AVAILABLE:
        return min(actual, amount_needed)
    if rrs_is_city_excluded(session, int(city["id"])):
        return 0
    available, _ = rrs_get_reservation_snapshot(
        session, int(city["id"]), resource_index, actual
    )
    min_take = rrs_get_config(session)["min_ship_capacity"]
    if available < min_take:
        return 0
    return min(available, amount_needed)


def _rrs_free_amount(session, city, resource_index):
    """How much of resource_index is free (unreserved) in city?
    Falls back to raw availableResources when RRS is not installed."""
    actual = city["availableResources"][resource_index]
    if not RRS_AVAILABLE:
        return actual
    if rrs_is_city_excluded(session, int(city["id"])):
        return 0
    available, _ = rrs_get_reservation_snapshot(
        session, int(city["id"]), resource_index, actual
    )
    return available


def _rrs_load_summary(session):
    """Load {city_id: {res_idx: reserved}} once for multi-city scans.
    Returns empty dict when RRS is not installed."""
    if not RRS_AVAILABLE:
        return {}
    return rrs_get_summary(session)


def _rrs_free_from_summary(summary, city_id, resource_index, actual):
    """Compute free amount using a pre-loaded summary dict."""
    try:
        cid = int(city_id)
    except (TypeError, ValueError):
        return actual
    reserved = summary.get(cid, {}).get(resource_index, 0)
    return max(0, actual - reserved)


def _rrs_is_excluded(session, city_id):
    """Check if a city is excluded. Returns False when RRS not installed."""
    if not RRS_AVAILABLE:
        return False
    return rrs_is_city_excluded(session, int(city_id))


def _rrs_excluded_set(session):
    """Return set of excluded city IDs. Empty set when RRS not installed."""
    if not RRS_AVAILABLE:
        return set()
    return {e["city_id"] for e in rrs_get_excluded_cities(session)}


def _rrs_reserve(session, city_id, city_name, resource_index, amount,
                 reason, duration_seconds):
    """Create a reservation. No-op when RRS not installed. Returns rid or None."""
    if not RRS_AVAILABLE or amount <= 0:
        return None
    return rrs_reserve(
        session,
        city_id=int(city_id),
        city_name=str(city_name),
        resource_index=resource_index,
        amount=amount,
        module_name=MODULE_NAME,
        reason=reason,
        release_at=time.time() + duration_seconds,
    )


def _rrs_release(session, reservation_id):
    """Release a single reservation. No-op when RRS not installed."""
    if not RRS_AVAILABLE or reservation_id is None:
        return
    rrs_release(session, reservation_id, MODULE_NAME)


def _rrs_release_all(session):
    """Release all reservations owned by this module. No-op when RRS not installed."""
    if not RRS_AVAILABLE:
        return 0
    return rrs_release_all_for_module(session, MODULE_NAME)


def _rrs_min_ship_capacity(session):
    """Get minimum usable amount from RRS config. Defaults to 0 without RRS."""
    if not RRS_AVAILABLE:
        return 0
    return rrs_get_config(session).get("min_ship_capacity", 500)


# ============================================================================
#  BANNER
# ============================================================================

def print_module_banner(page_title=None):
    bar = "\u2550" * 58
    rule = "\u2500" * 58
    print("\n")
    print(f"{C.HEADER}\u2554{bar}\u2557")
    print(f"\u2551          RESOURCE TRANSPORT MANAGER v10.0.0                 \u2551")
    print(f"\u255a{bar}\u255d{C.RESET}")
    if page_title:
        print(f"\n{C.BOLD}{page_title}{C.RESET}")
        print(f"{C.DIM}{rule}{C.RESET}")
    print("")


# ============================================================================
#  NOTIFICATION CONFIG  (replaces overloaded telegram_enabled)
# ============================================================================

def get_notification_config(telegram_enabled, event, preset=None):
    global _NOTIF_PRESET
    active_preset = preset if preset is not None else _NOTIF_PRESET
    if active_preset is not None:
        return active_preset

    if telegram_enabled is False:
        print_module_banner()
        print(f"  {C.WARN}Telegram is not set up.{C.RESET}")
        print(f"  Continue without notifications? {C.BOLD}[Y/n]{C.RESET}")
        rta = read(values=["y", "Y", "n", "N", ""])
        if rta.lower() == "n":
            event.set()
            return None
        return {"level": "none", "telegram": False}

    print_module_banner("Telegram Notifications")
    print(f"  How much do you want to be notified?\n")
    print(f"  {C.BOLD}(1){C.RESET} Partial — summary at start of each cycle + errors")
    print(f"  {C.BOLD}(2){C.RESET} All — a message for every shipment sent")
    print(f"  {C.BOLD}(3){C.RESET} Problems only — silent unless something goes wrong")
    print(f"  {C.BOLD}(4){C.RESET} None — no notifications at all")
    print(f"  {C.BOLD}('){C.RESET} Back")
    choice = read(min=1, max=4, digit=True, additionalValues=["'"])
    if choice == "'":
        event.set()
        return None
    levels = {1: "partial", 2: "all", 3: "problems", 4: "none"}
    return {"level": levels[choice], "telegram": True}


def should_notify(notif_config, event_type):
    if not notif_config or not notif_config.get("telegram"):
        return False
    level = notif_config.get("level", "none")
    if level == "none":
        return False
    if level == "all":
        return True
    if level == "partial":
        return event_type in ("start", "error", "complete")
    if level == "problems":
        return event_type in ("error",)
    return False


# ============================================================================
#  SHIPMENT LOG
# ============================================================================

LOG_COLUMNS = [
    "Date", "Time", "Account", "Mode", "Source_City", "Source_Island",
    "Dest_City", "Dest_Island", "Dest_Player",
    "Wood", "Wine", "Marble", "Crystal", "Sulphur", "Total_Resources",
    "Ships_Used", "Ship_Type", "Status", "Error", "Next_Shipment",
]

PREFS_FILE = os.path.join(os.path.expanduser("~"), ".ikabot_rtm_prefs.json")


def load_prefs():
    """Load saved preferences (log path, CSV path, etc.)."""
    try:
        if os.path.isfile(PREFS_FILE):
            with open(PREFS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def save_prefs(prefs):
    """Save preferences to disk."""
    try:
        with open(PREFS_FILE, "w", encoding="utf-8") as f:
            json.dump(prefs, f, indent=2)
    except Exception:
        pass


def get_log_path(session):
    """Get path to the shared shipment log file.
    Remembers the last-used path so you can just press Enter next time.
    """
    prefs = load_prefs()
    saved = prefs.get("log_path", "")
    fallback = os.path.join(os.path.expanduser("~"), "shipment_log.csv")
    default_path = saved if saved else fallback

    if saved:
        # Already have a saved path — use it silently
        print(f"  {C.DIM}Shipment log:{C.RESET} {default_path}")
        return default_path
    # First time — ask the user
    print(f"  {C.DIM}Shipment log records every shipment to a CSV file.{C.RESET}")
    print(f"  {C.DIM}Press Enter to use the default path:{C.RESET}")
    print(f"  {C.CYAN}{default_path}{C.RESET}")
    print(f"  {C.HINT}All accounts share one file — each row has an Account column.{C.RESET}")
    user_path = read(msg="Log path: ", empty=True)
    chosen = user_path.strip() if user_path.strip() else default_path
    prefs["log_path"] = chosen
    save_prefs(prefs)
    return chosen


def log_shipment(log_path, session, mode, source_city, source_island,
                 dest_city, dest_island, dest_player, resources,
                 ships_used, ship_type, status, error_msg=None,
                 next_shipment=None):
    if not log_path:
        return
    try:
        file_exists = os.path.isfile(log_path)
        now = datetime.datetime.now()
        row = {
            "Date": now.strftime("%Y-%m-%d"),
            "Time": now.strftime("%H:%M:%S"),
            "Account": session.username,
            "Mode": mode,
            "Source_City": source_city,
            "Source_Island": source_island,
            "Dest_City": dest_city,
            "Dest_Island": dest_island,
            "Dest_Player": dest_player,
            "Wood": resources[0] if len(resources) > 0 else 0,
            "Wine": resources[1] if len(resources) > 1 else 0,
            "Marble": resources[2] if len(resources) > 2 else 0,
            "Crystal": resources[3] if len(resources) > 3 else 0,
            "Sulphur": resources[4] if len(resources) > 4 else 0,
            "Total_Resources": sum(resources),
            "Ships_Used": ships_used,
            "Ship_Type": ship_type,
            "Status": status,
            "Error": error_msg or "",
            "Next_Shipment": next_shipment or "",
        }
        with open(log_path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=LOG_COLUMNS)
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)
    except Exception:
        pass  # never crash the main script for a logging failure


# ============================================================================
#  LOCK FILE  (atomic create — fixes race condition)
# ============================================================================

def get_lock_file_path(session, use_freighters=False):
    ship_type = "freighters" if use_freighters else "merchant_ships"
    safe_server = session.servidor.replace("/", "_").replace("\\", "_")
    safe_username = session.username.replace("/", "_").replace("\\", "_")
    lock_filename = f".ikabot_shared_{ship_type}_{safe_server}_{safe_username}.lock"
    return os.path.join(os.path.expanduser("~"), lock_filename)


def acquire_shipping_lock(session, use_freighters=False, timeout=300):
    lock_file = get_lock_file_path(session, use_freighters)
    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                lock_data = json.dumps({
                    "pid": os.getpid(),
                    "timestamp": time.time(),
                    "ship_type": "freighters" if use_freighters else "merchant_ships",
                    "server": session.servidor,
                    "username": session.username,
                })
                os.write(fd, lock_data.encode())
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            try:
                with open(lock_file, "r") as f:
                    lock_data = json.load(f)
                    if time.time() - lock_data.get("timestamp", 0) > 600:
                        os.remove(lock_file)
                        continue
            except (json.JSONDecodeError, KeyError, IOError):
                try:
                    os.remove(lock_file)
                except Exception:
                    pass
                continue
        except Exception:
            pass
        time.sleep(5)
    return False


def release_shipping_lock(session, use_freighters=False):
    lock_file = get_lock_file_path(session, use_freighters)
    try:
        if os.path.exists(lock_file):
            try:
                with open(lock_file, "r") as f:
                    lock_data = json.load(f)
                    if lock_data.get("pid") == os.getpid():
                        os.remove(lock_file)
            except Exception:
                try:
                    os.remove(lock_file)
                except Exception:
                    pass
    except Exception:
        pass


# ============================================================================
#  TRANSPORT SCHEDULE CSV  — persistent state for all shipping modes
# ============================================================================

SCHEDULE_SCHEMA_VERSION = 2

SCHEDULE_COLUMNS = [
    "schedule_id",
    "mode",
    "ship_type",
    "source_city_ids",
    "dest_city_ids",
    "resource_config",
    "send_mode",
    "dest_targets",
    "source_reserves",
    "dest_minimums",
    "bulk_csv_path",
    "bulk_run_column",
    "ap_max_wait_minutes",
    "min_shipment_threshold",
    "interval_hours",
    "run_at_time",
    "notif_level",
    "status",
    "last_run",
    "next_run",
    "total_shipments",
    "created_at",
    "notes",
    "schema_version",
]

SCHEDULE_INT_COLS = {
    "schedule_id", "interval_hours", "total_shipments",
    "created_at", "schema_version", "ap_max_wait_minutes",
    "min_shipment_threshold",
}
SCHEDULE_INT_OR_BLANK_COLS = {"last_run", "next_run"}
SCHEDULE_JSON_COLS = {
    "source_city_ids", "dest_city_ids", "resource_config",
    "dest_targets", "source_reserves", "dest_minimums",
}
VALID_SCHEDULE_MODES = (
    "consolidate", "distribute", "even", "autosend", "bulk", "topup",
)
VALID_SCHEDULE_STATUSES = (
    "pending", "active", "paused", "completed", "error",
)

WORKER_LOCK_STALE_SECONDS = 600   # 10 min — stale lock threshold
TICK_BUDGET_SECONDS = 60          # max sleep between scheduler checks
TRANSPORT_WORKER_PREFS = {}       # runtime state shared with worker process
_NOTIF_PRESET = None              # user's notification preset (set via (n) menu)


def _safe(value):
    return re.sub(r'[^\w.-]', '_', str(value))


def _account_suffix(session):
    return f"{_safe(session.servidor)}_{_safe(session.username)}"


def transport_csv_path(session):
    return os.path.join(
        os.path.expanduser("~"),
        f".ikabot_transport_{_account_suffix(session)}.csv",
    )


def transport_csv_lock_path(session):
    return os.path.join(
        os.path.expanduser("~"),
        f".ikabot_transport_{_account_suffix(session)}.lock",
    )


def transport_schema_sidecar_path(session):
    return os.path.join(
        os.path.expanduser("~"),
        f".ikabot_transport_{_account_suffix(session)}.schema",
    )


def transport_worker_lock_path(session):
    return os.path.join(
        os.path.expanduser("~"),
        f".ikabot_transport_worker_{_account_suffix(session)}.lock",
    )


def transport_stop_flag_path(session):
    return os.path.join(
        os.path.expanduser("~"),
        f".ikabot_transport_stop_{_account_suffix(session)}",
    )


def transport_wake_flag_path(session):
    return os.path.join(
        os.path.expanduser("~"),
        f".ikabot_transport_wake_{_account_suffix(session)}",
    )


def _touch_wake_flag(session):
    try:
        with open(transport_wake_flag_path(session), "w") as f:
            f.write(str(int(time.time())))
    except OSError:
        pass


def _consume_wake_flag(session):
    path = transport_wake_flag_path(session)
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass
        return True
    return False


# --- Lock helpers (reusable for both shipping lock and CSV lock) ---

def _lock_acquire(lock_path, timeout=30, stale_after=60):
    start = time.time()
    my_payload = json.dumps({
        "pid": os.getpid(),
        "timestamp": time.time(),
    }).encode()
    while time.time() - start < timeout:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, my_payload)
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            try:
                with open(lock_path, "r") as f:
                    data = json.load(f)
                lock_pid = data.get("pid")
                stale_by_time = time.time() - data.get("timestamp", 0) > stale_after
                stale_by_pid = lock_pid and not _is_pid_alive(lock_pid)
                if stale_by_time or stale_by_pid:
                    tmp = lock_path + f".{os.getpid()}.tmp"
                    try:
                        fd2 = os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                        try:
                            os.write(fd2, my_payload)
                        finally:
                            os.close(fd2)
                        os.replace(tmp, lock_path)
                        with open(lock_path, "r") as f2:
                            check = json.load(f2)
                        if check.get("pid") == os.getpid():
                            return True
                    except Exception:
                        try:
                            os.remove(tmp)
                        except OSError:
                            pass
            except (json.JSONDecodeError, IOError, OSError):
                pass
        except Exception:
            pass
        time.sleep(1)
    return False


def _lock_refresh(lock_path):
    """Rewrite the lock file with a fresh timestamp to prevent stale detection."""
    try:
        with open(lock_path, "w") as f:
            json.dump({"pid": os.getpid(), "timestamp": time.time()}, f)
    except Exception:
        pass


def _lock_release(lock_path):
    try:
        if os.path.exists(lock_path):
            with open(lock_path, "r") as f:
                data = json.load(f)
            if data.get("pid") == os.getpid():
                os.remove(lock_path)
    except Exception:
        pass


class _transport_csv_lock:
    def __init__(self, session):
        self.path = transport_csv_lock_path(session)

    def __enter__(self):
        if not _lock_acquire(self.path, timeout=30, stale_after=60):
            raise RuntimeError(
                f"Could not acquire transport CSV lock at {self.path}"
            )
        return self

    def __exit__(self, exc_type, exc, tb):
        _lock_release(self.path)


# --- Schema enforcement ---

def enforce_transport_schema_or_abort(session):
    sidecar = transport_schema_sidecar_path(session)
    if not os.path.exists(sidecar):
        with open(sidecar, "w", encoding="utf-8") as f:
            json.dump({
                "version": SCHEDULE_SCHEMA_VERSION,
                "columns": SCHEDULE_COLUMNS,
            }, f)
        return True
    try:
        with open(sidecar, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        print("Cannot read transport schema sidecar.")
        return False
    on_disk = int(data.get("version", -1))
    if on_disk == SCHEDULE_SCHEMA_VERSION:
        return True
    if on_disk == 1 and SCHEDULE_SCHEMA_VERSION == 2:
        csv_file = transport_csv_path(session)
        if os.path.exists(csv_file):
            try:
                rows = []
                with open(csv_file, newline="", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        row.setdefault("run_at_time", "")
                        rows.append(row)
                write_csv_atomic(csv_file, SCHEDULE_COLUMNS, rows)
            except Exception as e:
                print(f"  {C.WARN}Migration failed: {e}{C.RESET}")
                return False
        with open(sidecar, "w", encoding="utf-8") as f:
            json.dump({
                "version": SCHEDULE_SCHEMA_VERSION,
                "columns": SCHEDULE_COLUMNS,
            }, f)
        return True
    print(f"Schedule data format has changed (was v{on_disk}, now v{SCHEDULE_SCHEMA_VERSION}).")
    print(f"  Your existing schedules can't be loaded with this version.")
    print(f"  To start fresh, delete or rename these two files:")
    print(f"    {transport_csv_path(session)}")
    print(f"    {sidecar}")
    print(f"  WARNING: This will erase all saved schedules.")
    return False


# --- Row coercion ---

def _coerce_schedule_in(raw):
    row = dict(raw)
    for col in SCHEDULE_INT_COLS:
        v = row.get(col, "")
        try:
            row[col] = int(v) if str(v) != "" else 0
        except (TypeError, ValueError):
            row[col] = 0
    for col in SCHEDULE_INT_OR_BLANK_COLS:
        v = row.get(col, "")
        if v in ("", None):
            row[col] = ""
        else:
            try:
                row[col] = int(v)
            except (TypeError, ValueError):
                row[col] = ""
    for col in SCHEDULE_JSON_COLS:
        v = row.get(col, "")
        if isinstance(v, str) and v.strip():
            try:
                row[col] = json.loads(v)
            except (json.JSONDecodeError, TypeError):
                row[col] = None
        elif not isinstance(v, (list, dict)):
            row[col] = None
    if row.get("mode", "") not in VALID_SCHEDULE_MODES:
        row["mode"] = ""
    if row.get("status", "") not in VALID_SCHEDULE_STATUSES:
        row["status"] = "pending"
    return row


def _coerce_schedule_out(row):
    out = {}
    for col in SCHEDULE_COLUMNS:
        v = row.get(col, "")
        if v is None:
            out[col] = ""
        elif col in SCHEDULE_JSON_COLS and isinstance(v, (list, dict)):
            out[col] = json.dumps(v)
        else:
            out[col] = str(v)
    return out


# --- CRUD operations ---

def transport_csv_load(session):
    with _transport_csv_lock(session):
        try:
            with open(transport_csv_path(session), "r",
                       newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                return [_coerce_schedule_in(r) for r in reader]
        except FileNotFoundError:
            return []


def transport_csv_save_all(session, rows):
    path = transport_csv_path(session)
    tmp = path + ".tmp"
    with _transport_csv_lock(session):
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=SCHEDULE_COLUMNS)
            w.writeheader()
            for r in rows:
                w.writerow(_coerce_schedule_out(r))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)


def _transport_csv_modify(session, fn):
    path = transport_csv_path(session)
    tmp = path + ".tmp"
    with _transport_csv_lock(session):
        try:
            with open(path, "r", newline="", encoding="utf-8") as f:
                rows = [_coerce_schedule_in(r) for r in csv.DictReader(f)]
        except FileNotFoundError:
            rows = []
        fn(rows)
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=SCHEDULE_COLUMNS)
            w.writeheader()
            for r in rows:
                w.writerow(_coerce_schedule_out(r))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)


def transport_csv_append(session, row):
    _transport_csv_modify(session, lambda rows: rows.append(row))
    _touch_wake_flag(session)


def transport_csv_delete(session, schedule_id):
    sid = int(schedule_id)
    def _delete(rows):
        rows[:] = [r for r in rows if r["schedule_id"] != sid]
    _transport_csv_modify(session, _delete)


def transport_csv_update(session, schedule_id, **fields):
    sid = int(schedule_id)
    def _apply(rows):
        for r in rows:
            if r["schedule_id"] == sid:
                for k, v in fields.items():
                    r[k] = v
                break
    _transport_csv_modify(session, _apply)


def next_schedule_id(rows):
    if not rows:
        return 1
    return max(r.get("schedule_id", 0) for r in rows) + 1


def transport_csv_count_active(session):
    rows = transport_csv_load(session)
    return sum(1 for r in rows if r.get("status") == "active")


def transport_csv_count_by_status(session):
    rows = transport_csv_load(session)
    counts = {}
    for r in rows:
        s = r.get("status", "pending")
        counts[s] = counts.get(s, 0) + 1
    return counts


def build_schedule_row(schedule_id, mode, ship_type="m",
                       source_city_ids=None, dest_city_ids=None,
                       resource_config=None, send_mode="na",
                       dest_targets=None, source_reserves=None,
                       dest_minimums=None, bulk_csv_path="",
                       bulk_run_column="", ap_max_wait_minutes=120,
                       min_shipment_threshold=0, interval_hours=0,
                       run_at_time="",
                       notif_level="none", status="pending",
                       notes=""):
    now_ts = int(time.time())
    if run_at_time:
        first_run = _next_run_for_time(run_at_time)
    else:
        first_run = now_ts
    return {
        "schedule_id":     schedule_id,
        "mode":            mode,
        "ship_type":       ship_type,
        "source_city_ids": source_city_ids or [],
        "dest_city_ids":   dest_city_ids or [],
        "resource_config": resource_config or [0, 0, 0, 0, 0],
        "send_mode":       send_mode,
        "dest_targets":    dest_targets or {},
        "source_reserves": source_reserves or {},
        "dest_minimums":   dest_minimums or [0, 0, 0, 0, 0],
        "bulk_csv_path":   bulk_csv_path,
        "bulk_run_column": bulk_run_column,
        "ap_max_wait_minutes": ap_max_wait_minutes,
        "min_shipment_threshold": min_shipment_threshold,
        "interval_hours":  interval_hours,
        "run_at_time":     run_at_time,
        "notif_level":     notif_level,
        "status":          status,
        "last_run":        "",
        "next_run":        first_run,
        "total_shipments": 0,
        "created_at":      now_ts,
        "notes":           notes,
        "schema_version":  SCHEDULE_SCHEMA_VERSION,
    }


def _next_run_for_time(time_str):
    """Calculate the next Unix timestamp for a given HH:MM time string (server time).
    If the time has already passed today, returns tomorrow's occurrence."""
    try:
        hour, minute = int(time_str.split(":")[0]), int(time_str.split(":")[1])
    except (ValueError, IndexError):
        return int(time.time())
    now = datetime.datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    return int(target.timestamp())


def _get_schedule_timing(event, mode_name):
    """Shared timing setup for all modes. Returns (interval_hours, run_at_time)
    or None if user cancels."""
    print_module_banner(f"{mode_name} — Schedule Timing")
    print(f"  {C.DIM}When should this run?{C.RESET}\n")
    print(f"  {C.BOLD}(1){C.RESET} One-time (send once and done)")
    print(f"  {C.BOLD}(2){C.RESET} Repeat every X hours")
    print(f"  {C.BOLD}(3){C.RESET} Daily at a specific time (server time)")
    print(f"  {C.BOLD}('){C.RESET} Back")
    choice = read(min=1, max=3, digit=True, additionalValues=["'"])
    if choice == "'":
        event.set()
        return None

    if choice == 1:
        return (0, "")

    if choice == 2:
        print(f"\n  Repeat every how many hours?")
        hours = read(min=1, digit=True, additionalValues=["'"])
        if hours == "'":
            event.set()
            return None
        print(f"  {C.CYAN}Repeats every {hours} hour(s){C.RESET}")
        return (int(hours), "")

    print(f"\n  {C.DIM}All times use server time to avoid timezone issues.{C.RESET}")
    print(f"  Enter time in HH:MM format (24h, e.g. 14:30):")
    while True:
        time_input = read(msg="  Time: ", additionalValues=["'"])
        if time_input == "'":
            event.set()
            return None
        match = re.match(r'^(\d{1,2}):(\d{2})$', time_input.strip())
        if match:
            h, m = int(match.group(1)), int(match.group(2))
            if 0 <= h <= 23 and 0 <= m <= 59:
                time_str = f"{h:02d}:{m:02d}"
                print(f"  {C.CYAN}Runs daily at {time_str} server time{C.RESET}")
                return (24, time_str)
        print(f"  {C.HINT}Invalid format. Use HH:MM (e.g. 06:00, 14:30, 23:00){C.RESET}")


# ============================================================================
#  SHIP HELPERS
# ============================================================================

def getShipCapacity(session):
    try:
        html = session.post("view=merchantNavy")
        ship_capacity = int(
            html.split('singleTransporterCapacity":')[1]
            .split(',"singleFreighterCapacity')[0]
        )
        freighter_capacity = int(
            html.split('singleFreighterCapacity":')[1]
            .split(',"draftEffect')[0]
        )
        return ship_capacity, freighter_capacity
    except Exception:
        raise Exception(
            "Could not read ship capacity from game server — "
            "the response format may have changed"
        )


def wait_for_ships(session, useFreighters, status_prefix="", max_wait=3600):
    ship_type = "freighters" if useFreighters else "merchant ships"
    start = time.time()
    while True:
        available = (
            getAvailableFreighters(session) if useFreighters
            else getAvailableShips(session)
        )
        if available > 0:
            session.setStatus(
                f"{status_prefix}Found {available} {ship_type}"
            )
            return available
        elapsed = int(time.time() - start)
        if elapsed > max_wait:
            session.setStatus(
                f"{status_prefix}Timed out waiting for {ship_type} ({elapsed}s)"
            )
            return 0
        session.setStatus(
            f"{status_prefix}Waiting for {ship_type}... ({elapsed}s)"
        )
        time.sleep(20 + random.randint(-5, 5))


def getActionPoints(html):
    """Parse action points from full page HTML (global menu element)."""
    match = re.search(r'js_GlobalMenu_maxActionPoints"[^>]*>(\d+)<', html)
    if match:
        return int(match.group(1))
    return None


def _extract_action_request(html):
    m = re.search(r'"actionRequest"\s*:\s*"([^"]+)"', html)
    return m.group(1) if m else ""


def wait_for_action_points(session, origin_city_id, status_prefix="",
                           max_wait=1800):
    """Navigate to source city and wait until action points are available.
    Returns the AP count (>0) on success, 0 on timeout, None if unparseable."""
    start = time.time()
    while True:
        html = session.get()
        current = getCity(html)
        if str(current["id"]) != str(origin_city_id):
            ar = _extract_action_request(html)
            session.post(params={
                "action": "header",
                "function": "changeCurrentCity",
                "actionRequest": ar,
                "oldView": "city",
                "cityId": origin_city_id,
                "backgroundView": "city",
                "currentCityId": current["id"],
                "ajax": "1",
            })
            html = session.get()

        ap = getActionPoints(html)
        if ap is None:
            return None
        if ap > 0:
            return ap

        elapsed = int(time.time() - start)
        if elapsed > max_wait:
            return 0
        session.setStatus(
            f"{status_prefix}Waiting for action points (0 available, "
            f"{elapsed}s elapsed)..."
        )
        time.sleep(60 + random.randint(-5, 5))


# ============================================================================
#  CITY STATUS CHECKS  (occupation, port blockade)
# ============================================================================

def _is_city_occupied(html):
    """Detect if a city is under enemy occupation from page HTML."""
    if re.search(r'"occupier"\s*:', html):
        return True
    if re.search(r'"isOccupied"\s*:\s*true', html, re.IGNORECASE):
        return True
    if re.search(r'id="?occupation', html, re.IGNORECASE):
        return True
    return False


def _is_port_blockaded(html):
    """Detect if a city's port is blockaded from page HTML."""
    if re.search(r'"blockade"\s*:\s*["\d{]', html):
        return True
    if re.search(r'"isBlockaded"\s*:\s*true', html, re.IGNORECASE):
        return True
    if re.search(r'id="?blockade', html, re.IGNORECASE):
        return True
    if re.search(r'portBlock', html):
        return True
    return False


def _check_city_status(session, city_id):
    """Fetch city page and check for occupation/blockade.
    Returns (occupied: bool, blockaded: bool)."""
    html = session.get(city_url + str(city_id))
    return _is_city_occupied(html), _is_port_blockaded(html)


# ============================================================================
#  SHARED SEND SHIPMENT  (lock → verify → send → verify → unlock → log)
# ============================================================================

def send_shipment(session, route, useFreighters, notif_config, log_path,
                  mode_name, dest_island_coords="", dest_player="",
                  max_lock_retries=3, next_shipment_str=None,
                  min_threshold=0):
    origin_city = route[0]
    dest_city = route[1]
    resources = list(route[3:])
    total_cargo = sum(resources)
    ship_type_name = "freighters" if useFreighters else "merchant ships"
    prefix = f"{origin_city['name']} -> {dest_city['name']} | "

    result = {"success": False, "error": None, "ships_used": 0,
              "no_ap": False, "below_threshold": False,
              "city_unavailable": False, "shortfalls": {}}

    if min_threshold > 0 and total_cargo < min_threshold:
        result["below_threshold"] = True
        result["error"] = (
            f"Below minimum ({total_cargo:,} < {min_threshold:,})"
        )
        return result

    # 0. Check source city for occupation / blockade
    src_occ, src_block = _check_city_status(session, origin_city["id"])
    if src_occ:
        result["city_unavailable"] = True
        result["error"] = f"{origin_city['name']} is occupied by enemy"
        if should_notify(notif_config, "error"):
            sendToBot(session,
                      f"SHIPMENT SKIPPED\n{prefix}\n{result['error']}")
        log_shipment(log_path, session, mode_name,
                     origin_city["name"], "", dest_city["name"],
                     dest_island_coords, dest_player, resources,
                     0, ship_type_name, "SKIPPED", result["error"],
                     next_shipment_str)
        return result
    if src_block:
        result["city_unavailable"] = True
        result["error"] = f"{origin_city['name']} port is blockaded"
        if should_notify(notif_config, "error"):
            sendToBot(session,
                      f"SHIPMENT SKIPPED\n{prefix}\n{result['error']}")
        log_shipment(log_path, session, mode_name,
                     origin_city["name"], "", dest_city["name"],
                     dest_island_coords, dest_player, resources,
                     0, ship_type_name, "SKIPPED", result["error"],
                     next_shipment_str)
        return result

    # 0b. Check destination city for occupation / blockade
    dest_occ, dest_block = _check_city_status(session, dest_city["id"])
    if dest_occ:
        result["city_unavailable"] = True
        result["error"] = f"{dest_city['name']} (dest) is occupied by enemy"
        if should_notify(notif_config, "error"):
            sendToBot(session,
                      f"SHIPMENT SKIPPED\n{prefix}\n{result['error']}")
        log_shipment(log_path, session, mode_name,
                     origin_city["name"], "", dest_city["name"],
                     dest_island_coords, dest_player, resources,
                     0, ship_type_name, "SKIPPED", result["error"],
                     next_shipment_str)
        return result
    if dest_block:
        result["city_unavailable"] = True
        result["error"] = f"{dest_city['name']} (dest) port is blockaded"
        if should_notify(notif_config, "error"):
            sendToBot(session,
                      f"SHIPMENT SKIPPED\n{prefix}\n{result['error']}")
        log_shipment(log_path, session, mode_name,
                     origin_city["name"], "", dest_city["name"],
                     dest_island_coords, dest_player, resources,
                     0, ship_type_name, "SKIPPED", result["error"],
                     next_shipment_str)
        return result

    # 1. Wait for ships (with timeout)
    available = wait_for_ships(session, useFreighters, prefix)
    if available == 0:
        result["error"] = f"No {ship_type_name} available (timed out)"
        if should_notify(notif_config, "error"):
            sendToBot(session,
                      f"SHIPMENT SKIPPED\n{prefix}\n{result['error']}")
        log_shipment(log_path, session, mode_name,
                     origin_city["name"], "", dest_city["name"],
                     dest_island_coords, dest_player, resources,
                     0, ship_type_name, "SKIPPED", result["error"],
                     next_shipment_str)
        return result

    # 1b. Check action points on source city (quick check, no long wait)
    ap = wait_for_action_points(session, origin_city["id"], prefix,
                                max_wait=30)
    if ap == 0:
        result["no_ap"] = True
        result["error"] = (
            f"No action points for {origin_city['name']}"
        )
        return result

    # 2. Acquire lock with retries
    lock_acquired = False
    for attempt in range(1, max_lock_retries + 1):
        session.setStatus(
            f"{prefix}Acquiring lock ({attempt}/{max_lock_retries})..."
        )
        if acquire_shipping_lock(session, use_freighters=useFreighters,
                                 timeout=300):
            lock_acquired = True
            break
        if attempt < max_lock_retries:
            if should_notify(notif_config, "error"):
                sendToBot(session,
                          f"Lock attempt {attempt}/{max_lock_retries} "
                          f"failed for {prefix}Retrying in 60s...")
            time.sleep(60)

    if not lock_acquired:
        result["error"] = (
            f"Could not acquire shipping lock after "
            f"{max_lock_retries} attempts"
        )
        if should_notify(notif_config, "error"):
            sendToBot(session,
                      f"SHIPMENT FAILED\n{prefix}\n{result['error']}")
        log_shipment(log_path, session, mode_name,
                     origin_city["name"], "", dest_city["name"],
                     dest_island_coords, dest_player, resources,
                     0, ship_type_name, "FAILED", result["error"],
                     next_shipment_str)
        return result

    # 3. Lock held — ALWAYS release in finally
    try:
        ships_before = (
            getAvailableFreighters(session) if useFreighters
            else getAvailableShips(session)
        )
        if ships_before == 0:
            result["error"] = "Ships became unavailable before sending"
            if should_notify(notif_config, "error"):
                sendToBot(session,
                          f"SHIPMENT DELAYED\n{prefix}\n{result['error']}")
            log_shipment(log_path, session, mode_name,
                         origin_city["name"], "", dest_city["name"],
                         dest_island_coords, dest_player, resources,
                         0, ship_type_name, "DELAYED", result["error"],
                         next_shipment_str)
            return result

        # 3a. Re-check source city for resource exhaustion
        try:
            html_recheck = session.get(city_url + str(origin_city["id"]))
            src_fresh = getCity(html_recheck)
            shortfalls = {}
            adjusted = list(resources)
            for i in range(min(len(adjusted), 5)):
                if adjusted[i] <= 0:
                    continue
                actual_avail = src_fresh["availableResources"][i]
                if actual_avail < adjusted[i]:
                    shortfalls[i] = adjusted[i] - actual_avail
                    adjusted[i] = max(0, actual_avail)
            if shortfalls:
                result["shortfalls"] = shortfalls
                resources = adjusted
                total_cargo = sum(resources)
                route = (origin_city, dest_city, route[2], *resources)
                if total_cargo == 0:
                    result["success"] = True
                    result["ships_used"] = 0
                    log_shipment(log_path, session, mode_name,
                                 origin_city["name"], "", dest_city["name"],
                                 dest_island_coords, dest_player, resources,
                                 0, ship_type_name, "EXHAUSTED",
                                 "All planned resources exhausted at source",
                                 next_shipment_str)
                    return result
        except Exception:
            pass

        session.setStatus(f"{prefix}Sending resources...")
        executeRoutes(session, [route], useFreighters)

        # If executeRoutes completes without error, the shipment was sent.
        # We do NOT verify by comparing ship counts before/after because
        # ships from earlier shipments can return during sending, making
        # the count unreliable and causing false "failure" reports.
        ship_cap, freighter_cap = getShipCapacity(session)
        capacity = freighter_cap if useFreighters else ship_cap
        ships_needed = math.ceil(total_cargo / capacity) if capacity > 0 else 0

        result["success"] = True
        result["ships_used"] = ships_needed

        res_desc = ", ".join(
            f"{addThousandSeparator(resources[i])} {materials_names[i]}"
            for i in range(len(materials_names))
            if i < len(resources) and resources[i] > 0
        )
        if should_notify(notif_config, "all"):
            sendToBot(session,
                      f"SHIPMENT SENT\nAccount: {session.username}\n"
                      f"From: {origin_city['name']}\n"
                      f"To: {dest_island_coords} {dest_city['name']}\n"
                      f"Ships: {ships_needed} {ship_type_name}\n"
                      f"Sent: {res_desc}")

        log_shipment(log_path, session, mode_name,
                     origin_city["name"], "", dest_city["name"],
                     dest_island_coords, dest_player, resources,
                     ships_needed, ship_type_name, "SENT",
                     next_shipment=next_shipment_str)

    except Exception as e:
        result["error"] = str(e)
        if should_notify(notif_config, "error"):
            sendToBot(session,
                      f"SHIPMENT FAILED\nAccount: {session.username}\n"
                      f"From: {origin_city['name']}\n"
                      f"To: {dest_island_coords} {dest_city['name']}\n"
                      f"Error: {result['error']}")
        log_shipment(log_path, session, mode_name,
                     origin_city["name"], "", dest_city["name"],
                     dest_island_coords, dest_player, resources,
                     0, ship_type_name, "FAILED", result["error"],
                     next_shipment_str)
    finally:
        release_shipping_lock(session, use_freighters=useFreighters)

    return result


# ============================================================================
#  UTILITY FUNCTIONS
# ============================================================================

def normalize_text(value):
    return str(value or "").strip().lower()


def parse_run_column_datetime(column_name):
    if not column_name.startswith("Run_"):
        return None
    try:
        return datetime.datetime.strptime(column_name[4:], "%Y-%m-%d_%H-%M-%S")
    except Exception:
        return None


def build_run_column_name(dt=None):
    if dt is None:
        dt = datetime.datetime.now()
    return f"Run_{dt.strftime('%Y-%m-%d_%H-%M-%S')}"


def write_csv_atomic(csv_path, fieldnames, rows):
    directory = os.path.dirname(os.path.abspath(csv_path)) or "."
    fd, tmp_path = tempfile.mkstemp(
        prefix=".tmp_bulkdist_", suffix=".csv", dir=directory
    )
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as tmp_file:
            writer = csv.DictWriter(tmp_file, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_path, csv_path)
    except Exception:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise


def ensure_run_columns(fieldnames, rows, max_slots=7):
    run_columns = [c for c in fieldnames if c.startswith("Run_")]
    missing = max_slots - len(run_columns)
    if missing > 0:
        for i in range(missing):
            old_dt = datetime.datetime(1970, 1, 1, 0, 0, i)
            col = build_run_column_name(old_dt)
            while col in fieldnames:
                old_dt += datetime.timedelta(seconds=1)
                col = build_run_column_name(old_dt)
            fieldnames.append(col)
            run_columns.append(col)
            for row in rows:
                row[col] = ""
    for rc in run_columns:
        issues_col = rc.replace("Run_", "Issues_", 1)
        if issues_col not in fieldnames:
            fieldnames.append(issues_col)
            for row in rows:
                row[issues_col] = ""
    return fieldnames, run_columns


def get_city_location_token(city_data):
    location_keys = [
        "position", "cityLocation", "location", "slot",
        "islandPosition", "citySlot", "index", "nr", "number",
    ]
    for key in location_keys:
        if key in city_data and str(city_data.get(key, "")).strip() != "":
            return str(city_data.get(key)).strip()
    return None


def ensure_transport_column(fieldnames, rows):
    """Add Transport column at position 0 if missing (backward compatibility)."""
    if "Transport" not in fieldnames:
        fieldnames.insert(0, "Transport")
        for row in rows:
            row["Transport"] = "m"
    return fieldnames


def parse_transport_value(val):
    """Parse Transport column: 'f' -> True (freighters), else False (merchant)."""
    return val.strip().lower() == "f"


def ensure_from_column(fieldnames, rows):
    """Add From column after Sulphur if missing (backward compatibility)."""
    if "From" not in fieldnames:
        insert_idx = len(fieldnames)
        if "Sulphur" in fieldnames:
            insert_idx = fieldnames.index("Sulphur") + 1
        else:
            for i, col in enumerate(fieldnames):
                if col in ("Hours", "Issues") or col.startswith("Run_"):
                    insert_idx = i
                    break
        fieldnames.insert(insert_idx, "From")
        for row in rows:
            row["From"] = ""
    return fieldnames


def parse_from_column(val):
    """Parse From column: '' -> None, 'a' -> 'all', '1,3,5' -> [1,3,5]."""
    val = val.strip()
    if not val:
        return None
    if val.lower() == "a":
        return "all"
    indices = []
    for part in val.split(","):
        part = part.strip()
        if part.isdigit() and int(part) >= 1:
            indices.append(int(part))
    return indices if indices else None


def get_source_cities_for_row(session, from_val, city_cache):
    """Return list of (city_index, city_obj) for a row's From value.
    city_cache is a dict with keys 'ids' and 'objects' to avoid redundant fetches."""
    if "ids" not in city_cache:
        ids, cities_map = getIdsOfCities(session)
        city_cache["ids"] = ids
        city_cache["map"] = cities_map

    ids = city_cache["ids"]

    if from_val == "all":
        target_indices = list(range(1, len(ids) + 1))
    else:
        target_indices = [i for i in from_val if 1 <= i <= len(ids)]

    result = []
    for idx in target_indices:
        city_id = ids[idx - 1]
        if city_id not in city_cache.get("objects", {}):
            html = session.get(city_url + city_id)
            city_cache.setdefault("objects", {})[city_id] = getCity(html)
        result.append((idx, city_cache["objects"][city_id]))
    return result


def issues_col_for_run(run_column):
    return run_column.replace("Run_", "Issues_", 1)


def _parse_amount(s):
    """Parse a number string with optional 'k' suffix and comma separators.
    '500' -> 500, '10k' -> 10000, '1.5k' -> 1500, '10,000' -> 10000."""
    s = s.strip().replace(",", "")
    if not s:
        return 0
    if s.lower().endswith("k"):
        num = s[:-1].strip()
        try:
            return int(float(num) * 1000)
        except ValueError:
            return 0
    try:
        return int(s)
    except ValueError:
        return 0


def parse_resource_value(val):
    """Parse a resource cell.
    Exact amounts:  '500' -> ('exact', 500),  '10k' -> ('exact', 10000)
    Send all:       'all' or 'a' -> ('except', 0)
    All-except:     'all-10k' or 'a-5000' -> ('except', 10000) / ('except', 5000)
    Legacy prefix:  'e10000' -> ('except', 10000)
    """
    val = val.strip()
    low = val.lower()
    if low in ("all", "a"):
        return ("except", 0)
    if low.startswith("all-") or low.startswith("a-"):
        rest = val.split("-", 1)[1]
        return ("except", _parse_amount(rest))
    if low.startswith("e") and not low[1:].strip().startswith("-"):
        num_part = val[1:]
        if num_part.strip():
            return ("except", _parse_amount(num_part))
        return ("except", 0)
    return ("exact", _parse_amount(val))


def resolve_resources(parsed, source_available, row, csv_resource_cols,
                      issues_key="Issues"):
    """Resolve parsed resource values against source city stock.
    'except' mode: send (available - reserve), log issue if insufficient."""
    resolved = []
    for i, (mode, amount) in enumerate(parsed):
        if mode == "except":
            avail = source_available[i] if i < len(source_available) else 0
            if avail <= amount:
                resolved.append(0)
                if row is not None:
                    prev = row.get(issues_key, "")
                    note = f"{csv_resource_cols[i]}: stock {avail} <= reserve {amount}"
                    row[issues_key] = f"{prev}; {note}" if prev else note
            else:
                resolved.append(avail - amount)
        else:
            resolved.append(amount)
    return resolved


def choose_run_slot(session, event, rows, run_columns):
    print_module_banner("Bulk Distribution — Run Slot")
    print(f"  {C.DIM}Each run tracks progress (which cities have been sent to).{C.RESET}\n")
    print(f"  {C.BOLD}(1){C.RESET} Start fresh — begin a new run")
    print(f"  {C.DIM}    Replaces the oldest run slot with a clean starting point.{C.RESET}")
    print(f"  {C.BOLD}(2){C.RESET} Resume — continue a previous run")
    print(f"  {C.DIM}    Pick up where you left off (unsent cities still pending).{C.RESET}")
    print(f"  {C.BOLD}('){C.RESET} Back")
    mode = read(min=1, max=2, digit=True, additionalValues=["'"])
    if mode == "'":
        event.set()
        return None, None

    total_rows = len(rows)
    completion = {
        col: sum(1 for r in rows if normalize_text(r.get(col, "")) == "x")
        for col in run_columns
    }

    if mode == 1:
        dated_cols = []
        for col in run_columns:
            dt = parse_run_column_datetime(col)
            if dt is None:
                dt = datetime.datetime(1970, 1, 1)
            dated_cols.append((dt, col))
        dated_cols.sort(key=lambda t: t[0])
        oldest_col = dated_cols[0][1]
        new_col = build_run_column_name()
        while new_col in run_columns:
            new_col = build_run_column_name(
                datetime.datetime.now() + datetime.timedelta(seconds=1)
            )

        print(f"Fresh run will reuse oldest slot: {oldest_col}")
        print(f"New slot name: {new_col}")
        print("Proceed? [Y/n]")
        confirm = read(
            values=["y", "Y", "n", "N", "", "'"], additionalValues=["'"]
        )
        if confirm == "'" or confirm.lower() == "n":
            event.set()
            return None, None

        for i, col in enumerate(run_columns):
            if col == oldest_col:
                run_columns[i] = new_col
                break
        old_issues = oldest_col.replace("Run_", "Issues_", 1)
        new_issues = new_col.replace("Run_", "Issues_", 1)
        for row in rows:
            row[new_col] = ""
            row[new_issues] = ""
            if oldest_col in row:
                del row[oldest_col]
            if old_issues in row:
                del row[old_issues]
        return mode, new_col

    # Resume mode
    print("")
    print(f"  {C.DIM}Select which run to continue:{C.RESET}")
    for i, col in enumerate(run_columns):
        done = completion.get(col, 0)
        print(f"  {C.BOLD}({i + 1}){C.RESET} {col[4:]}  [{done}/{total_rows} sent]")
    print(f"  {C.BOLD}('){C.RESET} Back")
    choice = read(
        min=1, max=len(run_columns), digit=True, additionalValues=["'"]
    )
    if choice == "'":
        event.set()
        return None, None
    return mode, run_columns[choice - 1]


# ============================================================================
#  RESOURCE INPUT
# ============================================================================

def readResourceAmount(resource_name):
    while True:
        user_input = read(
            msg=f"{resource_name}: ", empty=True,
            additionalValues=["'", "="]
        )
        if user_input == "'":
            return "EXIT"
        if user_input == "=":
            return "RESTART"
        if user_input == "":
            return None
        low = user_input.strip().lower()
        if low in ("all", "a"):
            print(f"  -> Send ALL")
            return "SEND_ALL"
        if low.startswith("all-") or low.startswith("a-"):
            reserve = _parse_amount(user_input.split("-", 1)[1])
            print(f"  -> Send all, keep {addThousandSeparator(reserve)}")
            return ("EXCEPT", reserve)
        amount = _parse_amount(user_input)
        if amount > 0:
            print(f"  -> Set to: {addThousandSeparator(amount)}")
            return amount
        if amount == 0:
            return None
        print(f"  {C.HINT}Examples: 500, 10k, all, a, all-10k, a-5000{C.RESET}")
        print(f"  {C.HINT}blank = skip, ' = exit, = = restart{C.RESET}")


def get_resource_config(send_mode=2):
    print(f"  {C.HINT}Resource input:  500 or 10k = exact amount{C.RESET}")
    print(f"  {C.HINT}                 all or a   = send everything{C.RESET}")
    print(f"  {C.HINT}                 all-10k    = send all, keep 10k{C.RESET}")
    print(f"  {C.HINT}                 blank      = skip resource{C.RESET}")
    print(f"  {C.HINT}                 '          = exit    =  = restart{C.RESET}\n")
    while True:
        config = []
        restart = False
        for i, resource in enumerate(materials_names):
            amount = readResourceAmount(resource)
            if amount == "EXIT":
                return None
            if amount == "RESTART":
                print("\nRestarting resource configuration...\n")
                restart = True
                break
            if amount == "SEND_ALL":
                amount = 0
            elif isinstance(amount, tuple) and amount[0] == "EXCEPT":
                config.append(("except", amount[1]))
                continue
            elif send_mode == 2 and amount is None:
                amount = 0
            config.append(amount)
        if not restart:
            return config


def get_dest_minimums():
    print("")
    print(f"  {C.DIM}Skip sending if the destination already has enough?{C.RESET}")
    print(f"  {C.DIM}If set, resources only ship when the destination is below the target.{C.RESET}\n")
    print(f"  {C.BOLD}(1){C.RESET} Yes — set a target threshold per resource")
    print(f"  {C.BOLD}(2){C.RESET} No — always send regardless")
    choice = read(min=1, max=2, digit=True)
    if choice == 2:
        return None
    print("")
    print(f"  {C.DIM}Enter the target per resource. Shipments only happen when{C.RESET}")
    print(f"  {C.DIM}the destination has less than this amount.{C.RESET}")
    print(f"  {C.HINT}blank = no limit (always send this resource){C.RESET}\n")
    minimums = []
    for resource in materials_names:
        amount = readResourceAmount(f"Target {resource}")
        if amount in ("EXIT", "RESTART", "SEND_ALL"):
            return None
        minimums.append(amount if amount is not None else 0)
    return minimums


def apply_dest_minimums(sendable, dest_current, minimum):
    if minimum is None or minimum == 0:
        return sendable
    if dest_current >= minimum:
        return 0
    needed = minimum - dest_current
    return min(sendable, needed)


def _format_resource_list(values, skip_zero=True):
    """Format a list of 5 resource values as 'Wood: 5,000 | Wine: 3,000'."""
    parts = []
    for i, res in enumerate(materials_names):
        v = values[i] if i < len(values) else 0
        if skip_zero and (v is None or v == 0):
            continue
        parts.append(f"{res}: {addThousandSeparator(v)}")
    return " | ".join(parts) if parts else "(none)"


# ============================================================================
#  CITY SELECTION HELPERS  (show full resource names + coordinates)
# ============================================================================

_TRADEGOOD_NAMES = {1: "Wine", 2: "Marble", 3: "Crystal", 4: "Sulphur"}


def _format_city_line(index, city, longest_name):
    """Format a single city line with name, resource, and coordinates."""
    name = city["name"]
    pad = " " * (longest_name - len(name) + 2)
    resource = _TRADEGOOD_NAMES.get(int(city.get("tradegood", 0)), "???")
    coords = city.get("coords", "").strip()
    return f"{index: >2}: {name}{pad}{resource:<9} {coords}"


def rtm_chooseCity(session):
    """Replacement for chooseCity that shows full resource names + coords."""
    (ids, cities) = getIdsOfCities(session)
    if not ids:
        print("No cities available!")
        return None
    longest = max(len(cities[cid]["name"]) for cid in ids)
    print("")
    for i, city_id in enumerate(ids, 1):
        print(_format_city_line(i, cities[city_id], longest))
    selected = read(min=1, max=len(ids))
    html = session.get(city_url + ids[selected - 1])
    return getCity(html)


def rtm_ignoreCities(session, msg=None, exclude_mode=False):
    """City picker. By default: positive selection (click to add).
    With exclude_mode=True: click to remove (old behavior)."""
    (all_ids, all_cities) = getIdsOfCities(session)
    selected_ids = [] if not exclude_mode else list(all_ids)
    selected_names = [] if not exclude_mode else []

    while True:
        print_module_banner()
        if msg is not None:
            print(f"{msg}")
        if not exclude_mode and selected_names:
            print(f'  Selected: {", ".join(selected_names)}')
        if exclude_mode and selected_names:
            print(f'  Excluded: {", ".join(selected_names)}')
        print("")

        longest = max(
            len(all_cities[cid]["name"]) for cid in all_ids
        ) if all_ids else 0
        for i, city_id in enumerate(all_ids, 1):
            city = all_cities[city_id]
            name = city["name"]
            pad = " " * (longest - len(name) + 2)
            resource = _TRADEGOOD_NAMES.get(
                int(city.get("tradegood", 0)), "???")
            coords = city.get("coords", "").strip()
            cid_str = str(city["id"])
            if not exclude_mode:
                marker = " [+]" if cid_str in selected_ids else ""
            else:
                marker = " [-]" if cid_str not in selected_ids else ""
            print(f"  {i}) {name}{pad}{resource:<9} {coords}{marker}")

        if not exclude_mode:
            ct = len(selected_ids)
            print(f"\n  (0) Done ({ct} selected)  |  (a) Select all")
        else:
            remaining = len(selected_ids)
            print(f"\n  (0) Done ({remaining} remaining)")

        choice = read(min=0, max=len(all_ids),
                      additionalValues=["a", "A"] if not exclude_mode else [])

        if choice == 0:
            break

        if isinstance(choice, str) and choice.lower() == "a":
            selected_ids = [str(all_cities[cid]["id"]) for cid in all_ids]
            selected_names = [all_cities[cid]["name"] for cid in all_ids]
            print(f"  All {len(selected_ids)} cities selected.")
            continue

        city_id = str(all_cities[all_ids[choice - 1]]["id"])
        city_name = all_cities[all_ids[choice - 1]]["name"]

        if not exclude_mode:
            if city_id in selected_ids:
                selected_ids.remove(city_id)
                selected_names.remove(city_name)
                print(f"  Removed: {city_name}")
            else:
                selected_ids.append(city_id)
                selected_names.append(city_name)
                print(f"  Added: {city_name}")
        else:
            if city_id in selected_ids:
                selected_ids.remove(city_id)
                selected_names.append(city_name)
                print(f"  Excluded: {city_name}")
            else:
                selected_ids.append(city_id)
                if city_name in selected_names:
                    selected_names.remove(city_name)
                print(f"  Restored: {city_name}")

    result_cities = {
        cid: all_cities[cid] for cid in all_ids
        if str(all_cities[cid]["id"]) in selected_ids
    }
    result_ids = [
        cid for cid in all_ids
        if str(all_cities[cid]["id"]) in selected_ids
    ]
    return result_ids, result_cities




# ============================================================================
#  MAIN ENTRY POINT
# ============================================================================

def _scheduler_status_line(session):
    """Return a coloured one-line scheduler status string."""
    worker_running = _is_transport_worker_running(session)
    counts = transport_csv_count_by_status(session)
    active_ct = counts.get("active", 0)
    pending_ct = counts.get("pending", 0)
    total = sum(counts.values())

    if worker_running:
        status = f"{C.OK}RUNNING{C.RESET}"
    else:
        status = f"{C.WARN}STOPPED{C.RESET}"

    parts = f"Scheduler: {status}"
    if total > 0:
        parts += f"  |  {active_ct} active"
        if pending_ct:
            parts += f", {pending_ct} pending"
        parts += f" of {total} schedule(s)"
    else:
        parts += f"  |  No schedules"
    return parts


def _clear_all_schedules(session):
    """Delete every schedule from the CSV after confirmation."""
    rows = transport_csv_load(session)
    if not rows:
        print(f"  {C.DIM}No schedules to clear.{C.RESET}")
        enter()
        return
    print(f"\n  {C.WARN}This will delete ALL {len(rows)} schedule(s).{C.RESET}")
    print(f"  Type {C.BOLD}yes{C.RESET} to confirm:")
    confirm = read(msg="  > ", additionalValues=["'", "yes", "Yes", "YES"])
    if confirm.lower() != "yes":
        print("  Cancelled.")
        enter()
        return
    transport_csv_save_all(session, [])
    print(f"  {C.OK}All schedules cleared.{C.RESET}")
    enter()


_NOTIF_LABELS = {
    "partial": "Partial",
    "all": "All",
    "problems": "Problems only",
    "none": "None",
}


def _configure_notif_preset(telegram_enabled, event):
    if telegram_enabled is False:
        print(f"\n  {C.WARN}Telegram is not set up. Cannot configure notifications.{C.RESET}")
        enter()
        return None

    print_module_banner("Notification Preset")
    print(f"  {C.DIM}This applies automatically to all new schedules.{C.RESET}\n")
    print(f"  {C.BOLD}(1){C.RESET} Partial — cycle start/complete + problems")
    print(f"  {C.BOLD}(2){C.RESET} All — a message for every shipment")
    print(f"  {C.BOLD}(3){C.RESET} Problems only — silent unless something goes wrong")
    print(f"  {C.DIM}    (skips, blockades, occupation, 0 ships, errors){C.RESET}")
    print(f"  {C.BOLD}(4){C.RESET} None — no notifications at all")
    print(f"  {C.BOLD}(0){C.RESET} Off — ask each time (default)")
    print(f"  {C.BOLD}('){C.RESET} Cancel")
    choice = read(min=0, max=4, digit=True, additionalValues=["'"])
    if choice == "'":
        return "CANCEL"
    if choice == 0:
        return None
    levels = {1: "partial", 2: "all", 3: "problems", 4: "none"}
    return {"level": levels[choice], "telegram": True}


def resourceTransportManager(session, event, stdin_fd, predetermined_input):
    sys.stdin = os.fdopen(stdin_fd)
    config.predetermined_input = predetermined_input

    try:
        telegram_enabled = checkTelegramData(session)

        print_module_banner("Shipment Log Setup")
        log_path = get_log_path(session)
        notif_preset = None

        while True:
            print_module_banner()
            print(f"  {_scheduler_status_line(session)}")
            worker_running = _is_transport_worker_running(session)
            if not worker_running:
                counts = transport_csv_count_by_status(session)
                if counts.get("active", 0) + counts.get("pending", 0) > 0:
                    print(f"  {C.WARN}Schedules exist but the scheduler is stopped."
                          f" Press (s) to start it.{C.RESET}")

            if notif_preset is not None:
                preset_label = _NOTIF_LABELS.get(
                    notif_preset.get("level", ""), "?")
                notif_display = f"{C.OK}{preset_label}{C.RESET}"
            else:
                notif_display = f"{C.DIM}ask each time{C.RESET}"

            print(f"\n  {C.BOLD}(s){C.RESET} Start scheduler   "
                  f"{C.BOLD}(o){C.RESET} Stop scheduler   "
                  f"{C.BOLD}(x){C.RESET} Clear all schedules")
            print(f"  {C.BOLD}(n){C.RESET} Notifications: {notif_display}")
            print(f"{C.DIM}  After creating a schedule (options 1-6), start the scheduler"
                  f" to run it.{C.RESET}")

            print(f"\n  {C.HEADER}── Shipping Modes ──{C.RESET}\n")
            print(f"  {C.BOLD}(1){C.RESET} Consolidate")
            print(f"  {C.DIM}    Collect resources from multiple cities into one destination.{C.RESET}")
            print(f"  {C.BOLD}(2){C.RESET} Distribute")
            print(f"  {C.DIM}    Send resources from one city out to several destinations.{C.RESET}")
            print(f"  {C.BOLD}(3){C.RESET} Even Distribution")
            print(f"  {C.DIM}    Spread a resource evenly across selected cities so each has the same.{C.RESET}")
            print(f"  {C.BOLD}(4){C.RESET} Auto Send")
            print(f"  {C.DIM}    Request a total amount — the system gathers it from all your cities.{C.RESET}")
            print(f"  {C.BOLD}(5){C.RESET} Bulk Distribution")
            print(f"  {C.DIM}    Send to many cities at once using a CSV spreadsheet file.{C.RESET}")
            print(f"  {C.BOLD}(6){C.RESET} Keep Topped Up")
            print(f"  {C.DIM}    Automatically refill cities when resources drop below a target.{C.RESET}")

            print(f"\n  {C.HEADER}── Management ──{C.RESET}\n")
            print(f"  {C.BOLD}(7){C.RESET} Manage Schedules")
            print(f"  {C.DIM}    View, edit, pause, or delete saved schedules.{C.RESET}")
            print(f"\n  {C.BOLD}('){C.RESET} Back to main menu")

            shipping_mode = read(min=1, max=7, digit=True,
                                 additionalValues=["'", "s", "S", "o", "O",
                                                    "x", "X", "n", "N"])
            if shipping_mode == "'":
                event.set()
                return

            if isinstance(shipping_mode, str):
                letter = shipping_mode.lower()
                if letter == "s":
                    _activate_transport_worker(session, event)
                    return
                elif letter == "o":
                    _stop_transport_worker(session)
                    continue
                elif letter == "x":
                    _clear_all_schedules(session)
                    continue
                elif letter == "n":
                    global _NOTIF_PRESET
                    result = _configure_notif_preset(telegram_enabled, event)
                    if result != "CANCEL":
                        notif_preset = result
                        _NOTIF_PRESET = result
                    continue

            if shipping_mode == 7:
                manage_schedules_menu(session, event, telegram_enabled,
                                      log_path)
                continue

            if shipping_mode == 1:
                consolidateMode(session, event, stdin_fd, predetermined_input,
                                telegram_enabled, log_path)
            elif shipping_mode == 2:
                distributeMode(session, event, stdin_fd, predetermined_input,
                               telegram_enabled, log_path)
            elif shipping_mode == 3:
                evenDistributionMode(session, event, stdin_fd,
                                     predetermined_input, telegram_enabled,
                                     log_path)
            elif shipping_mode == 4:
                autoSendMode(session, event, stdin_fd, predetermined_input,
                             telegram_enabled, log_path)
            elif shipping_mode == 5:
                bulkDistributionMode(session, event, stdin_fd,
                                     predetermined_input, telegram_enabled,
                                     log_path)
            elif shipping_mode == 6:
                topUpMode(session, event, stdin_fd,
                          predetermined_input, telegram_enabled,
                          log_path)
            return

    except KeyboardInterrupt:
        event.set()
        return


# ============================================================================
#  MODE 1: CONSOLIDATE  (many sources -> one destination)
# ============================================================================

def consolidateMode(session, event, stdin_fd, predetermined_input,
                    telegram_enabled, log_path):
    try:
        print_module_banner("Consolidate — Ship Type")
        print(f"  {C.DIM}Which ships carry the resources?{C.RESET}\n")
        print(f"  {C.BOLD}(1){C.RESET} Merchant ships")
        print(f"  {C.BOLD}(2){C.RESET} Freighters")
        print(f"  {C.BOLD}('){C.RESET} Back")
        shiptype = read(min=1, max=2, digit=True, additionalValues=["'"])
        if shiptype == "'":
            event.set()
            return
        useFreighters = (shiptype == 2)

        print_module_banner("Consolidate — Source Cities")
        print(f"  {C.DIM}Where do the resources come from?{C.RESET}\n")
        print(f"  {C.BOLD}(1){C.RESET} Single city")
        print(f"  {C.BOLD}(2){C.RESET} Multiple cities")
        print(f"  {C.BOLD}('){C.RESET} Back")
        source_option = read(min=1, max=2, digit=True, additionalValues=["'"])
        if source_option == "'":
            event.set()
            return

        origin_cities = []
        if source_option == 1:
            print_module_banner("Consolidate — Pick Source City")
            print(f"  {C.DIM}Choose the city to send resources from:{C.RESET}")
            origin_city = rtm_chooseCity(session)
            if origin_city is None:
                event.set()
                return
            origin_cities.append(origin_city)
        else:
            print_module_banner("Consolidate — Pick Source Cities")
            source_msg = (f"{C.DIM}Click cities to add them as sources "
                          f"(the ones sending resources):{C.RESET}")
            source_city_ids, _ = rtm_ignoreCities(session, msg=source_msg)
            if not source_city_ids:
                print(f"  {C.WARN}No cities selected!{C.RESET}")
                enter()
                event.set()
                return
            for city_id in source_city_ids:
                html = session.get(city_url + city_id)
                city = getCity(html)
                origin_cities.append(city)

        print_module_banner("Consolidate — Sending Mode")
        source_summary = ", ".join(c["name"] for c in origin_cities)
        print(f"  Source: {C.CYAN}{source_summary}{C.RESET}\n")
        print(f"  {C.DIM}How should resource amounts be calculated?{C.RESET}\n")
        print(f"  {C.BOLD}(1){C.RESET} Keep reserves — send everything except a reserve amount")
        print(f"  {C.DIM}    You set how much to keep; the rest gets shipped.{C.RESET}")
        print(f"  {C.BOLD}(2){C.RESET} Send specific — send an exact amount per resource")
        print(f"  {C.DIM}    You set exactly how much to send.{C.RESET}")
        print(f"  {C.BOLD}('){C.RESET} Back")
        send_mode = read(min=1, max=2, digit=True, additionalValues=["'"])
        if send_mode == "'":
            event.set()
            return

        print_module_banner("Consolidate — Resources")
        print(f"  Source: {C.CYAN}{source_summary}{C.RESET}\n")
        if send_mode == 1:
            print(f"  {C.DIM}Enter how much of each resource to KEEP in reserve.{C.RESET}")
            print(f"  {C.DIM}Everything above that amount will be sent.{C.RESET}\n")
            print(f"  {C.HINT}  number = keep that amount, send the rest{C.RESET}")
            print(f"  {C.HINT}  e = send ALL (keep nothing)  |  blank = skip{C.RESET}")
            print(f"  {C.HINT}  = restart  |  ' exit{C.RESET}\n")
        else:
            print(f"  {C.DIM}Enter how much of each resource to SEND.{C.RESET}\n")
            print(f"  {C.HINT}  number = send that amount  |  e = send ALL{C.RESET}")
            print(f"  {C.HINT}  blank = skip  |  = restart  |  ' exit{C.RESET}\n")
            if len(origin_cities) == 1:
                html = session.get(city_url + str(origin_cities[0]["id"]))
                cdata = getCity(html)
                for i, res in enumerate(materials_names):
                    avail = cdata["availableResources"][i]
                    print(f"  {C.CYAN}{res}:{C.RESET} {addThousandSeparator(avail)} available")
                print("")

        resource_config = get_resource_config(send_mode)
        if resource_config is None:
            event.set()
            return

        # --- Destination ---
        print_module_banner("Consolidate — Destination")
        print(f"  Source: {C.CYAN}{source_summary}{C.RESET}\n")
        print(f"  {C.DIM}Where should the resources be delivered?{C.RESET}\n")
        print(f"  {C.BOLD}(1){C.RESET} Your own city")
        print(f"  {C.BOLD}(2){C.RESET} Another player's city (enter island coordinates)")
        print(f"  {C.BOLD}('){C.RESET} Back")
        dest_type = read(min=1, max=2, digit=True, additionalValues=["'"])
        if dest_type == "'":
            event.set()
            return

        if dest_type == 2:
            # External city
            coords_done = False
            while not coords_done:
                print_module_banner("Consolidate — Island Coordinates")
                print(f"  {C.DIM}Enter the island location of the target city.{C.RESET}")
                print(f"  {C.HINT}' exit  |  = restart{C.RESET}\n")
                x_coord = read(msg="X coordinate: ", digit=True,
                               additionalValues=["'", "="])
                if x_coord == "'":
                    event.set()
                    return
                if x_coord == "=":
                    continue
                y_coord = read(msg="Y coordinate: ", digit=True,
                               additionalValues=["'", "="])
                if y_coord == "'":
                    event.set()
                    return
                if y_coord == "=":
                    continue

                html = session.get(
                    f"view=island&xcoord={x_coord}&ycoord={y_coord}"
                )
                island = getIsland(html)
                cities_on_island = [
                    c for c in island["cities"] if c["type"] == "city"
                ]
                if not cities_on_island:
                    print(f"No cities on island [{x_coord}:{y_coord}]!")
                    continue

                print(f"\nIsland: {island['name']} [{island['x']}:{island['y']}]")
                print(f"Resource: {materials_names[int(island['tradegood'])]}\n")
                print("Select destination city:")
                print(f"    {'City Name':<20} {'Player':<15}")
                print(f"    {'-'*20} {'-'*15}")
                for i, c in enumerate(cities_on_island):
                    cn = c.get("name", "?")[:20]
                    pn = c.get("Name", "?")[:15]
                    print(f"({i+1:>2}) {cn:<20} {pn:<15}")
                print("(') Back | (=) Restart\n")
                cc = read(min=0, max=len(cities_on_island),
                          additionalValues=["'", "="])
                if cc == "'" or cc == 0:
                    event.set()
                    return
                if cc == "=":
                    continue

                dest_data = cities_on_island[cc - 1]
                dest_id = dest_data["id"]
                html = session.get(city_url + str(dest_id))
                destination_city = getCity(html)
                destination_city["isOwnCity"] = (
                    dest_data.get("state", "") == ""
                    and dest_data.get("Name", "") == session.username
                )
                dest_player = dest_data.get("Name", "Unknown")

                print(f"\nSelected: {destination_city['name']} ({dest_player})")
                print("Confirm? [Y/n] (= restart)")
                conf = read(values=["y", "Y", "n", "N", "", "="])
                if conf == "=":
                    continue
                if conf.lower() == "n":
                    continue
                coords_done = True
        else:
            # Internal city
            print_module_banner("Consolidate — Pick Destination")
            print(f"  {C.DIM}Choose which of your cities will receive the resources:{C.RESET}\n")
            destination_city = rtm_chooseCity(session)
            if destination_city is None:
                event.set()
                return
            html = session.get(city_url + str(destination_city["id"]))
            destination_city = getCity(html)
            island_id = destination_city["islandId"]
            html = session.get(island_url + island_id)
            island = getIsland(html)
            destination_city["isOwnCity"] = True
            dest_player = session.username

        # Auto-exclude destination from sources
        before = len(origin_cities)
        origin_cities = [
            c for c in origin_cities if c["id"] != destination_city["id"]
        ]
        if before - len(origin_cities) > 0:
            print(f"\nExcluded destination '{destination_city['name']}' from sources")
        if not origin_cities:
            print("Error: No source cities remaining!")
            enter()
            event.set()
            return

        # Resource minimums for internal destinations
        dest_minimums = None
        if destination_city.get("isOwnCity", False):
            dest_minimums = get_dest_minimums()

        # Notifications
        notif_config = get_notification_config(telegram_enabled, event)
        if notif_config is None:
            return

        timing = _get_schedule_timing(event, "Consolidate")
        if timing is None:
            return
        interval_hours, run_at_time = timing

        total_send = [0] * len(materials_names)
        space_warnings = []
        for oc in origin_cities:
            html = session.get(city_url + str(oc["id"]))
            odata = getCity(html)
            for i in range(len(materials_names)):
                if resource_config[i] is None:
                    continue
                avail = odata["availableResources"][i]
                if send_mode == 1:
                    s = avail if resource_config[i] == 0 else max(0, avail - resource_config[i])
                else:
                    s = 0 if resource_config[i] == 0 else min(resource_config[i], avail)
                if destination_city.get("isOwnCity", False):
                    free = destination_city["freeSpaceForResources"][i]
                    if s > free:
                        space_warnings.append(
                            f"    {C.WARN}{materials_names[i]}: reduced "
                            f"{addThousandSeparator(s)} -> "
                            f"{addThousandSeparator(free)} "
                            f"(destination full){C.RESET}")
                    s = min(s, free)
                if dest_minimums:
                    s = apply_dest_minimums(
                        s, destination_city["availableResources"][i],
                        dest_minimums[i]
                    )
                total_send[i] += s

        min_threshold = 0

        # Final confirmation with dry-run option
        while True:
            print_module_banner("Consolidate — Summary")
            ship_label = "Freighters" if useFreighters else "Merchant ships"
            print(f"  {C.BOLD}Ship type:{C.RESET}   {ship_label}")
            mode_label = "Keep reserves" if send_mode == 1 else "Send specific"
            print(f"  {C.BOLD}Mode:{C.RESET}        {mode_label}")
            print(f"  {C.BOLD}Sources:{C.RESET}     {source_summary} ({len(origin_cities)})")
            print(f"  {C.BOLD}Destination:{C.RESET} {destination_city['name']}")
            if dest_minimums:
                print(f"  {C.BOLD}Send if below:{C.RESET} {_format_resource_list(dest_minimums)}")
            if run_at_time:
                int_label = f"Daily at {run_at_time} (server time)"
            elif interval_hours == 0:
                int_label = "One-time"
            else:
                int_label = f"Every {interval_hours}h"
            print(f"  {C.BOLD}Schedule:{C.RESET}    {int_label}")
            if min_threshold > 0:
                print(f"  {C.BOLD}Min ship:{C.RESET}    {min_threshold:,} total resources")
            print(f"  {C.BOLD}Total:{C.RESET}       {C.OK}{addThousandSeparator(sum(total_send))}{C.RESET} resources")
            if space_warnings:
                print(f"\n  {C.WARN}Some amounts reduced (warehouse space):{C.RESET}")
                for w in space_warnings:
                    print(w)
            print("")
            print(f"  {C.OK}(Y){C.RESET} Proceed  "
                  f"{C.CYAN}(T){C.RESET} Min shipment  "
                  f"{C.WARN}(N){C.RESET} Cancel")
            rta = read(values=["y", "Y", "n", "N", "t", "T", ""])
            if rta.lower() == "n":
                event.set()
                return
            if rta.lower() == "t":
                print(f"\n  Current minimum: "
                      f"{'off' if min_threshold == 0 else f'{min_threshold:,}'}")
                print("  Minimum total resources per shipment (0=off):")
                t_input = read(min=0, digit=True, additionalValues=["'"])
                if t_input == "'":
                    continue
                min_threshold = int(t_input)
                if min_threshold > 0:
                    print(f"  {C.OK}Shipments below {min_threshold:,} "
                          f"will be skipped{C.RESET}")
                else:
                    print(f"  {C.OK}Min shipment filter disabled{C.RESET}")
                enter()
                continue
            break

        enter()

    except KeyboardInterrupt:
        event.set()
        return

    src_names = ", ".join(c["name"] for c in origin_cities)
    schedule_row = build_schedule_row(
        schedule_id=0,
        mode="consolidate",
        ship_type="f" if useFreighters else "m",
        source_city_ids=[str(c["id"]) for c in origin_cities],
        dest_city_ids=[str(destination_city["id"])],
        resource_config=resource_config,
        send_mode="keep" if send_mode == 1 else "send",
        dest_minimums=dest_minimums or [0, 0, 0, 0, 0],
        min_shipment_threshold=min_threshold,
        interval_hours=interval_hours,
        run_at_time=run_at_time,
        notif_level=notif_config.get("level", "none"),
        notes=f"{src_names} -> {destination_city['name']}",
    )
    _save_and_maybe_activate(session, event, schedule_row, notif_config,
                             log_path)




# ============================================================================
#  MODE 2: DISTRIBUTE  (one source -> many destinations)
# ============================================================================

def distributeMode(session, event, stdin_fd, predetermined_input,
                   telegram_enabled, log_path):
    try:
        print_module_banner("Distribute — Ship Type")
        print(f"  {C.DIM}Which ships carry the resources?{C.RESET}\n")
        print(f"  {C.BOLD}(1){C.RESET} Merchant ships")
        print(f"  {C.BOLD}(2){C.RESET} Freighters")
        print(f"  {C.BOLD}('){C.RESET} Back")
        shiptype = read(min=1, max=2, digit=True, additionalValues=["'"])
        if shiptype == "'":
            event.set()
            return
        useFreighters = (shiptype == 2)

        print_module_banner("Distribute — Source City")
        print(f"  {C.DIM}Choose the city that will send resources:{C.RESET}\n")
        origin_city = rtm_chooseCity(session)
        if origin_city is None:
            event.set()
            return

        print_module_banner("Distribute — Destinations")
        print(f"  Source: {C.CYAN}{origin_city['name']}{C.RESET}")
        print(f"  {C.DIM}The source city is automatically excluded from this list.{C.RESET}\n")
        dest_msg = (f"{C.DIM}Click cities to add them as destinations "
                    f"(the ones receiving resources):{C.RESET}")
        dest_ids, _ = rtm_ignoreCities(session, msg=dest_msg)

        src_id = str(origin_city["id"])
        if src_id in dest_ids:
            dest_ids.remove(src_id)
            print(f"  {C.DIM}Removed {origin_city['name']} from destinations (it's the source){C.RESET}")

        if not dest_ids:
            print(f"  {C.WARN}No destination cities selected!{C.RESET}")
            enter()
            event.set()
            return

        destination_cities = []
        for cid in dest_ids:
            html = session.get(city_url + cid)
            destination_cities.append(getCity(html))

        print_module_banner("Distribute — Resources")
        dest_summary = ", ".join(c["name"] for c in destination_cities)
        print(f"  Source: {C.CYAN}{origin_city['name']}{C.RESET}")
        print(f"  Destinations: {C.CYAN}{dest_summary}{C.RESET}\n")
        print(f"  {C.DIM}Enter how much of each resource to send to EACH destination.{C.RESET}\n")
        print(f"  {C.HINT}  500 or 10k = exact  |  all or a = send all{C.RESET}")
        print(f"  {C.HINT}  all-10k = keep 10k  |  blank = skip  |  ' = exit{C.RESET}\n")

        resource_config = get_resource_config(send_mode=2)
        if resource_config is None:
            event.set()
            return

        # Destination minimums (all internal)
        dest_minimums = get_dest_minimums()

        # Notifications
        notif_config = get_notification_config(telegram_enabled, event)
        if notif_config is None:
            return

        timing = _get_schedule_timing(event, "Distribute")
        if timing is None:
            return
        interval_hours, run_at_time = timing

        total_needed = [a * len(destination_cities) for a in resource_config]
        grand = sum(total_needed)

        min_threshold = 0

        while True:
            print_module_banner("Distribute — Summary")
            ship_label = "Freighters" if useFreighters else "Merchant ships"
            print(f"  {C.BOLD}Ship type:{C.RESET}     {ship_label}")
            print(f"  {C.BOLD}Source:{C.RESET}        {origin_city['name']}")
            print(f"  {C.BOLD}Destinations:{C.RESET}  {dest_summary} ({len(destination_cities)})")
            if dest_minimums:
                print(f"  {C.BOLD}Send if below:{C.RESET} {_format_resource_list(dest_minimums)}")
            if run_at_time:
                int_label = f"Daily at {run_at_time} (server time)"
            elif interval_hours == 0:
                int_label = "One-time"
            else:
                int_label = f"Every {interval_hours}h"
            print(f"  {C.BOLD}Schedule:{C.RESET}      {int_label}")
            if min_threshold > 0:
                print(f"  {C.BOLD}Min ship:{C.RESET}      {min_threshold:,} total resources")
            print(f"  {C.BOLD}Total:{C.RESET}         {C.OK}{addThousandSeparator(grand)}{C.RESET} resources\n")
            print(f"  {C.OK}(Y){C.RESET} Proceed  "
                  f"{C.CYAN}(T){C.RESET} Min shipment  "
                  f"{C.WARN}(N){C.RESET} Cancel")
            rta = read(values=["y", "Y", "n", "N", "t", "T", ""])
            if rta.lower() == "n":
                event.set()
                return
            if rta.lower() == "t":
                print(f"\n  Current minimum: "
                      f"{'off' if min_threshold == 0 else f'{min_threshold:,}'}")
                print("  Minimum total resources per shipment (0=off):")
                t_input = read(min=0, digit=True, additionalValues=["'"])
                if t_input == "'":
                    continue
                min_threshold = int(t_input)
                if min_threshold > 0:
                    print(f"  {C.OK}Shipments below {min_threshold:,} "
                          f"will be skipped{C.RESET}")
                else:
                    print(f"  {C.OK}Min shipment filter disabled{C.RESET}")
                enter()
                continue
            break

        enter()

    except KeyboardInterrupt:
        event.set()
        return

    dest_names = ", ".join(c["name"] for c in destination_cities)
    schedule_row = build_schedule_row(
        schedule_id=0,
        mode="distribute",
        ship_type="f" if useFreighters else "m",
        source_city_ids=[str(origin_city["id"])],
        dest_city_ids=[str(c["id"]) for c in destination_cities],
        resource_config=resource_config,
        dest_minimums=dest_minimums or [0, 0, 0, 0, 0],
        min_shipment_threshold=min_threshold,
        interval_hours=interval_hours,
        run_at_time=run_at_time,
        notif_level=notif_config.get("level", "none"),
        notes=f"{origin_city['name']} -> {dest_names[:30]}",
    )
    _save_and_maybe_activate(session, event, schedule_row, notif_config,
                             log_path)


# ============================================================================
#  MODE 3: EVEN DISTRIBUTION  (balance resources across cities)
#  Now supports MULTI-RESOURCE balancing
# ============================================================================

def evenDistributionMode(session, event, stdin_fd, predetermined_input,
                         telegram_enabled, log_path):
    try:
        # Select resources (multi-select)
        print_module_banner("Even Distribution — Pick Resources")
        print(f"  {C.DIM}Choose which resource(s) to split evenly across cities.{C.RESET}\n")
        for i, res in enumerate(materials_names):
            print(f"  {C.BOLD}({i+1}){C.RESET} {res}")
        print(f"\n  {C.HINT}Enter one or more numbers, comma-separated (e.g. 1,3,5){C.RESET}")
        print(f"  {C.BOLD}('){C.RESET} Back")
        raw = read(msg="Resources: ", additionalValues=["'"])
        if raw == "'":
            event.set()
            return

        resource_indices = []
        for part in str(raw).split(","):
            part = part.strip()
            if part.isdigit():
                idx = int(part) - 1
                if 0 <= idx < len(materials_names):
                    resource_indices.append(idx)
        resource_indices = list(dict.fromkeys(resource_indices))  # dedupe
        if not resource_indices:
            print("No valid resources selected!")
            enter()
            event.set()
            return

        selected_names = ", ".join(materials_names[i] for i in resource_indices)
        print(f"\nBalancing: {selected_names}")

        # Ship type
        print_module_banner("Even Distribution — Ship Type")
        print(f"  Balancing: {C.CYAN}{selected_names}{C.RESET}\n")
        print(f"  {C.DIM}Which ships carry the resources?{C.RESET}\n")
        print(f"  {C.BOLD}(1){C.RESET} Merchant ships")
        print(f"  {C.BOLD}(2){C.RESET} Freighters")
        print(f"  {C.BOLD}('){C.RESET} Back")
        shiptype = read(min=1, max=2, digit=True, additionalValues=["'"])
        if shiptype == "'":
            event.set()
            return
        useFreighters = (shiptype == 2)

        # Select cities
        print_module_banner("Even Distribution — Cities")
        print(f"  Balancing: {C.CYAN}{selected_names}{C.RESET}\n")
        balance_msg = (f"{C.DIM}Click cities to include them in the balancing pool:{C.RESET}")
        included_ids, _ = rtm_ignoreCities(session, msg=balance_msg)

        all_cities = []
        for cid in included_ids:
            html_c = session.get(city_url + cid)
            all_cities.append(getCity(html_c))

        if not all_cities:
            print("No cities available for balancing!")
            enter()
            event.set()
            return

        # Calculate plan for each resource
        all_shipments = {}  # resource_index -> list of shipments
        for res_idx in resource_indices:
            res_name = materials_names[res_idx]
            total = sum(c["availableResources"][res_idx] for c in all_cities)
            target = total // len(all_cities)

            shipments = []
            for city in all_cities:
                current = city["availableResources"][res_idx]
                diff = current - target
                if diff > 0:
                    shipments.append({"from": city, "amount": diff, "type": "sender"})
                elif diff < 0:
                    shipments.append({"to": city, "amount": abs(diff), "type": "receiver"})

            all_shipments[res_idx] = shipments

            print(f"\n  {res_name}: total={addThousandSeparator(total)}, "
                  f"target/city={addThousandSeparator(target)}")
            senders = [s for s in shipments if s["type"] == "sender"]
            receivers = [s for s in shipments if s["type"] == "receiver"]
            for s in senders:
                print(f"    SEND: {s['from']['name']} -> {addThousandSeparator(s['amount'])}")
            for r in receivers:
                print(f"    RECV: {r['to']['name']} <- {addThousandSeparator(r['amount'])}")

        # Build preview routes
        preview_routes = []
        for res_idx in resource_indices:
            shipments = all_shipments[res_idx]
            senders = [s for s in shipments if s["type"] == "sender"]
            receivers = [s for s in shipments if s["type"] == "receiver"]
            si, ri = 0, 0
            s_rem = senders[si]["amount"] if senders else 0
            r_rem = receivers[ri]["amount"] if receivers else 0
            while si < len(senders) and ri < len(receivers):
                amt = min(s_rem, r_rem)
                if amt > 0:
                    res_list = [0] * len(materials_names)
                    res_list[res_idx] = amt
                    preview_routes.append({
                        "source": senders[si]["from"]["name"],
                        "dest": receivers[ri]["to"]["name"],
                        "resources": res_list,
                    })
                s_rem -= amt
                r_rem -= amt
                if s_rem == 0:
                    si += 1
                    if si < len(senders):
                        s_rem = senders[si]["amount"]
                if r_rem == 0:
                    ri += 1
                    if ri < len(receivers):
                        r_rem = receivers[ri]["amount"]

        if not preview_routes:
            print(f"\n  {C.OK}All cities are already balanced! Nothing to do.{C.RESET}")
            enter()
            event.set()
            return

        min_threshold = 0

        while True:
            print(f"\n  {C.BOLD}{len(preview_routes)} shipment(s) planned.{C.RESET}")
            if min_threshold > 0:
                print(f"  {C.BOLD}Min ship:{C.RESET} {min_threshold:,} total resources")
            print(f"  {C.OK}(Y){C.RESET} Confirm — start balancing")
            print(f"  {C.CYAN}(T){C.RESET} Min shipment")
            print(f"  {C.WARN}(N){C.RESET} Cancel")
            choice = read(values=["y", "Y", "n", "N", "t", "T", ""])
            if choice.lower() == "n":
                event.set()
                return
            if choice.lower() == "t":
                print(f"\n  Current minimum: "
                      f"{'off' if min_threshold == 0 else f'{min_threshold:,}'}")
                print("  Minimum total resources per shipment (0=off):")
                t_input = read(min=0, digit=True, additionalValues=["'"])
                if t_input == "'":
                    continue
                min_threshold = int(t_input)
                if min_threshold > 0:
                    print(f"  {C.OK}Shipments below {min_threshold:,} "
                          f"will be skipped{C.RESET}")
                else:
                    print(f"  {C.OK}Min shipment filter disabled{C.RESET}")
                enter()
                continue
            break

        # Notifications
        notif_config = get_notification_config(telegram_enabled, event)
        if notif_config is None:
            return

        enter()

    except KeyboardInterrupt:
        event.set()
        return

    city_ids_for_balance = [str(c["id"]) for c in all_cities]
    schedule_row = build_schedule_row(
        schedule_id=0,
        mode="even",
        ship_type="f" if useFreighters else "m",
        source_city_ids=city_ids_for_balance,
        resource_config=resource_indices,
        min_shipment_threshold=min_threshold,
        interval_hours=0,
        notif_level=notif_config.get("level", "none"),
        notes=f"Balance {selected_names}",
    )
    _save_and_maybe_activate(session, event, schedule_row, notif_config,
                             log_path)




# ============================================================================
#  MODE 4: AUTO SEND  (request resources, auto-collect from all cities)
# ============================================================================

def autoSendMode(session, event, stdin_fd, predetermined_input,
                 telegram_enabled, log_path):
    try:
        print_module_banner("Auto Send — Ship Type")
        print(f"  {C.DIM}Which ships carry the resources?{C.RESET}\n")
        print(f"  {C.BOLD}(1){C.RESET} Merchant ships")
        print(f"  {C.BOLD}(2){C.RESET} Freighters")
        print(f"  {C.BOLD}('){C.RESET} Back")
        shiptype = read(min=1, max=2, digit=True, additionalValues=["'"])
        if shiptype == "'":
            event.set()
            return
        useFreighters = (shiptype == 2)
        min_threshold = 0

        while True:
            print_module_banner("Auto Send — Destination")
            print(f"  {C.DIM}Choose the city that needs resources:{C.RESET}\n")
            destination_city = rtm_chooseCity(session)
            if destination_city is None:
                event.set()
                return

            html = session.get(island_url + destination_city["islandId"])
            destination_island = getIsland(html)

            print_module_banner("Auto Send")
            print(f"  {C.DIM}Scanning your cities for available resources...{C.RESET}")
            html = session.get()
            city_ids = re.findall(
                r'<option value="(\d+)" class="cityowntown"', html
            )

            suppliers = []
            totals = [0] * len(materials_names)
            for cid in city_ids:
                if str(cid) == str(destination_city["id"]):
                    continue
                html_c = session.get(city_url + str(cid))
                cdata = getCity(html_c)
                suppliers.append(cdata)
                for i in range(len(materials_names)):
                    totals[i] += cdata["availableResources"][i]

            if not suppliers:
                print("No supplier cities available!")
                enter()
                event.set()
                return

            print_module_banner("Auto Send — Request Resources")
            print(f"  Destination: {C.CYAN}{destination_city['name']}{C.RESET} "
                  f"[{destination_island['x']}:{destination_island['y']}]\n")
            print(f"  {C.DIM}Available across all your other cities:{C.RESET}")
            for i, res in enumerate(materials_names):
                print(f"    {C.CYAN}{res:<12}{C.RESET} {addThousandSeparator(totals[i]):>12}")
            print("")

            while True:
                print(f"  {C.DIM}Enter how much of each resource to collect and send here:{C.RESET}")
                print(f"  {C.HINT}blank = skip  |  e = collect all  |  ' = exit  |  = restart{C.RESET}\n")

                requested = [0] * len(materials_names)
                restart = False
                for i, res in enumerate(materials_names):
                    result = readResourceAmount(res)
                    if result == "EXIT":
                        event.set()
                        return
                    if result == "RESTART":
                        restart = True
                        break
                    requested[i] = result if result and result > 0 else 0

                if restart:
                    break

                if sum(requested) == 0:
                    print("\n  No resources requested.")
                    enter()
                    event.set()
                    return

                over = [
                    f"    {materials_names[i]}: requested "
                    f"{addThousandSeparator(requested[i])}, "
                    f"available {addThousandSeparator(totals[i])}"
                    for i in range(len(materials_names))
                    if requested[i] > totals[i]
                ]
                if over:
                    print("\n  ERROR: Exceeds available:")
                    for line in over:
                        print(line)
                    print("\n  Re-enter amounts.\n")
                    continue

                routes = allocate_from_suppliers(
                    requested, suppliers, destination_city, destination_island
                )
                if routes is None:
                    print("\n  ERROR: Could not allocate across suppliers.")
                    enter()
                    event.set()
                    return

                ship_cap, freighter_cap = getShipCapacity(session)
                capacity = freighter_cap if useFreighters else ship_cap

                choice = render_auto_send_review(
                    destination_city, destination_island, routes,
                    useFreighters, capacity
                )

                if choice == "C":
                    event.set()
                    return
                elif choice == "E":
                    break
                elif choice == "T":
                    print(f"\n  Current minimum: "
                          f"{'off' if min_threshold == 0 else f'{min_threshold:,}'}")
                    print("  Minimum total resources per shipment (0=off):")
                    t_input = read(min=0, digit=True, additionalValues=["'"])
                    if t_input != "'":
                        min_threshold = int(t_input)
                        if min_threshold > 0:
                            print(f"  {C.OK}Shipments below {min_threshold:,} "
                                  f"will be skipped{C.RESET}")
                        else:
                            print(f"  {C.OK}Min shipment filter disabled{C.RESET}")
                        enter()
                    continue
                else:
                    # Notifications
                    notif_config = get_notification_config(
                        telegram_enabled, event
                    )
                    if notif_config is None:
                        return

                    schedule_row = build_schedule_row(
                        schedule_id=0,
                        mode="autosend",
                        ship_type="f" if useFreighters else "m",
                        dest_city_ids=[str(destination_city["id"])],
                        resource_config=list(requested),
                        min_shipment_threshold=min_threshold,
                        interval_hours=0,
                        notif_level=notif_config.get("level", "none"),
                        notes=f"Auto Send -> {destination_city['name']}",
                    )
                    _save_and_maybe_activate(
                        session, event, schedule_row,
                        notif_config, log_path,
                    )
                    return

    except KeyboardInterrupt:
        event.set()
        return


def allocate_from_suppliers(requested, suppliers, destination_city,
                            destination_island, rrs_summary=None):
    remaining = list(requested)
    routes = []
    for supplier in suppliers:
        to_send = [0] * len(materials_names)
        has_cargo = False
        for i in range(len(materials_names)):
            if remaining[i] <= 0:
                continue
            actual = supplier["availableResources"][i]
            if rrs_summary is not None and RRS_AVAILABLE:
                can_give = _rrs_free_from_summary(rrs_summary, supplier["id"], i, actual)
            else:
                can_give = actual
            give = min(remaining[i], can_give)
            to_send[i] = give
            remaining[i] -= give
            if give > 0:
                has_cargo = True
        if has_cargo:
            route = (supplier, destination_city,
                     destination_island["id"], *to_send)
            routes.append(route)
        if all(r <= 0 for r in remaining):
            break
    if any(r > 0 for r in remaining):
        return None
    return routes


def render_auto_send_review(destination_city, destination_island, routes,
                            useFreighters, capacity):
    ship_type_name = "Freighters" if useFreighters else "Merchant ships"
    print_module_banner("Auto Send - Review")
    print(f"  Destination: {destination_city['name']} "
          f"[{destination_island['x']}:{destination_island['y']}]")
    print(f"  Ship type:   {ship_type_name} "
          f"(capacity: {addThousandSeparator(capacity)})\n")
    print("  Planned Shipments:")
    print(f"  {'#':<4} {'From':<18}", end="")
    for res in materials_names:
        print(f" {res:>9}", end="")
    print(f" {'Ships':>7}")
    print(f"  {'--':<4} {'-'*18:<18}", end="")
    for _ in materials_names:
        print(f" {'-'*9:>9}", end="")
    print(f" {'-'*7:>7}")

    grand = [0] * len(materials_names)
    total_ships = 0
    for idx, route in enumerate(routes):
        origin = route[0]
        amounts = route[3:]
        cargo = sum(amounts)
        ships = math.ceil(cargo / capacity) if capacity > 0 else 0
        name = origin["name"][:18] if len(origin["name"]) <= 18 else origin["name"][:15] + "..."
        print(f"  {idx+1:<4} {name:<18}", end="")
        for i in range(len(materials_names)):
            val = amounts[i] if i < len(amounts) else 0
            grand[i] += val
            if val > 0:
                print(f" {addThousandSeparator(val):>9}", end="")
            else:
                print(f" {'0':>9}", end="")
        print(f" {ships:>7}")
        total_ships += ships

    print(f"  {'--':<4} {'-'*18:<18}", end="")
    for _ in materials_names:
        print(f" {'-'*9:>9}", end="")
    print(f" {'-'*7:>7}")
    print(f"  {'':4} {'TOTAL':<18}", end="")
    for i in range(len(materials_names)):
        print(f" {addThousandSeparator(grand[i]):>9}", end="")
    print(f" {total_ships:>7}\n")

    print(f"  {C.OK}(Y){C.RESET} Proceed")
    print(f"  {C.CYAN}(T){C.RESET} Min shipment")
    print(f"  {C.YELLOW}(E){C.RESET} Edit — re-enter amounts")
    print(f"  {C.WARN}(C){C.RESET} Cancel")
    choice = read(values=["y", "Y", "e", "E", "c", "C",
                          "t", "T", ""])
    if choice == "" or choice.upper() == "Y":
        return "Y"
    return choice.upper()


# ============================================================================
#  MODE 5: BULK DISTRIBUTION  (persistent CSV-driven sends)
# ============================================================================

BULK_CSV_COLUMNS = [
    "Transport", "X", "Y", "Player", "City", "City_Location",
    "Wood", "Wine", "Marble", "Crystal", "Sulphur", "From", "Hours",
]


# ----------------------------------------------------------------------------
#  CSV Template Creator
# ----------------------------------------------------------------------------

def _account_data_dir(session):
    suffix = _account_suffix(session)
    path = os.path.join(os.path.expanduser("~"), f".ikabot_data_{suffix}")
    os.makedirs(path, exist_ok=True)
    return path


def _create_csv_template(session):
    print_module_banner("Bulk Distribution — Create CSV Template")
    print(f"  {C.DIM}Creates a blank CSV template for bulk distribution.{C.RESET}\n")

    print(f"  Template name (e.g. 'wine_run', 'alliance_supply'):")
    print(f"  {C.HINT}This becomes the filename: <name>.csv{C.RESET}")
    name = read(msg="  Name: ", empty=True, additionalValues=["'"])
    if name == "'" or not name.strip():
        return None
    safe_name = re.sub(r'[^\w.-]', '_', name.strip())
    if not safe_name.endswith(".csv"):
        safe_name += ".csv"

    print(f"\n  {C.BOLD}Where to save?{C.RESET}")
    acct_dir = _account_data_dir(session)
    generic_dir = os.path.join(os.path.expanduser("~"), ".ikabot_templates")
    print(f"  {C.BOLD}(1){C.RESET} Account folder: {C.DIM}{acct_dir}{C.RESET}")
    print(f"  {C.BOLD}(2){C.RESET} Shared folder:  {C.DIM}{generic_dir}{C.RESET}")
    print(f"  {C.BOLD}(3){C.RESET} Custom location")
    print(f"  {C.BOLD}('){C.RESET} Cancel")
    loc = read(min=1, max=3, digit=True, additionalValues=["'"])
    if loc == "'":
        return None

    if loc == 1:
        save_dir = acct_dir
    elif loc == 2:
        os.makedirs(generic_dir, exist_ok=True)
        save_dir = generic_dir
    else:
        print("  Enter full directory path:")
        custom = read(msg="  Path: ", empty=True, additionalValues=["'"])
        if custom == "'" or not custom.strip():
            return None
        save_dir = os.path.expanduser(custom.strip())
        if not os.path.isdir(save_dir):
            try:
                os.makedirs(save_dir, exist_ok=True)
            except OSError as e:
                print(f"  {C.WARN}Cannot create directory: {e}{C.RESET}")
                enter()
                return None

    csv_path = os.path.join(save_dir, safe_name)
    if os.path.exists(csv_path):
        print(f"\n  {C.WARN}File already exists: {csv_path}{C.RESET}")
        print(f"  {C.BOLD}(1){C.RESET} Overwrite  {C.BOLD}(2){C.RESET} Cancel")
        if read(min=1, max=2, digit=True) == 2:
            return None

    example_rows = [
        {"Transport": "m", "X": "99", "Y": "39", "Player": "player_name",
         "City": "Example City", "City_Location": "1",
         "Wood": "0", "Wine": "5000", "Marble": "0", "Crystal": "0",
         "Sulphur": "0", "From": "", "Hours": "24"},
    ]

    try:
        write_csv_atomic(csv_path, BULK_CSV_COLUMNS, example_rows)
        print(f"\n  {C.OK}Template created: {csv_path}{C.RESET}")
        print(f"  {C.DIM}Edit it with a spreadsheet or the built-in editor.{C.RESET}")
        print(f"\n  {C.HINT}Resource values:  500 or 10k = exact amount{C.RESET}")
        print(f"  {C.HINT}                  all or a   = send everything{C.RESET}")
        print(f"  {C.HINT}                  all-10k    = send all, keep 10k{C.RESET}")
        print(f"  {C.HINT}From column:      a = all cities, 1,3 = specific city indices{C.RESET}")
        print(f"  {C.HINT}Transport:        m = merchant, f = freighters{C.RESET}")
        enter()
        return csv_path
    except Exception as e:
        print(f"  {C.WARN}Error creating template: {e}{C.RESET}")
        enter()
        return None


# ----------------------------------------------------------------------------
#  Bulk Distribution In-App Editor
# ----------------------------------------------------------------------------

def _bulk_editor_menu(session, csv_path, event):
    try:
        rows = []
        fieldnames = list(BULK_CSV_COLUMNS)
        if os.path.isfile(csv_path):
            with open(csv_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                fieldnames = list(reader.fieldnames or BULK_CSV_COLUMNS)
                for row in reader:
                    rows.append(row)
    except Exception as e:
        print(f"  Error reading CSV: {e}")
        enter()
        return

    while True:
        print_module_banner("Bulk CSV Editor")
        print(f"  {C.DIM}CSV:{C.RESET} {csv_path}")
        print(f"  {C.DIM}Rows:{C.RESET} {len(rows)}\n")

        print(f"  {C.BOLD}(1){C.RESET} Add cities")
        print(f"  {C.DIM}    Browse islands and pick cities to add as rows.{C.RESET}")
        print(f"  {C.BOLD}(2){C.RESET} View all rows")
        print(f"  {C.BOLD}(3){C.RESET} Edit row(s)")
        print(f"  {C.BOLD}(4){C.RESET} Delete row(s)")
        print(f"  {C.BOLD}(5){C.RESET} Set resources")
        print(f"  {C.DIM}    Set resource amounts for all rows or specific rows.{C.RESET}")
        print(f"  {C.BOLD}(6){C.RESET} Set transport & source")
        print(f"  {C.DIM}    Choose ship type and which cities send resources.{C.RESET}")
        print(f"  {C.OK}(7){C.RESET} Save and back")
        print(f"  {C.WARN}('){C.RESET} Cancel without saving")

        choice = read(min=1, max=7, digit=True, additionalValues=["'"])
        if choice == "'":
            print("  Discarded changes.")
            enter()
            return
        if choice == 1:
            _bulk_editor_add_cities(session, rows, fieldnames)
        elif choice == 2:
            _bulk_editor_view(rows)
        elif choice == 3:
            _bulk_editor_edit_row(rows)
        elif choice == 4:
            _bulk_editor_delete_rows(rows)
        elif choice == 5:
            _bulk_editor_set_resources(rows)
        elif choice == 6:
            _bulk_editor_set_transport_from(rows)
        elif choice == 7:
            for col in BULK_CSV_COLUMNS:
                if col not in fieldnames:
                    fieldnames.append(col)
            try:
                write_csv_atomic(csv_path, fieldnames, rows)
                print(f"  Saved {len(rows)} rows to {csv_path}")
            except Exception as e:
                print(f"  Error saving: {e}")
            enter()
            return


def _bulk_editor_add_cities(session, rows, fieldnames):
    print_module_banner("Add Cities — Fast Entry")
    print("  Enter island coordinates (two numbers with space, e.g. 44 03)")
    print("  Then select city positions from the island.")
    print("  Type 'done' when finished adding cities.\n")

    while True:
        raw = read(msg="  Island coords (or 'done'): ",
                   empty=True, additionalValues=["done", "Done", "'"])
        if raw in ("done", "Done", "'", ""):
            if raw == "":
                print("  (Enter 'done' to finish, or island coords)")
                continue
            break

        parts = raw.strip().split()
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            print("  Invalid. Enter two numbers separated by space (e.g. 44 03)")
            continue

        x_coord, y_coord = parts[0], parts[1]
        try:
            html = session.get(
                f"view=island&xcoord={x_coord}&ycoord={y_coord}"
            )
            island = getIsland(html)
        except Exception as e:
            print(f"  Error fetching island: {e}")
            continue

        cities_on_island = [
            c for c in island["cities"] if c.get("type") == "city"
        ]
        if not cities_on_island:
            print(f"  No cities found on island [{x_coord}:{y_coord}]")
            continue

        tradegood = materials_names[int(island.get("tradegood", 0))]
        print(f"\n  Island [{x_coord}:{y_coord}] — {island.get('name', '?')}"
              f" ({tradegood})")
        print(f"  {'Pos':<4} {'City':<20} {'Player':<15} {'Loc'}")
        print(f"  {'─'*4} {'─'*20} {'─'*15} {'─'*5}")
        for i, c in enumerate(cities_on_island, 1):
            cn = c.get("name", "?")[:20]
            pn = c.get("Name", "?")[:15]
            loc = get_city_location_token(c) or ""
            print(f"  {i:<4} {cn:<20} {pn:<15} {loc}")

        selected_positions = set()
        print(f"\n  Type position numbers to add (e.g. 1,4,5)")
        print(f"  Type d + number to deselect (e.g. d3 or d1,4)")
        print(f"  Press Enter when done with this island:")

        while True:
            sel = read(msg="  > ", empty=True, additionalValues=["'"])
            if sel == "'":
                break
            if sel == "":
                break

            if sel.lower().startswith("d"):
                nums = sel[1:].strip()
                try:
                    to_remove = [int(x.strip()) for x in nums.split(",")]
                    removed = []
                    for p in to_remove:
                        if p in selected_positions:
                            selected_positions.discard(p)
                            removed.append(
                                cities_on_island[p - 1].get("name", "?")
                                if 1 <= p <= len(cities_on_island) else "?"
                            )
                    if removed:
                        print(f"  Removed: {', '.join(removed)}")
                    else:
                        print("  Nothing to remove.")
                except ValueError:
                    print("  Invalid. Use d1 or d1,3,5")
                continue

            try:
                positions = [int(x.strip()) for x in sel.split(",")]
                added = []
                for p in positions:
                    if 1 <= p <= len(cities_on_island):
                        if p not in selected_positions:
                            selected_positions.add(p)
                            added.append(
                                cities_on_island[p - 1].get("name", "?")
                            )
                    else:
                        print(f"  Position {p} out of range "
                              f"(1-{len(cities_on_island)})")
                if added:
                    print(f"  Added: {', '.join(added)}")
            except ValueError:
                print("  Invalid. Enter numbers comma-separated (e.g. 1,4,5)")

        if selected_positions:
            for p in sorted(selected_positions):
                c = cities_on_island[p - 1]
                row = {col: "" for col in BULK_CSV_COLUMNS}
                row["X"] = x_coord
                row["Y"] = y_coord
                row["Player"] = c.get("Name", "")
                row["City"] = c.get("name", "")
                row["City_Location"] = get_city_location_token(c) or ""
                row["Transport"] = "m"
                row["From"] = "a"
                row["Hours"] = "1"
                rows.append(row)
            print(f"  {len(selected_positions)} city/cities added from "
                  f"[{x_coord}:{y_coord}].\n")

    print(f"\n  Total rows: {len(rows)}")


def _bulk_editor_set_resources(rows):
    if not rows:
        print("  No rows to configure.\n")
        enter()
        return

    print_module_banner("Set Resources")
    print(f"  {len(rows)} row(s) in CSV.\n")
    print(f"  {C.BOLD}(1){C.RESET} Same resources for all rows")
    print(f"  {C.BOLD}(2){C.RESET} Set per-row")
    print(f"  {C.BOLD}('){C.RESET} Cancel")

    choice = read(min=1, max=2, digit=True, additionalValues=["'"])
    if choice == "'":
        return

    if choice == 1:
        print("\n  Enter resource amounts for ALL rows:")
        print("  (Formats: 5000, e0=send all, e10000=except 10k, "
              "0 or blank=skip)")
        res_names = ["Wood", "Wine", "Marble", "Crystal", "Sulphur"]
        values = []
        for rn in res_names:
            val = read(msg=f"  {rn}: ", empty=True, additionalValues=["'"])
            if val == "'":
                return
            values.append(val.strip() if val else "0")
        for row in rows:
            for i, rn in enumerate(res_names):
                row[rn] = values[i]
        print(f"  Resources set for all {len(rows)} rows.")
        enter()

    elif choice == 2:
        res_names = ["Wood", "Wine", "Marble", "Crystal", "Sulphur"]
        print("\n  Enter row numbers to edit (comma-sep or range, e.g. 1,3,5-8):")
        print("  (or 'a' for all)")
        raw = read(additionalValues=["'", "a", "A"])
        if raw == "'":
            return
        if raw.lower() == "a":
            indices = list(range(len(rows)))
        else:
            indices = _parse_row_selection(raw, len(rows))
            if indices is None:
                print("  Invalid selection.")
                enter()
                return

        for idx in indices:
            row = rows[idx]
            city = row.get("City", "?")
            player = row.get("Player", "?")
            print(f"\n  Row {idx+1}: {player}/{city} "
                  f"[{row.get('X', '?')}:{row.get('Y', '?')}]")
            for rn in res_names:
                current = row.get(rn, "0")
                val = read(msg=f"    {rn} [{current}]: ",
                           empty=True, additionalValues=["'"])
                if val == "'":
                    return
                if val.strip():
                    row[rn] = val.strip()
        print("  Resources updated.")
        enter()


def _bulk_editor_set_transport_from(rows):
    if not rows:
        print("  No rows to configure.\n")
        enter()
        return

    print_module_banner("Set Transport & From")
    print(f"  {len(rows)} row(s) in CSV.\n")

    print("  Ship type for all rows:")
    print(f"  {C.BOLD}(1){C.RESET} Merchant ships (m)")
    print(f"  {C.BOLD}(2){C.RESET} Freighters (f)")
    print(f"  {C.BOLD}(3){C.RESET} Keep current / set per-row later")
    print(f"  {C.BOLD}('){C.RESET} Cancel")
    st = read(min=1, max=3, digit=True, additionalValues=["'"])
    if st == "'":
        return
    if st == 1:
        for row in rows:
            row["Transport"] = "m"
        print("  All rows set to merchant ships.")
    elif st == 2:
        for row in rows:
            row["Transport"] = "f"
        print("  All rows set to freighters.")

    print("\n  From (source cities) for all rows:")
    print(f"  {C.BOLD}(1){C.RESET} All cities (a)")
    print(f"  {C.BOLD}(2){C.RESET} Specific indices (enter value)")
    print(f"  {C.BOLD}(3){C.RESET} Keep current / set per-row later")
    fc = read(min=1, max=3, digit=True, additionalValues=["'"])
    if fc == "'":
        return
    if fc == 1:
        for row in rows:
            row["From"] = "a"
        print("  All rows set to 'a' (all cities).")
    elif fc == 2:
        print("  Enter From value (e.g. 1,3,5):")
        val = read(msg="  > ", additionalValues=["'"])
        if val == "'":
            return
        for row in rows:
            row["From"] = val.strip()
        print(f"  All rows set to '{val.strip()}'.")

    print("\n  Hours (interval) for all rows:")
    print("  (Enter a number, or ' to skip)")
    hrs = read(msg="  Hours: ", empty=True, additionalValues=["'"])
    if hrs != "'" and hrs.strip():
        for row in rows:
            row["Hours"] = hrs.strip()
        print(f"  All rows set to {hrs.strip()} hours.")

    enter()


def _bulk_editor_view(rows):
    if not rows:
        print("\n  No rows.\n")
        enter()
        return

    print(f"\n  {'#':<4} {'X':>3} {'Y':>3} {'Player':<14} {'City':<16} "
          f"{'Wood':>6} {'Wine':>6} {'Marb':>6} {'Crys':>6} {'Sulp':>6} {'Ship'} {'From':<5}")
    print(f"  {'─'*4} {'─'*3} {'─'*3} {'─'*14} {'─'*16} "
          f"{'─'*6} {'─'*6} {'─'*6} {'─'*6} {'─'*6} {'─'*4} {'─'*5}")

    for i, row in enumerate(rows, 1):
        x = row.get("X", "")[:3]
        y = row.get("Y", "")[:3]
        player = (row.get("Player", "") or "")[:14]
        city = (row.get("City", "") or "")[:16]
        w = (row.get("Wood", "0") or "0")[:6]
        v = (row.get("Wine", "0") or "0")[:6]
        m = (row.get("Marble", "0") or "0")[:6]
        c = (row.get("Crystal", "0") or "0")[:6]
        s = (row.get("Sulphur", "0") or "0")[:6]
        t_raw = (row.get("Transport", "m") or "m")[0].lower()
        t = "M" if t_raw == "m" else "F"
        fr = (row.get("From", "a") or "a")[:5]
        print(f"  {i:<4} {x:>3} {y:>3} {player:<14} {city:<16} "
              f"{w:>6} {v:>6} {m:>6} {c:>6} {s:>6} {t:>4} {fr:<5}")

    print(f"\n  {len(rows)} row(s)\n")
    enter()


def _bulk_editor_edit_row(rows):
    if not rows:
        print("  No rows.\n")
        enter()
        return

    print("  Enter row number to edit (or ' to cancel):")
    raw = read(additionalValues=["'"])
    if raw == "'":
        return
    try:
        idx = int(raw) - 1
    except ValueError:
        print("  Invalid row number.")
        enter()
        return
    if idx < 0 or idx >= len(rows):
        print(f"  Out of range (1-{len(rows)}).")
        enter()
        return

    row = rows[idx]
    editable = ["Transport", "X", "Y", "Player", "City", "City_Location",
                "Wood", "Wine", "Marble", "Crystal", "Sulphur", "From", "Hours"]

    print(f"\n  Row {idx+1}:")
    for col in editable:
        print(f"    {col}: {row.get(col, '')}")
    print("")
    print("  Enter field name to edit (or ' to go back):")

    while True:
        field = read(msg="  Field: ", additionalValues=["'"])
        if field == "'":
            return
        matched = None
        for col in editable:
            if col.lower() == field.lower():
                matched = col
                break
        if not matched:
            print(f"  Unknown field. Options: {', '.join(editable)}")
            continue
        current = row.get(matched, "")
        val = read(msg=f"  {matched} [{current}]: ",
                   empty=True, additionalValues=["'"])
        if val == "'":
            return
        if val.strip():
            row[matched] = val.strip()
            print(f"  {matched} updated.")
        else:
            print(f"  {matched} unchanged.")
        print("  Edit another field? (Enter field name or ' to finish)")


def _bulk_editor_delete_rows(rows):
    if not rows:
        print("  No rows.\n")
        enter()
        return

    print(f"  {len(rows)} row(s). Enter row numbers to delete:")
    print("  (comma-separated or range, e.g. 1,3,5-8, or ' to cancel)")
    raw = read(additionalValues=["'"])
    if raw == "'":
        return

    indices = _parse_row_selection(raw, len(rows))
    if indices is None:
        print("  Invalid selection.")
        enter()
        return

    if not indices:
        print("  Nothing selected.")
        enter()
        return

    print(f"  Delete {len(indices)} row(s)? [y/N]")
    confirm = read(values=["y", "Y", "n", "N", ""])
    if confirm.lower() != "y":
        return

    for idx in sorted(indices, reverse=True):
        rows.pop(idx)
    print(f"  Deleted {len(indices)} row(s). {len(rows)} remaining.")
    enter()


def _parse_row_selection(raw, total):
    indices = set()
    try:
        for part in raw.split(","):
            part = part.strip()
            if "-" in part:
                start, end = part.split("-", 1)
                start = int(start.strip())
                end = int(end.strip())
                for i in range(start, end + 1):
                    if 1 <= i <= total:
                        indices.add(i - 1)
            elif part.isdigit():
                i = int(part)
                if 1 <= i <= total:
                    indices.add(i - 1)
            else:
                return None
    except (ValueError, TypeError):
        return None
    return sorted(indices)


def _bulkDistributionModeInner(session, event, stdin_fd, predetermined_input,
                               telegram_enabled, log_path):
    try:
        print_module_banner("Bulk Distribution — CSV File")
        print(f"  {C.DIM}Send resources to many cities using a spreadsheet (CSV file).{C.RESET}")
        print(f"  {C.DIM}You can edit the file in a spreadsheet app or in the built-in editor.{C.RESET}\n")
        prefs = load_prefs()
        saved_csv = prefs.get("csv_path", "")
        if saved_csv:
            print(f"  {C.CYAN}Last used:{C.RESET} {saved_csv}")
            print(f"  {C.DIM}Press Enter to reuse, or type a new path.{C.RESET}")
        else:
            print(f"  Enter the full path to your CSV file, or create a template.")
        print(f"\n  {C.HINT}Resource values:  500 or 10k = exact, all or a = send all{C.RESET}")
        print(f"  {C.HINT}                  all-10k    = send all but keep 10k{C.RESET}")
        print(f"  {C.HINT}From: a = all cities, or 1,3,5  |  Transport: m or f{C.RESET}")
        print(f"\n  {C.BOLD}(N){C.RESET} Create new template")
        print(f"  {C.BOLD}('){C.RESET} Back\n")
        csv_input = read(msg="CSV path: ", empty=True,
                         additionalValues=["'", "n", "N"])
        if csv_input == "'":
            event.set()
            return
        if csv_input.lower() == "n":
            template_path = _create_csv_template(session)
            if template_path:
                csv_path = template_path
                prefs["csv_path"] = csv_path
                save_prefs(prefs)
            else:
                return "restart"
        else:
            csv_path = csv_input.strip() if csv_input.strip() else saved_csv
            if not csv_path:
                print("No CSV path provided.")
                enter()
                event.set()
                return
        # Save for next time
        prefs["csv_path"] = csv_path
        save_prefs(prefs)

        if not os.path.isfile(csv_path):
            print(f"  {C.WARN}File not found:{C.RESET} {csv_path}")
            print(f"\n  {C.BOLD}(1){C.RESET} Create new CSV with the built-in editor")
            print(f"  {C.BOLD}('){C.RESET} Back\n")
            choice = read(values=["1", "'"], additionalValues=["'"])
            if choice == "'":
                event.set()
                return
            _bulk_editor_menu(session, csv_path, event)
            if not os.path.isfile(csv_path):
                print("No CSV created. Returning.")
                enter()
                event.set()
                return
            return "restart"

        try:
            rows = []
            with open(csv_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                fieldnames = list(reader.fieldnames or [])
                required = {
                    "X", "Y", "Player", "City", "City_Location",
                    "Wood", "Wine", "Marble", "Crystal", "Sulphur", "Hours",
                }
                missing = required - set(fieldnames)
                if missing:
                    print(f"CSV missing columns: {', '.join(sorted(missing))}")
                    enter()
                    event.set()
                    return
                for row in reader:
                    rows.append(row)
        except Exception as e:
            print(f"Error reading CSV: {e}")
            enter()
            event.set()
            return

        if not rows:
            print("CSV has no data rows.")
            enter()
            event.set()
            return

        hours_raw = str(rows[0].get("Hours", "")).strip()
        hours_match = re.search(r"\d+", hours_raw)
        if not hours_match:
            print("Invalid Hours value in CSV row 1 (no number found).")
            enter()
            event.set()
            return
        interval_hours = int(hours_match.group())

        if interval_hours <= 0:
            print("Hours must be >= 1 for Bulk Distribution.")
            enter()
            event.set()
            return

        fieldnames, run_columns = ensure_run_columns(fieldnames, rows)
        fieldnames = ensure_transport_column(fieldnames, rows)
        fieldnames = ensure_from_column(fieldnames, rows)

        mode, run_column = choose_run_slot(session, event, rows, run_columns)
        if run_column is None:
            return

        fieldnames_no_runs = [c for c in fieldnames
                              if not c.startswith("Run_")
                              and not c.startswith("Issues_")]
        run_issue_pairs = []
        for rc in run_columns:
            run_issue_pairs.append(rc)
            run_issue_pairs.append(rc.replace("Run_", "Issues_", 1))
        fieldnames = fieldnames_no_runs + run_issue_pairs
        backup_path = f"{csv_path}.bak"
        try:
            write_csv_atomic(csv_path, fieldnames, rows)
            write_csv_atomic(backup_path, fieldnames, rows)
        except Exception as e:
            print(f"Error preparing CSV: {e}")
            enter()
            event.set()
            return

        # Notifications
        notif_config = get_notification_config(telegram_enabled, event)
        if notif_config is None:
            return

        ap_max_wait = 120  # default 2 hours
        min_threshold = 0  # 0 = disabled (no minimum)

        # Final confirmation with dry run
        while True:
            done_count = sum(
                1 for r in rows
                if normalize_text(r.get(run_column, "")) == "x"
            )
            pending_count = len(rows) - done_count
            print_module_banner("Bulk Distribution — Summary")
            print(f"  {C.BOLD}CSV rows:{C.RESET}  {len(rows)}")
            print(f"  {C.BOLD}Progress:{C.RESET}  {done_count} done, {pending_count} pending")
            print(f"  {C.BOLD}Interval:{C.RESET}  every {interval_hours}h")
            print(f"  {C.BOLD}AP wait:{C.RESET}   {ap_max_wait} min")
            if min_threshold > 0:
                print(f"  {C.BOLD}Min ship:{C.RESET}  {min_threshold:,} total resources")
            else:
                print(f"  {C.BOLD}Min ship:{C.RESET}  {C.DIM}off{C.RESET}")
            print(f"  {C.BOLD}Run slot:{C.RESET}  {run_column[4:]}\n")
            print(f"  {C.OK}(Y){C.RESET} Proceed  "
                  f"{C.YELLOW}(E){C.RESET} Edit CSV  "
                  f"{C.CYAN}(R){C.RESET} Reset progress")
            print(f"  {C.CYAN}(A){C.RESET} AP wait timer  "
                  f"{C.CYAN}(T){C.RESET} Min shipment  "
                  f"{C.WARN}(N){C.RESET} Cancel")
            rta = read(values=["y", "Y", "n", "N", "e", "E",
                               "a", "A", "t", "T", "r", "R", "", "'"],
                       additionalValues=["'"])
            if rta == "'" or rta.lower() == "n":
                event.set()
                return
            if rta.lower() == "r":
                print(f"\n  {C.BOLD}Reset Progress{C.RESET}")
                print(f"  Current: {done_count}/{len(rows)} completed\n")
                print(f"  {C.BOLD}(1){C.RESET} Reset all — start completely from scratch")
                print(f"  {C.BOLD}(2){C.RESET} Reset from row — clear from a specific row onwards")
                print(f"  {C.BOLD}(3){C.RESET} Reset specific rows — pick rows by number/range")
                print(f"  {C.BOLD}('){C.RESET} Cancel")
                rc = read(min=1, max=3, digit=True, additionalValues=["'"])
                if rc == "'":
                    continue
                if rc == 1:
                    for row in rows:
                        row[run_column] = ""
                    try:
                        write_csv_atomic(csv_path, fieldnames, rows)
                    except Exception as _e:
                        print(f"  {C.WARN}CSV write error: {_e}{C.RESET}")
                    print(f"  {C.OK}All {len(rows)} rows reset.{C.RESET}")
                    enter()
                    continue
                if rc == 2:
                    print(f"  Start from which row? (1-{len(rows)}):")
                    start_row = read(min=1, max=len(rows), digit=True,
                                     additionalValues=["'"])
                    if start_row == "'":
                        continue
                    cleared = 0
                    for i in range(start_row - 1, len(rows)):
                        if rows[i].get(run_column, "").strip():
                            rows[i][run_column] = ""
                            cleared += 1
                    try:
                        write_csv_atomic(csv_path, fieldnames, rows)
                    except Exception as _e:
                        print(f"  {C.WARN}CSV write error: {_e}{C.RESET}")
                    print(f"  {C.OK}Cleared {cleared} row(s) from row {start_row} onwards.{C.RESET}")
                    enter()
                    continue
                if rc == 3:
                    print(f"  Enter row numbers (comma-sep, ranges, e.g. 1-5, 8, 12-20):")
                    raw = read(additionalValues=["'"])
                    if raw == "'":
                        continue
                    indices = _parse_row_selection(raw, len(rows))
                    if indices is None or not indices:
                        print("  Invalid selection.")
                        enter()
                        continue
                    cleared = 0
                    for idx in indices:
                        if rows[idx].get(run_column, "").strip():
                            rows[idx][run_column] = ""
                            cleared += 1
                    try:
                        write_csv_atomic(csv_path, fieldnames, rows)
                    except Exception as _e:
                        print(f"  {C.WARN}CSV write error: {_e}{C.RESET}")
                    print(f"  {C.OK}Cleared {cleared} row(s).{C.RESET}")
                    enter()
                    continue
            if rta.lower() == "e":
                _bulk_editor_menu(session, csv_path, event)
                return "restart"
            if rta.lower() == "a":
                print(f"\n  Current AP wait: {ap_max_wait} minutes")
                print("  How long to retry AP-blocked cities (minutes, 0=no retry):")
                ap_input = read(min=0, digit=True, additionalValues=["'"])
                if ap_input == "'":
                    continue
                ap_max_wait = int(ap_input)
                print(f"  {C.OK}AP wait set to {ap_max_wait} min{C.RESET}")
                enter()
                continue
            if rta.lower() == "t":
                print(f"\n  Current minimum: "
                      f"{'off' if min_threshold == 0 else f'{min_threshold:,}'}")
                print("  Minimum total resources per shipment (0=off):")
                t_input = read(min=0, digit=True, additionalValues=["'"])
                if t_input == "'":
                    continue
                min_threshold = int(t_input)
                if min_threshold > 0:
                    print(f"  {C.OK}Shipments below {min_threshold:,} "
                          f"will be skipped{C.RESET}")
                else:
                    print(f"  {C.OK}Min shipment filter disabled{C.RESET}")
                enter()
                continue
            break

        enter()

    except KeyboardInterrupt:
        event.set()
        return

    schedule_row = build_schedule_row(
        schedule_id=0,
        mode="bulk",
        bulk_csv_path=csv_path,
        bulk_run_column=run_column,
        ap_max_wait_minutes=ap_max_wait,
        min_shipment_threshold=min_threshold,
        interval_hours=interval_hours,
        notif_level=notif_config.get("level", "none"),
        notes=f"CSV: {os.path.basename(csv_path)}",
    )
    _save_and_maybe_activate(session, event, schedule_row, notif_config,
                             log_path)


def bulkDistributionMode(session, event, stdin_fd, predetermined_input,
                         telegram_enabled, log_path):
    while True:
        result = _bulkDistributionModeInner(
            session, event, stdin_fd, predetermined_input,
            telegram_enabled, log_path,
        )
        if result != "restart":
            return






# ============================================================================
#  MODE 6: KEEP TOPPED UP  (periodically fill destinations from sources)
# ============================================================================

def topUpMode(session, event, stdin_fd, predetermined_input,
              telegram_enabled, log_path):
    try:
        # --- Step 1: Ship type ---
        ship_confirmed = False
        while not ship_confirmed:
            print_module_banner("Keep Topped Up — Ship Type")
            print(f"  {C.DIM}Which ships carry the resources?{C.RESET}\n")
            print(f"  {C.BOLD}(1){C.RESET} Merchant ships")
            print(f"  {C.BOLD}(2){C.RESET} Freighters")
            print(f"  {C.BOLD}('){C.RESET} Back")
            shiptype = read(min=1, max=2, digit=True, additionalValues=["'"])
            if shiptype == "'":
                event.set()
                return
            useFreighters = (shiptype == 2)
            ship_label = "Freighters" if useFreighters else "Merchant ships"
            print(f"\nShip type: {ship_label}")
            print(f"  {C.BOLD}(1){C.RESET} Confirm  {C.BOLD}(2){C.RESET} Re-enter  {C.BOLD}('){C.RESET} Back")
            c = read(min=1, max=2, digit=True, additionalValues=["'"])
            if c == "'":
                event.set()
                return
            if c == 1:
                ship_confirmed = True

        # --- Step 2: Destination cities (multi-destination loop) ---
        destinations = []
        adding_dests = True
        while adding_dests:
            dest_confirmed = False
            while not dest_confirmed:
                print_module_banner("Keep Topped Up — Destinations")
                print(f"  {C.DIM}Choose cities that should be kept stocked up.{C.RESET}\n")
                if destinations:
                    print(f"  Current: {C.CYAN}{', '.join(d['name'] for d in destinations)}{C.RESET}")
                    print("")
                print(f"  {C.DIM}Select destination city:{C.RESET}")
                dest = rtm_chooseCity(session)
                if dest is None:
                    event.set()
                    return
                print(f"\nSelected: {dest['name']}")
                print(f"  {C.BOLD}(1){C.RESET} Confirm  {C.BOLD}(2){C.RESET} Re-enter  {C.BOLD}('){C.RESET} Back")
                c = read(min=1, max=2, digit=True, additionalValues=["'"])
                if c == "'":
                    event.set()
                    return
                if c == 1:
                    dest_confirmed = True
            destinations.append(dest)

            print(f"\nDestinations so far: {', '.join(d['name'] for d in destinations)}")
            print(f"  {C.BOLD}(1){C.RESET} Add another destination  {C.BOLD}(2){C.RESET} Done adding")
            c = read(min=1, max=2, digit=True)
            if c == 2:
                adding_dests = False

        # --- Step 3: Resource targets (per destination) ---
        dest_configs = {}
        for dest in destinations:
            targets_confirmed = False
            while not targets_confirmed:
                print_module_banner(f"Keep Topped Up — Targets for {dest['name']}")
                cap = dest.get("storageCapacity", 0)
                fill_95 = math.floor(cap * 0.95)
                print(f"  {C.DIM}Set the target amount for each resource in this city.{C.RESET}")
                print(f"  {C.DIM}Whenever it drops below the target, resources will be shipped.{C.RESET}\n")
                print(f"  Storage: {C.CYAN}{addThousandSeparator(cap)}{C.RESET}")
                print(f"  {C.HINT}  f = fill to 95% ({addThousandSeparator(fill_95)}){C.RESET}")
                print(f"  {C.HINT}  0 or blank = skip  |  = restart  |  ' exit{C.RESET}\n")

                targets = []
                restart = False
                for i, res in enumerate(materials_names):
                    val = read(msg=f"  Target {res}: ",
                               additionalValues=["f", "F", "=", "'", ""])
                    if val == "'":
                        event.set()
                        return
                    if val == "=":
                        restart = True
                        break
                    if isinstance(val, str) and val.lower() == "f":
                        targets.append(fill_95)
                    elif val == "" or val == 0:
                        targets.append(None)
                    else:
                        try:
                            n = int(str(val).replace(",", ""))
                            targets.append(n if n > 0 else None)
                        except ValueError:
                            targets.append(None)
                if restart:
                    continue

                print(f"\nTargets for {dest['name']}:")
                for i, res in enumerate(materials_names):
                    if targets[i] is None:
                        print(f"  {res}: skip")
                    else:
                        print(f"  {res}: {addThousandSeparator(targets[i])}")
                print(f"  {C.BOLD}(1){C.RESET} Confirm  {C.BOLD}(2){C.RESET} Re-enter  {C.BOLD}('){C.RESET} Back")
                c = read(min=1, max=2, digit=True, additionalValues=["'"])
                if c == "'":
                    event.set()
                    return
                if c == 1:
                    targets_confirmed = True
                    dest_configs[str(dest["id"])] = targets

        # --- Step 4: Source cities ---
        src_confirmed = False
        while not src_confirmed:
            src_msg = (f"{C.DIM}Click cities to add them as sources (they'll supply the resources).\n"
                       f"  You can set reserve protection per city after confirming.{C.RESET}")
            source_city_ids, source_cities = rtm_ignoreCities(session, msg=src_msg)
            if not source_city_ids:
                print("No source cities selected!")
                enter()
                event.set()
                return
            print_module_banner("Source City Confirmation")
            print(f"Source cities ({len(source_city_ids)}):")
            for cid in source_city_ids:
                print(f"  {source_cities[cid]['name']}")
            print("\n(1) Confirm  (2) Re-select  (') Back to main menu")
            c = read(min=1, max=2, digit=True, additionalValues=["'"])
            if c == "'":
                event.set()
                return
            if c == 1:
                src_confirmed = True

        # --- Step 5: Reserve protection (optional, per source city) ---
        source_reserves = {}
        print_module_banner("Keep Topped Up — Reserve Protection")
        print(f"  {C.DIM}Optionally prevent source cities from being emptied.{C.RESET}")
        print(f"  {C.DIM}Set a minimum amount to keep in each source city.{C.RESET}\n")
        print(f"  {C.BOLD}(1){C.RESET} Set up reserve protection")
        print(f"  {C.BOLD}(2){C.RESET} No protection (send everything available)")
        reserve_choice = read(min=1, max=2, digit=True)
        if reserve_choice == 1:
            reserves_confirmed = False
            while not reserves_confirmed:
                source_reserves = {}
                for cid in source_city_ids:
                    cname = source_cities[cid]["name"]
                    print_module_banner(f"Reserve — {cname}")
                    print(f"Enter amount to keep in reserve per resource.")
                    print(f"(0 or blank = no reserve for this resource)")
                    print(f"(= restart this city | ' exit)\n")
                    reserves = []
                    restart = False
                    for i, res in enumerate(materials_names):
                        val = read(msg=f"  Reserve {res}: ",
                                   min=0, digit=True,
                                   additionalValues=["=", "'", ""])
                        if val == "'":
                            event.set()
                            return
                        if val == "=":
                            restart = True
                            break
                        if val == "" or val == 0:
                            reserves.append(0)
                        else:
                            reserves.append(int(str(val).replace(",", "")))
                    if restart:
                        continue
                    source_reserves[cid] = reserves

                print_module_banner("Reserve Protection Summary")
                for cid in source_city_ids:
                    cname = source_cities[cid]["name"]
                    res_list = source_reserves.get(cid, [0] * len(materials_names))
                    if all(r == 0 for r in res_list):
                        print(f"  {cname}: no reserves")
                    else:
                        parts = []
                        for i, res in enumerate(materials_names):
                            if res_list[i] > 0:
                                parts.append(f"{res} {addThousandSeparator(res_list[i])}")
                            else:
                                parts.append(f"{res} none")
                        print(f"  {cname}: {' | '.join(parts)}")
                print(f"\n  {C.BOLD}(1){C.RESET} Confirm  {C.BOLD}(2){C.RESET} Re-enter  {C.BOLD}('){C.RESET} Back")
                c = read(min=1, max=2, digit=True, additionalValues=["'"])
                if c == "'":
                    event.set()
                    return
                if c == 1:
                    reserves_confirmed = True

        timing = _get_schedule_timing(event, "Keep Topped Up")
        if timing is None:
            return
        interval_hours, run_at_time = timing
        if interval_hours == 0:
            interval_hours = 1

        # --- Step 7: Notifications ---
        notif_config = get_notification_config(telegram_enabled, event)
        if notif_config is None:
            return

        min_threshold = 0

        # --- Step 8: Final summary + dry run ---
        while True:
            print_module_banner("Keep Topped Up — Summary")
            print(f"  {C.BOLD}Ship type:{C.RESET}  {ship_label}")
            print(f"  {C.BOLD}Destinations ({len(destinations)}):{C.RESET}")
            for dest in destinations:
                parts = []
                tgts = dest_configs[str(dest["id"])]
                for i, res in enumerate(materials_names):
                    if tgts[i] is None:
                        continue
                    parts.append(f"{res}: {addThousandSeparator(tgts[i])}")
                print(f"    {C.CYAN}{dest['name']}{C.RESET} — {' | '.join(parts) if parts else 'none'}")
            src_names = ", ".join(source_cities[cid]["name"] for cid in source_city_ids)
            print(f"  {C.BOLD}Sources ({len(source_city_ids)}):{C.RESET} {src_names}")
            if source_reserves:
                print(f"  {C.BOLD}Reserve protection:{C.RESET} {C.OK}enabled{C.RESET}")
                for cid in source_city_ids:
                    res_list = source_reserves.get(cid, [0] * len(materials_names))
                    if any(r > 0 for r in res_list):
                        parts = [f"{materials_names[i]} {addThousandSeparator(res_list[i])}"
                                 for i in range(len(materials_names)) if res_list[i] > 0]
                        print(f"    {source_cities[cid]['name']}: {' | '.join(parts)}")
            else:
                print(f"  {C.BOLD}Reserve protection:{C.RESET} none")
            if run_at_time:
                print(f"  {C.BOLD}Schedule:{C.RESET}        Daily at {run_at_time} (server time)")
            else:
                print(f"  {C.BOLD}Check interval:{C.RESET} every {interval_hours}h")
            if min_threshold > 0:
                print(f"  {C.BOLD}Min ship:{C.RESET}       {min_threshold:,} total resources")
            print("")
            print(f"  {C.OK}(Y){C.RESET} Proceed  "
                  f"{C.CYAN}(T){C.RESET} Min shipment  "
                  f"{C.WARN}(N){C.RESET} Cancel")
            rta = read(values=["y", "Y", "n", "N", "t", "T", ""])
            if rta.lower() == "n":
                event.set()
                return
            if rta.lower() == "t":
                print(f"\n  Current minimum: "
                      f"{'off' if min_threshold == 0 else f'{min_threshold:,}'}")
                print("  Minimum total resources per shipment (0=off):")
                t_input = read(min=0, digit=True, additionalValues=["'"])
                if t_input == "'":
                    continue
                min_threshold = int(t_input)
                if min_threshold > 0:
                    print(f"  {C.OK}Shipments below {min_threshold:,} "
                          f"will be skipped{C.RESET}")
                else:
                    print(f"  {C.OK}Min shipment filter disabled{C.RESET}")
                enter()
                continue
            break

        enter()

    except KeyboardInterrupt:
        event.set()
        return

    dest_names = ", ".join(d["name"] for d in destinations)
    schedule_row = build_schedule_row(
        schedule_id=0,
        mode="topup",
        ship_type="f" if useFreighters else "m",
        source_city_ids=list(source_city_ids),
        dest_city_ids=[str(d["id"]) for d in destinations],
        dest_targets=dest_configs,
        source_reserves=source_reserves,
        min_shipment_threshold=min_threshold,
        interval_hours=interval_hours,
        run_at_time=run_at_time,
        notif_level=notif_config.get("level", "none"),
        notes=f"TopUp: {dest_names[:30]}",
    )
    _save_and_maybe_activate(session, event, schedule_row, notif_config,
                             log_path)






# ============================================================================
#  SAVE SCHEDULE + ACTIVATE HELPER  (shared by all mode setup functions)
# ============================================================================

def _save_and_maybe_activate(session, event, schedule_row, notif_config,
                             log_path):
    if not enforce_transport_schema_or_abort(session):
        event.set()
        return

    rows = transport_csv_load(session)
    schedule_row["schedule_id"] = next_schedule_id(rows)
    transport_csv_append(session, schedule_row)
    sid = schedule_row["schedule_id"]
    mode_label = schedule_row.get("mode", "?").capitalize()

    worker_running = _is_transport_worker_running(session)

    if worker_running:
        transport_csv_update(session, sid, status="active")
        print(f"\n  {C.OK}Schedule #{sid} ({mode_label}) saved and activated.{C.RESET}")
        print(f"  {C.DIM}The scheduler is running — it will pick this up within "
              f"{TICK_BUDGET_SECONDS}s.{C.RESET}")
        enter()
        event.set()
        return

    print(f"\n  {C.OK}Schedule #{sid} ({mode_label}) saved.{C.RESET}")
    print(f"  {C.WARN}The scheduler is not running.{C.RESET}\n")
    print(f"  {C.BOLD}(1){C.RESET} Start scheduler now")
    print(f"  {C.DIM}    Launches a background process to run all schedules.{C.RESET}")
    print(f"  {C.BOLD}(2){C.RESET} Return to menu")
    print(f"  {C.DIM}    Start the scheduler later with (s) on the main page.{C.RESET}")
    choice = read(min=1, max=2, digit=True)

    if choice == 2:
        print(f"  {C.DIM}Schedule #{sid} saved as pending. Press (s) on the main page to start.{C.RESET}")
        enter()
        event.set()
        return

    transport_csv_update(session, sid, status="active")

    wlock = transport_worker_lock_path(session)
    if not _lock_acquire(wlock, timeout=1, stale_after=WORKER_LOCK_STALE_SECONDS):
        if _try_recover_stale_lock(session, wlock):
            if not _lock_acquire(wlock, timeout=5,
                                 stale_after=WORKER_LOCK_STALE_SECONDS):
                print("  Scheduler started by another process — "
                      "it will pick up the schedule.")
                enter()
                event.set()
                return
        else:
            print("  Scheduler started by another process — "
                  "it will pick up the schedule.")
            enter()
            event.set()
            return

    all_rows = transport_csv_load(session)
    extra_activated = 0
    for r in all_rows:
        if r.get("status") == "pending" and r.get("schedule_id") != sid:
            transport_csv_update(session, r["schedule_id"], status="active")
            extra_activated += 1
    if extra_activated > 0:
        print(f"  Also activated {extra_activated} other pending schedule(s).")

    TRANSPORT_WORKER_PREFS["notif_config"] = notif_config
    TRANSPORT_WORKER_PREFS["log_path"] = log_path

    try:
        os.remove(transport_stop_flag_path(session))
    except OSError:
        pass

    set_child_mode(session)
    event.set()

    info = (
        f"\nTransport worker started (schedule #{sid} {mode_label})\n"
        f"  CSV: {transport_csv_path(session)}\n"
    )
    setInfoSignal(session, info)

    stop_event = threading.Event()
    try:
        transport_scheduler_loop(session, stop_event)
    except Exception:
        try:
            sendToBot(
                session,
                f"Transport worker crashed:\n{traceback.format_exc()}",
            )
        except Exception:
            pass
    finally:
        _lock_release(wlock)
        try:
            session.logout()
        except Exception:
            pass


def _is_pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _try_recover_stale_lock(session, wlock):
    """Attempt to recover from a stale scheduler lock.
    Signals the old process to stop, waits for it to die, then clears the lock.
    Returns True if the lock was recovered."""
    if not os.path.exists(wlock):
        return True
    try:
        with open(wlock, "r") as f:
            data = json.load(f)
    except Exception:
        try:
            os.remove(wlock)
        except OSError:
            pass
        return True

    lock_pid = data.get("pid")
    if not lock_pid or not _is_pid_alive(lock_pid):
        try:
            os.remove(wlock)
        except OSError:
            pass
        return True

    # Process is alive — signal it to stop via the stop flag
    stop_path = transport_stop_flag_path(session)
    try:
        with open(stop_path, "w") as f:
            f.write(str(int(time.time())))
    except OSError:
        pass

    for _ in range(15):
        time.sleep(2)
        if not _is_pid_alive(lock_pid):
            try:
                os.remove(wlock)
            except OSError:
                pass
            return True
        if not os.path.exists(wlock):
            return True

    return False


def _is_transport_worker_running(session):
    wlock = transport_worker_lock_path(session)
    if not os.path.exists(wlock):
        return False
    try:
        with open(wlock, "r") as f:
            data = json.load(f)
        if time.time() - data.get("timestamp", 0) > WORKER_LOCK_STALE_SECONDS:
            return False
        pid = data.get("pid")
        if pid and not _is_pid_alive(pid):
            try:
                os.remove(wlock)
            except OSError:
                pass
            return False
        return True
    except Exception:
        return False


# ----------------------------------------------------------------------------
#  Mode-specific single-cycle handlers
# ----------------------------------------------------------------------------

_RES_NAMES = ["Wood", "Wine", "Marble", "Crystal", "Sulphur"]


def _notify_resource_exhaustion(session, notif_config, mode_label,
                                exhaustion_log):
    if not exhaustion_log:
        return
    if not should_notify(notif_config, "error"):
        return
    lines = ["RESOURCE SUPPLY EXHAUSTED"]
    total_missing = [0] * 5
    for src_name, dst_name, shortfalls_dict in exhaustion_log:
        parts = [f"{_RES_NAMES[i]}:{v:,}" for i, v in shortfalls_dict.items()
                 if v > 0]
        lines.append(f"  {src_name} -> {dst_name}: missing {', '.join(parts)}")
        for i, v in shortfalls_dict.items():
            if i < 5:
                total_missing[i] += v
    tot_parts = [f"{_RES_NAMES[i]}:{v:,}"
                 for i, v in enumerate(total_missing) if v > 0]
    lines.append(f"Total cancelled: {', '.join(tot_parts)}")
    lines.append("These resources were cancelled for this cycle.")
    sendToBot(session, f"{mode_label}\n" + "\n".join(lines))


def _notify_small_shipments(session, notif_config, mode_label,
                            small_shipments, min_threshold):
    if not small_shipments:
        return
    if not should_notify(notif_config, "error"):
        return
    lines = [f"SMALL SHIPMENTS SKIPPED (below {min_threshold:,})"]
    total_unshipped = [0] * 5
    for src, dst, res in small_shipments:
        parts = [f"{_RES_NAMES[i]}:{v:,}" for i, v in enumerate(res) if v > 0]
        lines.append(f"  {src} -> {dst}: {', '.join(parts)}")
        for i in range(min(5, len(res))):
            total_unshipped[i] += res[i]
    tot_parts = [f"{_RES_NAMES[i]}:{v:,}"
                 for i, v in enumerate(total_unshipped) if v > 0]
    lines.append(f"Total unshipped: {', '.join(tot_parts)}")
    sendToBot(session, f"{mode_label}\n" + "\n".join(lines))


def run_consolidate_cycle(session, sched, notif_config, log_path):
    source_city_ids = sched.get("source_city_ids") or []
    dest_city_ids = sched.get("dest_city_ids") or []
    resource_config = sched.get("resource_config") or [0, 0, 0, 0, 0]
    send_mode_str = sched.get("send_mode", "send")
    send_mode = 1 if send_mode_str == "keep" else 2
    dest_minimums = sched.get("dest_minimums")
    ship_type = sched.get("ship_type", "m")
    useFreighters = (ship_type == "f")

    if not source_city_ids or not dest_city_ids:
        return 0

    dest_city_id = str(dest_city_ids[0])
    html = session.get(city_url + dest_city_id)
    try:
        destination_city = getCity(html)
    except (AttributeError, TypeError, KeyError):
        if should_notify(notif_config, "error"):
            sendToBot(session,
                      f"CONSOLIDATE ERROR\n"
                      f"Could not load destination city (ID: {dest_city_id}).\n"
                      f"Session may have expired or city no longer exists.")
        return 0
    dest_isl_id = destination_city["islandId"]
    html_isl = session.get(island_url + str(dest_isl_id))
    island = getIsland(html_isl)
    coords = f"[{island['x']}:{island['y']}]"

    excluded = _rrs_excluded_set(session)
    summary = _rrs_load_summary(session)
    min_threshold = int(sched.get("min_shipment_threshold", 0) or 0)
    small_shipments = []
    exhaustion_log = []

    cycle_sent = 0
    for cid in source_city_ids:
        if int(cid) in excluded:
            continue
        html = session.get(city_url + str(cid))
        try:
            oc_fresh = getCity(html)
        except (AttributeError, TypeError, KeyError):
            continue

        toSend = [0] * len(materials_names)
        total = 0
        for i in range(len(materials_names)):
            if i >= len(resource_config) or resource_config[i] is None:
                continue
            avail = oc_fresh["availableResources"][i]
            free = _rrs_free_from_summary(summary, cid, i, avail) if RRS_AVAILABLE else avail
            if send_mode == 1:
                s = (free if resource_config[i] == 0
                     else max(0, free - resource_config[i]))
            else:
                s = (0 if resource_config[i] == 0
                     else min(resource_config[i], free))
            try:
                s = min(s, destination_city["freeSpaceForResources"][i])
            except (KeyError, IndexError):
                pass
            if dest_minimums and i < len(dest_minimums) and dest_minimums[i]:
                s = apply_dest_minimums(
                    s, destination_city["availableResources"][i],
                    dest_minimums[i],
                )
            toSend[i] = s
            total += s

        if total > 0:
            route = (oc_fresh, destination_city, island["id"], *toSend)
            result = send_shipment(
                session, route, useFreighters, notif_config, log_path,
                "Consolidate", coords, min_threshold=min_threshold,
            )
            if result.get("below_threshold"):
                small_shipments.append(
                    (oc_fresh["name"], destination_city["name"], toSend))
            elif result["success"]:
                cycle_sent += 1
                html = session.get(city_url + dest_city_id)
                destination_city = getCity(html)
            if result.get("shortfalls"):
                exhaustion_log.append(
                    (oc_fresh["name"], destination_city["name"],
                     result["shortfalls"]))

    _notify_small_shipments(session, notif_config, "CONSOLIDATE",
                            small_shipments, min_threshold)
    _notify_resource_exhaustion(session, notif_config, "CONSOLIDATE",
                                exhaustion_log)
    return cycle_sent


def run_distribute_cycle(session, sched, notif_config, log_path):
    source_city_ids = sched.get("source_city_ids") or []
    dest_city_ids = sched.get("dest_city_ids") or []
    resource_config = sched.get("resource_config") or [0, 0, 0, 0, 0]
    dest_minimums = sched.get("dest_minimums")
    ship_type = sched.get("ship_type", "m")
    useFreighters = (ship_type == "f")

    if not source_city_ids or not dest_city_ids:
        return 0

    src_city_id = str(source_city_ids[0])
    if _rrs_is_excluded(session, src_city_id):
        return 0

    min_threshold = int(sched.get("min_shipment_threshold", 0) or 0)
    small_shipments = []
    exhaustion_log = []
    exhausted_res = set()
    cycle_sent = 0

    for dcid in dest_city_ids:
        html = session.get(city_url + str(dcid))
        try:
            dest_city = getCity(html)
        except (AttributeError, TypeError, KeyError):
            continue
        dest_isl_id = dest_city["islandId"]
        html_isl = session.get(island_url + str(dest_isl_id))
        dest_island = getIsland(html_isl)
        coords = f"[{dest_island['x']}:{dest_island['y']}]"

        html = session.get(city_url + src_city_id)
        try:
            origin_city = getCity(html)
        except (AttributeError, TypeError, KeyError):
            return cycle_sent

        toSend = [0] * len(materials_names)
        total = 0
        for i in range(len(materials_names)):
            if i in exhausted_res:
                continue
            if i >= len(resource_config) or resource_config[i] is None:
                continue
            avail = _rrs_free_amount(session, origin_city, i)
            s = (0 if resource_config[i] == 0
                 else min(resource_config[i], avail))
            try:
                s = min(s, dest_city["freeSpaceForResources"][i])
            except (KeyError, IndexError):
                pass
            if dest_minimums and i < len(dest_minimums) and dest_minimums[i]:
                s = apply_dest_minimums(
                    s, dest_city["availableResources"][i],
                    dest_minimums[i],
                )
            toSend[i] = s
            total += s

        if total > 0:
            route = (origin_city, dest_city, dest_island["id"], *toSend)
            result = send_shipment(
                session, route, useFreighters, notif_config, log_path,
                "Distribute", coords, min_threshold=min_threshold,
            )
            if result.get("below_threshold"):
                small_shipments.append(
                    (origin_city["name"], dest_city["name"], toSend))
            elif result["success"]:
                cycle_sent += 1
            if result.get("shortfalls"):
                exhaustion_log.append(
                    (origin_city["name"], dest_city["name"],
                     result["shortfalls"]))
                exhausted_res.update(result["shortfalls"].keys())

    _notify_small_shipments(session, notif_config, "DISTRIBUTE",
                            small_shipments, min_threshold)
    _notify_resource_exhaustion(session, notif_config, "DISTRIBUTE",
                                exhaustion_log)
    return cycle_sent


def run_topup_cycle(session, sched, notif_config, log_path):
    dest_city_ids = sched.get("dest_city_ids") or []
    source_city_ids = sched.get("source_city_ids") or []
    dest_targets = sched.get("dest_targets") or {}
    source_reserves = sched.get("source_reserves") or {}
    ship_type = sched.get("ship_type", "m")
    useFreighters = (ship_type == "f")

    if not dest_city_ids or not source_city_ids:
        return 0

    excluded = _rrs_excluded_set(session)
    summary = _rrs_load_summary(session)
    min_threshold = int(sched.get("min_shipment_threshold", 0) or 0)
    small_shipments = []
    exhaustion_log = []

    cycle_sent = 0
    for dcid in dest_city_ids:
        dcid_str = str(dcid)
        targets = dest_targets.get(dcid_str)
        if not targets:
            continue

        html = session.get(city_url + dcid_str)
        try:
            dest_fresh = getCity(html)
        except (AttributeError, TypeError, KeyError):
            continue
        dest_isl_id = dest_fresh["islandId"]
        html_isl = session.get(island_url + str(dest_isl_id))
        dest_island = getIsland(html_isl)
        coords = f"[{dest_island['x']}:{dest_island['y']}]"

        exhausted_res = set()
        for cid in source_city_ids:
            cid_str = str(cid)
            if int(cid) in excluded:
                continue
            needed = [0] * len(materials_names)
            for i in range(len(materials_names)):
                if i in exhausted_res:
                    continue
                if i >= len(targets) or targets[i] is None:
                    continue
                gap = targets[i] - dest_fresh["availableResources"][i]
                needed[i] = max(
                    0, min(gap, dest_fresh["freeSpaceForResources"][i])
                )

            if all(n <= 0 for n in needed):
                break

            html = session.get(city_url + cid_str)
            try:
                src_fresh = getCity(html)
            except (AttributeError, TypeError, KeyError):
                continue
            reserves = source_reserves.get(cid_str, [0] * len(materials_names))
            to_send = [0] * len(materials_names)
            for i in range(len(materials_names)):
                if needed[i] <= 0:
                    continue
                avail = src_fresh["availableResources"][i]
                free = _rrs_free_from_summary(summary, cid, i, avail) if RRS_AVAILABLE else avail
                reserve = reserves[i] if i < len(reserves) else 0
                sendable = max(0, free - reserve)
                to_send[i] = min(needed[i], sendable)

            if sum(to_send) > 0:
                route = (src_fresh, dest_fresh, dest_island["id"], *to_send)
                result = send_shipment(
                    session, route, useFreighters, notif_config, log_path,
                    "TopUp", coords, min_threshold=min_threshold,
                )
                if result.get("below_threshold"):
                    small_shipments.append(
                        (src_fresh["name"], dest_fresh["name"], to_send))
                elif result["success"]:
                    cycle_sent += 1
                    try:
                        html = session.get(city_url + dcid_str)
                        dest_fresh = getCity(html)
                    except (AttributeError, TypeError, KeyError):
                        break
                if result.get("shortfalls"):
                    exhaustion_log.append(
                        (src_fresh["name"], dest_fresh["name"],
                         result["shortfalls"]))
                    exhausted_res.update(result["shortfalls"].keys())

    _notify_small_shipments(session, notif_config, "TOPUP",
                            small_shipments, min_threshold)
    _notify_resource_exhaustion(session, notif_config, "TOPUP",
                                exhaustion_log)
    return cycle_sent


def run_even_cycle(session, sched, notif_config, log_path):
    city_ids = sched.get("source_city_ids") or []
    resource_indices = sched.get("resource_config") or []
    ship_type = sched.get("ship_type", "m")
    useFreighters = (ship_type == "f")

    if not city_ids or not resource_indices:
        return 0

    excluded = _rrs_excluded_set(session)
    summary = _rrs_load_summary(session)
    min_threshold = int(sched.get("min_shipment_threshold", 0) or 0)
    small_shipments = []
    exhaustion_log = []

    all_cities = []
    for cid in city_ids:
        if int(cid) in excluded:
            continue
        html = session.get(city_url + str(cid))
        try:
            all_cities.append(getCity(html))
        except (AttributeError, TypeError, KeyError):
            continue

    if not all_cities:
        return 0

    cycle_sent = 0

    for res_idx in resource_indices:
        if not isinstance(res_idx, int) or res_idx < 0 or res_idx >= len(materials_names):
            continue
        res_name = materials_names[res_idx]

        total = sum(
            _rrs_free_from_summary(summary, c["id"], res_idx, c["availableResources"][res_idx])
            if RRS_AVAILABLE else c["availableResources"][res_idx]
            for c in all_cities
        )
        target = total // len(all_cities)

        senders = []
        receivers = []
        for city in all_cities:
            actual = city["availableResources"][res_idx]
            current = _rrs_free_from_summary(summary, city["id"], res_idx, actual) if RRS_AVAILABLE else actual
            diff = current - target
            if diff > 0:
                senders.append({"from": city, "amount": diff})
            elif diff < 0:
                receivers.append({"to": city, "amount": abs(diff)})

        if not senders or not receivers:
            continue

        si, ri = 0, 0
        s_rem = senders[0]["amount"]
        r_rem = receivers[0]["amount"]

        while si < len(senders) and ri < len(receivers):
            amount = min(s_rem, r_rem)

            if amount > 0:
                toSend = [0] * len(materials_names)
                toSend[res_idx] = amount

                dest_isl_id = receivers[ri]["to"]["islandId"]
                html = session.get(island_url + str(dest_isl_id))
                dest_island = getIsland(html)

                route = (senders[si]["from"], receivers[ri]["to"],
                         dest_island["id"], *toSend)
                coords = f"[{dest_island['x']}:{dest_island['y']}]"

                result = send_shipment(
                    session, route, useFreighters, notif_config, log_path,
                    "Even Distribution", coords, min_threshold=min_threshold,
                )
                if result.get("below_threshold"):
                    small_shipments.append(
                        (senders[si]["from"]["name"],
                         receivers[ri]["to"]["name"], toSend))
                    s_rem -= amount
                    r_rem -= amount
                elif result["success"]:
                    cycle_sent += 1
                    s_rem -= amount
                    r_rem -= amount
                else:
                    break

                if result.get("shortfalls"):
                    shortfall_amt = result["shortfalls"].get(res_idx, 0)
                    exhaustion_log.append(
                        (senders[si]["from"]["name"],
                         receivers[ri]["to"]["name"],
                         result["shortfalls"]))
                    if shortfall_amt > 0:
                        s_rem = 0

            if s_rem == 0:
                si += 1
                if si < len(senders):
                    s_rem = senders[si]["amount"]
            if r_rem == 0:
                ri += 1
                if ri < len(receivers):
                    r_rem = receivers[ri]["amount"]

    _notify_small_shipments(session, notif_config, "EVEN DIST",
                            small_shipments, min_threshold)
    _notify_resource_exhaustion(session, notif_config, "EVEN DIST",
                                exhaustion_log)
    return cycle_sent


def run_autosend_cycle(session, sched, notif_config, log_path):
    dest_city_ids = sched.get("dest_city_ids") or []
    requested = sched.get("resource_config") or [0, 0, 0, 0, 0]
    ship_type = sched.get("ship_type", "m")
    useFreighters = (ship_type == "f")

    if not dest_city_ids or sum(requested) == 0:
        return 0

    dest_city_id = str(dest_city_ids[0])
    html = session.get(city_url + dest_city_id)
    try:
        destination_city = getCity(html)
    except (AttributeError, TypeError, KeyError):
        return 0
    html = session.get(island_url + destination_city["islandId"])
    destination_island = getIsland(html)

    excluded = _rrs_excluded_set(session)
    summary = _rrs_load_summary(session)

    html = session.get()
    city_ids = re.findall(r'<option value="(\d+)" class="cityowntown"', html)
    suppliers = []
    for cid in city_ids:
        if str(cid) == dest_city_id:
            continue
        if int(cid) in excluded:
            continue
        html_c = session.get(city_url + str(cid))
        try:
            suppliers.append(getCity(html_c))
        except (AttributeError, TypeError, KeyError):
            continue

    if not suppliers:
        return 0

    routes = allocate_from_suppliers(
        list(requested), suppliers, destination_city, destination_island,
        rrs_summary=summary,
    )
    if routes is None:
        if should_notify(notif_config, "error"):
            sendToBot(session,
                      f"AUTO SEND: Could not allocate resources for "
                      f"{destination_city['name']}")
        return 0

    min_threshold = int(sched.get("min_shipment_threshold", 0) or 0)
    small_shipments = []
    exhaustion_log = []
    cycle_sent = 0
    for route in routes:
        result = send_shipment(
            session, route, useFreighters, notif_config, log_path,
            "Auto Send", min_threshold=min_threshold,
        )
        if result.get("below_threshold"):
            small_shipments.append(
                (route[0]["name"], route[1]["name"], list(route[3:])))
        elif result["success"]:
            cycle_sent += 1
        elif result["error"] and "lock" in result["error"].lower():
            break
        if result.get("shortfalls"):
            exhaustion_log.append(
                (route[0]["name"], route[1]["name"],
                 result["shortfalls"]))

    _notify_small_shipments(session, notif_config, "AUTO SEND",
                            small_shipments, min_threshold)
    _notify_resource_exhaustion(session, notif_config, "AUTO SEND",
                                exhaustion_log)
    return cycle_sent


def run_bulk_cycle(session, sched, notif_config, log_path):
    csv_path = sched.get("bulk_csv_path", "")
    run_column = sched.get("bulk_run_column", "")
    if not csv_path or not run_column:
        return 0

    csv_resource_cols = ["Wood", "Wine", "Marble", "Crystal", "Sulphur"]

    try:
        rows = []
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames or [])
            for row in reader:
                rows.append(row)
    except Exception as e:
        if should_notify(notif_config, "error"):
            sendToBot(session, f"BULK DIST ERROR\nCould not read CSV: {e}")
        return 0

    if run_column not in (fieldnames or []):
        fieldnames, run_columns = ensure_run_columns(fieldnames, rows)
        if run_column not in run_columns:
            run_column = run_columns[0] if run_columns else run_column
        try:
            write_csv_atomic(csv_path, fieldnames, rows)
        except Exception:
            return 0
    else:
        fieldnames, _ = ensure_run_columns(fieldnames, rows)

    fieldnames = ensure_transport_column(fieldnames, rows)
    fieldnames = ensure_from_column(fieldnames, rows)
    issues_col = issues_col_for_run(run_column)
    for row in rows:
        row[issues_col] = ""

    city_cache = {}
    mismatches = []
    validated_cities = {}

    excluded = _rrs_excluded_set(session)
    summary = _rrs_load_summary(session)

    session.setStatus(
        f"[PRE-SCAN] Bulk Distribution | Validating {len(rows)} rows..."
    )

    for row_num, row in enumerate(rows, start=1):
        try:
            run_val = normalize_text(row.get(run_column, ""))
            if run_val == "x":
                continue

            x = row["X"].strip()
            y = row["Y"].strip()
            expected_player = row["Player"].strip()
            expected_city = row["City"].strip()
            expected_location = str(row.get("City_Location", "")).strip()

            parsed_resources = [
                parse_resource_value(row.get(col, "0"))
                for col in csv_resource_cols
            ]
            has_resources = any(
                amt > 0 or mode == "except"
                for mode, amt in parsed_resources
            )
            if not has_resources:
                continue

            from_val = parse_from_column(row.get("From", ""))
            if from_val is None:
                issue = "From column is empty"
                row[issues_col] = issue
                mismatches.append(f"Row {row_num}: {issue}")
                continue
            if isinstance(from_val, list):
                if "ids" not in city_cache:
                    ids_tmp, map_tmp = getIdsOfCities(session)
                    city_cache["ids"] = ids_tmp
                    city_cache["map"] = map_tmp
                max_idx = len(city_cache["ids"])
                bad = [str(i) for i in from_val if i > max_idx]
                if bad:
                    issue = f"From: city index {','.join(bad)} out of range"
                    row[issues_col] = issue
                    mismatches.append(f"Row {row_num}: {issue}")
                    continue

            html = session.get(f"view=island&xcoord={x}&ycoord={y}")
            island = getIsland(html)
            cities_on_island = [
                c for c in island["cities"] if c.get("type") == "city"
            ]

            matched_city = None
            candidates = []
            exp_city_n = normalize_text(expected_city)
            exp_player_n = normalize_text(expected_player)

            for c in cities_on_island:
                cn = normalize_text(c.get("name", ""))
                pn = normalize_text(c.get("Name", ""))
                if cn == exp_city_n and pn == exp_player_n:
                    candidates.append(c)

            if candidates:
                exp_loc = normalize_text(expected_location)
                if exp_loc:
                    for c in candidates:
                        loc = get_city_location_token(c)
                        if normalize_text(loc) == exp_loc:
                            matched_city = c
                            break
                if matched_city is None and len(candidates) == 1:
                    matched_city = candidates[0]

            if matched_city is None:
                issue = (f"City not found: {expected_player}/"
                         f"{expected_city} at [{x}:{y}]")
                row[issues_col] = issue
                mismatches.append(f"Row {row_num}: {issue}")
                continue

            if not expected_location:
                loc_token = get_city_location_token(matched_city)
                if loc_token:
                    row["City_Location"] = loc_token

            validated_cities[row_num] = (matched_city, island)

        except Exception as e:
            issue = f"Error: {e}"
            row[issues_col] = issue
            mismatches.append(f"Row {row_num}: {issue}")

    try:
        write_csv_atomic(csv_path, fieldnames, rows)
    except Exception as _csv_err:
        session.setStatus(f"[WARN] CSV write failed: {_csv_err}")

    if mismatches and should_notify(notif_config, "error"):
        sendToBot(session,
                  f"BULK DIST ISSUES\n" + "\n".join(mismatches))

    routes = []
    session.setStatus(
        f"[PROCESSING] Bulk Distribution | "
        f"Building routes for {len(validated_cities)} row(s)..."
    )

    city_cache.pop("objects", None)

    for row_num, row in enumerate(rows, start=1):
        run_val = normalize_text(row.get(run_column, ""))
        if run_val == "x":
            continue
        if row_num not in validated_cities:
            continue

        matched_city, island = validated_cities[row_num]
        x = row["X"].strip()
        y = row["Y"].strip()
        expected_player = row["Player"].strip()
        parsed_resources = [
            parse_resource_value(row.get(col, "0"))
            for col in csv_resource_cols
        ]

        from_val = parse_from_column(row.get("From", ""))
        row_use_freighters = parse_transport_value(row.get("Transport", "m"))
        try:
            src_cities = get_source_cities_for_row(
                session, from_val, city_cache
            )
        except Exception as e:
            row[issues_col] = f"Error resolving source cities: {e}"
            continue

        done_indices = set()
        if run_val and run_val != "x":
            for p in run_val.split(","):
                p = p.strip()
                if p.isdigit():
                    done_indices.add(int(p))

        try:
            dest_html = session.get(city_url + str(matched_city["id"]))
            dest_city = getCity(dest_html)
        except Exception as e:
            row[issues_col] = f"Error fetching city details: {e}"
            try:
                write_csv_atomic(csv_path, fieldnames, rows)
            except Exception as _csv_err:
                session.setStatus(f"[WARN] CSV write failed: {_csv_err}")
            continue

        dest_space = dest_city.get(
            "freeSpaceForResources", [0] * len(materials_names)
        )
        for src_idx, src_city in src_cities:
            if src_idx in done_indices:
                continue
            if int(src_city.get("id", 0)) in excluded:
                continue
            raw_avail = src_city.get("availableResources", [])
            if RRS_AVAILABLE:
                adjusted_avail = [
                    _rrs_free_from_summary(summary, src_city["id"], i, raw_avail[i])
                    if i < len(raw_avail) else 0
                    for i in range(len(materials_names))
                ]
            else:
                adjusted_avail = raw_avail
            resources = resolve_resources(
                parsed_resources, adjusted_avail,
                row, csv_resource_cols,
                issues_key=issues_col,
            )
            for i in range(len(resources)):
                if i < len(dest_space):
                    resources[i] = min(resources[i], dest_space[i])
            if sum(resources) == 0:
                continue
            route = (src_city, dest_city, island["id"], *resources)
            routes.append((
                row_num, route, resources, dest_city["name"],
                expected_player, x, y, src_city["name"], src_idx,
                row_use_freighters, parsed_resources,
            ))

    if len(routes) > 1:
        from collections import defaultdict
        groups = defaultdict(list)
        for route_info in routes:
            src_id = str(route_info[1][0]["id"])
            groups[src_id].append(route_info)
        interleaved = []
        while any(groups.values()):
            for src_id in list(groups.keys()):
                if groups[src_id]:
                    interleaved.append(groups[src_id].pop(0))
                else:
                    del groups[src_id]
        routes = interleaved

    if not routes:
        if should_notify(notif_config, "error"):
            sendToBot(session, "BULK DIST: No valid routes found")
        return 0

    if should_notify(notif_config, "start"):
        sendToBot(session, f"BULK DIST SCHEDULED\n{len(routes)} shipment(s)")

    completed = 0
    skipped = 0
    total = len(routes)
    summary = _rrs_load_summary(session)

    AP_CHECK_INTERVAL = 300  # 5 minutes between AP re-checks
    ap_blocked_cities = {}   # {src_city_id: last_check_timestamp}
    deferred_routes = []     # routes deferred due to no AP
    ap_wait_mins = int(sched.get("ap_max_wait_minutes", 120) or 120)
    max_ap_retries = max(0, ap_wait_mins // 5)  # e.g. 120min / 5 = 24 retries
    min_threshold = int(sched.get("min_shipment_threshold", 0) or 0)
    small_shipments = []     # (src_name, dest_name, resources) below threshold
    exhaustion_log = []
    exhausted_by_src = {}    # {src_city_id: set(resource_indices)}

    def _try_send_route(route_info):
        """Attempt to send one route. Returns (success, deferred, small)."""
        (row_num, route, resources, dest_name,
         player, rx, ry, src_name, src_idx,
         row_freighters, parsed_res) = route_info

        src_city_id = str(route[0]["id"])

        if src_city_id in ap_blocked_cities:
            return False, True, False

        src_exhausted = exhausted_by_src.get(src_city_id, set())

        has_except = any(m == "except" for m, _ in parsed_res)
        if has_except:
            src_fresh = getCity(session.get(city_url + src_city_id))
            raw_a = src_fresh.get("availableResources", [])
            if RRS_AVAILABLE:
                adj_a = [
                    _rrs_free_from_summary(summary, src_city_id, i, raw_a[i])
                    if i < len(raw_a) else 0
                    for i in range(len(materials_names))
                ]
            else:
                adj_a = raw_a
            resources = resolve_resources(
                parsed_res, adj_a,
                None, csv_resource_cols,
            )
            dest_city_id = str(route[1]["id"])
            dest_fresh = getCity(session.get(city_url + dest_city_id))
            dest_space = dest_fresh.get(
                "freeSpaceForResources", [0] * len(materials_names)
            )
            for i in range(len(resources)):
                if i < len(dest_space):
                    resources[i] = min(resources[i], dest_space[i])
            for i in src_exhausted:
                if i < len(resources):
                    resources[i] = 0
            if sum(resources) == 0:
                return False, False, False
            route = (src_fresh, dest_fresh, route[2], *resources)
        elif src_exhausted:
            resources = list(route[3:])
            for i in src_exhausted:
                if i < len(resources):
                    resources[i] = 0
            if sum(resources) == 0:
                return False, False, False
            route = (route[0], route[1], route[2], *resources)

        coords = f"[{rx}:{ry}]"
        result = send_shipment(
            session, route, row_freighters, notif_config,
            log_path, "Bulk Distribution", coords, player,
            min_threshold=min_threshold,
        )

        if result.get("shortfalls"):
            exhaustion_log.append((src_name, dest_name, result["shortfalls"]))
            if src_city_id not in exhausted_by_src:
                exhausted_by_src[src_city_id] = set()
            exhausted_by_src[src_city_id].update(result["shortfalls"].keys())

        if result.get("below_threshold"):
            small_shipments.append((src_name, dest_name, list(route[3:])))
            return False, False, True

        if result.get("no_ap"):
            ap_blocked_cities[src_city_id] = time.time()
            session.setStatus(
                f"[AP BLOCKED] {src_name} — deferring, will retry in 5min"
            )
            return False, True, False

        if result["success"]:
            from_val = parse_from_column(rows[row_num - 1].get("From", ""))
            cur = rows[row_num - 1].get(run_column, "").strip()
            done = set()
            if cur and cur.upper() != "X":
                for p in cur.split(","):
                    p = p.strip()
                    if p.isdigit():
                        done.add(int(p))
            done.add(src_idx)
            try:
                expected = get_source_cities_for_row(
                    session, from_val, city_cache
                )
                expected_indices = {i for i, _ in expected}
            except Exception:
                expected_indices = done
            if done >= expected_indices:
                rows[row_num - 1][run_column] = "X"
            else:
                rows[row_num - 1][run_column] = ",".join(
                    str(d) for d in sorted(done)
                )
            try:
                write_csv_atomic(csv_path, fieldnames, rows)
            except Exception as _csv_err:
                session.setStatus(f"[WARN] CSV write failed: {_csv_err}")
            return True, False, False

        return False, False, False

    def _mark_route_complete(route_info):
        """Mark a route's run column entry as done (for small-shipment skips)."""
        (row_num, route, _res, _dn, _pl, _rx, _ry, _sn, src_idx,
         _fr, _pr) = route_info
        from_val = parse_from_column(rows[row_num - 1].get("From", ""))
        cur = rows[row_num - 1].get(run_column, "").strip()
        done = set()
        if cur and cur.upper() != "X":
            for p in cur.split(","):
                p = p.strip()
                if p.isdigit():
                    done.add(int(p))
        done.add(src_idx)
        try:
            expected = get_source_cities_for_row(
                session, from_val, city_cache
            )
            expected_indices = {i for i, _ in expected}
        except Exception:
            expected_indices = done
        if done >= expected_indices:
            rows[row_num - 1][run_column] = "X"
        else:
            rows[row_num - 1][run_column] = ",".join(
                str(d) for d in sorted(done)
            )
        try:
            write_csv_atomic(csv_path, fieldnames, rows)
        except Exception:
            pass

    # --- First pass: send all routes, deferring AP-blocked ones ---
    for idx, route_info in enumerate(routes):
        src_name = route_info[7]
        dest_name = route_info[3]
        session.setStatus(
            f"[SENDING] Bulk Dist [{idx+1}/{total}] "
            f"{src_name} -> {dest_name}"
        )
        success, deferred, small = _try_send_route(route_info)
        if success:
            completed += 1
        elif small:
            _mark_route_complete(route_info)
            completed += 1
        elif deferred:
            deferred_routes.append(route_info)
        else:
            skipped += 1

    # --- Retry loop: re-check AP-blocked cities every 5 min ---
    retry_round = 0
    while deferred_routes and retry_round < max_ap_retries:
        retry_round += 1
        session.setStatus(
            f"[AP WAIT] {len(deferred_routes)} shipment(s) deferred, "
            f"waiting 5min (retry {retry_round}/{max_ap_retries})..."
        )
        time.sleep(AP_CHECK_INTERVAL)

        # Re-check AP for blocked cities
        unblocked = set()
        for cid in list(ap_blocked_cities.keys()):
            html = session.get(city_url + cid)
            ap = getActionPoints(html)
            if ap is not None and ap > 0:
                unblocked.add(cid)
                del ap_blocked_cities[cid]

        if not unblocked:
            continue

        session.setStatus(
            f"[AP RESTORED] {len(unblocked)} city(ies) unblocked, "
            f"retrying {len(deferred_routes)} shipment(s)..."
        )

        still_deferred = []
        for route_info in deferred_routes:
            src_city_id = str(route_info[1][0]["id"])
            if src_city_id in ap_blocked_cities:
                still_deferred.append(route_info)
                continue
            src_name = route_info[7]
            dest_name = route_info[3]
            session.setStatus(
                f"[RETRY] Bulk Dist {src_name} -> {dest_name}"
            )
            success, deferred, small = _try_send_route(route_info)
            if success:
                completed += 1
            elif small:
                _mark_route_complete(route_info)
                completed += 1
            elif deferred:
                still_deferred.append(route_info)
            else:
                skipped += 1
        deferred_routes = still_deferred

    if deferred_routes:
        skipped += len(deferred_routes)
        if should_notify(notif_config, "error"):
            blocked_names = set()
            for ri in deferred_routes:
                blocked_names.add(ri[7])
            sendToBot(session,
                      f"BULK DIST: {len(deferred_routes)} shipment(s) "
                      f"permanently skipped (no AP after "
                      f"{ap_wait_mins}min)\n"
                      f"Cities: {', '.join(sorted(blocked_names))}")

    _notify_small_shipments(session, notif_config, "BULK DIST",
                            small_shipments, min_threshold)
    _notify_resource_exhaustion(session, notif_config, "BULK DIST",
                                exhaustion_log)

    if should_notify(notif_config, "complete"):
        summ_str = f"{completed}/{total} sent"
        if skipped:
            summ_str += f", {skipped} skipped"
        if small_shipments:
            summ_str += f", {len(small_shipments)} below min"
        if exhaustion_log:
            summ_str += f", {len(exhaustion_log)} resource(s) exhausted"
        run_done = sum(
            1 for r in rows
            if normalize_text(r.get(run_column, "")) == "x"
        )
        sendToBot(session,
                  f"BULK DIST COMPLETE\n"
                  f"Slot: {run_column[4:]}\n"
                  f"Cycle: {summ_str}\n"
                  f"Progress: {run_done}/{len(rows)}")

    return completed


MODE_HANDLERS = {
    "consolidate": run_consolidate_cycle,
    "distribute":  run_distribute_cycle,
    "even":        run_even_cycle,
    "autosend":    run_autosend_cycle,
    "bulk":        run_bulk_cycle,
    "topup":       run_topup_cycle,
}


# ----------------------------------------------------------------------------
#  Schedule dispatcher
# ----------------------------------------------------------------------------

def execute_schedule(session, sched, notif_config, log_path):
    mode = sched.get("mode", "")
    handler = MODE_HANDLERS.get(mode)
    if handler is None:
        return 0

    sid = sched.get("schedule_id", "?")
    mode_label = mode.capitalize()
    session.setStatus(f"Schedule #{sid} ({mode_label}): executing cycle...")

    if should_notify(notif_config, "start"):
        sendToBot(
            session,
            f"SCHEDULE #{sid} CYCLE STARTING\n"
            f"Account: {session.username}\n"
            f"Mode: {mode_label}",
        )

    try:
        cycle_sent = handler(session, sched, notif_config, log_path)
    except Exception:
        _rrs_release_all(session)
        if should_notify(notif_config, "error"):
            sendToBot(
                session,
                f"SCHEDULE #{sid} ERROR\n"
                f"Mode: {mode_label}\n"
                f"Error: {traceback.format_exc()}",
            )
        return 0

    if should_notify(notif_config, "complete"):
        sendToBot(
            session,
            f"SCHEDULE #{sid} CYCLE COMPLETE\n"
            f"Mode: {mode_label}\n"
            f"Shipments: {cycle_sent}",
        )

    return cycle_sent


WAKE_POLL_SECONDS = 2

def _wait_or_wake(session, stop_event, total_seconds):
    elapsed = 0
    while elapsed < total_seconds:
        if stop_event.is_set():
            return
        chunk = min(WAKE_POLL_SECONDS, total_seconds - elapsed)
        stop_event.wait(chunk)
        elapsed += chunk
        if _consume_wake_flag(session):
            return


# ----------------------------------------------------------------------------
#  Scheduler main loop
# ----------------------------------------------------------------------------

def transport_scheduler_loop(session, stop_event):
    notif_config = TRANSPORT_WORKER_PREFS.get(
        "notif_config", {"level": "none", "telegram": False}
    )
    log_path = TRANSPORT_WORKER_PREFS.get("log_path", "")

    while not stop_event.is_set():
        if os.path.exists(transport_stop_flag_path(session)):
            stop_event.set()
            break

        _lock_refresh(transport_worker_lock_path(session))

        now = int(time.time())
        schedules = transport_csv_load(session)
        active = [s for s in schedules if s.get("status") == "active"]

        if not active:
            session.setStatus("Transport worker: no active schedules, sleeping...")
            _wait_or_wake(session, stop_event, TICK_BUDGET_SECONDS)
            continue

        for sched in active:
            if stop_event.is_set():
                break

            next_run = sched.get("next_run", "")
            if isinstance(next_run, int) and next_run > now:
                continue

            sid = sched["schedule_id"]
            try:
                cycle_sent = execute_schedule(session, sched, notif_config, log_path)
            except Exception as exc:
                try:
                    msg = f"Schedule {sid} crashed: {exc}"
                    sendToBot(session, msg)
                except Exception:
                    pass
                transport_csv_update(
                    session, sid,
                    last_run=now, next_run=now + 3600,
                    status="active",
                )
                continue

            total = sched.get("total_shipments", 0) + cycle_sent
            interval = sched.get("interval_hours", 0)
            run_at = sched.get("run_at_time", "")

            if interval > 0:
                if run_at:
                    next_ts = _next_run_for_time(run_at)
                else:
                    next_ts = now + interval * 3600
                transport_csv_update(
                    session, sid,
                    last_run=now, next_run=next_ts,
                    total_shipments=total, status="active",
                )
            else:
                transport_csv_delete(session, sid)

        # Auto-cleanup: delete one-time schedules older than 24h
        schedules = transport_csv_load(session)
        for s in schedules:
            if s.get("interval_hours", 0) == 0:
                created = s.get("created_at", 0)
                if isinstance(created, int) and created > 0:
                    if now - created > 86400:
                        transport_csv_delete(session, s.get("schedule_id"))

        schedules = transport_csv_load(session)
        active = [s for s in schedules if s.get("status") == "active"]
        now_ts = int(time.time())
        next_dues = []
        for s in active:
            nr = s.get("next_run", "")
            if isinstance(nr, int) and nr > now_ts:
                next_dues.append(nr)

        if next_dues:
            sleep_for = max(1, min(TICK_BUDGET_SECONDS, min(next_dues) - now_ts))
        else:
            sleep_for = TICK_BUDGET_SECONDS

        try:
            session.setStatus(
                f"Transport worker: {len(active)} schedule(s), "
                f"sleeping {sleep_for}s"
            )
        except Exception:
            pass

        _wait_or_wake(session, stop_event, sleep_for)

    # Cleanup
    _rrs_release_all(session)
    _consume_wake_flag(session)
    try:
        os.remove(transport_stop_flag_path(session))
    except OSError:
        pass
    _lock_release(transport_worker_lock_path(session))


# ----------------------------------------------------------------------------
#  Worker activation / stop
# ----------------------------------------------------------------------------

def _activate_transport_worker(session, event):
    try:
        telegram_enabled = checkTelegramData(session)
    except Exception:
        telegram_enabled = False

    notif_config = get_notification_config(telegram_enabled, event)
    if notif_config is None:
        return

    log_path = get_log_path(session)

    wlock = transport_worker_lock_path(session)
    if not _lock_acquire(wlock, timeout=1, stale_after=WORKER_LOCK_STALE_SECONDS):
        print(f"  {C.WARN}Lock detected — attempting to recover...{C.RESET}")
        if _try_recover_stale_lock(session, wlock):
            print(f"  {C.OK}Old scheduler stopped. Starting fresh.{C.RESET}")
            if not _lock_acquire(wlock, timeout=5,
                                 stale_after=WORKER_LOCK_STALE_SECONDS):
                print(f"  {C.WARN}Could not acquire lock after recovery.{C.RESET}")
                print(f"  {C.DIM}Try deleting: {wlock}{C.RESET}")
                enter()
                event.set()
                return
        else:
            print(f"  {C.WARN}Could not stop the existing scheduler.{C.RESET}")
            print(f"  {C.DIM}Try deleting: {wlock}{C.RESET}")
            enter()
            event.set()
            return

    if not enforce_transport_schema_or_abort(session):
        _lock_release(wlock)
        enter()
        event.set()
        return

    schedules = transport_csv_load(session)
    activatable = [
        s for s in schedules
        if s.get("status") in ("active", "pending")
    ]

    if not activatable:
        print(f"  {C.WARN}No schedules to run.{C.RESET}")
        print(f"  {C.DIM}Create a schedule first using options 1-6.{C.RESET}")
        _lock_release(wlock)
        enter()
        event.set()
        return

    print(f"\n  {C.BOLD}{len(activatable)} schedule(s) to activate:{C.RESET}\n")
    for s in activatable:
        sid = s.get("schedule_id", "?")
        mode = s.get("mode", "?").capitalize()
        interval = s.get("interval_hours", 0)
        notes = s.get("notes", "")
        status = s.get("status", "?")
        interval_str = f"every {interval}h" if interval > 0 else "one-shot"
        line = f"  {C.CYAN}#{sid}{C.RESET} {mode} ({interval_str})"
        if notes:
            line += f" — {notes}"
        if status == "pending":
            line += f" {C.YELLOW}[NEW]{C.RESET}"
        print(line)

    print(f"\n  {C.BOLD}Resume mode:{C.RESET}")
    print(f"  {C.BOLD}(1){C.RESET} Continue as scheduled")
    print(f"  {C.DIM}    Missed runs execute immediately, then resume normal timing.{C.RESET}")
    print(f"  {C.BOLD}(2){C.RESET} Start from now")
    print(f"  {C.DIM}    Reset all timers — first run happens after the interval.{C.RESET}")
    print(f"  {C.BOLD}('){C.RESET} Cancel")

    choice = read(min=1, max=2, digit=True, additionalValues=["'"])
    if choice == "'":
        _lock_release(wlock)
        event.set()
        return

    resume_mode = "continue" if choice == 1 else "from_now"

    now = int(time.time())
    for s in activatable:
        sid = s["schedule_id"]
        updates = {}
        if s.get("status") == "pending":
            updates["status"] = "active"
        if resume_mode == "from_now":
            interval = s.get("interval_hours", 0)
            updates["next_run"] = (now + interval * 3600) if interval > 0 else now
        elif resume_mode == "continue":
            nr = s.get("next_run", "")
            if nr == "" or nr == 0:
                updates["next_run"] = now
        if updates:
            transport_csv_update(session, sid, **updates)

    TRANSPORT_WORKER_PREFS["notif_config"] = notif_config
    TRANSPORT_WORKER_PREFS["log_path"] = log_path

    try:
        os.remove(transport_stop_flag_path(session))
    except OSError:
        pass

    set_child_mode(session)
    event.set()

    info = (
        f"\nTransport worker\n"
        f"  CSV: {transport_csv_path(session)}\n"
        f"  Schedules: {len(activatable)}\n"
        f"  Resume: {resume_mode}\n"
    )
    setInfoSignal(session, info)

    stop_event = threading.Event()
    try:
        transport_scheduler_loop(session, stop_event)
    except Exception:
        try:
            sendToBot(
                session,
                f"Transport worker crashed:\n{traceback.format_exc()}",
            )
        except Exception:
            pass
    finally:
        _lock_release(wlock)
        try:
            session.logout()
        except Exception:
            pass


def _stop_transport_worker(session):
    flag = transport_stop_flag_path(session)
    wlock = transport_worker_lock_path(session)
    if not os.path.exists(wlock):
        print(f"  {C.DIM}No scheduler appears to be running.{C.RESET}")
        enter()
        return
    pathlib.Path(flag).touch()
    print(
        f"  {C.OK}Stop signal sent.{C.RESET} "
        f"{C.DIM}The scheduler will exit within "
        f"{TICK_BUDGET_SECONDS}s after finishing any active shipment.{C.RESET}"
    )
    enter()


# ----------------------------------------------------------------------------
#  Schedule management menu  (Option 7)
# ----------------------------------------------------------------------------

def manage_schedules_menu(session, event, telegram_enabled, log_path):
    while True:
        if not enforce_transport_schema_or_abort(session):
            enter()
            return

        counts = transport_csv_count_by_status(session)
        total = sum(counts.values())

        print_module_banner("Manage Schedules")
        print(f"  {_scheduler_status_line(session)}\n")
        print(f"  {C.DIM}Use (s)/(o) on the main page to start/stop the scheduler.{C.RESET}\n")

        print(f"  {C.BOLD}(1){C.RESET} View schedules")
        print(f"  {C.DIM}    See all saved schedules and their current status.{C.RESET}")
        print(f"  {C.BOLD}(2){C.RESET} Modify schedule")
        print(f"  {C.DIM}    Change interval, resources, ship type, notes, etc.{C.RESET}")
        print(f"  {C.BOLD}(3){C.RESET} Pause / resume a schedule")
        print(f"  {C.BOLD}(4){C.RESET} Delete schedule(s)")
        print(f"  {C.BOLD}('){C.RESET} Back")

        choice = read(min=1, max=4, digit=True, additionalValues=["'"])
        if choice == "'":
            return

        if choice == 1:
            _view_schedules(session)
        elif choice == 2:
            _modify_schedule(session)
        elif choice == 3:
            _toggle_schedule_pause(session)
        elif choice == 4:
            _delete_schedules(session)


def _view_schedules(session):
    rows = transport_csv_load(session)
    if not rows:
        print(f"\n  {C.DIM}No schedules found.{C.RESET}\n")
        enter()
        return

    print(f"\n  {C.BOLD}{'ID':>4} {'Mode':<13} {'Status':<10} {'Repeat':<10} "
          f"{'Ships':<5} {'Sent':>6} {'Last Run':<12} {'Notes'}{C.RESET}")
    print(f"  {'---':>4} {'---':<13} {'---':<10} {'---':<10} "
          f"{'---':<5} {'---':>6} {'---':<12} {'---'}")

    _status_colours = {"pending": C.YELLOW, "active": C.GREEN,
                       "paused": C.DIM, "completed": C.CYAN,
                       "error": C.RED}
    _status_display = {"pending": "waiting", "active": "active",
                        "paused": "paused", "completed": "done",
                        "error": "error"}
    for r in rows:
        sid = r.get("schedule_id", "?")
        mode = r.get("mode", "?").capitalize()
        raw_status = r.get("status", "?")
        status = _status_display.get(raw_status, raw_status)
        sc = _status_colours.get(raw_status, "")
        interval = r.get("interval_hours", 0)
        ship = "F" if r.get("ship_type", "m") == "f" else "M"
        total_sent = r.get("total_shipments", 0)
        notes = (r.get("notes", "") or "")[:20]
        interval_str = f"{interval}h" if interval > 0 else "once"

        last_run = r.get("last_run", "")
        if isinstance(last_run, int) and last_run > 0:
            try:
                last_str = getDateTime(last_run)[5:16]
            except Exception:
                last_str = ""
        else:
            last_str = "never"

        print(f"  {sid:>4} {mode:<13} {sc}{status:<10}{C.RESET} {interval_str:<10} "
              f"{ship:<5} {total_sent:>6} {last_str:<12} {notes}")

    print(f"\n  {C.DIM}Total: {len(rows)} schedule(s){C.RESET}\n")
    enter()


def _view_schedule_detail(sched):
    sid = sched.get("schedule_id", "?")
    mode = sched.get("mode", "?")
    status = sched.get("status", "?")
    interval = sched.get("interval_hours", 0)
    ship = "Freighters" if sched.get("ship_type", "m") == "f" else "Merchant"
    total_sent = sched.get("total_shipments", 0)
    notes = sched.get("notes", "") or ""
    notif = sched.get("notif_level", "none")

    _sd = {"pending": "waiting", "active": "active", "paused": "paused",
           "completed": "done", "error": "error"}
    print(f"\n  Schedule #{sid}")
    print(f"  {'─' * 40}")
    print(f"  Mode:          {mode.capitalize()}")
    print(f"  Status:        {_sd.get(status, status)}")
    print(f"  Ship type:     {ship}")
    run_at = sched.get("run_at_time", "")
    if run_at:
        print(f"  Schedule:      Daily at {run_at} (server time)")
    else:
        print(f"  Interval:      {'one-shot' if interval == 0 else f'{interval}h'}")
    print(f"  Notifications: {notif}")
    print(f"  Total sent:    {total_sent}")
    print(f"  Notes:         {notes or '(none)'}")

    last_run = sched.get("last_run", "")
    if isinstance(last_run, int) and last_run > 0:
        try:
            print(f"  Last run:      {getDateTime(last_run)}")
        except Exception:
            pass

    next_run = sched.get("next_run", "")
    if isinstance(next_run, int) and next_run > 0:
        try:
            print(f"  Next run:      {getDateTime(next_run)}")
        except Exception:
            pass

    src_ids = sched.get("source_city_ids") or []
    if src_ids:
        count = len(src_ids) if isinstance(src_ids, list) else 1
        print(f"  Sources:       {count} city/cities (IDs: {', '.join(str(s) for s in src_ids)})")
    dest_ids = sched.get("dest_city_ids") or []
    if dest_ids:
        count = len(dest_ids) if isinstance(dest_ids, list) else 1
        print(f"  Destinations:  {count} city/cities (IDs: {', '.join(str(d) for d in dest_ids)})")
    res_cfg = sched.get("resource_config")
    if res_cfg:
        mode = sched.get("mode", "")
        if mode == "even" and isinstance(res_cfg, list):
            named = [materials_names[i] if i < len(materials_names) else f"#{i}"
                     for i in res_cfg]
            print(f"  Balancing:     {', '.join(named)}")
        elif isinstance(res_cfg, list) and len(res_cfg) == len(materials_names):
            print(f"  Resources:     {_format_resource_list(res_cfg)}")
        else:
            print(f"  Resources:     {res_cfg}")
    send_mode = sched.get("send_mode", "na")
    if send_mode != "na":
        labels = {"keep": "Keep reserves (send all except X)",
                  "send": "Send specific amounts"}
        print(f"  Send mode:     {labels.get(send_mode, send_mode)}")
    dest_min = sched.get("dest_minimums")
    if dest_min and any(d for d in dest_min if d):
        print(f"  Send if below: {_format_resource_list(dest_min)}")
    dest_tgt = sched.get("dest_targets")
    if dest_tgt:
        print(f"  Top-up targets:")
        if isinstance(dest_tgt, dict):
            for cid, vals in dest_tgt.items():
                print(f"    City {cid}: {_format_resource_list(vals)}")
        else:
            print(f"    {dest_tgt}")
    src_res = sched.get("source_reserves")
    if src_res:
        print(f"  Src reserves:")
        if isinstance(src_res, dict):
            for cid, vals in src_res.items():
                print(f"    City {cid}: {_format_resource_list(vals)}")
        else:
            print(f"    {src_res}")
    bulk_csv = sched.get("bulk_csv_path", "")
    if bulk_csv:
        print(f"  Bulk CSV:      {bulk_csv}")
    bulk_col = sched.get("bulk_run_column", "")
    if bulk_col:
        print(f"  Run column:    {bulk_col}")

    print(f"  {'─' * 40}")


def _modify_schedule(session):
    rows = transport_csv_load(session)
    if not rows:
        print("\n  No schedules found.\n")
        enter()
        return

    _view_schedules_compact(rows)
    print("  Enter schedule ID to modify (or ' to cancel):")
    sid_input = read(additionalValues=["'"])
    if sid_input == "'":
        return

    try:
        sid = int(sid_input)
    except ValueError:
        print("  Invalid ID.")
        enter()
        return

    target = None
    for r in rows:
        if r.get("schedule_id") == sid:
            target = r
            break

    if not target:
        print(f"  Schedule #{sid} not found.")
        enter()
        return

    while True:
        _view_schedule_detail(target)

        print(f"\n  {C.BOLD}What to modify?{C.RESET}")
        print(f"  {C.BOLD}(1){C.RESET} Interval (hours)")
        print(f"  {C.BOLD}(2){C.RESET} Ship type")
        print(f"  {C.BOLD}(3){C.RESET} Notes")
        print(f"  {C.BOLD}(4){C.RESET} Notification level")
        print(f"  {C.BOLD}(5){C.RESET} Resources")
        is_consolidate = target.get("mode") == "consolidate"
        if is_consolidate:
            print(f"  {C.BOLD}(6){C.RESET} Send mode")
        print(f"  {C.BOLD}(7){C.RESET} Send-only-if-below thresholds")
        mode = target.get("mode", "")
        has_cities = mode not in ("bulk", "even")
        if has_cities:
            print(f"  {C.BOLD}(8){C.RESET} Source / destination cities")
        print(f"  {C.BOLD}(9){C.RESET} AP wait & min shipment")
        print(f"  {C.BOLD}('){C.RESET} Back")

        choice = read(min=1, max=9, digit=True, additionalValues=["'"])
        if choice == "'":
            return

        if choice == 1:
            cur_rat = target.get("run_at_time", "")
            cur_int = target.get("interval_hours", 0)
            if cur_rat:
                print(f"\n  Current: Daily at {cur_rat} (server time)")
            else:
                print(f"\n  Current interval: {cur_int}h")
            print(f"\n  {C.BOLD}(1){C.RESET} Every X hours")
            print(f"  {C.BOLD}(2){C.RESET} Daily at specific time (server time)")
            print(f"  {C.BOLD}('){C.RESET} Cancel")
            tc = read(min=1, max=2, digit=True, additionalValues=["'"])
            if tc == "'":
                continue
            if tc == 1:
                print("  New interval (0 = one-shot, 1+ = recurring):")
                val = read(min=0, digit=True, additionalValues=["'"])
                if val == "'":
                    continue
                transport_csv_update(session, sid, interval_hours=val,
                                     run_at_time="")
                target["interval_hours"] = val
                target["run_at_time"] = ""
                if val > 0 and target.get("next_run", "") in ("", 0):
                    next_ts = int(time.time()) + val * 3600
                    transport_csv_update(session, sid, next_run=next_ts)
                    target["next_run"] = next_ts
                print(f"  Interval updated to {val}h.")
            else:
                print(f"  {C.DIM}All times use server time.{C.RESET}")
                print("  Enter time in HH:MM format (24h):")
                while True:
                    ti = read(msg="  Time: ", additionalValues=["'"])
                    if ti == "'":
                        break
                    m = re.match(r'^(\d{1,2}):(\d{2})$', ti.strip())
                    if m and 0 <= int(m.group(1)) <= 23 and 0 <= int(m.group(2)) <= 59:
                        ts = f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
                        transport_csv_update(session, sid, interval_hours=24,
                                             run_at_time=ts)
                        target["interval_hours"] = 24
                        target["run_at_time"] = ts
                        next_ts = _next_run_for_time(ts)
                        transport_csv_update(session, sid, next_run=next_ts)
                        target["next_run"] = next_ts
                        print(f"  {C.OK}Schedule set to daily at {ts} (server time).{C.RESET}")
                        break
                    print(f"  {C.HINT}Invalid. Use HH:MM (e.g. 06:00, 14:30){C.RESET}")

        elif choice == 2:
            current = "Freighters" if target.get("ship_type") == "f" else "Merchant"
            print(f"\n  Current: {current}")
            print(f"  {C.BOLD}(1){C.RESET} Merchant ships  {C.BOLD}(2){C.RESET} Freighters")
            st = read(min=1, max=2, digit=True, additionalValues=["'"])
            if st == "'":
                continue
            new_type = "f" if st == 2 else "m"
            transport_csv_update(session, sid, ship_type=new_type)
            target["ship_type"] = new_type
            label = "Freighters" if new_type == "f" else "Merchant ships"
            print(f"  {C.OK}Ship type updated to {label}.{C.RESET}")

        elif choice == 3:
            current = target.get("notes", "") or "(none)"
            print(f"\n  Current notes: {current}")
            print("  New notes (or Enter to clear):")
            new_notes = read(msg="  > ", empty=True, additionalValues=["'"])
            if new_notes == "'":
                continue
            transport_csv_update(session, sid, notes=new_notes)
            target["notes"] = new_notes
            print(f"  Notes updated.")

        elif choice == 4:
            current = target.get("notif_level", "none")
            print(f"\n  Current: {current}")
            print(f"  {C.BOLD}(1){C.RESET} Partial  {C.BOLD}(2){C.RESET} All  {C.BOLD}(3){C.RESET} Errors only")
            nl = read(min=1, max=3, digit=True, additionalValues=["'"])
            if nl == "'":
                continue
            levels = {1: "partial", 2: "all", 3: "none"}
            new_level = levels[nl]
            transport_csv_update(session, sid, notif_level=new_level)
            target["notif_level"] = new_level
            print(f"  Notification level updated to {new_level}.")

        elif choice == 5:
            current = target.get("resource_config") or []
            mode = target.get("mode", "")
            if mode == "even":
                named = [materials_names[i] if i < len(materials_names)
                         else f"#{i}" for i in (current or [])]
                print(f"\n  Currently balancing: {', '.join(named) or '(none)'}")
                print("  Enter resource numbers (comma-separated, e.g. 1,3,5):")
                print("  1=Wood, 2=Wine, 3=Marble, 4=Crystal, 5=Sulphur")
                raw = read(additionalValues=["'"])
                if raw == "'":
                    continue
                try:
                    indices = [int(x.strip()) - 1 for x in raw.split(",")]
                    indices = [i for i in indices if 0 <= i < len(materials_names)]
                except ValueError:
                    print("  Invalid input.")
                    enter()
                    continue
                transport_csv_update(session, sid, resource_config=indices)
                target["resource_config"] = indices
            else:
                print(f"\n  Current: {_format_resource_list(current) if isinstance(current, list) and len(current) == len(materials_names) else current}")
                print("  Enter new amounts (5 values, comma-separated):")
                print("  Wood,Wine,Marble,Crystal,Sulphur")
                print("  (0 = skip this resource, blank = keep current)")
                raw = read(msg="  > ", empty=True, additionalValues=["'"])
                if raw == "'" or not raw.strip():
                    continue
                try:
                    vals = [int(x.strip().replace(",", ""))
                            for x in raw.split(",")]
                    while len(vals) < 5:
                        vals.append(0)
                    vals = vals[:5]
                except ValueError:
                    print("  Invalid input.")
                    enter()
                    continue
                transport_csv_update(session, sid, resource_config=vals)
                target["resource_config"] = vals
            print(f"  Resources updated.")

        elif choice == 6:
            if not is_consolidate:
                print("  Send mode is only available for Consolidate schedules.")
                enter()
                continue
            current = target.get("send_mode", "send")
            print(f"\n  Current: {current}")
            print(f"  {C.BOLD}(1){C.RESET} Keep reserves (send all except X)")
            print(f"  {C.BOLD}(2){C.RESET} Send specific amounts")
            sm = read(min=1, max=2, digit=True, additionalValues=["'"])
            if sm == "'":
                continue
            new_sm = "keep" if sm == 1 else "send"
            transport_csv_update(session, sid, send_mode=new_sm)
            target["send_mode"] = new_sm
            print(f"  Send mode updated to {new_sm}.")

        elif choice == 7:
            current = target.get("dest_minimums") or [0, 0, 0, 0, 0]
            print(f"\n  Current: {_format_resource_list(current)}")
            print("  Enter new targets (5 values, comma-separated):")
            print("  Wood,Wine,Marble,Crystal,Sulphur")
            print("  (Only sends when destination has less than this, 0 = always send)")
            raw = read(msg="  > ", empty=True, additionalValues=["'"])
            if raw == "'" or not raw.strip():
                continue
            try:
                vals = [int(x.strip().replace(",", ""))
                        for x in raw.split(",")]
                while len(vals) < 5:
                    vals.append(0)
                vals = vals[:5]
            except ValueError:
                print("  Invalid input.")
                enter()
                continue
            transport_csv_update(session, sid, dest_minimums=vals)
            target["dest_minimums"] = vals
            print(f"  Destination minimums updated.")

        elif choice == 8:
            if not has_cities:
                print("  City editing is not available for this mode.")
                enter()
                continue
            src_ids = target.get("source_city_ids") or []
            dst_ids = target.get("dest_city_ids") or []
            print(f"\n  {C.BOLD}Current source cities:{C.RESET} "
                  f"{len(src_ids)} (IDs: {', '.join(str(s) for s in src_ids) or 'none'})")
            print(f"  {C.BOLD}Current destination cities:{C.RESET} "
                  f"{len(dst_ids)} (IDs: {', '.join(str(d) for d in dst_ids) or 'none'})")
            print(f"\n  {C.BOLD}(1){C.RESET} Change source cities")
            print(f"  {C.BOLD}(2){C.RESET} Change destination cities")
            print(f"  {C.BOLD}('){C.RESET} Back")
            city_choice = read(min=1, max=2, digit=True, additionalValues=["'"])
            if city_choice == "'":
                continue
            if city_choice == 1:
                if mode == "autosend":
                    print("  Auto Send selects sources automatically.")
                    enter()
                    continue
                print(f"\n  {C.DIM}Select source cities:{C.RESET}")
                src_msg = f"{C.DIM}Click cities to add as sources:{C.RESET}"
                new_src_ids, _ = rtm_ignoreCities(session, msg=src_msg)
                if not new_src_ids:
                    print(f"  {C.WARN}No cities selected, keeping current.{C.RESET}")
                    enter()
                    continue
                new_src = [str(sid_val) for sid_val in new_src_ids]
                transport_csv_update(session, sid, source_city_ids=new_src)
                target["source_city_ids"] = new_src
                print(f"  {C.OK}Source cities updated ({len(new_src)} selected).{C.RESET}")
            else:
                print(f"\n  {C.DIM}Select destination cities:{C.RESET}")
                dst_msg = f"{C.DIM}Click cities to add as destinations:{C.RESET}"
                new_dst_ids, _ = rtm_ignoreCities(session, msg=dst_msg)
                if not new_dst_ids:
                    print(f"  {C.WARN}No cities selected, keeping current.{C.RESET}")
                    enter()
                    continue
                new_dst = [str(did) for did in new_dst_ids]
                transport_csv_update(session, sid, dest_city_ids=new_dst)
                target["dest_city_ids"] = new_dst
                print(f"  {C.OK}Destination cities updated ({len(new_dst)} selected).{C.RESET}")

        elif choice == 9:
            ap_cur = target.get("ap_max_wait_minutes", 120)
            mt_cur = target.get("min_shipment_threshold", 0)
            print(f"\n  {C.BOLD}AP wait:{C.RESET}       {ap_cur} minutes")
            print(f"  {C.BOLD}Min shipment:{C.RESET}  "
                  f"{'off' if mt_cur == 0 else f'{mt_cur:,}'}")
            print(f"\n  {C.BOLD}(1){C.RESET} Change AP wait timer")
            print(f"  {C.BOLD}(2){C.RESET} Change min shipment threshold")
            print(f"  {C.BOLD}('){C.RESET} Back")
            adv_choice = read(min=1, max=2, digit=True, additionalValues=["'"])
            if adv_choice == "'":
                continue
            if adv_choice == 1:
                print(f"  How long to retry AP-blocked cities (minutes, 0=no retry):")
                ap_input = read(min=0, digit=True, additionalValues=["'"])
                if ap_input == "'":
                    continue
                ap_val = int(ap_input)
                transport_csv_update(session, sid, ap_max_wait_minutes=ap_val)
                target["ap_max_wait_minutes"] = ap_val
                print(f"  {C.OK}AP wait set to {ap_val} min.{C.RESET}")
            else:
                print(f"  Minimum total resources per shipment (0=off):")
                mt_input = read(min=0, digit=True, additionalValues=["'"])
                if mt_input == "'":
                    continue
                mt_val = int(mt_input)
                transport_csv_update(session, sid, min_shipment_threshold=mt_val)
                target["min_shipment_threshold"] = mt_val
                if mt_val > 0:
                    print(f"  {C.OK}Min shipment set to {mt_val:,}.{C.RESET}")
                else:
                    print(f"  {C.OK}Min shipment filter disabled.{C.RESET}")

        enter()


def _toggle_schedule_pause(session):
    rows = transport_csv_load(session)
    if not rows:
        print("\n  No schedules found.\n")
        enter()
        return

    _view_schedules_compact(rows)
    print("  Enter schedule ID to pause/resume (or ' to cancel):")
    sid_input = read(additionalValues=["'"])
    if sid_input == "'":
        return

    try:
        sid = int(sid_input)
    except ValueError:
        print("  Invalid ID.")
        enter()
        return

    target = None
    for r in rows:
        if r.get("schedule_id") == sid:
            target = r
            break

    if not target:
        print(f"  Schedule #{sid} not found.")
        enter()
        return

    current_status = target.get("status", "")
    if current_status == "active":
        transport_csv_update(session, sid, status="paused")
        print(f"  Schedule #{sid} paused. It won't run until you resume it.")
    elif current_status == "paused":
        transport_csv_update(session, sid, status="active")
        print(f"  Schedule #{sid} resumed. It will run on its next scheduled time.")
    elif current_status == "pending":
        transport_csv_update(session, sid, status="active")
        print(f"  Schedule #{sid} activated. Start the background scheduler to run it.")
    else:
        _sd = {"completed": "done", "error": "error"}
        label = _sd.get(current_status, current_status)
        print(f"  Schedule #{sid} is '{label}' and cannot be paused/resumed.")
    enter()


def _parse_id_ranges(text):
    """Parse comma-separated IDs and ranges (e.g. '1-20, 25-90, 3')."""
    ids = set()
    for part in text.split(","):
        part = part.strip()
        if "-" in part:
            bounds = part.split("-", 1)
            try:
                lo, hi = int(bounds[0].strip()), int(bounds[1].strip())
                ids.update(range(lo, hi + 1))
            except ValueError:
                continue
        else:
            try:
                ids.add(int(part))
            except ValueError:
                continue
    return sorted(ids)


def _delete_schedules(session):
    rows = transport_csv_load(session)
    if not rows:
        print("\n  No schedules found.\n")
        enter()
        return

    _view_schedules_compact(rows)
    print("  Enter ID(s) to delete (comma-sep, ranges with -, e.g. 1-20, 25-90):")
    sid_input = read(additionalValues=["'"])
    if sid_input == "'":
        return

    sids = _parse_id_ranges(sid_input)
    if not sids:
        print("  Invalid input.")
        enter()
        return

    existing = {r.get("schedule_id") for r in rows}
    to_delete = [s for s in sids if s in existing]
    not_found = [s for s in sids if s not in existing]

    if not_found:
        print(f"  Not found: {not_found}")
    if not to_delete:
        print("  Nothing to delete.")
        enter()
        return

    print(f"  Delete {len(to_delete)} schedule(s)? [y/N]")
    confirm = read(values=["y", "Y", "n", "N", ""])
    if confirm.lower() != "y":
        return

    for sid in to_delete:
        transport_csv_delete(session, sid)
    print(f"  Deleted {len(to_delete)} schedule(s).")
    enter()


def _view_schedules_compact(rows):
    for r in rows:
        sid = r.get("schedule_id", "?")
        mode = r.get("mode", "?").capitalize()
        status = r.get("status", "?")
        interval = r.get("interval_hours", 0)
        interval_str = f"every {interval}h" if interval > 0 else "once"
        print(f"    #{sid} {mode} ({interval_str}) [{status}]")
