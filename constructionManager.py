#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""Construction Manager — CSV-backed, multi-city construction queue.

See `construction/construction module plan.txt` for the design.
"""

import csv
import glob
import importlib.util
import json
import math
import os
import re
import sys
import threading
import time
import traceback
from decimal import Decimal

from ikabot import config
from ikabot.config import (
    actionRequest,
    city_url,
    island_url,
    materials_names,
)
from ikabot.helpers.botComm import sendToBot, sendToBotDebug
from ikabot.helpers.getJson import getCity, getIsland
from ikabot.helpers.gui import banner, bcolors, enter
from ikabot.helpers.pedirInfo import chooseCity, getIdsOfCities, read
from ikabot.helpers.process import set_child_mode
from ikabot.helpers.signals import setInfoSignal
from ikabot.helpers.varios import addThousandSeparator, getDateTime, wait

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1

COLUMNS = [
    "queue_id",
    "city_id",
    "city_name",
    "slot_position",
    "action",
    "building",
    "building_id",
    "target_level",
    "wood",
    "wine",
    "marble",
    "crystal",
    "sulphur",
    "transport_mode",
    "auto_transport_enabled",
    "status",
    "expected_finish",
    "added_at",
    "notes",
    "schema_version",
]

INT_COLS = {
    "queue_id", "city_id", "slot_position", "target_level",
    "wood", "wine", "marble", "crystal", "sulphur",
    "added_at", "schema_version",
}
INT_OR_BLANK_COLS = {"building_id", "expected_finish"}
RESOURCE_COLS = ("wood", "wine", "marble", "crystal", "sulphur")

VALID_TRANSPORT_MODES = ("jit", "bulk", "none")
VALID_STATUSES = ("pending", "shipping", "running", "skipped")

TICK_BUDGET_SECONDS = 60
ETA_FUDGE_SECONDS = 15
SHIP_RETRY_SECONDS = 60
SHIP_TIMEOUT_SECONDS = 2 * 3600
SHORTAGE_COOLDOWN_SECONDS = 15 * 60
WORKER_LOCK_STALE_SECONDS = 600
ADD_SESSION_SLOT_CAP = 10
HARD_LEVEL_CAP = 150

# ---------------------------------------------------------------------------
# Path helpers — every persistent file is per (server, username)
# ---------------------------------------------------------------------------

def _safe(value):
    return str(value).replace("/", "_").replace("\\", "_")


def _account_suffix(session):
    return f"{_safe(session.servidor)}_{_safe(session.username)}"


def csv_path(session):
    return os.path.join(
        os.path.expanduser("~"),
        f".ikabot_construction_{_account_suffix(session)}.csv",
    )


def schema_sidecar_path(session):
    return os.path.join(
        os.path.expanduser("~"),
        f".ikabot_construction_{_account_suffix(session)}.schema",
    )


def csv_lock_path(session):
    return os.path.join(
        os.path.expanduser("~"),
        f".ikabot_construction_{_account_suffix(session)}.lock",
    )


def worker_lock_path(session):
    return os.path.join(
        os.path.expanduser("~"),
        f".ikabot_construction_worker_{_account_suffix(session)}.lock",
    )


def stop_flag_path(session):
    return os.path.join(
        os.path.expanduser("~"),
        f".ikabot_construction_stop_{_account_suffix(session)}",
    )


# ---------------------------------------------------------------------------
# Schema sidecar — fail loudly on mismatch instead of silently corrupting
# ---------------------------------------------------------------------------

def enforce_schema_or_abort(session):
    sidecar = schema_sidecar_path(session)
    if not os.path.exists(csv_path(session)) and not os.path.exists(sidecar):
        # First run: create sidecar.
        with open(sidecar, "w", encoding="utf-8") as f:
            json.dump({"version": SCHEMA_VERSION, "columns": COLUMNS}, f)
        return True
    if not os.path.exists(sidecar):
        # CSV exists but no sidecar — assume current schema and create one.
        with open(sidecar, "w", encoding="utf-8") as f:
            json.dump({"version": SCHEMA_VERSION, "columns": COLUMNS}, f)
        return True
    try:
        with open(sidecar, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        print(f"{bcolors.RED}Cannot read schema sidecar {sidecar}.{bcolors.ENDC}")
        return False
    on_disk = int(data.get("version", -1))
    if on_disk != SCHEMA_VERSION:
        print(f"{bcolors.RED}CSV schema version mismatch: file={on_disk}, "
              f"module={SCHEMA_VERSION}.{bcolors.ENDC}")
        print(f"  CSV : {csv_path(session)}")
        print(f"  Side: {sidecar}")
        print("  Move/rename both files to start fresh, then reopen this menu.")
        return False
    return True


# ---------------------------------------------------------------------------
# Lock helpers — single-writer file locks via O_CREAT|O_EXCL, mirroring the
# pattern in resourceTransportManager_v7.0.py:194 (acquire_shipping_lock).
# ---------------------------------------------------------------------------

def _lock_acquire(lock_file, timeout, stale_after):
    start = time.time()
    while time.time() - start < timeout:
        try:
            fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                payload = json.dumps({
                    "pid": os.getpid(),
                    "timestamp": time.time(),
                })
                os.write(fd, payload.encode())
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            try:
                with open(lock_file, "r") as f:
                    data = json.load(f)
                if time.time() - data.get("timestamp", 0) > stale_after:
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
        time.sleep(0.5)
    return False


def _lock_release(lock_file):
    try:
        if not os.path.exists(lock_file):
            return
        try:
            with open(lock_file, "r") as f:
                data = json.load(f)
            if data.get("pid") != os.getpid():
                return
        except Exception:
            pass
        try:
            os.remove(lock_file)
        except Exception:
            pass
    except Exception:
        pass


class _csv_lock:
    """Brief-hold cross-process lock for the CSV file."""

    def __init__(self, session):
        self.path = csv_lock_path(session)

    def __enter__(self):
        if not _lock_acquire(self.path, timeout=30, stale_after=60):
            raise RuntimeError(f"Could not acquire CSV lock at {self.path}")
        return self

    def __exit__(self, exc_type, exc, tb):
        _lock_release(self.path)


# ---------------------------------------------------------------------------
# CSV layer — every read/write is locked + atomic
# ---------------------------------------------------------------------------

def _coerce_row_in(raw):
    """Convert string fields from disk into native types."""
    row = dict(raw)
    for col in INT_COLS:
        v = row.get(col, "")
        try:
            row[col] = int(v) if str(v) != "" else 0
        except (TypeError, ValueError):
            row[col] = 0
    for col in INT_OR_BLANK_COLS:
        v = row.get(col, "")
        if v in ("", None):
            row[col] = ""
        else:
            try:
                row[col] = int(v)
            except (TypeError, ValueError):
                row[col] = ""
    if row.get("auto_transport_enabled", "") not in ("yes", "no"):
        row["auto_transport_enabled"] = (
            "no" if row.get("transport_mode") == "none" else "yes"
        )
    if row.get("status", "") not in VALID_STATUSES:
        row["status"] = "pending"
    if row.get("transport_mode", "") not in VALID_TRANSPORT_MODES:
        row["transport_mode"] = "jit"
    return row


def _coerce_row_out(row):
    """Convert native types back to strings before writing to disk."""
    out = {}
    for col in COLUMNS:
        v = row.get(col, "")
        if v is None:
            out[col] = ""
        elif isinstance(v, bool):
            out[col] = "yes" if v else "no"
        else:
            out[col] = str(v)
    return out


def csv_load(session):
    if not os.path.exists(csv_path(session)):
        return []
    with _csv_lock(session):
        with open(csv_path(session), "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return [_coerce_row_in(r) for r in reader]


def csv_save_all(session, rows):
    path = csv_path(session)
    tmp = path + ".tmp"
    with _csv_lock(session):
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader()
            for r in rows:
                w.writerow(_coerce_row_out(r))
        os.replace(tmp, path)


def csv_append(session, row):
    rows = csv_load(session)
    rows.append(row)
    csv_save_all(session, rows)


def csv_delete(session, queue_id):
    rows = csv_load(session)
    rows = [r for r in rows if r["queue_id"] != int(queue_id)]
    csv_save_all(session, rows)


def csv_update(session, queue_id, **fields):
    rows = csv_load(session)
    qid = int(queue_id)
    for r in rows:
        if r["queue_id"] == qid:
            for k, v in fields.items():
                r[k] = v
            if "transport_mode" in fields:
                r["auto_transport_enabled"] = (
                    "no" if fields["transport_mode"] == "none" else "yes"
                )
            break
    csv_save_all(session, rows)


def csv_next_queue_id(session):
    rows = csv_load(session)
    if not rows:
        return 1
    return max(r["queue_id"] for r in rows) + 1


def csv_pending_city_ids(session):
    rows = csv_load(session)
    return sorted({r["city_id"] for r in rows if r["status"] != "skipped"})


def csv_count_pending(session):
    return sum(1 for r in csv_load(session) if r["status"] == "pending")


def csv_count_cities_with_work(session):
    return len({r["city_id"] for r in csv_load(session)
                if r["status"] in ("pending", "shipping", "running")})


# ---------------------------------------------------------------------------
# Dynamic loader for resourceTransportManager*.py
# ---------------------------------------------------------------------------

_rtm_module = None


def _parse_version_suffix(filename):
    m = re.search(r"resourceTransportManager_v(\d+)\.(\d+)\.py$", filename)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)))


def load_rtm():
    """Locate and import a resourceTransportManager*.py sibling.

    Preference: unversioned > highest numeric (major, minor) > newest mtime.
    """
    global _rtm_module
    if _rtm_module is not None:
        return _rtm_module
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = glob.glob(os.path.join(here, "resourceTransportManager*.py"))
    if not candidates:
        raise FileNotFoundError(
            "Could not find resourceTransportManager*.py next to "
            f"{__file__}. Place a copy of resourceTransportManager.py "
            "(or resourceTransportManager_vX.Y.py) alongside this file."
        )
    unversioned = [
        c for c in candidates
        if os.path.basename(c) == "resourceTransportManager.py"
    ]
    if unversioned:
        target = unversioned[0]
    else:
        versioned = [(c, _parse_version_suffix(c)) for c in candidates]
        versioned = [(c, v) for c, v in versioned if v is not None]
        if versioned:
            versioned.sort(key=lambda cv: cv[1])
            target = versioned[-1][0]
        else:
            candidates.sort(key=os.path.getmtime)
            target = candidates[-1]
    spec = importlib.util.spec_from_file_location(
        "_construction_rtm", target,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _rtm_module = mod
    return mod


# === END CHUNK 1 — chunks 2 and 3 inserted below this line ===
