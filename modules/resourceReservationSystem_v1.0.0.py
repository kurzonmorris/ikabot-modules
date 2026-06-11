#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""Resource Reservation System — cross-module resource reservation tracker.

Data layer only: reserve / release / query.
Fulfillment logic, retry scheduling, and shipping all live in calling modules.

Public API
----------
reserve(session, city_id, city_name, resource_index, amount,
        module_name, reason, release_at)        -> int (reservation_id)
update_reservation(session, reservation_id,
        module_name, release_at)                -> True | False | None
release(session, reservation_id, module_name)   -> True | False | None
release_all_for_module(session, module_name)    -> int (count)
release_all_for_city(session, city_id,
        module_name="__admin__")                -> int (count)
get_available(session, city_id, resource_index,
        actual_amount)                          -> int
get_total_reserved(session, city_id,
        resource_index)                         -> int
get_reservation_snapshot(session, city_id,
        resource_index, actual_amount)          -> (available, reserved)
get_summary(session)                            -> dict
get_reservations(session, city_id=None,
        module_name=None, active_only=True)     -> list[dict]

exclude_city(session, city_id, city_name="")
include_city(session, city_id)
get_excluded_cities(session)                    -> list[dict]
is_city_excluded(session, city_id)              -> bool

get_config(session)                             -> dict
update_config(session, **kwargs)

Return-value convention for release/update
------------------------------------------
  True  — reservation found, owned, and acted upon
  False — reservation not found (or already inactive)
  None  — reservation found but caller does not own it (permission denied)

Files (all per server+username, never shared across accounts)
-------------------------------------------------------------
~/.ikabot_reservations_<server>_<username>.csv
~/.ikabot_reservations_<server>_<username>.lock
~/.ikabot_reservations_config_<server>_<username>.json
"""

import copy
import csv
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from datetime import datetime

from ikabot import config
from ikabot.config import materials_names
from ikabot.helpers.gui import banner, bcolors, enter
from ikabot.helpers.pedirInfo import read

# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1
MENU_LABEL = "Resource Reservation System"
MENU_ORDER = 45

COLUMNS = [
    "reservation_id",
    "city_id",
    "city_name",
    "resource_index",
    "reserved_amount",
    "module_name",
    "reason",
    "created_at",
    "release_at",
    "hard_expires_at",
    "status",
    "schema_version",
]

INT_COLS = {
    "reservation_id", "city_id", "resource_index", "reserved_amount",
    "created_at", "release_at", "hard_expires_at", "schema_version",
}

VALID_STATUSES = ("active", "released", "expired")
VALID_RESOURCE_INDICES = range(5)   # 0=Wood 1=Wine 2=Marble 3=Crystal 4=Sulphur

# hard_expires_at = release_at + this buffer (crash-protection safety net)
HARD_EXPIRY_BUFFER_SECONDS = 24 * 3600

# non-active rows are pruned this long after their hard_expires_at
PRUNE_AFTER_EXPIRY_SECONDS = 48 * 3600

# lock file is considered stale after this many seconds (brief hold)
LOCK_STALE_SECONDS = 60

# config defaults
DEFAULT_MIN_SHIP_CAPACITY = 500
DEFAULT_RETRY_HOURS = 6

# History display cap in the view (shown with a note if truncated)
HISTORY_DISPLAY_CAP = 15

# ---------------------------------------------------------------------------
# File-path helpers — every persistent file is keyed by (server, username)
# ---------------------------------------------------------------------------

def _safe(value):
    return re.sub(r'[^\w.-]', '_', str(value))


def _suffix(session):
    return f"{_safe(session.servidor)}_{_safe(session.username)}"


def _csv_path(session):
    return os.path.join(
        os.path.expanduser("~"),
        f".ikabot_reservations_{_suffix(session)}.csv",
    )


def _lock_path(session):
    return os.path.join(
        os.path.expanduser("~"),
        f".ikabot_reservations_{_suffix(session)}.lock",
    )


def _config_path(session):
    return os.path.join(
        os.path.expanduser("~"),
        f".ikabot_reservations_config_{_suffix(session)}.json",
    )


# ---------------------------------------------------------------------------
# Lock — atomic O_CREAT|O_EXCL with JSON timestamp + stale-lock detection.
# Mirrors the pattern used in constructionManager.py:_lock_acquire.
# ---------------------------------------------------------------------------

@contextmanager
def _reservation_lock(session, timeout=30):
    """Acquire the per-account reservation lock for the duration of the block."""
    lock_file = _lock_path(session)
    start = time.time()
    acquired = False

    while time.time() - start < timeout:
        try:
            fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(
                    fd,
                    json.dumps({"pid": os.getpid(), "timestamp": time.time()}).encode(),
                )
            finally:
                os.close(fd)
            acquired = True
            break
        except FileExistsError:
            try:
                with open(lock_file, "r") as f:
                    data = json.load(f)
                if time.time() - data.get("timestamp", 0) > LOCK_STALE_SECONDS:
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
        time.sleep(0.2)

    if not acquired:
        raise RuntimeError(
            f"Could not acquire reservation lock after {timeout}s: {lock_file}"
        )

    try:
        yield
    finally:
        try:
            with open(lock_file, "r") as f:
                data = json.load(f)
            if data.get("pid") == os.getpid():
                os.remove(lock_file)
        except Exception:
            try:
                os.remove(lock_file)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Row coercion
# ---------------------------------------------------------------------------

def _coerce_in(raw):
    """Convert raw CSV strings to native Python types on load."""
    row = dict(raw)
    for col in INT_COLS:
        v = row.get(col, "")
        try:
            row[col] = int(v) if str(v).strip() != "" else 0
        except (TypeError, ValueError):
            row[col] = 0
    # Default unknown statuses to "expired" — never silently reactivate a row
    if row.get("status", "") not in VALID_STATUSES:
        row["status"] = "expired"
    return row


def _coerce_out(row):
    """Convert native types back to strings for CSV writing."""
    out = {}
    for col in COLUMNS:
        v = row.get(col, "")
        out[col] = "" if v is None else str(v)
    return out


# ---------------------------------------------------------------------------
# CSV I/O — called only from within _reservation_lock
# ---------------------------------------------------------------------------

def _read_csv(session):
    try:
        with open(_csv_path(session), "r", newline="", encoding="utf-8") as f:
            return [_coerce_in(r) for r in csv.DictReader(f)]
    except FileNotFoundError:
        return []


def _write_csv(session, rows):
    path = _csv_path(session)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(_coerce_out(r))
    os.replace(tmp, path)  # atomic rename — crash-safe


def _clean_rows(rows):
    """Lazily expire overdue active rows; prune old non-active rows.

    Returns (cleaned_list, changed_bool).  Never touches disk directly.
    """
    now = time.time()
    changed = False
    cleaned = []
    for row in rows:
        if row["status"] == "active" and row["hard_expires_at"] and row["hard_expires_at"] < now:
            row["status"] = "expired"
            changed = True
        if row["status"] != "active":
            cutoff = (row["hard_expires_at"] or 0) + PRUNE_AFTER_EXPIRY_SECONDS
            if cutoff < now:
                changed = True
                continue  # drop from list
        cleaned.append(row)
    return cleaned, changed


def _next_id(rows):
    return max((r["reservation_id"] for r in rows), default=0) + 1


# ---------------------------------------------------------------------------
# Core load / modify primitives
# ---------------------------------------------------------------------------

def _load_rows(session):
    """Load CSV, run lazy cleanup, persist only if something changed, return rows."""
    with _reservation_lock(session):
        rows, changed = _clean_rows(_read_csv(session))
        if changed:
            _write_csv(session, rows)
        return rows


def _modify_rows(session, fn):
    """Load, clean, apply fn(rows) in-place, save only if changed.

    fn must return True if it modified rows, False (or None) otherwise.
    The write is also triggered if lazy cleanup marked any rows as expired.
    """
    with _reservation_lock(session):
        rows, cleaning_changed = _clean_rows(_read_csv(session))
        fn_changed = fn(rows)
        if cleaning_changed or fn_changed:
            _write_csv(session, rows)


# ---------------------------------------------------------------------------
# Config (excluded cities + per-account settings)
# ---------------------------------------------------------------------------

_CONFIG_DEFAULTS = {
    "excluded_cities": [],
    "min_ship_capacity": DEFAULT_MIN_SHIP_CAPACITY,
    "retry_hours": DEFAULT_RETRY_HOURS,
    "schema_version": SCHEMA_VERSION,
}


def _read_config(session):
    try:
        with open(_config_path(session), "r", encoding="utf-8") as f:
            data = json.load(f)
        cfg = copy.deepcopy(_CONFIG_DEFAULTS)  # deep copy — excluded_cities is a list
        cfg.update(data)
        return cfg
    except (FileNotFoundError, json.JSONDecodeError, IOError):
        return copy.deepcopy(_CONFIG_DEFAULTS)


def _write_config(session, cfg):
    path = _config_path(session)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, path)


def _modify_config(session, fn):
    with _reservation_lock(session):
        cfg = _read_config(session)
        fn(cfg)
        _write_config(session, cfg)


# ===========================================================================
# PUBLIC API
# ===========================================================================

def reserve(session, city_id, city_name, resource_index, amount, module_name, reason, release_at):
    """Create a new reservation and return its reservation_id (int).

    Parameters
    ----------
    city_id : int
        Game city ID.
    city_name : str
        Human-readable city name (stored for display only).
    resource_index : int
        0=Wood 1=Wine 2=Marble 3=Crystal 4=Sulphur.
    amount : int
        Units being reserved.  Must be > 0.
    module_name : str
        Identifier of the calling module (used for ownership checks on release).
    reason : str
        Human-readable description (e.g. "Hoplite queue — 200 units").
    release_at : int|float
        Unix timestamp when the module expects to be done with these resources.
        hard_expires_at is automatically set to release_at + 24 h.
        A warning is printed if release_at is already in the past.
    """
    if int(amount) <= 0:
        raise ValueError(f"reserved amount must be positive, got {amount}")
    if int(resource_index) not in VALID_RESOURCE_INDICES:
        raise ValueError(
            f"resource_index must be 0–4 (Wood/Wine/Marble/Crystal/Sulphur), got {resource_index}"
        )
    if int(release_at) < time.time():
        print(
            f"{bcolors.WARNING}Warning: release_at ({_fmt_ts(release_at)}) is in the past. "
            f"The reservation will hard-expire ~24 h from now.{bcolors.ENDC}"
        )

    rid = [None]

    def _add(rows):
        rid[0] = _next_id(rows)
        rows.append({
            "reservation_id": rid[0],
            "city_id": int(city_id),
            "city_name": str(city_name),
            "resource_index": int(resource_index),
            "reserved_amount": int(amount),
            "module_name": str(module_name),
            "reason": str(reason),
            "created_at": int(time.time()),
            "release_at": int(release_at),
            "hard_expires_at": int(release_at) + HARD_EXPIRY_BUFFER_SECONDS,
            "status": "active",
            "schema_version": SCHEMA_VERSION,
        })
        return True  # always modifies rows

    _modify_rows(session, _add)
    return rid[0]


def update_reservation(session, reservation_id, module_name, release_at):
    """Extend or modify the release_at of an existing active reservation.

    Use this when an operation is taking longer than originally estimated —
    extend the reservation rather than release-and-recreate (which opens a
    gap where another module could claim the resources).

    Only the owning module may update a reservation (or ``"__admin__"``).

    Parameters
    ----------
    release_at : int|float
        New unix timestamp for when the module expects to be done.

    Returns
    -------
    True   reservation found, owned, and updated
    False  reservation not found (or already inactive)
    None   reservation found but caller does not own it
    """
    rid = int(reservation_id)
    result = [False]

    if int(release_at) < time.time():
        print(
            f"{bcolors.WARNING}Warning: release_at ({_fmt_ts(release_at)}) is in the past.{bcolors.ENDC}"
        )

    def _update(rows):
        for row in rows:
            if row["reservation_id"] == rid and row["status"] == "active":
                if row["module_name"] == str(module_name) or str(module_name) == "__admin__":
                    row["release_at"] = int(release_at)
                    row["hard_expires_at"] = int(release_at) + HARD_EXPIRY_BUFFER_SECONDS
                    result[0] = True
                    return True   # rows changed
                result[0] = None  # found but not authorised — no write needed
                return False
        return False  # not found — no write needed

    _modify_rows(session, _update)
    return result[0]


def release(session, reservation_id, module_name):
    """Mark a reservation as released.

    Only the module that created the reservation may release it, or the
    special sentinel ``"__admin__"`` (used by the interactive menu).

    Returns
    -------
    True   reservation found, owned, and released
    False  reservation not found (or already inactive)
    None   reservation found but caller does not own it
    """
    rid = int(reservation_id)
    result = [False]

    def _release(rows):
        for row in rows:
            if row["reservation_id"] == rid and row["status"] == "active":
                if row["module_name"] == str(module_name) or str(module_name) == "__admin__":
                    row["status"] = "released"
                    result[0] = True
                    return True   # rows changed
                result[0] = None  # found but not authorised — no write needed
                return False
        return False  # not found — no write needed

    _modify_rows(session, _release)
    return result[0]


def release_all_for_module(session, module_name):
    """Release every active reservation owned by *module_name*.

    Returns the number of reservations released.
    """
    count = [0]

    def _release_all(rows):
        for row in rows:
            if row["module_name"] == str(module_name) and row["status"] == "active":
                row["status"] = "released"
                count[0] += 1
        return count[0] > 0  # True if anything changed

    _modify_rows(session, _release_all)
    return count[0]


def release_all_for_city(session, city_id, module_name="__admin__"):
    """Atomically release all active reservations for a city.

    When called with ``"__admin__"`` (the default) every reservation in the
    city is released regardless of owning module — useful for city abandonment
    or server migration.  When called with a specific module_name only that
    module's reservations in the city are released.

    Returns the number of reservations released.
    """
    cid = int(city_id)
    count = [0]

    def _release_city(rows):
        for row in rows:
            if row["city_id"] == cid and row["status"] == "active":
                if row["module_name"] == str(module_name) or str(module_name) == "__admin__":
                    row["status"] = "released"
                    count[0] += 1
        return count[0] > 0

    _modify_rows(session, _release_city)
    return count[0]


def get_available(session, city_id, resource_index, actual_amount):
    """Return the free (unreserved) amount of a resource in a city.

    Parameters
    ----------
    actual_amount : int
        The live resource count from the game API for this city+resource.

    Returns the larger of 0 and (actual_amount − total active reservations).
    """
    _, reserved = get_reservation_snapshot(session, city_id, resource_index, actual_amount)
    return max(0, int(actual_amount) - reserved)


def get_total_reserved(session, city_id, resource_index):
    """Return the total amount currently reserved for a city+resource pair."""
    rows = _load_rows(session)
    cid = int(city_id)
    ridx = int(resource_index)
    return sum(
        r["reserved_amount"]
        for r in rows
        if r["city_id"] == cid
        and r["resource_index"] == ridx
        and r["status"] == "active"
    )


def get_reservation_snapshot(session, city_id, resource_index, actual_amount):
    """Return (available, reserved) for a city+resource in a single lock acquisition.

    Prefer this over separate get_available() + get_total_reserved() calls when
    you need both values, as it halves the number of lock acquisitions.

    Parameters
    ----------
    actual_amount : int
        Live resource count from the game API.

    Returns
    -------
    (available, reserved) : tuple[int, int]
    """
    rows = _load_rows(session)
    cid = int(city_id)
    ridx = int(resource_index)
    reserved = sum(
        r["reserved_amount"]
        for r in rows
        if r["city_id"] == cid
        and r["resource_index"] == ridx
        and r["status"] == "active"
    )
    available = max(0, int(actual_amount) - reserved)
    return available, reserved


def get_summary(session):
    """Return a nested dict of all active reservations in a single lock acquisition.

    Returns
    -------
    dict : ``{city_id: {resource_index: total_reserved_amount}}``

    Calling modules that need to scan across many cities should use this
    instead of calling get_available() per city, to avoid repeated lock
    acquisitions and CSV reads.

    Example
    -------
    ::

        summary = get_summary(session)
        for city in my_cities:
            sulphur_reserved = summary.get(city["id"], {}).get(4, 0)
            free = city["availableResources"][4] - sulphur_reserved
    """
    rows = _load_rows(session)
    summary = {}
    for r in rows:
        if r["status"] != "active":
            continue
        cid = r["city_id"]
        ridx = r["resource_index"]
        if cid not in summary:
            summary[cid] = {}
        summary[cid][ridx] = summary[cid].get(ridx, 0) + r["reserved_amount"]
    return summary


def get_reservations(session, city_id=None, module_name=None, active_only=True):
    """Return reservations, optionally filtered.

    Parameters
    ----------
    city_id : int|None
        Restrict to a specific city.
    module_name : str|None
        Restrict to a specific module.
    active_only : bool
        When True (default) only return rows with status=="active".
        Pass False to include recently released/expired rows still on disk.
    """
    rows = _load_rows(session)
    if active_only:
        rows = [r for r in rows if r["status"] == "active"]
    if city_id is not None:
        rows = [r for r in rows if r["city_id"] == int(city_id)]
    if module_name is not None:
        rows = [r for r in rows if r["module_name"] == str(module_name)]
    return rows


# ---------------------------------------------------------------------------
# City exclusion
# ---------------------------------------------------------------------------

def exclude_city(session, city_id, city_name=""):
    """Add *city_id* to the exclusion list (all resources treated as fully reserved)."""
    def _exclude(cfg):
        cid = int(city_id)
        if any(e["city_id"] == cid for e in cfg["excluded_cities"]):
            return
        cfg["excluded_cities"].append({"city_id": cid, "city_name": str(city_name)})
    _modify_config(session, _exclude)


def include_city(session, city_id):
    """Remove *city_id* from the exclusion list."""
    cid = int(city_id)
    def _include(cfg):
        cfg["excluded_cities"] = [e for e in cfg["excluded_cities"] if e["city_id"] != cid]
    _modify_config(session, _include)


def get_excluded_cities(session):
    """Return the list of excluded city dicts: [{city_id, city_name}, ...]."""
    return _read_config(session).get("excluded_cities", [])


def is_city_excluded(session, city_id):
    """Return True if *city_id* is on the exclusion list."""
    cid = int(city_id)
    return any(e["city_id"] == cid for e in get_excluded_cities(session))


# ---------------------------------------------------------------------------
# Config access
# ---------------------------------------------------------------------------

def get_config(session):
    """Return the full config dict for this account."""
    return _read_config(session)


def update_config(session, **kwargs):
    """Update one or more config keys.

    Accepted keys
    -------------
    min_ship_capacity : int
        Minimum free resources before a city is worth shipping from.
    retry_hours : int | float
        Advisory retry interval exposed to calling modules via get_config().
    """
    allowed = {"min_ship_capacity", "retry_hours"}

    def _update(cfg):
        for k, v in kwargs.items():
            if k in allowed:
                cfg[k] = v

    _modify_config(session, _update)


# ===========================================================================
# INTERACTIVE MENU  (ikabot plugin entry point)
# ===========================================================================

def _fmt_ts(ts):
    if not ts:
        return "—"
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)


def _resource_name(idx):
    try:
        return materials_names[int(idx)]
    except (IndexError, TypeError, ValueError):
        return f"resource[{idx}]"


def _view_reservations(session):
    banner()
    rows = get_reservations(session, active_only=False)
    if not rows:
        print("No reservations on record.")
        enter()
        return

    active = [r for r in rows if r["status"] == "active"]
    other = [r for r in rows if r["status"] != "active"]

    if active:
        header = (
            f"{'ID':>4}  {'City':<20} {'Resource':<10} "
            f"{'Reserved':>12}  {'Module':<22}  {'Release at':<17}  Reason"
        )
        print(bcolors.BOLD + header + bcolors.ENDC)
        print("-" * 105)
        for r in sorted(active, key=lambda x: (x["city_name"], x["resource_index"])):
            print(
                f"{r['reservation_id']:>4}  "
                f"{r['city_name']:<20} "
                f"{_resource_name(r['resource_index']):<10} "
                f"{r['reserved_amount']:>12,}  "
                f"{r['module_name']:<22}  "
                f"{_fmt_ts(r['release_at']):<17}  "
                f"{r['reason']}"
            )
    else:
        print("No active reservations.")

    if other:
        capped = sorted(other, key=lambda x: x["hard_expires_at"], reverse=True)
        truncated = len(capped) > HISTORY_DISPLAY_CAP
        capped = capped[:HISTORY_DISPLAY_CAP]
        note = f" (showing {HISTORY_DISPLAY_CAP} most recent)" if truncated else ""
        print(
            f"\n{bcolors.BLACK}"
            f"{'ID':>4}  {'City':<20} {'Resource':<10} "
            f"{'Reserved':>12}  {'Module':<22}  {'Status':<10}  Hard expires"
            f"{note}"
            + bcolors.ENDC
        )
        print(bcolors.BLACK + "-" * 105 + bcolors.ENDC)
        for r in capped:
            print(
                bcolors.BLACK
                + f"{r['reservation_id']:>4}  "
                  f"{r['city_name']:<20} "
                  f"{_resource_name(r['resource_index']):<10} "
                  f"{r['reserved_amount']:>12,}  "
                  f"{r['module_name']:<22}  "
                  f"{r['status']:<10}  "
                  f"{_fmt_ts(r['hard_expires_at'])}"
                + bcolors.ENDC
            )
    print()
    enter()


def _release_reservation_menu(session):
    banner()
    rows = get_reservations(session, active_only=True)
    if not rows:
        print("No active reservations to release.")
        enter()
        return

    print(
        f"{'ID':>4}  {'City':<20} {'Resource':<10} "
        f"{'Reserved':>12}  {'Module':<22}  Reason"
    )
    print("-" * 100)
    for r in sorted(rows, key=lambda x: x["reservation_id"]):
        print(
            f"{r['reservation_id']:>4}  "
            f"{r['city_name']:<20} "
            f"{_resource_name(r['resource_index']):<10} "
            f"{r['reserved_amount']:>12,}  "
            f"{r['module_name']:<22}  "
            f"{r['reason']}"
        )
    print()
    rid = read(msg="Reservation ID to release (0 to cancel): ", digit=True)
    if not rid or int(rid) == 0:
        return
    result = release(session, int(rid), "__admin__")
    if result is True:
        print(f"{bcolors.GREEN}Reservation {rid} released.{bcolors.ENDC}")
    elif result is None:
        print(f"{bcolors.RED}Permission denied.{bcolors.ENDC}")
    else:
        print(f"{bcolors.RED}Reservation {rid} not found or already inactive.{bcolors.ENDC}")
    enter()


def _release_module_menu(session):
    banner()
    rows = get_reservations(session, active_only=True)
    if not rows:
        print("No active reservations.")
        enter()
        return

    modules = sorted(set(r["module_name"] for r in rows))
    print("Release all reservations for which module?\n")
    for i, m in enumerate(modules, 1):
        count = sum(1 for r in rows if r["module_name"] == m)
        print(f"  {i}) {m}  ({count} reservation{'s' if count != 1 else ''})")
    print()
    choice = read(msg="Choice (0 to cancel): ", digit=True)
    if not choice or int(choice) == 0:
        return
    idx = int(choice) - 1
    if idx < 0 or idx >= len(modules):
        return
    module = modules[idx]
    n = release_all_for_module(session, module)
    print(f"{bcolors.GREEN}Released {n} reservation(s) for '{module}'.{bcolors.ENDC}")
    enter()


def _release_city_menu(session):
    banner()
    rows = get_reservations(session, active_only=True)
    if not rows:
        print("No active reservations.")
        enter()
        return

    cities = {}
    for r in rows:
        cid = r["city_id"]
        if cid not in cities:
            cities[cid] = {"name": r["city_name"], "count": 0}
        cities[cid]["count"] += 1

    city_list = sorted(cities.items(), key=lambda x: x[1]["name"])
    print("Release all reservations for which city?\n")
    for i, (cid, info) in enumerate(city_list, 1):
        print(f"  {i}) {info['name']}  (ID: {cid}, {info['count']} reservation{'s' if info['count'] != 1 else ''})")
    print()
    choice = read(msg="Choice (0 to cancel): ", digit=True)
    if not choice or int(choice) == 0:
        return
    idx = int(choice) - 1
    if idx < 0 or idx >= len(city_list):
        return
    cid, info = city_list[idx]
    n = release_all_for_city(session, cid, module_name="__admin__")
    print(f"{bcolors.GREEN}Released {n} reservation(s) for {info['name']}.{bcolors.ENDC}")
    enter()


def _excluded_cities_menu(session):
    while True:
        banner()
        excluded = get_excluded_cities(session)
        print(f"{bcolors.BOLD}Excluded Cities{bcolors.ENDC}")
        print("Resources in these cities are treated as fully reserved by all queries.\n")
        if excluded:
            for i, e in enumerate(excluded, 1):
                print(f"  {i}) {e['city_name'] or '(unnamed)'}  (ID: {e['city_id']})")
        else:
            print("  (none)")
        print()
        print("  a) Add exclusion")
        print("  r) Remove exclusion")
        print("  0) Back")
        print()
        choice = read(msg="Choice: ").strip().lower()

        if choice == "0":
            break
        elif choice == "a":
            banner()
            city_id_raw = read(msg="City ID to exclude: ", digit=True)
            if not city_id_raw:
                continue
            city_name = read(msg="City name (optional, for display): ").strip()
            exclude_city(session, int(city_id_raw), city_name)
            print(f"{bcolors.GREEN}City {city_id_raw} added to exclusion list.{bcolors.ENDC}")
            enter()
        elif choice == "r":
            if not excluded:
                continue
            banner()
            for i, e in enumerate(excluded, 1):
                print(f"  {i}) {e['city_name'] or '(unnamed)'}  (ID: {e['city_id']})")
            print()
            choice2 = read(msg="Remove entry number (0 to cancel): ", digit=True)
            if not choice2 or int(choice2) == 0:
                continue
            idx = int(choice2) - 1
            if 0 <= idx < len(excluded):
                include_city(session, excluded[idx]["city_id"])
                print(f"{bcolors.GREEN}Removed.{bcolors.ENDC}")
                enter()


def _settings_menu(session):
    while True:
        banner()
        cfg = get_config(session)
        print(f"{bcolors.BOLD}Reservation System Settings{bcolors.ENDC}\n")
        print(
            f"  1) Min ship capacity  "
            f"(skip city if free resources < this)  :  {cfg['min_ship_capacity']}"
        )
        print(
            f"  2) Retry wait hours   "
            f"(advisory — used by calling modules)   :  {cfg['retry_hours']}"
        )
        print(f"\n  0) Back\n")
        choice = read(msg="Choice: ", digit=True)
        if not choice or int(choice) == 0:
            break
        elif int(choice) == 1:
            val = read(
                msg=f"New min ship capacity [{cfg['min_ship_capacity']}]: ",
                digit=True,
            )
            if val:
                update_config(session, min_ship_capacity=int(val))
        elif int(choice) == 2:
            val = read(
                msg=f"New retry wait hours [{cfg['retry_hours']}] (decimals allowed, e.g. 0.5): ",
            )
            if val:
                try:
                    update_config(session, retry_hours=float(val))
                except ValueError:
                    print(f"{bcolors.RED}Invalid number.{bcolors.ENDC}")
                    enter()


def resourceReservationSystem(session, event, stdin_fd, predetermined_input):
    """ikabot plugin entry point — interactive management menu."""
    sys.stdin = os.fdopen(stdin_fd)
    config.predetermined_input = predetermined_input
    try:
        while True:
            banner()
            print(f"{bcolors.BOLD}Resource Reservation System{bcolors.ENDC}\n")
            active_rows = get_reservations(session, active_only=True)
            cfg = get_config(session)
            excl = get_excluded_cities(session)
            print(f"  Active reservations : {len(active_rows)}")
            print(f"  Excluded cities     : {len(excl)}")
            print(f"  Min ship capacity   : {cfg['min_ship_capacity']}")
            print(f"  Retry wait hours    : {cfg['retry_hours']}")
            print()
            print("  1) View reservations")
            print("  2) Manually release a reservation")
            print("  3) Release all reservations for a module")
            print("  4) Release all reservations for a city")
            print("  5) Excluded cities")
            print("  6) Settings")
            print("  0) Back")
            print()
            choice = read(msg="Choice: ", digit=True)
            if not choice or int(choice) == 0:
                break
            c = int(choice)
            if c == 1:
                _view_reservations(session)
            elif c == 2:
                _release_reservation_menu(session)
            elif c == 3:
                _release_module_menu(session)
            elif c == 4:
                _release_city_menu(session)
            elif c == 5:
                _excluded_cities_menu(session)
            elif c == 6:
                _settings_menu(session)
    finally:
        event.set()
