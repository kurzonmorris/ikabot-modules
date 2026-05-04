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


# ============================================================================
#  BANNER
# ============================================================================

def print_module_banner(page_title=None):
    print("\n")
    print("\u2554" + "\u2550" * 58 + "\u2557")
    print("\u2551            RESOURCE TRANSPORT MANAGER v8.0                  \u2551")
    print("\u255a" + "\u2550" * 58 + "\u255d")
    if page_title:
        print(f"\n{page_title}")
        print("\u2500" * 58)
    print("")


# ============================================================================
#  NOTIFICATION CONFIG  (replaces overloaded telegram_enabled)
# ============================================================================

def get_notification_config(telegram_enabled, event):
    if telegram_enabled is False:
        print_module_banner()
        print("Telegram notifications are not configured.")
        print("Do you want to continue without notifications? [Y/n]")
        rta = read(values=["y", "Y", "n", "N", ""])
        if rta.lower() == "n":
            event.set()
            return None
        return {"level": "none", "telegram": False}

    print_module_banner("Notification Preferences")
    print("When do you want to receive Telegram notifications?")
    print("(1) Partial - Summary when each cycle starts + errors")
    print("(2) All - Every individual shipment")
    print("(3) None - No notifications")
    print("(') Back to main menu")
    choice = read(min=1, max=3, digit=True, additionalValues=["'"])
    if choice == "'":
        event.set()
        return None
    levels = {1: "partial", 2: "all", 3: "none"}
    return {"level": levels[choice], "telegram": True}


def should_notify(notif_config, event_type):
    if not notif_config or not notif_config.get("telegram"):
        return False
    level = notif_config.get("level", "none")
    if level == "none":
        return event_type == "error"
    if level == "all":
        return True
    if level == "partial":
        return event_type in ("start", "error", "complete")
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
        print(f"  Shipment log: {default_path}")
        return default_path
    # First time — ask the user
    print(f"Shipment log file (Enter for default):")
    print(f"  Default: {default_path}")
    print(f"  (All accounts share one file — each row has an Account column)")
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

SCHEDULE_SCHEMA_VERSION = 1

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
    "interval_hours",
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
    "created_at", "schema_version",
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


# --- Lock helpers (reusable for both shipping lock and CSV lock) ---

def _lock_acquire(lock_path, timeout=30, stale_after=60):
    start = time.time()
    while time.time() - start < timeout:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, json.dumps({
                    "pid": os.getpid(),
                    "timestamp": time.time(),
                }).encode())
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            try:
                with open(lock_path, "r") as f:
                    data = json.load(f)
                    if time.time() - data.get("timestamp", 0) > stale_after:
                        os.remove(lock_path)
                        continue
            except Exception:
                try:
                    os.remove(lock_path)
                except Exception:
                    pass
                continue
        except Exception:
            pass
        time.sleep(1)
    return False


def _lock_release(lock_path):
    try:
        if os.path.exists(lock_path):
            with open(lock_path, "r") as f:
                data = json.load(f)
                if data.get("pid") == os.getpid():
                    os.remove(lock_path)
                    return
            os.remove(lock_path)
    except Exception:
        try:
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
    if on_disk != SCHEDULE_SCHEMA_VERSION:
        print(f"Transport CSV schema version mismatch: file={on_disk}, "
              f"module={SCHEDULE_SCHEMA_VERSION}.")
        print(f"  CSV : {transport_csv_path(session)}")
        print(f"  Side: {sidecar}")
        print("  Move/rename both files to start fresh, then reopen.")
        return False
    return True


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
        os.replace(tmp, path)


def transport_csv_append(session, row):
    _transport_csv_modify(session, lambda rows: rows.append(row))


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
                       bulk_run_column="", interval_hours=0,
                       notif_level="none", status="pending",
                       notes=""):
    now_ts = int(time.time())
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
        "interval_hours":  interval_hours,
        "notif_level":     notif_level,
        "status":          status,
        "last_run":        "",
        "next_run":        now_ts if interval_hours > 0 else "",
        "total_shipments": 0,
        "created_at":      now_ts,
        "notes":           notes,
        "schema_version":  SCHEDULE_SCHEMA_VERSION,
    }


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


def wait_for_action_points(session, origin_city_id, status_prefix="",
                           max_wait=1800):
    """Navigate to source city and wait until action points are available.
    Returns the AP count (>0) on success, 0 on timeout, None if unparseable."""
    start = time.time()
    while True:
        html = session.get()
        current = getCity(html)
        if str(current["id"]) != str(origin_city_id):
            session.post(params={
                "action": "header",
                "function": "changeCurrentCity",
                "actionRequest": actionRequest,
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
#  SHARED SEND SHIPMENT  (lock → verify → send → verify → unlock → log)
# ============================================================================

def send_shipment(session, route, useFreighters, notif_config, log_path,
                  mode_name, dest_island_coords="", dest_player="",
                  max_lock_retries=3, next_shipment_str=None):
    origin_city = route[0]
    dest_city = route[1]
    resources = list(route[3:])
    total_cargo = sum(resources)
    ship_type_name = "freighters" if useFreighters else "merchant ships"
    prefix = f"{origin_city['name']} -> {dest_city['name']} | "

    result = {"success": False, "error": None, "ships_used": 0}

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

    # 1b. Wait for action points on source city
    ap = wait_for_action_points(session, origin_city["id"], prefix)
    if ap == 0:
        result["error"] = (
            f"No action points available for {origin_city['name']} (timed out)"
        )
        if should_notify(notif_config, "error"):
            sendToBot(session,
                      f"SHIPMENT SKIPPED\n{prefix}\n{result['error']}")
        log_shipment(log_path, session, mode_name,
                     origin_city["name"], "", dest_city["name"],
                     dest_island_coords, dest_player, resources,
                     0, ship_type_name, "SKIPPED", result["error"],
                     next_shipment_str)
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


def ensure_issues_column(fieldnames, rows):
    """Add Issues column if missing (backward compatibility). Returns updated fieldnames."""
    if "Issues" not in fieldnames:
        insert_idx = len(fieldnames)
        if "Hours" in fieldnames:
            insert_idx = fieldnames.index("Hours") + 1
        else:
            for i, col in enumerate(fieldnames):
                if col.startswith("Run_"):
                    insert_idx = i
                    break
        fieldnames.insert(insert_idx, "Issues")
        for row in rows:
            row["Issues"] = ""
    return fieldnames


def parse_resource_value(val):
    """Parse a resource cell: '500' -> ('exact', 500), 'e10000' -> ('except', 10000).
    Handles commas in numbers (e.g. '10,000' -> 10000)."""
    val = val.strip()
    if val.lower().startswith("e"):
        num_part = val[1:].replace(",", "").strip()
        match = re.search(r"\d+", num_part)
        return ("except", int(match.group()) if match else 0)
    val_clean = val.replace(",", "")
    return ("exact", int(val_clean) if val_clean.isdigit() else 0)


def resolve_resources(parsed, source_available, row, csv_resource_cols):
    """Resolve parsed resource values against source city stock.
    'except' mode: send (available - reserve), log issue if insufficient."""
    resolved = []
    for i, (mode, amount) in enumerate(parsed):
        if mode == "except":
            avail = source_available[i] if i < len(source_available) else 0
            if avail <= amount:
                resolved.append(0)
                if row is not None:
                    prev = row.get("Issues", "")
                    note = f"{csv_resource_cols[i]}: stock {avail} <= reserve {amount}"
                    row["Issues"] = f"{prev}; {note}" if prev else note
            else:
                resolved.append(avail - amount)
        else:
            resolved.append(amount)
    return resolved


def choose_run_slot(session, event, rows, run_columns):
    print_module_banner("Bulk Distribution - Run Slot")
    print("Choose how to start:")
    print("(1) Start fresh (reuses the OLDEST run slot)")
    print("(2) Resume from existing run slot")
    print("(') Back to main menu")
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
        for row in rows:
            row[new_col] = ""
            if oldest_col in row:
                del row[oldest_col]
        return mode, new_col

    # Resume mode
    print("")
    print("Select run slot to resume:")
    for i, col in enumerate(run_columns):
        done = completion.get(col, 0)
        print(f"({i + 1}) {col[4:]}  [{done}/{total_rows} sent]")
    print("(') Back to main menu")
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
        cleaned = user_input.replace(",", "").replace(" ", "")
        if cleaned.isdigit():
            amount = int(cleaned)
            if amount > 0:
                print(f"  -> Set to: {addThousandSeparator(amount)}")
            return amount
        print("  Please enter a number, 0, leave blank, or press ' to exit")


def get_resource_config(send_mode=2):
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
            if send_mode == 2 and amount is None:
                amount = 0
            config.append(amount)
        if not restart:
            return config


def get_dest_minimums():
    print("")
    print("Set minimum thresholds for destination?")
    print("(Only send if destination has LESS than the minimum)")
    print("(1) Yes - set minimums per resource")
    print("(2) No - send regardless")
    choice = read(min=1, max=2, digit=True)
    if choice == 2:
        return None
    print("")
    print("Enter minimum for each resource (blank = no minimum):")
    minimums = []
    for resource in materials_names:
        amount = readResourceAmount(f"Min {resource}")
        if amount in ("EXIT", "RESTART"):
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


def rtm_ignoreCities(session, msg=None):
    """Replacement for ignoreCities that shows full resource names + coords."""
    (cities_ids, cities) = getIdsOfCities(session)
    ignored_cities = []
    while True:
        print_module_banner()
        if msg is not None:
            print(f"{msg}")
        if ignored_cities:
            print(f'(currently ignoring: {", ".join(ignored_cities)})')
        print("(0) Continue")
        longest = max(len(cities[cid]["name"]) for cid in cities_ids) if cities_ids else 0
        choice_to_cityid_map = []
        for i, city_id in enumerate(cities_ids, 1):
            city = cities[city_id]
            choice_to_cityid_map.append(city["id"])
            name = city["name"]
            pad = " " * (longest - len(name) + 2)
            resource = _TRADEGOOD_NAMES.get(int(city.get("tradegood", 0)), "???")
            coords = city.get("coords", "").strip()
            print(f"{i}) {name}{pad}{resource:<9} {coords}")
        choice = read(min=0, max=len(cities_ids))
        if choice == 0:
            break
        city_id = choice_to_cityid_map[choice - 1]
        cities_ids = list(filter(lambda x: x != str(city_id), cities_ids))
        ignored_cities.append(cities[str(city_id)]["name"])
        del cities[str(city_id)]

    return cities_ids, cities


# ============================================================================
#  DRY RUN PREVIEW
# ============================================================================

def run_dry_preview(routes_info, mode_name):
    print_module_banner(f"DRY RUN PREVIEW - {mode_name}")
    print(f"  {'#':<4} {'From':<18} {'To':<18}", end="")
    for res in materials_names:
        print(f" {res:>9}", end="")
    print("")
    print(f"  {'--':<4} {'-'*18:<18} {'-'*18:<18}", end="")
    for _ in materials_names:
        print(f" {'-'*9:>9}", end="")
    print("")

    totals = [0] * len(materials_names)
    for idx, info in enumerate(routes_info):
        src = info["source"][:18] if len(info["source"]) <= 18 else info["source"][:15] + "..."
        dst = info["dest"][:18] if len(info["dest"]) <= 18 else info["dest"][:15] + "..."
        resources = info["resources"]
        print(f"  {idx+1:<4} {src:<18} {dst:<18}", end="")
        for i in range(len(materials_names)):
            val = resources[i] if i < len(resources) else 0
            totals[i] += val
            if val > 0:
                print(f" {addThousandSeparator(val):>9}", end="")
            else:
                print(f" {'0':>9}", end="")
        print("")

    print(f"  {'--':<4} {'-'*18:<18} {'-'*18:<18}", end="")
    for _ in materials_names:
        print(f" {'-'*9:>9}", end="")
    print("")
    print(f"  {'':4} {'':18} {'TOTAL':<18}", end="")
    for i in range(len(materials_names)):
        print(f" {addThousandSeparator(totals[i]):>9}", end="")
    print("\n")
    print("  --- DRY RUN: No resources were sent. ---\n")


# ============================================================================
#  MAIN ENTRY POINT
# ============================================================================

def resourceTransportManager(session, event, stdin_fd, predetermined_input):
    sys.stdin = os.fdopen(stdin_fd)
    config.predetermined_input = predetermined_input

    try:
        telegram_enabled = checkTelegramData(session)

        print_module_banner("Shipment Log Setup")
        log_path = get_log_path(session)

        print_module_banner("Shipping Mode Selection")
        print("Select shipping mode:")
        print("(1) Consolidate: Multiple cities -> One destination")
        print("(2) Distribute: One city -> Multiple destinations")
        print("(3) Even Distribution: Balance resources across cities")
        print("(4) Auto Send: Request resources, auto-collect from all")
        print("(5) Bulk Distribution: Persistent CSV-driven sends")
        print("(6) Keep Topped Up: Automatically top up a city's resources")
        print("(7) Manage Schedules")
        print("(') Back to main menu")
        shipping_mode = read(min=1, max=7, digit=True, additionalValues=["'"])
        if shipping_mode == "'":
            event.set()
            return

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
        elif shipping_mode == 7:
            manage_schedules_menu(session, event, telegram_enabled,
                                  log_path)

    except KeyboardInterrupt:
        event.set()
        return


# ============================================================================
#  MODE 1: CONSOLIDATE  (many sources -> one destination)
# ============================================================================

def consolidateMode(session, event, stdin_fd, predetermined_input,
                    telegram_enabled, log_path):
    try:
        print_module_banner("Ship Type Selection")
        print("What type of ships do you want to use?")
        print("(1) Merchant ships")
        print("(2) Freighters")
        print("(') Back to main menu")
        shiptype = read(min=1, max=2, digit=True, additionalValues=["'"])
        if shiptype == "'":
            event.set()
            return
        useFreighters = (shiptype == 2)

        print_module_banner("Source City Selection")
        print("Select source city option:")
        print("(1) Single city")
        print("(2) Multiple cities")
        print("(') Back to main menu")
        source_option = read(min=1, max=2, digit=True, additionalValues=["'"])
        if source_option == "'":
            event.set()
            return

        origin_cities = []
        if source_option == 1:
            print_module_banner("Single Source City")
            print("Select source city:")
            origin_city = rtm_chooseCity(session)
            origin_cities.append(origin_city)
        else:
            print_module_banner("Multiple Source Cities")
            source_msg = "Select source cities (cities to send resources from):"
            source_city_ids, _ = rtm_ignoreCities(session, msg=source_msg)
            if not source_city_ids:
                print("No cities selected!")
                enter()
                event.set()
                return
            for city_id in source_city_ids:
                html = session.get(city_url + city_id)
                city = getCity(html)
                origin_cities.append(city)

        print_module_banner("Sending Mode Selection")
        source_summary = ", ".join(c["name"] for c in origin_cities)
        print(f"Source cities: {source_summary}")
        print("")
        print("Choose sending mode:")
        print("(1) Send ALL EXCEPT a reserve amount (keep X, send rest)")
        print("(2) Send SPECIFIC amounts (send exactly X)")
        print("(') Back to main menu")
        send_mode = read(min=1, max=2, digit=True, additionalValues=["'"])
        if send_mode == "'":
            event.set()
            return

        print_module_banner("Resource Configuration")
        print(f"Source cities: {source_summary}\n")
        if send_mode == 1:
            print("Configure resource reserves (KEEP mode):")
            print("(Number = keep that amount, 0 = send ALL, blank = IGNORE)")
            print("(Commas optional: 6000 or 6,000)")
            print("(= restart | ' exit)\n")
        else:
            print("Configure resource amounts to send:")
            print("(Number = send that amount, 0 or blank = skip)")
            print("(Commas optional: 6000 or 6,000)")
            print("(= restart | ' exit)\n")
            if len(origin_cities) == 1:
                html = session.get(city_url + str(origin_cities[0]["id"]))
                cdata = getCity(html)
                for i, res in enumerate(materials_names):
                    avail = cdata["availableResources"][i]
                    print(f"  {res}: {addThousandSeparator(avail)} available")
                print("")

        resource_config = get_resource_config(send_mode)
        if resource_config is None:
            event.set()
            return

        # --- Destination ---
        print_module_banner("Destination Selection")
        print(f"Source cities: {source_summary}\n")
        print("Select destination type:")
        print("(1) Internal city (your cities)")
        print("(2) External city (island coordinates)")
        print("(') Back to main menu")
        dest_type = read(min=1, max=2, digit=True, additionalValues=["'"])
        if dest_type == "'":
            event.set()
            return

        if dest_type == 2:
            # External city
            coords_done = False
            while not coords_done:
                print_module_banner("Island Coordinates")
                print("Enter destination island coordinates:")
                print("(' exit | = restart)\n")
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
            print_module_banner("Internal City Selection")
            print("Select destination city:\n")
            destination_city = rtm_chooseCity(session)
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

        # Schedule
        print_module_banner("Schedule Configuration")
        interval_confirmed = False
        while not interval_confirmed:
            print("How often to send (in hours)?")
            print("(0 = one-time | 1+ = recurring)")
            print("(') Back to main menu")
            interval_hours = read(min=0, digit=True, additionalValues=["'"])
            if interval_hours == "'":
                event.set()
                return
            print("")
            if interval_hours == 0:
                print("Mode: One-time shipment")
            else:
                print(f"Interval: Every {interval_hours} hour(s)")
            print("(1) Confirm  (2) Re-enter")
            if read(min=1, max=2, digit=True) == 1:
                interval_confirmed = True

        # --- Calculate preview ---
        total_send = [0] * len(materials_names)
        preview_routes = []
        for oc in origin_cities:
            html = session.get(city_url + str(oc["id"]))
            odata = getCity(html)
            to_send = [0] * len(materials_names)
            for i in range(len(materials_names)):
                if resource_config[i] is None:
                    continue
                avail = odata["availableResources"][i]
                if send_mode == 1:
                    s = avail if resource_config[i] == 0 else max(0, avail - resource_config[i])
                else:
                    s = 0 if resource_config[i] == 0 else min(resource_config[i], avail)
                if destination_city.get("isOwnCity", False):
                    s = min(s, destination_city["freeSpaceForResources"][i])
                if dest_minimums:
                    s = apply_dest_minimums(
                        s, destination_city["availableResources"][i],
                        dest_minimums[i]
                    )
                to_send[i] = s
                total_send[i] += s
            if sum(to_send) > 0:
                preview_routes.append({
                    "source": odata["name"],
                    "dest": destination_city["name"],
                    "resources": to_send,
                })

        # Final confirmation with dry-run option
        while True:
            print_module_banner("Configuration Summary")
            ship_label = "Freighters" if useFreighters else "Merchant ships"
            print(f"  Ship type: {ship_label}")
            mode_label = "Keep reserves" if send_mode == 1 else "Send specific"
            print(f"  Mode: {mode_label}")
            print(f"  Sources ({len(origin_cities)}): {source_summary}")
            print(f"  Destination: {destination_city['name']}")
            if dest_minimums:
                print(f"  Dest minimums: {dest_minimums}")
            print(f"  Interval: {'One-time' if interval_hours == 0 else f'{interval_hours}h'}")
            print(f"  Total: {addThousandSeparator(sum(total_send))} resources\n")
            print("(Y) Proceed  (D) Dry run preview  (N) Cancel")
            rta = read(values=["y", "Y", "n", "N", "d", "D", ""])
            if rta.lower() == "n":
                event.set()
                return
            if rta.lower() == "d":
                run_dry_preview(preview_routes, "Consolidate")
                print("Press Enter to continue...")
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
        interval_hours=interval_hours,
        notif_level=notif_config.get("level", "none"),
        notes=f"{src_names} -> {destination_city['name']}",
    )
    _save_and_maybe_activate(session, event, schedule_row, notif_config,
                             log_path)


# ============================================================================
#  MODE 1 EXECUTION: do_it (Consolidate)
# ============================================================================

def do_it(session, origin_cities, destination_city, island,
          interval_hours, resource_config, useFreighters, send_mode,
          notif_config, dest_minimums, log_path):

    total_shipments = 0
    consecutive_failures = 0
    first_run = True
    next_run_time = datetime.datetime.now()

    while True:
        now = datetime.datetime.now()
        if not first_run and now < next_run_time:
            sleep_secs = max(0, (next_run_time - now).total_seconds())
            time.sleep(min(sleep_secs, 60))
            continue

        # Refresh destination
        html = session.get(city_url + str(destination_city["id"]))
        destination_city = getCity(html)

        # Start notification
        if should_notify(notif_config, "start"):
            src_names = ", ".join(c["name"] for c in origin_cities)
            sendToBot(session,
                      f"SHIPMENT CYCLE STARTING\n"
                      f"Account: {session.username}\n"
                      f"From: {src_names}\n"
                      f"To: [{island['x']}:{island['y']}] "
                      f"{destination_city['name']}")

        for oc in origin_cities:
            html = session.get(city_url + str(oc["id"]))
            oc_fresh = getCity(html)

            toSend = [0] * len(materials_names)
            total = 0
            for i in range(len(materials_names)):
                if resource_config[i] is None:
                    continue
                avail = oc_fresh["availableResources"][i]
                if send_mode == 1:
                    s = avail if resource_config[i] == 0 else max(0, avail - resource_config[i])
                else:
                    s = 0 if resource_config[i] == 0 else min(resource_config[i], avail)
                if destination_city.get("isOwnCity", False):
                    s = min(s, destination_city["freeSpaceForResources"][i])
                if dest_minimums:
                    s = apply_dest_minimums(
                        s, destination_city["availableResources"][i],
                        dest_minimums[i]
                    )
                toSend[i] = s
                total += s

            if total > 0:
                route = (oc_fresh, destination_city, island["id"], *toSend)
                coords = f"[{island['x']}:{island['y']}]"
                result = send_shipment(
                    session, route, useFreighters, notif_config, log_path,
                    "Consolidate", coords
                )
                if result["success"]:
                    total_shipments += 1
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    if consecutive_failures >= 3 and should_notify(notif_config, "error"):
                        sendToBot(session,
                                  f"WARNING: {consecutive_failures} consecutive failures")

        if interval_hours == 0:
            src_names = ", ".join(c["name"] for c in origin_cities)
            session.setStatus(
                f"One-time shipment done: {src_names} -> "
                f"{destination_city['name']}"
            )
            return

        next_run_time = datetime.datetime.now() + datetime.timedelta(
            hours=interval_hours
        )
        session.setStatus(
            f"Shipments: {total_shipments} | "
            f"Next: {getDateTime(next_run_time.timestamp())}"
        )
        first_run = False
        sleep_secs = max(0, (next_run_time - datetime.datetime.now()).total_seconds())
        time.sleep(sleep_secs)


# ============================================================================
#  MODE 2: DISTRIBUTE  (one source -> many destinations)
# ============================================================================

def distributeMode(session, event, stdin_fd, predetermined_input,
                   telegram_enabled, log_path):
    try:
        print_module_banner("Ship Type Selection")
        print("What type of ships do you want to use?")
        print("(1) Merchant ships")
        print("(2) Freighters")
        print("(') Back to main menu")
        shiptype = read(min=1, max=2, digit=True, additionalValues=["'"])
        if shiptype == "'":
            event.set()
            return
        useFreighters = (shiptype == 2)

        print_module_banner("Source City Selection")
        print("Select source city:\n")
        origin_city = rtm_chooseCity(session)

        print_module_banner("Distribution Setup")
        print(f"Source city: {origin_city['name']}")
        print("Note: Source city auto-excluded from destinations\n")
        dest_msg = "Select destination cities (cities to receive resources):"
        dest_ids, _ = rtm_ignoreCities(session, msg=dest_msg)

        src_id = str(origin_city["id"])
        if src_id in dest_ids:
            dest_ids.remove(src_id)
            print(f"Removed {origin_city['name']} from destinations (source)")

        if not dest_ids:
            print("No valid destination cities!")
            enter()
            event.set()
            return

        destination_cities = []
        for cid in dest_ids:
            html = session.get(city_url + cid)
            destination_cities.append(getCity(html))

        print_module_banner("Distribution Setup")
        dest_summary = ", ".join(c["name"] for c in destination_cities)
        print(f"Source: {origin_city['name']}")
        print(f"Destinations: {dest_summary}\n")
        print("Resources to send to EACH destination:")
        print("(Number = amount, 0 or blank = skip)")
        print("(= restart | ' exit)\n")

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

        # Schedule
        print_module_banner("Schedule Configuration")
        interval_confirmed = False
        while not interval_confirmed:
            print("How often to send (in hours)?")
            print("(0 = one-time | 1+ = recurring)")
            print("(') Back to main menu")
            interval_hours = read(min=0, digit=True, additionalValues=["'"])
            if interval_hours == "'":
                event.set()
                return
            print("")
            if interval_hours == 0:
                print("Mode: One-time shipment")
            else:
                print(f"Interval: Every {interval_hours} hour(s)")
            print("(1) Confirm  (2) Re-enter")
            if read(min=1, max=2, digit=True) == 1:
                interval_confirmed = True

        # Preview
        total_needed = [a * len(destination_cities) for a in resource_config]
        grand = sum(total_needed)
        preview_routes = []
        for dc in destination_cities:
            preview_routes.append({
                "source": origin_city["name"],
                "dest": dc["name"],
                "resources": resource_config,
            })

        while True:
            print_module_banner("Configuration Summary")
            ship_label = "Freighters" if useFreighters else "Merchant ships"
            print(f"  Ship type: {ship_label}")
            print(f"  Source: {origin_city['name']}")
            print(f"  Destinations ({len(destination_cities)}): {dest_summary}")
            if dest_minimums:
                print(f"  Dest minimums: {dest_minimums}")
            print(f"  Total resources: {addThousandSeparator(grand)}")
            int_label = "One-time" if interval_hours == 0 else f"{interval_hours}h"
            print(f"  Interval: {int_label}\n")
            print("(Y) Proceed  (D) Dry run preview  (N) Cancel")
            rta = read(values=["y", "Y", "n", "N", "d", "D", ""])
            if rta.lower() == "n":
                event.set()
                return
            if rta.lower() == "d":
                run_dry_preview(preview_routes, "Distribute")
                print("Press Enter to continue...")
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
        interval_hours=interval_hours,
        notif_level=notif_config.get("level", "none"),
        notes=f"{origin_city['name']} -> {dest_names[:30]}",
    )
    _save_and_maybe_activate(session, event, schedule_row, notif_config,
                             log_path)


# ============================================================================
#  MODE 2 EXECUTION: do_it_distribute
# ============================================================================

def do_it_distribute(session, origin_city, destination_cities,
                     interval_hours, resource_config, useFreighters,
                     notif_config, dest_minimums, log_path):

    total_shipments = 0
    consecutive_failures = 0
    first_run = True
    next_run_time = datetime.datetime.now()

    while True:
        now = datetime.datetime.now()
        if not first_run and now < next_run_time:
            sleep_secs = max(0, (next_run_time - now).total_seconds())
            time.sleep(min(sleep_secs, 60))
            continue

        # Refresh origin
        html = session.get(city_url + str(origin_city["id"]))
        origin_fresh = getCity(html)
        origin_island_id = origin_fresh["islandId"]
        html_isl = session.get(island_url + str(origin_island_id))
        origin_island = getIsland(html_isl)

        if should_notify(notif_config, "start"):
            dest_names = ", ".join(c["name"] for c in destination_cities)
            sendToBot(session,
                      f"DISTRIBUTION CYCLE STARTING\n"
                      f"Account: {session.username}\n"
                      f"From: {origin_fresh['name']}\n"
                      f"To: {len(destination_cities)} cities ({dest_names})")

        for dc in destination_cities:
            html = session.get(city_url + str(dc["id"]))
            dc_fresh = getCity(html)
            dest_isl_id = dc_fresh["islandId"]
            html_di = session.get(island_url + str(dest_isl_id))
            dest_island = getIsland(html_di)

            toSend = [0] * len(materials_names)
            total = 0
            for i in range(len(materials_names)):
                if resource_config[i] == 0:
                    continue
                avail = origin_fresh["availableResources"][i]
                s = min(resource_config[i], avail)
                dest_space = dc_fresh.get("freeSpaceForResources", [0] * len(materials_names))
                if i < len(dest_space):
                    s = min(s, dest_space[i])
                if dest_minimums:
                    s = apply_dest_minimums(
                        s, dc_fresh["availableResources"][i],
                        dest_minimums[i]
                    )
                toSend[i] = s
                total += s

            if total > 0:
                route = (origin_fresh, dc_fresh, dest_island["id"], *toSend)
                coords = f"[{dest_island['x']}:{dest_island['y']}]"
                result = send_shipment(
                    session, route, useFreighters, notif_config, log_path,
                    "Distribute", coords
                )
                if result["success"]:
                    total_shipments += 1
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    if consecutive_failures >= 3 and should_notify(notif_config, "error"):
                        sendToBot(session,
                                  f"WARNING: {consecutive_failures} consecutive failures")

        if interval_hours == 0:
            session.setStatus(
                f"One-time distribution done: {origin_fresh['name']}"
            )
            return

        next_run_time = datetime.datetime.now() + datetime.timedelta(
            hours=interval_hours
        )
        session.setStatus(
            f"{origin_fresh['name']} -> {len(destination_cities)} cities | "
            f"Shipments: {total_shipments} | "
            f"Next: {getDateTime(next_run_time.timestamp())}"
        )
        first_run = False
        sleep_secs = max(0, (next_run_time - datetime.datetime.now()).total_seconds())
        time.sleep(sleep_secs)


# ============================================================================
#  MODE 3: EVEN DISTRIBUTION  (balance resources across cities)
#  Now supports MULTI-RESOURCE balancing
# ============================================================================

def evenDistributionMode(session, event, stdin_fd, predetermined_input,
                         telegram_enabled, log_path):
    try:
        # Select resources (multi-select)
        print_module_banner("Resource Selection")
        print("Select which resource(s) to balance across cities:")
        for i, res in enumerate(materials_names):
            print(f"({i+1}) {res}")
        print("")
        print("Enter one or more numbers (comma-separated, e.g. 1,3,5):")
        print("(') Back to main menu")
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
        print_module_banner("Ship Type Selection")
        print(f"Balancing: {selected_names}\n")
        print("What type of ships do you want to use?")
        print("(1) Merchant ships")
        print("(2) Freighters")
        print("(') Back to main menu")
        shiptype = read(min=1, max=2, digit=True, additionalValues=["'"])
        if shiptype == "'":
            event.set()
            return
        useFreighters = (shiptype == 2)

        # Select cities
        print_module_banner("City Selection")
        print(f"Balancing: {selected_names}\n")
        print("Select cities to EXCLUDE from balancing:")
        excluded_ids, _ = rtm_ignoreCities(session, msg="Select cities to EXCLUDE:")

        html = session.get()
        city_ids = re.findall(r'<option value="(\d+)" class="cityowntown"', html)
        all_cities = []
        for cid in city_ids:
            if cid not in excluded_ids:
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
            print("\nAll cities are already balanced! Nothing to do.")
            enter()
            event.set()
            return

        # Confirmation with dry run
        while True:
            print(f"\n{len(preview_routes)} shipment(s) planned.")
            print("(Y) Confirm - Start balancing")
            print("(D) Dry run - preview shipments")
            print("(N) Cancel")
            choice = read(values=["y", "Y", "n", "N", "d", "D", ""])
            if choice.lower() == "n":
                event.set()
                return
            if choice.lower() == "d":
                run_dry_preview(preview_routes, "Even Distribution")
                print("Press Enter to continue...")
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
        interval_hours=0,
        notif_level=notif_config.get("level", "none"),
        notes=f"Balance {selected_names}",
    )
    _save_and_maybe_activate(session, event, schedule_row, notif_config,
                             log_path)


# ============================================================================
#  MODE 3 EXECUTION: do_even_distribution
# ============================================================================

def do_even_distribution(session, shipments, resource_index, resource_name,
                         useFreighters, notif_config, log_path):
    senders = [s for s in shipments if s["type"] == "sender"]
    receivers = [s for s in shipments if s["type"] == "receiver"]

    # FIX: check for empty lists
    if not senders or not receivers:
        msg = f"{resource_name}: Already balanced (no shipments needed)"
        if should_notify(notif_config, "complete"):
            sendToBot(session, msg)
        return

    if should_notify(notif_config, "start"):
        plan_lines = []
        si, ri = 0, 0
        s_rem, r_rem = senders[0]["amount"], receivers[0]["amount"]
        while si < len(senders) and ri < len(receivers):
            amt = min(s_rem, r_rem)
            if amt > 0:
                plan_lines.append(
                    f"{senders[si]['from']['name']} -> "
                    f"{receivers[ri]['to']['name']}: "
                    f"{addThousandSeparator(amt)} {resource_name}"
                )
            s_rem -= amt
            r_rem -= amt
            if s_rem == 0:
                si += 1
                s_rem = senders[si]["amount"] if si < len(senders) else 0
            if r_rem == 0:
                ri += 1
                r_rem = receivers[ri]["amount"] if ri < len(receivers) else 0
        sendToBot(session,
                  f"BALANCING PLAN\nResource: {resource_name}\n\n"
                  + "\n".join(plan_lines))

    # Execute
    si, ri = 0, 0
    s_rem = senders[0]["amount"]
    r_rem = receivers[0]["amount"]

    while si < len(senders) and ri < len(receivers):
        sender = senders[si]
        receiver = receivers[ri]
        amount = min(s_rem, r_rem)

        if amount > 0:
            toSend = [0] * len(materials_names)
            toSend[resource_index] = amount

            dest_isl_id = receiver["to"]["islandId"]
            html = session.get(island_url + str(dest_isl_id))
            dest_island = getIsland(html)

            route = (sender["from"], receiver["to"],
                     dest_island["id"], *toSend)
            coords = f"[{dest_island['x']}:{dest_island['y']}]"

            result = send_shipment(
                session, route, useFreighters, notif_config, log_path,
                "Even Distribution", coords
            )

            s_rem -= amount
            r_rem -= amount

        if s_rem == 0:
            si += 1
            if si < len(senders):
                s_rem = senders[si]["amount"]
        if r_rem == 0:
            ri += 1
            if ri < len(receivers):
                r_rem = receivers[ri]["amount"]

    if should_notify(notif_config, "complete"):
        sendToBot(session,
                  f"BALANCING COMPLETE\nResource: {resource_name}")


# ============================================================================
#  MODE 4: AUTO SEND  (request resources, auto-collect from all cities)
# ============================================================================

def autoSendMode(session, event, stdin_fd, predetermined_input,
                 telegram_enabled, log_path):
    try:
        print_module_banner("Auto Send")
        print("What type of ships do you want to use?")
        print("(1) Merchant ships")
        print("(2) Freighters")
        print("(') Back to main menu")
        shiptype = read(min=1, max=2, digit=True, additionalValues=["'"])
        if shiptype == "'":
            event.set()
            return
        useFreighters = (shiptype == 2)

        while True:
            print_module_banner("Auto Send")
            print("Select the destination city:\n")
            destination_city = rtm_chooseCity(session)

            html = session.get(island_url + destination_city["islandId"])
            destination_island = getIsland(html)

            print_module_banner("Auto Send")
            print("Scanning cities...")
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

            print_module_banner("Auto Send")
            print(f"  Destination: {destination_city['name']} "
                  f"[{destination_island['x']}:{destination_island['y']}]\n")
            print("  Available resources (excluding destination):")
            for i, res in enumerate(materials_names):
                print(f"    {res:<12} {addThousandSeparator(totals[i]):>12}")
            print("")

            while True:
                print("  Enter how much of each resource to collect:")
                print("  (blank = skip, ' = exit, = = restart)\n")

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
                elif choice == "D":
                    # Dry run
                    preview = []
                    for route in routes:
                        preview.append({
                            "source": route[0]["name"],
                            "dest": route[1]["name"],
                            "resources": list(route[3:]),
                        })
                    run_dry_preview(preview, "Auto Send")
                    print("Press Enter to continue...")
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
                            destination_island):
    remaining = list(requested)
    routes = []
    for supplier in suppliers:
        to_send = [0] * len(materials_names)
        has_cargo = False
        for i in range(len(materials_names)):
            if remaining[i] <= 0:
                continue
            can_give = supplier["availableResources"][i]
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

    print("  (Y) Proceed")
    print("  (D) Dry run preview")
    print("  (E) Edit - re-select")
    print("  (C) Cancel")
    choice = read(values=["y", "Y", "e", "E", "c", "C", "d", "D", ""])
    if choice == "" or choice.upper() == "Y":
        return "Y"
    return choice.upper()


# ============================================================================
#  MODE 4 EXECUTION: do_it_auto_send
# ============================================================================

def do_it_auto_send(session, routes, useFreighters, notif_config, log_path):
    total_routes = len(routes)
    completed = 0

    print(f"\n--- Auto Send: {total_routes} shipments ---\n")

    for idx, route in enumerate(routes):
        origin_city = route[0]
        dest_city = route[1]
        amounts = route[3:]
        res_desc = ", ".join(
            f"{addThousandSeparator(amounts[i])} {materials_names[i]}"
            for i in range(len(materials_names))
            if i < len(amounts) and amounts[i] > 0
        )

        print(f"  [{idx+1}/{total_routes}] {origin_city['name']} -> "
              f"{dest_city['name']}")
        print(f"    Resources: {res_desc}")

        result = send_shipment(
            session, route, useFreighters, notif_config, log_path,
            "Auto Send"
        )

        if result["success"]:
            completed += 1
            print(f"    SUCCESS ({completed}/{total_routes})")
        else:
            print(f"    FAILED: {result['error']}")
            if not result["success"] and result["error"] and "lock" in result["error"].lower():
                break  # Stop on lock failures

    print(f"\n--- Auto Send complete: {completed}/{total_routes} ---\n")
    if should_notify(notif_config, "complete"):
        sendToBot(session,
                  f"AUTO SEND COMPLETE\nAccount: {session.username}\n"
                  f"Shipments: {completed}/{total_routes}")


# ============================================================================
#  MODE 5: BULK DISTRIBUTION  (persistent CSV-driven sends)
# ============================================================================

BULK_CSV_COLUMNS = [
    "Transport", "X", "Y", "Player", "City", "City_Location",
    "Wood", "Wine", "Marble", "Crystal", "Sulphur", "From", "Hours", "Issues",
]


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
        print_module_banner("Bulk Distribution CSV Editor")
        print(f"  CSV: {csv_path}")
        print(f"  Rows: {len(rows)}\n")

        print("(1) Add cities")
        print("(2) View all rows")
        print("(3) Edit row(s)")
        print("(4) Delete row(s)")
        print("(5) Set resources for rows")
        print("(6) Set transport/from for rows")
        print("(7) Save and back")
        print("(') Cancel without saving")

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
        print(f"\n  Select positions (comma-sep), d# to remove, Enter=next island:")

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
    print("  (1) Same resources for all rows")
    print("  (2) Set per-row")
    print("  (') Cancel")

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
    print("  (1) Merchant ships (m)")
    print("  (2) Freighters (f)")
    print("  (3) Keep current / set per-row later")
    print("  (') Cancel")
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
    print("  (1) All cities (a)")
    print("  (2) Specific indices (enter value)")
    print("  (3) Keep current / set per-row later")
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
          f"{'W':>6} {'V':>6} {'M':>6} {'C':>6} {'S':>6} {'T'} {'From':<5}")
    print(f"  {'─'*4} {'─'*3} {'─'*3} {'─'*14} {'─'*16} "
          f"{'─'*6} {'─'*6} {'─'*6} {'─'*6} {'─'*6} {'─'} {'─'*5}")

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
        t = (row.get("Transport", "m") or "m")[0]
        fr = (row.get("From", "a") or "a")[:5]
        print(f"  {i:<4} {x:>3} {y:>3} {player:<14} {city:<16} "
              f"{w:>6} {v:>6} {m:>6} {c:>6} {s:>6} {t} {fr:<5}")

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


def bulkDistributionMode(session, event, stdin_fd, predetermined_input,
                         telegram_enabled, log_path):
    try:
        print_module_banner("Bulk Distribution")
        prefs = load_prefs()
        saved_csv = prefs.get("csv_path", "")
        if saved_csv:
            print(f"CSV file (Enter to reuse last: {saved_csv}):")
        else:
            print("Enter the full path to your CSV file:")
        print("(Columns: Transport, X, Y, Player, City, City_Location, "
              "Wood, Wine, Marble, Crystal, Sulphur, From, Hours, Issues)")
        print("(Transport: m = merchant ships, f = freighters)")
        print("(Resource values: 500 = send 500, e0 = send all, "
              "e10000 = send all except 10k)")
        print("(From: a = all cities, 1,3,5 = specific cities)")
        print("(') Back to main menu\n")
        csv_input = read(msg="CSV path: ", empty=True, additionalValues=["'"])
        if csv_input == "'":
            event.set()
            return
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
            print(f"File not found: {csv_path}")
            print("\n(1) Create new CSV with in-app editor")
            print("(') Back\n")
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
            # Re-enter to load the newly created file
            bulkDistributionMode(session, event, stdin_fd,
                                 predetermined_input, telegram_enabled,
                                 log_path)
            return

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
        fieldnames = ensure_issues_column(fieldnames, rows)

        mode, run_column = choose_run_slot(session, event, rows, run_columns)
        if run_column is None:
            return

        fieldnames_no_runs = [c for c in fieldnames if not c.startswith("Run_")]
        fieldnames = fieldnames_no_runs + run_columns
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

        # Final confirmation with dry run
        while True:
            print_module_banner("Bulk Distribution - Summary")
            print(f"  CSV rows: {len(rows)}")
            print(f"  Interval: every {interval_hours}h")
            print(f"  Run slot: {run_column[4:]}\n")
            print("(Y) Proceed  (D) Dry run preview  (E) Edit CSV  (N) Cancel")
            rta = read(values=["y", "Y", "n", "N", "d", "D", "e", "E",
                               "", "'"],
                       additionalValues=["'"])
            if rta == "'" or rta.lower() == "n":
                event.set()
                return
            if rta.lower() == "e":
                _bulk_editor_menu(session, csv_path, event)
                # Reload CSV after editing
                bulkDistributionMode(session, event, stdin_fd,
                                     predetermined_input,
                                     telegram_enabled, log_path)
                return
            if rta.lower() == "d":
                preview = _scan_csv_for_preview(session, rows, run_column)
                if preview:
                    run_dry_preview(preview, "Bulk Distribution")
                else:
                    print("  No valid routes found in scan.")
                print("Press Enter to continue...")
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
        interval_hours=interval_hours,
        notif_level=notif_config.get("level", "none"),
        notes=f"CSV: {os.path.basename(csv_path)}",
    )
    _save_and_maybe_activate(session, event, schedule_row, notif_config,
                             log_path)


def _scan_csv_for_preview(session, rows, run_column):
    """Quick scan for dry-run preview. Returns list of route info dicts."""
    csv_res_cols = ["Wood", "Wine", "Marble", "Crystal", "Sulphur"]
    preview = []
    for row in rows:
        if normalize_text(row.get(run_column, "")) == "x":
            continue
        if row.get("Issues", "").strip():
            continue
        parsed = [parse_resource_value(row.get(col, "0")) for col in csv_res_cols]
        has_resources = any(amt > 0 or mode == "except" for mode, amt in parsed)
        if not has_resources:
            continue
        has_except = any(m == "except" for m, _ in parsed)
        resources = [amt for _, amt in parsed]
        city_name = row.get("City", "?")
        player = row.get("Player", "?")
        from_val = parse_from_column(row.get("From", ""))
        if from_val is None:
            src_label = "From: (not set)"
        elif from_val == "all":
            src_label = "All cities"
        else:
            src_label = f"Cities {','.join(str(i) for i in from_val)}"
        transport = "Freighters" if parse_transport_value(row.get("Transport", "m")) else "Merchant"
        label = f"{src_label} ({transport})"
        if has_except:
            label += " [except mode — amounts resolved at send time]"
        preview.append({
            "source": label,
            "dest": f"{city_name} ({player})",
            "resources": resources,
        })
    return preview


# ============================================================================
#  MODE 5 EXECUTION: do_it_bulk_distribution
# ============================================================================

def do_it_bulk_distribution(session, csv_path, interval_hours,
                            notif_config, run_column, log_path):
    csv_resource_cols = ["Wood", "Wine", "Marble", "Crystal", "Sulphur"]

    while True:
        # Read CSV
        try:
            rows = []
            with open(csv_path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                fieldnames = list(reader.fieldnames or [])
                for row in reader:
                    rows.append(row)
        except Exception as e:
            if should_notify(notif_config, "error"):
                sendToBot(session,
                          f"BULK DIST ERROR\nCould not read CSV: {e}")
            time.sleep(3600)
            continue

        # Ensure run column exists
        if run_column not in (fieldnames or []):
            fieldnames, run_columns = ensure_run_columns(fieldnames, rows)
            if run_column not in run_columns:
                run_column = run_columns[0]
            try:
                write_csv_atomic(csv_path, fieldnames, rows)
            except Exception:
                time.sleep(3600)
                continue

        # Ensure Transport, From and Issues columns exist; clear Issues for this cycle
        fieldnames = ensure_transport_column(fieldnames, rows)
        fieldnames = ensure_from_column(fieldnames, rows)
        fieldnames = ensure_issues_column(fieldnames, rows)
        for row in rows:
            row["Issues"] = ""

        city_cache = {}
        mismatches = []
        validated_cities = {}

        session.setStatus(
            f"[PRE-SCAN] Bulk Distribution | "
            f"Validating {len(rows)} rows..."
        )

        # ---- PHASE 1: Pre-scan validation ----
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

                parsed_resources = [parse_resource_value(row.get(col, "0")) for col in csv_resource_cols]
                has_resources = any(amt > 0 or mode == "except" for mode, amt in parsed_resources)
                if not has_resources:
                    continue

                from_val = parse_from_column(row.get("From", ""))
                if from_val is None:
                    issue = "From column is empty (required: a, or city indices like 1,3,5)"
                    row["Issues"] = issue
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
                        issue = f"From: city index {','.join(bad)} out of range (max {max_idx})"
                        row["Issues"] = issue
                        mismatches.append(f"Row {row_num}: {issue}")
                        continue

                html = session.get(
                    f"view=island&xcoord={x}&ycoord={y}"
                )
                island = getIsland(html)
                cities_on_island = [
                    c for c in island["cities"]
                    if c.get("type") == "city"
                ]

                # Match city (case-insensitive)
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
                    row["Issues"] = issue
                    mismatches.append(f"Row {row_num}: {issue}")
                    continue

                # Auto-fill City_Location if empty
                if not expected_location:
                    loc_token = get_city_location_token(matched_city)
                    if loc_token:
                        row["City_Location"] = loc_token

                validated_cities[row_num] = (matched_city, island)

            except Exception as e:
                issue = f"Error: {e}"
                row["Issues"] = issue
                mismatches.append(f"Row {row_num}: {issue}")

        # Save CSV after pre-scan (persists Issues + City_Location auto-fills)
        try:
            write_csv_atomic(csv_path, fieldnames, rows)
        except Exception as we:
            print(f"    WARNING: pre-scan CSV write failed: {we}")

        if mismatches and should_notify(notif_config, "error"):
            sendToBot(session,
                      f"BULK DIST ISSUES\n"
                      + "\n".join(mismatches))

        # ---- PHASE 2: Build routes from validated rows ----
        routes = []
        session.setStatus(
            f"[PROCESSING] Bulk Distribution | "
            f"Building routes for {len(validated_cities)} row(s)..."
        )

        # Refresh city cache for "except" resolution
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
            parsed_resources = [parse_resource_value(row.get(col, "0")) for col in csv_resource_cols]

            from_val = parse_from_column(row.get("From", ""))
            row_use_freighters = parse_transport_value(row.get("Transport", "m"))
            try:
                src_cities = get_source_cities_for_row(
                    session, from_val, city_cache
                )
            except Exception as e:
                row["Issues"] = f"Error resolving source cities: {e}"
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
                row["Issues"] = f"Error fetching city details: {e}"
                try:
                    write_csv_atomic(csv_path, fieldnames, rows)
                except Exception:
                    pass
                continue

            dest_space = dest_city.get("freeSpaceForResources",
                                       [0] * len(materials_names))
            for src_idx, src_city in src_cities:
                if src_idx in done_indices:
                    continue
                resources = resolve_resources(
                    parsed_resources, src_city.get("availableResources", []),
                    row, csv_resource_cols
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
                    row_use_freighters, parsed_resources
                ))

        # Interleave routes by source city to spread action point usage
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
        else:
            if should_notify(notif_config, "start"):
                sendToBot(session,
                          f"BULK DIST SCHEDULED\n"
                          f"{len(routes)} shipment(s)")

            completed = 0
            skipped = 0
            total = len(routes)

            for idx, (row_num, route, resources, dest_name,
                      player, rx, ry, src_name, src_idx,
                      row_freighters, parsed_res) in enumerate(routes):

                # For "except" mode, re-resolve with fresh source data
                has_except = any(m == "except" for m, _ in parsed_res)
                if has_except:
                    src_city_id = str(route[0]["id"])
                    src_fresh = getCity(session.get(city_url + src_city_id))
                    resources = resolve_resources(
                        parsed_res, src_fresh.get("availableResources", []),
                        None, csv_resource_cols
                    )
                    dest_city_id = str(route[1]["id"])
                    dest_fresh = getCity(session.get(city_url + dest_city_id))
                    dest_space = dest_fresh.get("freeSpaceForResources",
                                                [0] * len(materials_names))
                    for i in range(len(resources)):
                        if i < len(dest_space):
                            resources[i] = min(resources[i], dest_space[i])
                    if sum(resources) == 0:
                        print(f"\n  [{idx+1}/{total}] {src_name} -> "
                              f"{dest_name}: SKIPPED (insufficient stock or space)")
                        skipped += 1
                        continue
                    route = (src_fresh, dest_fresh, route[2], *resources)

                ship_label = "F" if row_freighters else "M"
                res_desc = ", ".join(
                    f"{addThousandSeparator(resources[i])} "
                    f"{materials_names[i]}"
                    for i in range(len(materials_names))
                    if resources[i] > 0
                )
                print(f"\n  [{idx+1}/{total}] [{ship_label}] {src_name} -> "
                      f"{dest_name} ({player}) [{rx}:{ry}]")
                print(f"    {res_desc}")

                session.setStatus(
                    f"[SENDING] Bulk Dist [{idx+1}/{total}] "
                    f"{src_name} -> {dest_name}"
                )

                coords = f"[{rx}:{ry}]"
                result = send_shipment(
                    session, route, row_freighters, notif_config,
                    log_path, "Bulk Distribution", coords, player
                )

                if result["success"]:
                    completed += 1
                    print(f"    SUCCESS ({completed}/{total})")
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
                    except Exception as we:
                        print(f"    WARNING: checkpoint write failed: {we}")
                else:
                    print(f"    FAILED: {result['error']}")

            summary = f"{completed}/{total} sent"
            if skipped:
                summary += f", {skipped} skipped"
            print(f"\n--- Bulk Distribution complete: {summary} ---")
            if should_notify(notif_config, "complete"):
                run_done = sum(
                    1 for r in rows
                    if normalize_text(r.get(run_column, "")) == "x"
                )
                sendToBot(session,
                          f"BULK DIST COMPLETE\n"
                          f"Slot: {run_column[4:]}\n"
                          f"Cycle: {summary}\n"
                          f"Progress: {run_done}/{len(rows)}")

        # Schedule next cycle
        next_run = datetime.datetime.now() + datetime.timedelta(
            hours=interval_hours
        )
        session.setStatus(
            f"[WAITING] Bulk Dist | Next: "
            f"{getDateTime(next_run.timestamp())}"
        )
        sleep_secs = max(
            0, (next_run - datetime.datetime.now()).total_seconds()
        )
        time.sleep(sleep_secs)


# ============================================================================
#  MODE 6: KEEP TOPPED UP  (periodically fill destinations from sources)
# ============================================================================

def topUpMode(session, event, stdin_fd, predetermined_input,
              telegram_enabled, log_path):
    try:
        # --- Step 1: Ship type ---
        ship_confirmed = False
        while not ship_confirmed:
            print_module_banner("Ship Type Selection")
            print("What type of ships do you want to use?")
            print("(1) Merchant ships")
            print("(2) Freighters")
            print("(') Back to main menu")
            shiptype = read(min=1, max=2, digit=True, additionalValues=["'"])
            if shiptype == "'":
                event.set()
                return
            useFreighters = (shiptype == 2)
            ship_label = "Freighters" if useFreighters else "Merchant ships"
            print(f"\nShip type: {ship_label}")
            print("(1) Confirm  (2) Re-enter  (') Back to main menu")
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
                print_module_banner("Destination Selection")
                if destinations:
                    print("Current destinations: " +
                          ", ".join(d["name"] for d in destinations))
                    print("")
                print("Select destination city:")
                dest = rtm_chooseCity(session)
                if dest is None:
                    event.set()
                    return
                print(f"\nSelected: {dest['name']}")
                print("(1) Confirm  (2) Re-enter destination  (') Back to main menu")
                c = read(min=1, max=2, digit=True, additionalValues=["'"])
                if c == "'":
                    event.set()
                    return
                if c == 1:
                    dest_confirmed = True
            destinations.append(dest)

            print(f"\nDestinations so far: {', '.join(d['name'] for d in destinations)}")
            print("(1) Add another destination  (2) Done adding destinations")
            c = read(min=1, max=2, digit=True)
            if c == 2:
                adding_dests = False

        # --- Step 3: Resource targets (per destination) ---
        dest_configs = {}
        for dest in destinations:
            targets_confirmed = False
            while not targets_confirmed:
                print_module_banner(f"Resource Targets — {dest['name']}")
                cap = dest.get("storageCapacity", 0)
                fill_95 = math.floor(cap * 0.95)
                print(f"Storage capacity: {addThousandSeparator(cap)}")
                print(f"  f = fill to 95% ({addThousandSeparator(fill_95)})")
                print(f"  0 or blank = skip this resource")
                print(f"  or enter a specific amount")
                print(f"(= restart | ' exit)\n")

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
                print("(1) Confirm  (2) Re-enter  (') Back to main menu")
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
            src_msg = ("Select source cities to send from.\n"
                       "  (Exclude cities you don't want to use.)\n"
                       "  After confirming, you can set reserve protection per city.")
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
        print_module_banner("Reserve Protection")
        print("Reserve protection: Prevent source cities from being")
        print("emptied below a threshold per resource.")
        print("")
        print("(1) Set up reserve protection")
        print("(2) No reserve protection (default)")
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
                print("\n(1) Confirm  (2) Re-enter reserves  (') Back to main menu")
                c = read(min=1, max=2, digit=True, additionalValues=["'"])
                if c == "'":
                    event.set()
                    return
                if c == 1:
                    reserves_confirmed = True

        # --- Step 6: Check interval ---
        interval_confirmed = False
        while not interval_confirmed:
            print_module_banner("Check Interval")
            print("How often to check and top up (in hours)?")
            print("(Recommended: 1-4 hours)")
            print("(') Back to main menu")
            interval_hours = read(min=1, digit=True, additionalValues=["'"])
            if interval_hours == "'":
                event.set()
                return
            print(f"\nInterval: Every {interval_hours} hour(s)")
            print("(1) Confirm  (2) Re-enter  (') Back to main menu")
            c = read(min=1, max=2, digit=True, additionalValues=["'"])
            if c == "'":
                event.set()
                return
            if c == 1:
                interval_confirmed = True

        # --- Step 7: Notifications ---
        notif_config = get_notification_config(telegram_enabled, event)
        if notif_config is None:
            return

        # --- Step 8: Final summary + dry run ---
        while True:
            print_module_banner("Keep Topped Up — Summary")
            print(f"  Ship type: {ship_label}")
            print(f"  Destinations ({len(destinations)}):")
            for dest in destinations:
                parts = []
                tgts = dest_configs[str(dest["id"])]
                for i, res in enumerate(materials_names):
                    if tgts[i] is None:
                        continue
                    parts.append(f"{res}: {addThousandSeparator(tgts[i])}")
                print(f"    {dest['name']} — {' | '.join(parts) if parts else 'none'}")
            src_names = ", ".join(source_cities[cid]["name"] for cid in source_city_ids)
            print(f"  Sources ({len(source_city_ids)}): {src_names}")
            if source_reserves:
                print(f"  Reserve protection: enabled")
                for cid in source_city_ids:
                    res_list = source_reserves.get(cid, [0] * len(materials_names))
                    if any(r > 0 for r in res_list):
                        parts = [f"{materials_names[i]} {addThousandSeparator(res_list[i])}"
                                 for i in range(len(materials_names)) if res_list[i] > 0]
                        print(f"    {source_cities[cid]['name']}: {' | '.join(parts)}")
            else:
                print(f"  Reserve protection: none")
            print(f"  Check interval: every {interval_hours}h")
            print("")
            print("(Y) Proceed  (D) Dry run preview  (N) Cancel")
            rta = read(values=["y", "Y", "n", "N", "d", "D", ""])
            if rta.lower() == "n":
                event.set()
                return
            if rta.lower() == "d":
                preview_routes = _top_up_dry_run(
                    session, destinations, dest_configs,
                    source_city_ids, source_reserves)
                if preview_routes:
                    run_dry_preview(preview_routes, "Keep Topped Up")
                else:
                    print("\n  All destinations are already at target levels.\n")
                print("Press Enter to continue...")
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
        interval_hours=interval_hours,
        notif_level=notif_config.get("level", "none"),
        notes=f"TopUp: {dest_names[:30]}",
    )
    _save_and_maybe_activate(session, event, schedule_row, notif_config,
                             log_path)


def _top_up_dry_run(session, destinations, dest_configs,
                    source_city_ids, source_reserves):
    preview_routes = []
    for dest in destinations:
        html = session.get(city_url + str(dest["id"]))
        dest_fresh = getCity(html)
        targets = dest_configs[str(dest["id"])]
        needed = [0] * len(materials_names)
        for i in range(len(materials_names)):
            if targets[i] is None:
                continue
            gap = targets[i] - dest_fresh["availableResources"][i]
            needed[i] = max(0, min(gap, dest_fresh["freeSpaceForResources"][i]))

        for cid in source_city_ids:
            if all(n <= 0 for n in needed):
                break
            html = session.get(city_url + cid)
            src_fresh = getCity(html)
            reserves = source_reserves.get(cid, [0] * len(materials_names))
            to_send = [0] * len(materials_names)
            for i in range(len(materials_names)):
                if needed[i] <= 0:
                    continue
                avail = src_fresh["availableResources"][i]
                reserve = reserves[i] if i < len(reserves) else 0
                sendable = max(0, avail - reserve)
                to_send[i] = min(needed[i], sendable)
                needed[i] -= to_send[i]
            if sum(to_send) > 0:
                preview_routes.append({
                    "source": src_fresh["name"],
                    "dest": dest_fresh["name"],
                    "resources": to_send,
                })
    return preview_routes


# ============================================================================
#  MODE 6 EXECUTION: do_it_top_up
# ============================================================================

def do_it_top_up(session, destinations, dest_configs, source_city_ids,
                 source_reserves, useFreighters, interval_hours,
                 notif_config, log_path):

    total_shipments = 0
    first_run = True
    next_run_time = datetime.datetime.now()

    while True:
        now = datetime.datetime.now()
        if not first_run and now < next_run_time:
            sleep_secs = max(0, (next_run_time - now).total_seconds())
            time.sleep(min(sleep_secs, 60))
            continue

        if should_notify(notif_config, "start"):
            dest_names = ", ".join(d["name"] for d in destinations)
            sendToBot(session,
                      f"TOP-UP CYCLE STARTING\n"
                      f"Account: {session.username}\n"
                      f"Destinations: {dest_names}")

        cycle_sent = 0
        consecutive_failures = 0

        for dest in destinations:
            html = session.get(city_url + str(dest["id"]))
            dest_fresh = getCity(html)
            targets = dest_configs[str(dest["id"])]

            dest_isl_id = dest_fresh["islandId"]
            html_isl = session.get(island_url + str(dest_isl_id))
            dest_island = getIsland(html_isl)
            coords = f"[{dest_island['x']}:{dest_island['y']}]"

            for cid in source_city_ids:
                needed = [0] * len(materials_names)
                for i in range(len(materials_names)):
                    if targets[i] is None:
                        continue
                    gap = targets[i] - dest_fresh["availableResources"][i]
                    needed[i] = max(0, min(gap, dest_fresh["freeSpaceForResources"][i]))

                if all(n <= 0 for n in needed):
                    break

                html = session.get(city_url + cid)
                src_fresh = getCity(html)
                reserves = source_reserves.get(cid, [0] * len(materials_names))
                to_send = [0] * len(materials_names)
                for i in range(len(materials_names)):
                    if needed[i] <= 0:
                        continue
                    avail = src_fresh["availableResources"][i]
                    reserve = reserves[i] if i < len(reserves) else 0
                    sendable = max(0, avail - reserve)
                    to_send[i] = min(needed[i], sendable)

                if sum(to_send) > 0:
                    route = (src_fresh, dest_fresh, dest_island["id"], *to_send)
                    result = send_shipment(
                        session, route, useFreighters, notif_config, log_path,
                        "TopUp", coords
                    )
                    if result["success"]:
                        total_shipments += 1
                        cycle_sent += 1
                        consecutive_failures = 0
                        html = session.get(city_url + str(dest["id"]))
                        dest_fresh = getCity(html)
                    else:
                        consecutive_failures += 1
                        if consecutive_failures >= 3 and should_notify(notif_config, "error"):
                            sendToBot(session,
                                      f"WARNING: {consecutive_failures} consecutive failures")

        if should_notify(notif_config, "complete"):
            sendToBot(session,
                      f"TOP-UP CYCLE COMPLETE\n"
                      f"Account: {session.username}\n"
                      f"Shipments this cycle: {cycle_sent}\n"
                      f"Total shipments: {total_shipments}")

        next_run_time = datetime.datetime.now() + datetime.timedelta(
            hours=interval_hours
        )
        dest_names = ", ".join(d["name"] for d in destinations)
        session.setStatus(
            f"TopUp: {dest_names} | "
            f"Sent: {total_shipments} | "
            f"Next: {getDateTime(next_run_time.timestamp())}"
        )
        first_run = False
        sleep_secs = max(0, (next_run_time - datetime.datetime.now()).total_seconds())
        time.sleep(sleep_secs)


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
        print(f"\n  Schedule #{sid} ({mode_label}) saved and activated.")
        print(f"  Worker is already running — it will pick it up within "
              f"{TICK_BUDGET_SECONDS}s.")
        enter()
        event.set()
        return

    print(f"\n  Schedule #{sid} ({mode_label}) saved.")
    print(f"  (1) Activate now (start background worker)")
    print(f"  (2) Return to menu (activate later from Manage Schedules)")
    choice = read(min=1, max=2, digit=True)

    if choice == 2:
        print(f"  Schedule #{sid} saved as pending. "
              f"Activate from option 7 'Manage Schedules'.")
        enter()
        event.set()
        return

    transport_csv_update(session, sid, status="active")

    wlock = transport_worker_lock_path(session)
    if not _lock_acquire(wlock, timeout=1, stale_after=WORKER_LOCK_STALE_SECONDS):
        print("  Worker started by another process — "
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


def _is_transport_worker_running(session):
    wlock = transport_worker_lock_path(session)
    if not os.path.exists(wlock):
        return False
    try:
        with open(wlock, "r") as f:
            data = json.load(f)
        if time.time() - data.get("timestamp", 0) > WORKER_LOCK_STALE_SECONDS:
            return False
        return True
    except Exception:
        return False


# ----------------------------------------------------------------------------
#  Mode-specific single-cycle handlers
# ----------------------------------------------------------------------------

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
    destination_city = getCity(html)
    dest_isl_id = destination_city["islandId"]
    html_isl = session.get(island_url + str(dest_isl_id))
    island = getIsland(html_isl)
    coords = f"[{island['x']}:{island['y']}]"

    cycle_sent = 0
    for cid in source_city_ids:
        html = session.get(city_url + str(cid))
        oc_fresh = getCity(html)

        toSend = [0] * len(materials_names)
        total = 0
        for i in range(len(materials_names)):
            if i >= len(resource_config) or resource_config[i] is None:
                continue
            avail = oc_fresh["availableResources"][i]
            if send_mode == 1:
                s = (avail if resource_config[i] == 0
                     else max(0, avail - resource_config[i]))
            else:
                s = (0 if resource_config[i] == 0
                     else min(resource_config[i], avail))
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
                "Consolidate", coords,
            )
            if result["success"]:
                cycle_sent += 1
                html = session.get(city_url + dest_city_id)
                destination_city = getCity(html)

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
    cycle_sent = 0

    for dcid in dest_city_ids:
        html = session.get(city_url + str(dcid))
        dest_city = getCity(html)
        dest_isl_id = dest_city["islandId"]
        html_isl = session.get(island_url + str(dest_isl_id))
        dest_island = getIsland(html_isl)
        coords = f"[{dest_island['x']}:{dest_island['y']}]"

        html = session.get(city_url + src_city_id)
        origin_city = getCity(html)

        toSend = [0] * len(materials_names)
        total = 0
        for i in range(len(materials_names)):
            if i >= len(resource_config) or resource_config[i] is None:
                continue
            avail = origin_city["availableResources"][i]
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
                "Distribute", coords,
            )
            if result["success"]:
                cycle_sent += 1

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

    cycle_sent = 0
    for dcid in dest_city_ids:
        dcid_str = str(dcid)
        targets = dest_targets.get(dcid_str)
        if not targets:
            continue

        html = session.get(city_url + dcid_str)
        dest_fresh = getCity(html)
        dest_isl_id = dest_fresh["islandId"]
        html_isl = session.get(island_url + str(dest_isl_id))
        dest_island = getIsland(html_isl)
        coords = f"[{dest_island['x']}:{dest_island['y']}]"

        for cid in source_city_ids:
            cid_str = str(cid)
            needed = [0] * len(materials_names)
            for i in range(len(materials_names)):
                if i >= len(targets) or targets[i] is None:
                    continue
                gap = targets[i] - dest_fresh["availableResources"][i]
                needed[i] = max(
                    0, min(gap, dest_fresh["freeSpaceForResources"][i])
                )

            if all(n <= 0 for n in needed):
                break

            html = session.get(city_url + cid_str)
            src_fresh = getCity(html)
            reserves = source_reserves.get(cid_str, [0] * len(materials_names))
            to_send = [0] * len(materials_names)
            for i in range(len(materials_names)):
                if needed[i] <= 0:
                    continue
                avail = src_fresh["availableResources"][i]
                reserve = reserves[i] if i < len(reserves) else 0
                sendable = max(0, avail - reserve)
                to_send[i] = min(needed[i], sendable)

            if sum(to_send) > 0:
                route = (src_fresh, dest_fresh, dest_island["id"], *to_send)
                result = send_shipment(
                    session, route, useFreighters, notif_config, log_path,
                    "TopUp", coords,
                )
                if result["success"]:
                    cycle_sent += 1
                    html = session.get(city_url + dcid_str)
                    dest_fresh = getCity(html)

    return cycle_sent


def run_even_cycle(session, sched, notif_config, log_path):
    city_ids = sched.get("source_city_ids") or []
    resource_indices = sched.get("resource_config") or []
    ship_type = sched.get("ship_type", "m")
    useFreighters = (ship_type == "f")

    if not city_ids or not resource_indices:
        return 0

    all_cities = []
    for cid in city_ids:
        html = session.get(city_url + str(cid))
        all_cities.append(getCity(html))

    if not all_cities:
        return 0

    cycle_sent = 0

    for res_idx in resource_indices:
        if not isinstance(res_idx, int) or res_idx < 0 or res_idx >= len(materials_names):
            continue
        res_name = materials_names[res_idx]

        total = sum(c["availableResources"][res_idx] for c in all_cities)
        target = total // len(all_cities)

        senders = []
        receivers = []
        for city in all_cities:
            current = city["availableResources"][res_idx]
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
                    "Even Distribution", coords,
                )
                if result["success"]:
                    cycle_sent += 1

                s_rem -= amount
                r_rem -= amount

            if s_rem == 0:
                si += 1
                if si < len(senders):
                    s_rem = senders[si]["amount"]
            if r_rem == 0:
                ri += 1
                if ri < len(receivers):
                    r_rem = receivers[ri]["amount"]

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
    destination_city = getCity(html)
    html = session.get(island_url + destination_city["islandId"])
    destination_island = getIsland(html)

    html = session.get()
    city_ids = re.findall(r'<option value="(\d+)" class="cityowntown"', html)
    suppliers = []
    for cid in city_ids:
        if str(cid) == dest_city_id:
            continue
        html_c = session.get(city_url + str(cid))
        suppliers.append(getCity(html_c))

    if not suppliers:
        return 0

    routes = allocate_from_suppliers(
        list(requested), suppliers, destination_city, destination_island,
    )
    if routes is None:
        if should_notify(notif_config, "error"):
            sendToBot(session,
                      f"AUTO SEND: Could not allocate resources for "
                      f"{destination_city['name']}")
        return 0

    cycle_sent = 0
    for route in routes:
        result = send_shipment(
            session, route, useFreighters, notif_config, log_path,
            "Auto Send",
        )
        if result["success"]:
            cycle_sent += 1
        elif result["error"] and "lock" in result["error"].lower():
            break

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

    fieldnames = ensure_transport_column(fieldnames, rows)
    fieldnames = ensure_from_column(fieldnames, rows)
    fieldnames = ensure_issues_column(fieldnames, rows)
    for row in rows:
        row["Issues"] = ""

    city_cache = {}
    mismatches = []
    validated_cities = {}

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
                row["Issues"] = issue
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
                    row["Issues"] = issue
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
                row["Issues"] = issue
                mismatches.append(f"Row {row_num}: {issue}")
                continue

            if not expected_location:
                loc_token = get_city_location_token(matched_city)
                if loc_token:
                    row["City_Location"] = loc_token

            validated_cities[row_num] = (matched_city, island)

        except Exception as e:
            issue = f"Error: {e}"
            row["Issues"] = issue
            mismatches.append(f"Row {row_num}: {issue}")

    try:
        write_csv_atomic(csv_path, fieldnames, rows)
    except Exception:
        pass

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
            row["Issues"] = f"Error resolving source cities: {e}"
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
            row["Issues"] = f"Error fetching city details: {e}"
            try:
                write_csv_atomic(csv_path, fieldnames, rows)
            except Exception:
                pass
            continue

        dest_space = dest_city.get(
            "freeSpaceForResources", [0] * len(materials_names)
        )
        for src_idx, src_city in src_cities:
            if src_idx in done_indices:
                continue
            resources = resolve_resources(
                parsed_resources, src_city.get("availableResources", []),
                row, csv_resource_cols,
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

    for idx, (row_num, route, resources, dest_name,
              player, rx, ry, src_name, src_idx,
              row_freighters, parsed_res) in enumerate(routes):

        has_except = any(m == "except" for m, _ in parsed_res)
        if has_except:
            src_city_id = str(route[0]["id"])
            src_fresh = getCity(session.get(city_url + src_city_id))
            resources = resolve_resources(
                parsed_res, src_fresh.get("availableResources", []),
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
            if sum(resources) == 0:
                skipped += 1
                continue
            route = (src_fresh, dest_fresh, route[2], *resources)

        session.setStatus(
            f"[SENDING] Bulk Dist [{idx+1}/{total}] "
            f"{src_name} -> {dest_name}"
        )

        coords = f"[{rx}:{ry}]"
        result = send_shipment(
            session, route, row_freighters, notif_config,
            log_path, "Bulk Distribution", coords, player,
        )

        if result["success"]:
            completed += 1
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

    if should_notify(notif_config, "complete"):
        summary = f"{completed}/{total} sent"
        if skipped:
            summary += f", {skipped} skipped"
        run_done = sum(
            1 for r in rows
            if normalize_text(r.get(run_column, "")) == "x"
        )
        sendToBot(session,
                  f"BULK DIST COMPLETE\n"
                  f"Slot: {run_column[4:]}\n"
                  f"Cycle: {summary}\n"
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

        now = int(time.time())
        schedules = transport_csv_load(session)
        active = [s for s in schedules if s.get("status") == "active"]

        if not active:
            session.setStatus("Transport worker: no active schedules, sleeping...")
            stop_event.wait(TICK_BUDGET_SECONDS)
            continue

        for sched in active:
            if stop_event.is_set():
                break

            next_run = sched.get("next_run", "")
            if isinstance(next_run, int) and next_run > now:
                continue

            sid = sched["schedule_id"]
            cycle_sent = execute_schedule(session, sched, notif_config, log_path)

            total = sched.get("total_shipments", 0) + cycle_sent
            interval = sched.get("interval_hours", 0)

            if interval > 0:
                next_ts = now + interval * 3600
                transport_csv_update(
                    session, sid,
                    last_run=now, next_run=next_ts,
                    total_shipments=total, status="active",
                )
            else:
                transport_csv_update(
                    session, sid,
                    last_run=now, status="completed",
                    total_shipments=total,
                )

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

        stop_event.wait(sleep_for)

    # Cleanup
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
        print("  Another transport worker is already running for this account.")
        print(f"  Lock file: {wlock}")
        print("  Use 'Stop worker' to stop it, or remove the lock file")
        print("  if you're sure no worker is running.")
        enter()
        return

    if not enforce_transport_schema_or_abort(session):
        _lock_release(wlock)
        enter()
        return

    schedules = transport_csv_load(session)
    activatable = [
        s for s in schedules
        if s.get("status") in ("active", "pending")
    ]

    if not activatable:
        print("  No active or pending schedules found.")
        print("  Create schedules first using modes 1-6.")
        _lock_release(wlock)
        enter()
        return

    print(f"\n  {len(activatable)} schedule(s) to activate:\n")
    for s in activatable:
        sid = s.get("schedule_id", "?")
        mode = s.get("mode", "?").capitalize()
        interval = s.get("interval_hours", 0)
        notes = s.get("notes", "")
        status = s.get("status", "?")
        interval_str = f"every {interval}h" if interval > 0 else "one-shot"
        line = f"  #{sid} {mode} ({interval_str})"
        if notes:
            line += f" - {notes}"
        if status == "pending":
            line += " [NEW]"
        print(line)

    print(f"\n  Resume mode:")
    print(f"  (1) Continue as scheduled")
    print(f"      Missed runs execute immediately.")
    print(f"  (2) Start from now")
    print(f"      Reset all schedules to run from current time.")
    print(f"  (') Cancel")

    choice = read(min=1, max=2, digit=True, additionalValues=["'"])
    if choice == "'":
        _lock_release(wlock)
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
        print("  No transport worker appears to be running.")
        enter()
        return
    pathlib.Path(flag).touch()
    print(
        f"  Stop flag written. The worker will exit within "
        f"{TICK_BUDGET_SECONDS}s after finishing any active shipments."
    )
    enter()


# ----------------------------------------------------------------------------
#  Schedule management menu  (Option 7)
# ----------------------------------------------------------------------------

def manage_schedules_menu(session, event, telegram_enabled, log_path):
    while True:
        if not enforce_transport_schema_or_abort(session):
            enter()
            event.set()
            return

        counts = transport_csv_count_by_status(session)
        total = sum(counts.values())
        active_ct = counts.get("active", 0)
        pending_ct = counts.get("pending", 0)
        paused_ct = counts.get("paused", 0)
        worker_running = _is_transport_worker_running(session)

        print_module_banner("Transport Schedule Manager")
        print(f"  CSV: {transport_csv_path(session)}")
        print(f"  Schedules: {total} total "
              f"({active_ct} active, {pending_ct} pending, {paused_ct} paused)")
        print(f"  Worker: {'RUNNING' if worker_running else 'stopped'}\n")

        print("(1) View schedules")
        print("(2) Modify schedule")
        print("(3) Pause/resume schedule")
        print("(4) Delete schedule(s)")
        print("(5) Activate transport worker")
        print("(6) Stop transport worker")
        print("(') Back")

        choice = read(min=1, max=6, digit=True, additionalValues=["'"])
        if choice == "'":
            event.set()
            return

        if choice == 1:
            _view_schedules(session)
        elif choice == 2:
            _modify_schedule(session)
        elif choice == 3:
            _toggle_schedule_pause(session)
        elif choice == 4:
            _delete_schedules(session)
        elif choice == 5:
            _activate_transport_worker(session, event)
            return
        elif choice == 6:
            _stop_transport_worker(session)


def _view_schedules(session):
    rows = transport_csv_load(session)
    if not rows:
        print("\n  No schedules found.\n")
        enter()
        return

    print(f"\n  {'ID':>4} {'Mode':<13} {'Status':<10} {'Interval':<10} "
          f"{'Ship':<5} {'Sent':>6} {'Last Run':<12} {'Notes'}")
    print(f"  {'---':>4} {'---':<13} {'---':<10} {'---':<10} "
          f"{'---':<5} {'---':>6} {'---':<12} {'---'}")

    for r in rows:
        sid = r.get("schedule_id", "?")
        mode = r.get("mode", "?").capitalize()
        status = r.get("status", "?")
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

        print(f"  {sid:>4} {mode:<13} {status:<10} {interval_str:<10} "
              f"{ship:<5} {total_sent:>6} {last_str:<12} {notes}")

    print(f"\n  Total: {len(rows)} schedule(s)\n")
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

    print(f"\n  Schedule #{sid}")
    print(f"  {'─' * 40}")
    print(f"  Mode:          {mode.capitalize()}")
    print(f"  Status:        {status}")
    print(f"  Ship type:     {ship}")
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
        print(f"  Source IDs:    {src_ids}")
    dest_ids = sched.get("dest_city_ids") or []
    if dest_ids:
        print(f"  Dest IDs:      {dest_ids}")
    res_cfg = sched.get("resource_config")
    if res_cfg:
        print(f"  Resources:     {res_cfg}")
    send_mode = sched.get("send_mode", "na")
    if send_mode != "na":
        print(f"  Send mode:     {send_mode}")
    dest_min = sched.get("dest_minimums")
    if dest_min and any(d for d in dest_min if d):
        print(f"  Dest minimums: {dest_min}")
    dest_tgt = sched.get("dest_targets")
    if dest_tgt:
        print(f"  Dest targets:  {dest_tgt}")
    src_res = sched.get("source_reserves")
    if src_res:
        print(f"  Src reserves:  {src_res}")
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

        print("\n  What to modify?")
        print("  (1) Interval (hours)")
        print("  (2) Ship type")
        print("  (3) Notes")
        print("  (4) Notification level")
        print("  (5) Resources")
        print("  (6) Send mode (consolidate only)")
        print("  (7) Destination minimums")
        print("  (') Back")

        choice = read(min=1, max=7, digit=True, additionalValues=["'"])
        if choice == "'":
            return

        if choice == 1:
            print(f"\n  Current interval: {target.get('interval_hours', 0)}h")
            print("  New interval (0 = one-shot, 1+ = recurring):")
            val = read(min=0, digit=True, additionalValues=["'"])
            if val == "'":
                continue
            transport_csv_update(session, sid, interval_hours=val)
            target["interval_hours"] = val
            if val > 0 and target.get("next_run", "") in ("", 0):
                next_ts = int(time.time()) + val * 3600
                transport_csv_update(session, sid, next_run=next_ts)
                target["next_run"] = next_ts
            print(f"  Interval updated to {val}h.")

        elif choice == 2:
            current = "Freighters" if target.get("ship_type") == "f" else "Merchant"
            print(f"\n  Current: {current}")
            print("  (1) Merchant ships  (2) Freighters")
            st = read(min=1, max=2, digit=True, additionalValues=["'"])
            if st == "'":
                continue
            new_type = "f" if st == 2 else "m"
            transport_csv_update(session, sid, ship_type=new_type)
            target["ship_type"] = new_type
            label = "Freighters" if new_type == "f" else "Merchant ships"
            print(f"  Ship type updated to {label}.")

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
            print("  (1) Partial  (2) All  (3) None")
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
                print(f"\n  Current resource indices: {current}")
                print("  Enter new indices (comma-separated, e.g. 0,2,4):")
                print("  0=Wood, 1=Wine, 2=Marble, 3=Crystal, 4=Sulphur")
                raw = read(additionalValues=["'"])
                if raw == "'":
                    continue
                try:
                    indices = [int(x.strip()) for x in raw.split(",")]
                    indices = [i for i in indices if 0 <= i < len(materials_names)]
                except ValueError:
                    print("  Invalid input.")
                    enter()
                    continue
                transport_csv_update(session, sid, resource_config=indices)
                target["resource_config"] = indices
            else:
                print(f"\n  Current resources: {current}")
                print("  Enter new amounts (5 values, comma-separated):")
                print("  Wood,Wine,Marble,Crystal,Sulphur")
                print("  (Use 0 to skip, blank=keep current)")
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
            if target.get("mode") != "consolidate":
                print("  Send mode only applies to consolidate.")
                enter()
                continue
            current = target.get("send_mode", "send")
            print(f"\n  Current: {current}")
            print("  (1) Keep reserves (send all except X)")
            print("  (2) Send specific amounts")
            sm = read(min=1, max=2, digit=True, additionalValues=["'"])
            if sm == "'":
                continue
            new_sm = "keep" if sm == 1 else "send"
            transport_csv_update(session, sid, send_mode=new_sm)
            target["send_mode"] = new_sm
            print(f"  Send mode updated to {new_sm}.")

        elif choice == 7:
            current = target.get("dest_minimums") or [0, 0, 0, 0, 0]
            print(f"\n  Current minimums: {current}")
            print("  Enter new minimums (5 values, comma-separated):")
            print("  Wood,Wine,Marble,Crystal,Sulphur (0 = no minimum)")
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
        print(f"  Schedule #{sid} paused.")
    elif current_status == "paused":
        transport_csv_update(session, sid, status="active")
        print(f"  Schedule #{sid} resumed.")
    elif current_status == "pending":
        transport_csv_update(session, sid, status="active")
        print(f"  Schedule #{sid} activated.")
    else:
        print(f"  Schedule #{sid} has status '{current_status}' and cannot be toggled.")
    enter()


def _delete_schedules(session):
    rows = transport_csv_load(session)
    if not rows:
        print("\n  No schedules found.\n")
        enter()
        return

    _view_schedules_compact(rows)
    print("  Enter schedule ID(s) to delete (comma-separated, or ' to cancel):")
    sid_input = read(additionalValues=["'"])
    if sid_input == "'":
        return

    try:
        sids = [int(x.strip()) for x in sid_input.split(",")]
    except ValueError:
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

    print(f"  Delete schedule(s) {to_delete}? [y/N]")
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
