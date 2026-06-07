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
    print("\u2551            RESOURCE TRANSPORT MANAGER v7.0                  \u2551")
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
        prefix=".tmp_massdist_", suffix=".csv", dir=directory
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
    """LEGACY: previously inserted a single global Issues column. Mass
    distribution now uses one Issues column per Run_ slot (see
    ensure_per_slot_issues_columns). This function is kept only so
    callers outside the migration path don't break — it no longer
    inserts a column."""
    return fieldnames


def issues_col_for(run_column):
    """Map a Run_<slot> column name to its sibling Issues_<slot> column.

    Run column names are produced by build_run_column_name as
    'Run_YYYY-MM-DD_HH:MM'. The Issues column for the same slot
    is just that with 'Run_' replaced by 'Issues_'.
    """
    if not run_column or not run_column.startswith("Run_"):
        return "Issues"  # graceful fallback, shouldn't fire in practice
    return "Issues_" + run_column[len("Run_"):]


def ensure_per_slot_issues_columns(fieldnames, rows, run_columns):
    """Make sure there's an Issues_<slot> column immediately after each
    Run_<slot> column. Backward-compatibility: if the CSV still has the
    old global 'Issues' column, its values are copied into the OLDEST
    slot's Issues column (best-effort context preservation) and the
    old column is removed. Returns the updated fieldnames list.

    Idempotent — calling on an already-migrated CSV is a no-op.
    """
    # Step 1: insert missing per-slot Issues columns next to each Run_.
    for run_col in run_columns:
        issues_col = issues_col_for(run_col)
        if issues_col in fieldnames:
            continue
        insert_idx = fieldnames.index(run_col) + 1
        fieldnames.insert(insert_idx, issues_col)
        for row in rows:
            row[issues_col] = ""

    # Step 2: migrate any legacy global 'Issues' column.
    if "Issues" in fieldnames:
        # Pick the oldest run slot to receive legacy values — it's the
        # safest dumping ground because (a) the user is most likely
        # working in newer slots and (b) the migrated context becomes
        # historical rather than blocking the current cycle. If no
        # run column parses as a datetime, fall back to the first
        # available run column so we never silently drop content.
        oldest_run = None
        oldest_dt = None
        for run_col in run_columns:
            dt = parse_run_column_datetime(run_col)
            if dt is None:
                continue
            if oldest_dt is None or dt < oldest_dt:
                oldest_dt = dt
                oldest_run = run_col
        if oldest_run is None and run_columns:
            oldest_run = run_columns[0]
        target_issues = issues_col_for(oldest_run) if oldest_run else None
        if target_issues and target_issues in fieldnames:
            for row in rows:
                legacy = (row.get("Issues") or "").strip()
                if not legacy:
                    continue
                existing = (row.get(target_issues) or "").strip()
                if existing:
                    row[target_issues] = f"{existing}; (migrated) {legacy}"
                else:
                    row[target_issues] = f"(migrated) {legacy}"
        fieldnames.remove("Issues")
        for row in rows:
            row.pop("Issues", None)

    return fieldnames


def ensure_shipped_column(fieldnames, rows):
    """Add a 'Shipped' tracker column if missing.

    Stores a comma-separated list of resource names (Wood, Wine, ...)
    that have ever successfully shipped for this row. Used by the
    cancel-after-dribble logic for 'except' (reserve) orders: we only
    cancel a reserve order's resource if we've shipped it at least
    once before, so brand-new orders don't get killed off before they
    ever get a chance to send anything.
    """
    if "Shipped" not in fieldnames:
        # Put it near the other status/tracking columns. After
        # 'From' is a sensible neighbourhood; otherwise append.
        insert_idx = len(fieldnames)
        if "From" in fieldnames:
            insert_idx = fieldnames.index("From") + 1
        fieldnames.insert(insert_idx, "Shipped")
        for row in rows:
            row.setdefault("Shipped", "")
    return fieldnames


def parse_shipped_set(val):
    """Parse the Shipped cell into a set of resource names."""
    if not val:
        return set()
    return {p.strip() for p in str(val).split(",") if p.strip()}


def format_shipped_set(s):
    """Render a set of resource names as a stable, sorted CSV string."""
    return ",".join(sorted(s))


def parse_resource_value(val):
    """Parse a resource cell: '500' -> ('exact', 500), 'e10000' -> ('except', 10000)."""
    val = val.strip()
    if val.lower().startswith("e"):
        num_part = val[1:].strip()
        match = re.search(r"\d+", num_part)
        return ("except", int(match.group()) if match else 0)
    return ("exact", int(val) if val.isdigit() else 0)


def resolve_resources(parsed, source_available, row, csv_resource_cols,
                      issues_col="Issues"):
    """Resolve parsed resource values against source city stock.

    Modes:
      'exact':  send the literal amount unless source has less. If source
                stock < requested, ship min(stock, requested) and surface
                a note. The caller (mass distribution Phase 2) decides
                whether to cancel the resource based on its own policy
                (see do_it_mass_distribution).
      'except': send (available - reserve). If available <= reserve, ship
                0 and log a note.

    `issues_col` names the column to append notes to (per-slot Issues
    in mass distribution; falls back to the legacy 'Issues' otherwise).
    """
    resolved = []
    for i, (mode, amount) in enumerate(parsed):
        avail = source_available[i] if i < len(source_available) else 0
        if mode == "except":
            if avail <= amount:
                resolved.append(0)
                if row is not None:
                    prev = row.get(issues_col, "")
                    note = f"{csv_resource_cols[i]}: stock {avail} <= reserve {amount}"
                    row[issues_col] = f"{prev}; {note}" if prev else note
            else:
                resolved.append(avail - amount)
        else:
            # Exact mode: cap to whatever's actually in stock. If we have
            # to cap, leave a note — the caller may decide to cancel.
            if amount > 0 and avail < amount:
                resolved.append(avail)
                if row is not None and avail < amount:
                    prev = row.get(issues_col, "")
                    note = (
                        f"{csv_resource_cols[i]}: requested {amount}, "
                        f"source had {avail} — capped to {avail}"
                    )
                    row[issues_col] = f"{prev}; {note}" if prev else note
            else:
                resolved.append(amount)
    return resolved


def choose_run_slot(session, event, rows, run_columns):
    print_module_banner("Mass Distribution - Run Slot")
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
        print("(5) Mass Distribution: Send from CSV file")
        print("(6) Keep Topped Up: Automatically top up a city's resources")
        print("(') Back to main menu")
        shipping_mode = read(min=1, max=6, digit=True, additionalValues=["'"])
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
            massDistributionMode(session, event, stdin_fd,
                                 predetermined_input, telegram_enabled,
                                 log_path)
        else:
            topUpMode(session, event, stdin_fd,
                      predetermined_input, telegram_enabled,
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

    set_child_mode(session)
    event.set()

    info = (
        f"\nAuto-send from {source_summary} to "
        f"{destination_city['name']} every {interval_hours}h\n"
    )
    setInfoSignal(session, info)
    try:
        do_it(session, origin_cities, destination_city, island,
              interval_hours, resource_config, useFreighters, send_mode,
              notif_config, dest_minimums, log_path)
    except Exception:
        sendToBot(session, f"Error in:\n{info}\nCause:\n{traceback.format_exc()}")
    finally:
        session.logout()


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

    set_child_mode(session)
    event.set()

    info = (
        f"\nDistribute from {origin_city['name']} to "
        f"{len(destination_cities)} cities every {interval_hours}h\n"
    )
    setInfoSignal(session, info)
    try:
        do_it_distribute(session, origin_city, destination_cities,
                         interval_hours, resource_config, useFreighters,
                         notif_config, dest_minimums, log_path)
    except Exception:
        sendToBot(session, f"Error in:\n{info}\nCause:\n{traceback.format_exc()}")
    finally:
        session.logout()


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

    set_child_mode(session)
    event.set()

    info = f"\nBalancing {selected_names} across {len(all_cities)} cities\n"
    setInfoSignal(session, info)

    try:
        # Execute sequentially per resource
        for res_idx in resource_indices:
            res_name = materials_names[res_idx]
            shipments = all_shipments[res_idx]
            do_even_distribution(session, shipments, res_idx, res_name,
                                 useFreighters, notif_config, log_path)
    except Exception:
        sendToBot(session, f"Error in:\n{info}\nCause:\n{traceback.format_exc()}")
    finally:
        session.logout()


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

                    set_child_mode(session)
                    event.set()
                    info = f"\nAuto Send to {destination_city['name']}\n"
                    setInfoSignal(session, info)
                    try:
                        do_it_auto_send(session, routes, useFreighters,
                                        notif_config, log_path)
                    except Exception:
                        sendToBot(session,
                                  f"Error:\n{info}\n{traceback.format_exc()}")
                    finally:
                        session.logout()
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
#  MODE 5: MASS DISTRIBUTION  (CSV-driven bulk sends)
# ============================================================================

def massDistributionMode(session, event, stdin_fd, predetermined_input,
                         telegram_enabled, log_path):
    try:
        print_module_banner("Mass Distribution")
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
            enter()
            event.set()
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
            print("Hours must be >= 1 for Mass Distribution.")
            enter()
            event.set()
            return

        fieldnames, run_columns = ensure_run_columns(fieldnames, rows)
        fieldnames = ensure_transport_column(fieldnames, rows)
        fieldnames = ensure_from_column(fieldnames, rows)
        fieldnames = ensure_shipped_column(fieldnames, rows)
        # Per-slot Issues columns replace the old single global 'Issues'
        # column. Migration is handled inside the helper (legacy 'Issues'
        # values get copied into the oldest slot's column).
        fieldnames = ensure_per_slot_issues_columns(fieldnames, rows, run_columns)

        mode, run_column = choose_run_slot(session, event, rows, run_columns)
        if run_column is None:
            return

        # Reassemble fieldnames with stable column order: everything that
        # isn't a Run_/Issues_ slot first, then each Run_ slot followed
        # by its matching Issues_ slot. This keeps the per-day Issues
        # column visually next to its Run column when you open the CSV.
        fieldnames_no_runs = [
            c for c in fieldnames
            if not c.startswith("Run_") and not c.startswith("Issues_")
        ]
        run_and_issues = []
        for run_col in run_columns:
            run_and_issues.append(run_col)
            issues_col = issues_col_for(run_col)
            if issues_col in fieldnames:
                run_and_issues.append(issues_col)
        fieldnames = fieldnames_no_runs + run_and_issues
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
            print_module_banner("Mass Distribution - Summary")
            print(f"  CSV rows: {len(rows)}")
            print(f"  Interval: every {interval_hours}h")
            print(f"  Run slot: {run_column[4:]}\n")
            print("(Y) Proceed  (D) Dry run preview  (N) Cancel")
            rta = read(values=["y", "Y", "n", "N", "d", "D", "", "'"],
                       additionalValues=["'"])
            if rta == "'" or rta.lower() == "n":
                event.set()
                return
            if rta.lower() == "d":
                preview, skip_reasons = _scan_csv_for_preview(
                    session, rows, run_column
                )
                if preview:
                    run_dry_preview(preview, "Mass Distribution")
                    if skip_reasons:
                        # Even with some matches we still want to flag
                        # the rows that fell out, so the user can fix
                        # them before kicking off the real run.
                        print()
                        print("  Some rows were skipped during the scan:")
                        print(_format_skip_reasons(skip_reasons, len(rows)))
                else:
                    print("  No valid routes found in dry-run scan.")
                    print()
                    print(_format_skip_reasons(skip_reasons, len(rows)))
                    print()
                    print("  Note: the dry-run only looks at the CSV. It does")
                    print("  not fetch live stock, so 'except' (reserve) rows")
                    print("  that show here aren't guaranteed to ship during")
                    print("  the real run if your source cities don't actually")
                    print("  have surplus when the cycle fires.")
                print("Press Enter to continue...")
                enter()
                continue
            break

        enter()

    except KeyboardInterrupt:
        event.set()
        return

    set_child_mode(session)
    event.set()

    info = f"\nMass Distribution every {interval_hours}h\n"
    setInfoSignal(session, info)

    try:
        do_it_mass_distribution(session, csv_path, interval_hours,
                                notif_config, run_column, log_path)
    except Exception:
        sendToBot(session, f"Error:\n{info}\n{traceback.format_exc()}")
    finally:
        session.logout()


def _scan_csv_for_preview(session, rows, run_column):
    """Quick scan for dry-run preview.

    Returns (preview, skip_reasons) where preview is the list of
    route-info dicts that WOULD be shown and skip_reasons is a dict
    of {reason_text: count} for rows that were dropped. The caller
    uses skip_reasons to explain *why* the preview is empty instead
    of just saying 'no routes' and leaving the user guessing.

    Reasons we surface:
      - already marked done in this run slot      (run cell == "x")
      - has unresolved Issues from a prior cycle  (Issues non-empty)
      - no resources requested                    (all five amounts 0)
      - From column empty or unparseable          (no source set)
    Note: this scan is CSV-only — it does NOT fetch live stock from
    the server, so 'except' rows that would resolve to 0 against
    current stock still show up as candidates here. That's by design
    (a dry-run should be cheap), but the caller surfaces the caveat
    when the preview is empty so the user knows the real run may
    behave differently.
    """
    csv_res_cols = ["Wood", "Wine", "Marble", "Crystal", "Sulphur"]
    preview = []
    skip_reasons = {}
    issues_col = issues_col_for(run_column)
    def _skip(reason):
        skip_reasons[reason] = skip_reasons.get(reason, 0) + 1

    for row in rows:
        if normalize_text(row.get(run_column, "")) == "x":
            _skip("already marked done in this run slot")
            continue
        # Slot-scoped Issues: the dry-run only checks the issues column
        # for THIS slot, so a problem recorded against a different day
        # doesn't block the current cycle.
        if row.get(issues_col, "").strip():
            _skip(f"has unresolved Issues in {issues_col} from this slot's "
                  "last cycle (clear that column to re-try)")
            continue
        parsed = [parse_resource_value(row.get(col, "0")) for col in csv_res_cols]
        has_resources = any(amt > 0 or mode == "except" for mode, amt in parsed)
        if not has_resources:
            _skip("no resources requested (all five amounts are 0)")
            continue
        from_val_check = parse_from_column(row.get("From", ""))
        if from_val_check is None:
            _skip("From column is empty or unparseable "
                  "(needs 'a' for all, or city indices like 1,3,5)")
            continue
        resources = [amt for _, amt in parsed]
        city_name = row.get("City", "?")
        player = row.get("Player", "?")
        from_val = from_val_check
        if from_val == "all":
            src_label = "All cities"
        else:
            src_label = f"Cities {','.join(str(i) for i in from_val)}"
        transport = "Freighters" if parse_transport_value(row.get("Transport", "m")) else "Merchant"
        preview.append({
            "source": f"{src_label} ({transport})",
            "dest": f"{city_name} ({player})",
            "resources": resources,
        })
    return preview, skip_reasons


def _format_skip_reasons(skip_reasons, total_rows):
    """Render a {reason: count} dict as a bulleted block, with a
    line at the end telling the user how many rows we looked at
    and how many were skipped."""
    if not skip_reasons:
        return f"  (Scanned {total_rows} row(s), no rows were skipped — but no routes were produced either.)"
    skipped = sum(skip_reasons.values())
    lines = [f"  Scanned {total_rows} row(s), skipped {skipped}:"]
    for reason, count in sorted(skip_reasons.items(), key=lambda kv: -kv[1]):
        lines.append(f"    - {count} row(s): {reason}")
    return "\n".join(lines)


# ============================================================================
#  MODE 5 EXECUTION: do_it_mass_distribution
# ============================================================================

def do_it_mass_distribution(session, csv_path, interval_hours,
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
                          f"MASS DIST ERROR\nCould not read CSV: {e}")
            time.sleep(3600)
            continue

        # Ensure run column exists — always call (idempotent) so we have
        # the up-to-date list of run slots for the per-slot Issues mirror
        # below.
        fieldnames, run_columns = ensure_run_columns(fieldnames, rows)
        if run_column not in run_columns:
            run_column = run_columns[0]

        # Ensure Transport, From, Shipped, and per-slot Issues columns
        # exist. The Issues helper also migrates the legacy single
        # 'Issues' column into the oldest slot, then drops it.
        fieldnames = ensure_transport_column(fieldnames, rows)
        fieldnames = ensure_from_column(fieldnames, rows)
        fieldnames = ensure_shipped_column(fieldnames, rows)
        fieldnames = ensure_per_slot_issues_columns(fieldnames, rows, run_columns)

        # The Issues column for THIS run slot. All per-cycle writes go
        # here so a problem recorded against day N doesn't pollute the
        # display for day M.
        current_issues_col = issues_col_for(run_column)

        # Clear THIS slot's Issues column at the start of the cycle. We
        # deliberately do NOT touch other slots' Issues — those record
        # the state of their respective last runs and are user-facing
        # history.
        for row in rows:
            row[current_issues_col] = ""

        try:
            write_csv_atomic(csv_path, fieldnames, rows)
        except Exception:
            time.sleep(3600)
            continue

        city_cache = {}
        mismatches = []
        validated_cities = {}

        session.setStatus(
            f"[PRE-SCAN] Mass Distribution | "
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
                    row[current_issues_col] = issue
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
                        row[current_issues_col] = issue
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
                    row[current_issues_col] = issue
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
                row[current_issues_col] = issue
                mismatches.append(f"Row {row_num}: {issue}")

        # Save CSV after pre-scan (persists Issues + City_Location auto-fills)
        try:
            write_csv_atomic(csv_path, fieldnames, rows)
        except Exception as we:
            print(f"    WARNING: pre-scan CSV write failed: {we}")

        if mismatches and should_notify(notif_config, "error"):
            sendToBot(session,
                      f"MASS DIST ISSUES\n"
                      + "\n".join(mismatches))

        # ---- PHASE 2: Build routes from validated rows ----
        routes = []
        # Track per-reason counts AND a few sample rows for each reason,
        # so that when 'routes' ends up empty we can explain exactly
        # what happened instead of the old "No valid routes found"
        # one-liner that left the user guessing.
        skip_reasons = {}
        skip_samples = {}

        def _drop(reason, row_num, row):
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            samples = skip_samples.setdefault(reason, [])
            if len(samples) < 3:
                label = (f"row {row_num} "
                         f"({row.get('Player','?')}/{row.get('City','?')} "
                         f"[{row.get('X','?')}:{row.get('Y','?')}])")
                samples.append(label)

        # Rows skipped during phase 1 also belong in the breakdown, or
        # the count won't add up to "rows in the CSV".
        for row_num, row in enumerate(rows, start=1):
            run_val_p1 = normalize_text(row.get(run_column, ""))
            if run_val_p1 == "x":
                _drop("already marked done in this run slot", row_num, row)
                continue
            if row_num not in validated_cities:
                # Either it had no resources requested, an Issue from
                # validation, or one of the from-column / city-match
                # failures. The Issues column already carries the detail
                # for failures; we only need a coarse bucket here.
                parsed_p1 = [parse_resource_value(row.get(col, "0")) for col in csv_resource_cols]
                if not any(amt > 0 or mode == "except" for mode, amt in parsed_p1):
                    _drop("no resources requested (all five amounts are 0)", row_num, row)
                else:
                    _drop("failed pre-scan validation (see Issues column)", row_num, row)

        session.setStatus(
            f"[PROCESSING] Mass Distribution | "
            f"Building routes for {len(validated_cities)} row(s)..."
        )

        # Refresh city cache for "except" resolution
        city_cache.pop("objects", None)

        # Per-ship cargo capacity — needed by the partial-fill /
        # "dribble" detector. Both kinds (merchant + freighter) get
        # looked up once per cycle; per-row we pick the appropriate
        # one based on the row's Transport column.
        try:
            base_ship_cap, base_freighter_cap = getShipCapacity(session)
        except Exception:
            # If the lookup fails we still want the cycle to run; the
            # cancellation rule simply doesn't fire when we don't know
            # the capacity (treating it as "infinite" so no shipment
            # ever looks tiny).
            base_ship_cap = base_freighter_cap = 10 ** 9

        # Cancellations accumulated across the whole cycle so we send
        # one batched Telegram message at the end (or fall back to a
        # local print) rather than one alert per resource.
        cancellations_queue = []

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
            per_ship_cap = base_freighter_cap if row_use_freighters else base_ship_cap

            try:
                src_cities = get_source_cities_for_row(
                    session, from_val, city_cache
                )
            except Exception as e:
                row[current_issues_col] = f"Error resolving source cities: {e}"
                _drop(f"error resolving source cities ({e})", row_num, row)
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
                row[current_issues_col] = f"Error fetching city details: {e}"
                _drop(f"error fetching destination city ({e})", row_num, row)
                try:
                    write_csv_atomic(csv_path, fieldnames, rows)
                except Exception:
                    pass
                continue

            row_produced_route = False
            row_all_sources_done = True
            row_all_sources_empty = True
            for src_idx, src_city in src_cities:
                if src_idx in done_indices:
                    continue
                row_all_sources_done = False
                resources = resolve_resources(
                    parsed_resources, src_city.get("availableResources", []),
                    row, csv_resource_cols, current_issues_col
                )

                # ---- Per-resource cancel-on-partial-fill analysis ----
                # Decides per resource whether to send what we've got
                # and stop trying. Rules (chosen via AskUserQuestion):
                #   exact   : ANY shortfall — source had less than the
                #             requested amount — cancel.
                #   except  : cancel only if (a) we've shipped this
                #             resource before (Shipped tracker proves
                #             it) AND (b) this cycle's shipment is
                #             below per-ship capacity (a 'dribble').
                # Cancelling: zero the request cell so future cycles
                # don't re-attempt; append a note to THIS slot's
                # Issues column; queue a Telegram alert; remove the
                # resource from the current cycle's `resources` list
                # so the dribble itself doesn't get shipped either
                # (matches user intent: stop pestering with tiny
                # shipments).
                shipped_set = parse_shipped_set(row.get("Shipped", ""))
                cancellations_this_pass = []
                avail_list = src_city.get("availableResources") or []
                for i, res_name in enumerate(csv_resource_cols):
                    mode, requested = parsed_resources[i]
                    stock = avail_list[i] if i < len(avail_list) else 0
                    shipped_this = resources[i] if i < len(resources) else 0
                    if mode == "exact":
                        if requested > 0 and stock < requested:
                            cancellations_this_pass.append((
                                res_name,
                                f"exact request {requested}, source had "
                                f"{stock}, delivered {shipped_this} — "
                                f"cancelling further attempts"
                            ))
                    else:  # except (reserve) mode
                        if (res_name in shipped_set
                                and shipped_this < per_ship_cap):
                            cancellations_this_pass.append((
                                res_name,
                                f"reserve-order dribble: shipped "
                                f"{shipped_this} (< per-ship capacity "
                                f"{per_ship_cap}) after prior successful "
                                f"shipments — cancelling"
                            ))

                if cancellations_this_pass:
                    cancelled_names = {n for n, _ in cancellations_this_pass}
                    for res_name, reason in cancellations_this_pass:
                        i = csv_resource_cols.index(res_name)
                        prev = row.get(current_issues_col, "")
                        note = f"{res_name}: {reason}"
                        row[current_issues_col] = (
                            f"{prev}; {note}" if prev else note
                        )
                        # Zero the request cell. If the user wants to
                        # re-arm this resource later, they put a number
                        # back into the cell.
                        row[res_name] = "0"
                        # Drop from Shipped so future cycles don't see
                        # this resource as a candidate for the except-
                        # mode dribble rule.
                        shipped_set.discard(res_name)
                        # Zero in the live cycle so the dribble itself
                        # doesn't ship.
                        if i < len(resources):
                            resources[i] = 0
                        cancellations_queue.append({
                            "row_num": row_num,
                            "player": row.get("Player", "?"),
                            "city": row.get("City", "?"),
                            "x": row.get("X", "?"),
                            "y": row.get("Y", "?"),
                            "src": src_city.get("name", "?"),
                            "resource": res_name,
                            "reason": reason,
                        })
                    row["Shipped"] = format_shipped_set(shipped_set)

                if sum(resources) == 0:
                    # Either nothing was requested for this pass,
                    # resolve_resources zeroed it (reserve >= stock),
                    # or the cancellation logic just zeroed everything.
                    continue
                row_all_sources_empty = False
                row_produced_route = True
                route = (src_city, dest_city, island["id"], *resources)
                routes.append((
                    row_num, route, resources, dest_city["name"],
                    expected_player, x, y, src_city["name"], src_idx,
                    row_use_freighters
                ))

            if not row_produced_route:
                if not src_cities:
                    _drop("From column resolved to zero source cities "
                          "(check From values vs your city list)",
                          row_num, row)
                elif row_all_sources_done:
                    _drop("every source city for this row is already "
                          "marked done in this run slot",
                          row_num, row)
                elif row_all_sources_empty:
                    _drop("no source city had enough surplus above the "
                          "reserve to ship anything (see Issues column "
                          "for the per-resource detail)",
                          row_num, row)
                else:
                    _drop("produced no routes for an unexpected reason",
                          row_num, row)

        if not routes:
            # Build a human-readable explanation. The old code just
            # said 'No valid routes found' with no context — useless
            # when you're trying to figure out why your run is a no-op.
            if skip_reasons:
                lines = [
                    f"MASS DIST: No valid routes this cycle ({len(rows)} row(s) scanned)."
                ]
                for reason, count in sorted(skip_reasons.items(), key=lambda kv: -kv[1]):
                    lines.append(f"  • {count}× {reason}")
                    for sample in skip_samples.get(reason, []):
                        lines.append(f"      e.g. {sample}")
                detail = "\n".join(lines)
            else:
                detail = (
                    f"MASS DIST: No valid routes this cycle. "
                    f"({len(rows)} row(s) scanned, none qualified — "
                    f"check the CSV directives and the Issues column.)"
                )
            print(detail)
            if should_notify(notif_config, "error"):
                sendToBot(session, detail)
        else:
            if should_notify(notif_config, "start"):
                sendToBot(session,
                          f"MASS DIST SCHEDULED\n"
                          f"{len(routes)} shipment(s)")

            completed = 0
            total = len(routes)

            for idx, (row_num, route, resources, dest_name,
                      player, rx, ry, src_name, src_idx,
                      row_freighters) in enumerate(routes):
                ship_label = "F" if row_freighters else "M"
                res_desc = ", ".join(
                    f"{addThousandSeparator(resources[i])} "
                    f"{materials_names[i]}"
                    for i in range(len(materials_names))
                    if resources[i] > 0
                )
                print(f"\n  [{idx+1}/{total}] [{ship_label}] {src_name} -> "
                      f"{dest_name} ({player}) [{rx}:{ry}]")

                session.setStatus(
                    f"[SENDING] Mass Dist [{idx+1}/{total}] "
                    f"{src_name} -> {dest_name}"
                )

                coords = f"[{rx}:{ry}]"
                result = send_shipment(
                    session, route, row_freighters, notif_config,
                    log_path, "Mass Distribution", coords, player
                )

                if result["success"]:
                    completed += 1
                    print(f"    SUCCESS ({completed}/{total})")
                    # Update the Shipped tracker for any non-zero
                    # resource in this shipment. The except-mode
                    # cancellation rule keys off this set: a resource
                    # only becomes eligible for "dribble" cancellation
                    # once it has shipped successfully at least once.
                    cur_shipped = parse_shipped_set(
                        rows[row_num - 1].get("Shipped", "")
                    )
                    for i, res_name in enumerate(csv_resource_cols):
                        if i < len(resources) and resources[i] > 0:
                            cur_shipped.add(res_name)
                    rows[row_num - 1]["Shipped"] = format_shipped_set(cur_shipped)
                    # Per-city checkpoint
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
                    # Re-fetch sent source city if upcoming routes use "except"
                    sent_city_id = route[0]["id"]
                    if any(
                        any(parse_resource_value(rows[r[0] - 1].get(col, "0"))[0] == "except"
                            for col in csv_resource_cols)
                        for r in routes[idx + 1:]
                        if str(r[1][0]["id"]) == str(sent_city_id)
                    ):
                        city_cache.get("objects", {})[str(sent_city_id)] = getCity(
                            session.get(city_url + str(sent_city_id))
                        )
                else:
                    print(f"    FAILED: {result['error']}")

            print(f"\n--- Mass Distribution complete: {completed}/{total} sent ---")
            if should_notify(notif_config, "complete"):
                run_done = sum(
                    1 for r in rows
                    if normalize_text(r.get(run_column, "")) == "x"
                )
                sendToBot(session,
                          f"MASS DIST COMPLETE\n"
                          f"Slot: {run_column[4:]}\n"
                          f"Cycle: {completed}/{total}\n"
                          f"Progress: {run_done}/{len(rows)}")

        # Batched cancellation alert. Each entry in cancellations_queue
        # is one (row, resource) decision; we group by row so the
        # user sees one block per affected destination rather than a
        # wall of repetitive lines.
        if cancellations_queue:
            grouped = {}
            for c in cancellations_queue:
                key = (c["row_num"], c["player"], c["city"], c["x"], c["y"])
                grouped.setdefault(key, []).append(c)
            lines = ["MASS DIST: resources auto-cancelled this cycle."]
            lines.append("(Cell zeroed in the CSV; re-arm by typing a "
                         "new amount. Detail also in the slot's Issues column.)")
            for (row_num, player, city, rx, ry), items in grouped.items():
                lines.append(f"\nRow {row_num} — {player}/{city} [{rx}:{ry}]:")
                for c in items:
                    lines.append(
                        f"  • {c['resource']} (from {c['src']}): {c['reason']}"
                    )
            detail = "\n".join(lines)
            print()
            print(detail)
            if should_notify(notif_config, "error"):
                sendToBot(session, detail)

        # Persist Shipped + Issues + zeroed cells. Without this write,
        # the per-resource cancellations applied above would be lost
        # if the next cycle started reading from a stale CSV.
        try:
            write_csv_atomic(csv_path, fieldnames, rows)
        except Exception as we:
            print(f"    WARNING: end-of-cycle CSV write failed: {we}")

        # Schedule next cycle
        next_run = datetime.datetime.now() + datetime.timedelta(
            hours=interval_hours
        )
        session.setStatus(
            f"[WAITING] Mass Dist | Next: "
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

    set_child_mode(session)
    event.set()

    dest_names = ", ".join(d["name"] for d in destinations)
    info = (
        f"\nKeep Topped Up: {dest_names} "
        f"(every {interval_hours}h)\n"
    )
    setInfoSignal(session, info)
    try:
        do_it_top_up(session, destinations, dest_configs,
                     source_city_ids, source_reserves,
                     useFreighters, interval_hours, notif_config, log_path)
    except Exception:
        sendToBot(session, f"Error in:\n{info}\nCause:\n{traceback.format_exc()}")
    finally:
        session.logout()


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
