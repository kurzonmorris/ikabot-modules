#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""Construction Manager — CSV-backed, multi-city construction queue.

See `construction/construction module plan.txt` for the design.
"""

__version__ = "2.2.2"

import csv
import glob
import importlib.util
import itertools
import json
import math
import os
import pathlib
import queue
import random
import re
import sys
import threading
import time
import traceback
from decimal import Decimal

import psutil

from ikabot import config
from ikabot.config import (
    actionRequest,
    city_url,
    island_url,
    material_img_hash,
    materials_names,
)
from ikabot.helpers.botComm import checkTelegramData, sendToBot, sendToBotDebug
from ikabot.helpers.getJson import getCity, getIsland
from ikabot.helpers.gui import banner, bcolors, enter
from ikabot.helpers.pedirInfo import chooseCity, read
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
BUILD_POST_VERIFY_DELAY_SECONDS = 2
SUPPLIER_LIST_TTL_SECONDS = 120

# slug → ikipedia buildingId, captured from the live game's help grid
# (every entry uses helpId=1, so the detail URL is fully derivable from this).
# Keys are lowercased for case-insensitive lookup: city JSON uses camelCase
# ("townHall") while the construction-dialog scrape may yield lowercase.
BUILDING_HELP_IDS = {
    "townhall": 0,
    "port": 3,
    "academy": 4,
    "shipyard": 5,
    "barracks": 6,
    "warehouse": 7,
    "wall": 8,
    "tavern": 9,
    "museum": 10,
    "palace": 11,
    "embassy": 12,
    "branchoffice": 13,
    "workshop": 15,
    "safehouse": 16,
    "palacecolony": 17,
    "stonemason": 19,
    "glassblowing": 20,
    "winegrower": 21,
    "alchemist": 22,
    "carpentering": 23,
    "architect": 24,
    "optician": 25,
    "vineyard": 26,
    "fireworker": 27,
    "temple": 28,
    "dump": 29,
    "piratefortress": 30,
    "blackmarket": 31,
    "marinechartarchive": 32,
    "dockyard": 33,
    "shrineofolympus": 34,
    "chronosforge": 35,
}

# ---------------------------------------------------------------------------
# Path helpers — every persistent file is per (server, username)
# ---------------------------------------------------------------------------

def _safe(value):
    return re.sub(r'[^\w.-]', '_', str(value))


def _account_suffix(session):
    """Per-instance filename suffix: server + world + player.

    The world number must be part of this.  A player name is only unique
    within a world, so `en_Bob` collided when the same name existed on two
    worlds of the same community — both instances then shared one queue and
    one set of locks.  Mirrors ikabot's own log naming (server + mundo).
    """
    world = _safe(getattr(session, "mundo", "") or "")
    if not world:
        # No world on the session (shouldn't happen post-login): fall back to
        # the legacy suffix rather than inventing a new namespace.
        return _legacy_account_suffix(session)
    return f"{_safe(session.servidor)}{world}_{_safe(session.username)}"


def _legacy_account_suffix(session):
    """Pre-2.2.1 suffix, without the world. Only used for migration."""
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


def _legacy_path(session, template):
    return os.path.join(
        os.path.expanduser("~"),
        template.format(suffix=_legacy_account_suffix(session)),
    )


def migrate_legacy_account_files(session):
    """Rename pre-2.2.1 (world-less) queue files to the world-scoped names.

    Returns False if migration is unsafe — specifically when a worker started
    by an older build still holds the legacy worker lock, since moving the CSV
    out from under a running worker would break it.
    """
    if _account_suffix(session) == _legacy_account_suffix(session):
        return True

    moves = [
        (_legacy_path(session, ".ikabot_construction_{suffix}.csv"),
         csv_path(session)),
        (_legacy_path(session, ".ikabot_construction_{suffix}.schema"),
         schema_sidecar_path(session)),
    ]
    if not any(os.path.exists(old) for old, _ in moves):
        return True

    legacy_worker_lock = _legacy_path(
        session, ".ikabot_construction_worker_{suffix}.lock"
    )
    if _worker_lock_is_fresh(legacy_worker_lock):
        print(f"{bcolors.RED}A construction worker from an older version is "
              f"still running for this account.{bcolors.ENDC}")
        print("  Stop it first (press (o) in that instance), then reopen this "
              "menu to finish upgrading.")
        print(f"  Lock file: {legacy_worker_lock}")
        return False

    for old, new in moves:
        if os.path.exists(old) and not os.path.exists(new):
            try:
                os.replace(old, new)
                print(f"  Migrated {os.path.basename(old)} -> "
                      f"{os.path.basename(new)}")
            except OSError as exc:
                print(f"{bcolors.RED}Could not migrate {old}: {exc}{bcolors.ENDC}")
                return False
    # Transient files are simply recreated under the new name; drop the
    # leftovers so they don't linger in the home directory forever.
    for template in (".ikabot_construction_{suffix}.lock",
                     ".ikabot_construction_worker_{suffix}.lock",
                     ".ikabot_construction_stop_{suffix}"):
        try:
            os.remove(_legacy_path(session, template))
        except OSError:
            pass
    return True


# ---------------------------------------------------------------------------
# Schema sidecar — fail loudly on mismatch instead of silently corrupting
# ---------------------------------------------------------------------------

def enforce_schema_or_abort(session):
    sidecar = schema_sidecar_path(session)
    if not os.path.exists(sidecar):
        # First run, or CSV exists but sidecar was lost — create it now.
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

_lock_token_seq = itertools.count(1)
_lock_token_mutex = threading.Lock()


def _new_lock_token():
    """Unique per acquisition, so a holder only ever deletes its own lock."""
    with _lock_token_mutex:
        n = next(_lock_token_seq)
    return f"{os.getpid()}-{threading.get_ident()}-{n}"


def _lock_acquire(lock_file, timeout, stale_after, token=None):
    if token is None:
        token = _new_lock_token()
    # Build the payload before creating the file so the window in which the
    # lock exists but is still empty is as small as possible.
    payload = json.dumps({
        "pid": os.getpid(),
        "timestamp": time.time(),
        "token": token,
    }).encode()

    start = time.time()
    unreadable_since = None
    while time.time() - start < timeout:
        try:
            fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, payload)
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            broke = False
            try:
                with open(lock_file, "r") as f:
                    data = json.load(f)
                unreadable_since = None

                try:
                    holder_pid = int(data.get("pid"))
                except (TypeError, ValueError):
                    holder_pid = -1
                try:
                    held_at = float(data.get("timestamp", 0) or 0)
                except (TypeError, ValueError):
                    held_at = 0.0
                now = time.time()

                # Break the lock when the holder can no longer be waiting on
                # it.  `from_future` matters because a timestamp ahead of our
                # clock (skew, corrupt write) would otherwise never age out,
                # making the lock permanently unbreakable.
                try:
                    holder_dead = (holder_pid > 0
                                   and not psutil.pid_exists(holder_pid))
                except Exception:
                    holder_dead = False  # can't tell; fall back to staleness
                if (holder_dead
                        or holder_pid <= 0
                        or (now - held_at) > stale_after
                        or held_at > now + 60):
                    try:
                        os.remove(lock_file)
                        broke = True
                    except FileNotFoundError:
                        broke = True
                    except OSError:
                        pass  # Windows: holder still has it open; retry later
            except (ValueError, KeyError, OSError):
                # The lock may have been caught mid-creation (file exists, the
                # payload is not written yet).  Deleting it immediately would
                # steal a lock its owner thinks it holds, so give the owner a
                # grace period before treating an unreadable lock as junk.
                if unreadable_since is None:
                    unreadable_since = time.time()
                elif time.time() - unreadable_since > 2.0:
                    unreadable_since = None
                    try:
                        os.remove(lock_file)
                        broke = True
                    except OSError:
                        pass
            if broke:
                continue
        except OSError:
            pass
        # Short, jittered waits: holds last milliseconds, so polling every
        # 0.5s left the lock idle most of the time and let contenders
        # synchronise into repeated collisions.
        time.sleep(random.uniform(0.02, 0.12))
    return False


def _lock_release(lock_file, token=None):
    """Remove *lock_file* only if we are still its recorded owner.

    Matching on the acquisition token (not just the pid) matters because
    several threads in one process share a pid: without it, thread A's
    release could delete the lock thread B had just acquired.
    """
    try:
        with open(lock_file, "r") as f:
            data = json.load(f)
    except (ValueError, OSError):
        return
    if token is not None:
        if data.get("token") != token:
            return
    elif data.get("pid") != os.getpid():
        return
    try:
        os.remove(lock_file)
    except OSError:
        pass


# In-process serialisation for the CSV file lock.  The scheduler thread and
# every shipment thread share one pid, so without this they raced each other
# through the file lock — colliding on acquisition and, worse, releasing each
# other's locks.  Collapsing them into a single contender also cuts the churn
# that was starving waiters into "Could not acquire CSV lock".
_csv_guards = {}
_csv_guards_mutex = threading.Lock()


def _csv_guard(path):
    with _csv_guards_mutex:
        guard = _csv_guards.get(path)
        if guard is None:
            guard = {"lock": threading.RLock(), "depth": 0, "token": None}
            _csv_guards[path] = guard
        return guard


class _csv_lock:
    """Brief-hold lock for the CSV file, across both threads and processes."""

    def __init__(self, session):
        self.path = csv_lock_path(session)

    def __enter__(self):
        self._guard = _csv_guard(self.path)
        # Bounded so a wedged thread surfaces as an error instead of hanging
        # the scheduler forever.
        if not self._guard["lock"].acquire(timeout=120):
            raise RuntimeError(f"Timed out waiting for CSV lock at {self.path}")
        try:
            if self._guard["depth"] == 0:
                # stale_after MUST stay below timeout so an orphaned lock is
                # always broken before waiters give up.
                token = _new_lock_token()
                if not _lock_acquire(self.path, timeout=45, stale_after=15,
                                     token=token):
                    raise RuntimeError(
                        f"Could not acquire CSV lock at {self.path}"
                    )
                self._guard["token"] = token
            self._guard["depth"] += 1
        except BaseException:
            self._guard["lock"].release()
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            self._guard["depth"] -= 1
            if self._guard["depth"] <= 0:
                self._guard["depth"] = 0
                token = self._guard["token"]
                self._guard["token"] = None
                _lock_release(self.path, token)
        finally:
            self._guard["lock"].release()


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
    with _csv_lock(session):
        try:
            with open(csv_path(session), "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                return [_coerce_row_in(r) for r in reader]
        except FileNotFoundError:
            return []


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


def _csv_modify(session, fn):
    """Load rows, apply fn(rows) in-place, save — all under a single lock hold.

    Returns the saved rows so callers that need the post-write state can use
    them instead of taking the lock a second time for a fresh csv_load.
    """
    path = csv_path(session)
    tmp = path + ".tmp"
    with _csv_lock(session):
        try:
            with open(path, "r", newline="", encoding="utf-8") as f:
                rows = [_coerce_row_in(r) for r in csv.DictReader(f)]
        except FileNotFoundError:
            rows = []
        fn(rows)
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader()
            for r in rows:
                w.writerow(_coerce_row_out(r))
        os.replace(tmp, path)
        return rows


def csv_append(session, row):
    _csv_modify(session, lambda rows: rows.append(row))


def csv_delete(session, queue_id):
    qid = int(queue_id)
    def _delete(rows):
        rows[:] = [r for r in rows if r["queue_id"] != qid]
    _csv_modify(session, _delete)


def csv_update(session, queue_id, **fields):
    """Apply *fields* to the row with *queue_id*; returns the updated row.

    Returning the row lets callers avoid a second lock cycle just to read
    back what they wrote.
    """
    qid = int(queue_id)
    def _apply(rows):
        for r in rows:
            if r["queue_id"] == qid:
                for k, v in fields.items():
                    r[k] = v
                if "transport_mode" in fields:
                    r["auto_transport_enabled"] = (
                        "no" if fields["transport_mode"] == "none" else "yes"
                    )
                break
    rows = _csv_modify(session, _apply)
    return find_row(rows, qid)


def csv_skip_rest_of_slot(session, city_id, slot_position, after_queue_id,
                          reason):
    """Mark every pending row for (city_id, slot_position) with
    queue_id > after_queue_id as skipped.

    Used when a row fails on resource shortage: higher-level upgrades of the
    same building cost more and won't fit either, so don't bother trying.
    Returns the list of queue_ids that were skipped.
    """
    cid = int(city_id)
    sp  = int(slot_position)
    aft = int(after_queue_id)
    skipped = []
    note = f"skipped: {reason}"

    def _apply(rows):
        for r in rows:
            if (r["status"] == "pending"
                    and r["city_id"] == cid
                    and r["slot_position"] == sp
                    and r["queue_id"] > aft):
                r["status"] = "skipped"
                r["notes"] = note
                skipped.append(r["queue_id"])
    _csv_modify(session, _apply)
    return skipped


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


def _csv_insert_at(session, city_id, new_row_dicts, insert_idx):
    """Atomically insert new_row_dicts at insert_idx in city_id's pending queue.

    Locked rows (running/shipping) keep their existing queue_ids.
    All pending rows for the city (existing + new) are renumbered with fresh
    qids above the global max, in the new desired order.
    Returns the list of queue_ids assigned to the new rows.
    """
    assigned = []

    def _do_insert(rows):
        city_pending = sorted(
            [r for r in rows if r["city_id"] == city_id and r["status"] == "pending"],
            key=lambda r: r["queue_id"],
        )
        other = [r for r in rows if not (r["city_id"] == city_id and r["status"] == "pending")]

        clamp = max(0, min(insert_idx, len(city_pending)))
        new_order = city_pending[:clamp] + list(new_row_dicts) + city_pending[clamp:]

        base_qid = (max(r["queue_id"] for r in rows) + 1) if rows else 1
        for i, row in enumerate(new_order):
            row["queue_id"] = base_qid + i

        # Indices of new rows in new_order:  clamp .. clamp+len(new_row_dicts)-1
        assigned[:] = [base_qid + clamp + i for i in range(len(new_row_dicts))]

        rows[:] = other + new_order

    _csv_modify(session, _do_insert)
    return assigned


def csv_count_cities_with_work(session):
    return len({r["city_id"] for r in csv_load(session)
                if r["status"] in ("pending", "shipping", "running")})


# ---------------------------------------------------------------------------
# Dynamic loader for resourceTransportManager*.py
# ---------------------------------------------------------------------------

_rtm_module = None
_rtm_lock = threading.Lock()


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
    with _rtm_lock:
        if _rtm_module is not None:
            return _rtm_module
        return _load_rtm_locked()


def _load_rtm_locked():
    global _rtm_module
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


# === END CHUNK 1 OF 5 — chunk 2 of 5 inserted below this line ===

# ---------------------------------------------------------------------------
# Chunk 2 of 5: HTTP helpers + cost helper
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# HTTP helpers — thin wrappers used by both the menu and the scheduler
# ---------------------------------------------------------------------------

def fetch_city(session, city_id):
    """Return parsed city dict for city_id."""
    html = session.get(city_url + str(city_id))
    return getCity(html)


def fetch_island(session, city):
    """Return parsed island dict for the island this city sits on."""
    html = session.get(island_url + str(city["islandId"]))
    return getIsland(html)


# ---------------------------------------------------------------------------
# Cost helpers — ported from research/constructionList.py:156 + :186
# ---------------------------------------------------------------------------

def _get_cost_reducers(city):
    """Return [wood_lv, wine_lv, marble_lv, crystal_lv, sulphur_lv] of
    cost-reducer buildings present in the city (0 if absent).
    """
    reducers = [0] * 5
    for b in city.get("position", []):
        if b.get("building") in ("empty", "") or b.get("name") == "empty":
            continue
        lv = b.get("level", 0)
        slug = b.get("building", "")
        if slug == "carpentering":
            reducers[0] = lv
        elif slug == "vineyard":
            reducers[1] = lv
        elif slug == "architect":
            reducers[2] = lv
        elif slug == "optician":
            reducers[3] = lv
        elif slug == "fireworker":
            reducers[4] = lv
    return reducers


def _research_discount_pct(session, city):
    """Return cumulative economy-research cost-reduction as int 0..14.

    Caches the maximum (14 %) flag in session data to avoid repeated HTTP
    fetches across add-session calls, mirroring getResourcesNeeded's approach.
    """
    data = session.getSessionData()
    if data.get("reduccion_inv_max"):
        return 14
    url = (
        "view=noViewChange&researchType=economy&backgroundView=city"
        "&currentCityId={cid}&templateView=researchAdvisor"
        "&actionRequest={ar}&ajax=1"
    ).format(cid=city["id"], ar=actionRequest)
    try:
        rta = session.post(url)
        rta = json.loads(rta, strict=False)
        studies = json.loads(rta[2][1]["new_js_params"], strict=False)
        studies = studies["currResearchType"]
    except Exception:
        return 0
    pct = 0
    for s in studies.values():
        if s.get("liClass") != "explored":
            continue
        href = s.get("aHref", "")
        if "2020" in href:
            pct += 2
        elif "2060" in href:
            pct += 4
        elif "2100" in href:
            pct += 8
    if pct == 14:
        data["reduccion_inv_max"] = True
        session.setSessionData(data)
    return pct


def fetch_costs_for_building(session, city, building_slug):
    """Fetch the per-level cost table for *building_slug* from the ikipedia.

    Returns dict[int, list[int]] mapping level → [wood, wine, marble, crystal,
    sulphur].  Returns {} if the building has no BUILDING_HELP_IDS entry so
    callers can gracefully skip rather than crashing.

    Column-to-resource mapping is derived from the <th> CDN image hashes
    in the cost table — NOT assumed positional — because many buildings only
    show a subset of the five resources (carpentering = 1 col, barracks = 2,
    town hall = 3, etc.).  A positional assumption would write marble costs
    into the wine slot for a barracks row, which would break cost math and
    over-ship the wrong resources.
    """
    # Steps 1+2: build the detail URL directly from BUILDING_HELP_IDS.
    # The old approach scraped the ikipedia listing page for each building's
    # button, but the listing XHR no longer contains the button_building
    # icons (they are rendered client-side by JS), so scraping can never
    # match.  helpId=1 is constant for every building, so slug → buildingId
    # is all we need.  Unknown slugs return {} so the scheduler's existing
    # `cost is None → skip` branch handles them cleanly.
    building_id = BUILDING_HELP_IDS.get(building_slug.lower())
    if building_id is None:
        sendToBotDebug(
            session,
            f"fetch_costs_for_building: no buildingId mapping for slug "
            f"'{building_slug}' in city {city.get('id')}",
            True,
        )
        return {}
    cost_url = (
        "view=buildingDetail&buildingId={bid}&helpId=1"
        "&backgroundView=city&currentCityId={cid}"
        "&templateView=buildingDetail&actionRequest={ar}&ajax=1"
    ).format(bid=building_id, cid=city["id"], ar=actionRequest)

    # Step 3: per-building cost table
    try:
        resp2 = session.post(cost_url)
        html_costs = json.loads(resp2, strict=False)[1][1][1]
    except Exception:
        sendToBotDebug(
            session,
            f"fetch_costs_for_building: cost table fetch failed for slug "
            f"'{building_slug}' city {city.get('id')}:\n{traceback.format_exc()}",
            True,
        )
        return {}

    # Step 4: derive column → resource-index from <th> CDN image hashes.
    # CDN URLs look like //gfN.geo.gfsrv.net/cdn{XX}/{30hex}.png — filenames
    # are opaque MD5 hashes, not resource names.  Reconstruct the full 32-char
    # MD5 from the two-char cdn-path prefix + the 30-char filename, then look
    # it up in material_img_hash (order: wood, wine, marble, crystal, sulfur).
    # The last <th class="costs"> is always a clock/time icon — drop it with [:-1].
    th_srcs = re.findall(r'<th class="costs"><img src="(.*?)"', html_costs)
    col_to_res = []
    for src in th_srcs[:-1]:  # last th = time icon, skip it
        cdn_match = re.search(r'/cdn([0-9a-f]{2})/([0-9a-f]+)\.png', src, re.IGNORECASE)
        if cdn_match:
            full_hash = cdn_match.group(1) + cdn_match.group(2)
            try:
                idx = material_img_hash.index(full_hash)
            except ValueError:
                idx = -1
        else:
            idx = -1
        col_to_res.append(idx)

    # Step 5: research-discount multiplier (shared across all levels in this call)
    pct = _research_discount_pct(session, city)
    discount_factor = Decimal(1) - Decimal(pct) / Decimal(100)

    # Step 6: cost-reducer buildings in destination city
    reducers = _get_cost_reducers(city)

    # Step 7: parse per-level rows, apply discounts, build output dict
    row_pat = re.compile(
        r'<td class="level">(\d+)</td>'
        r'((?:\s*<td class="costs">.*?</td>)+)',
        re.DOTALL,
    )
    cell_pat = re.compile(
        r'<td class="costs"><div.*?>([\d,\.\s\xa0]*)</div></div></td>',
        re.DOTALL,
    )
    out = {}
    for rm in row_pat.finditer(html_costs):
        lv = int(rm.group(1))
        cells = cell_pat.findall(rm.group(2))
        adjusted = [0] * 5
        for col_i, raw in enumerate(cells):
            if col_i >= len(col_to_res):
                break
            res_i = col_to_res[col_i]
            if res_i < 0:
                continue
            clean = (
                raw.replace("\xa0", "")
                   .replace(" ", "")
                   .replace(",", "")
                   .replace(".", "")
            )
            raw_val = int(clean) if clean else 0
            post = Decimal(raw_val)
            # Mirror getResourcesNeeded math exactly:
            # post_research = pre_research * discount_factor
            # final = post - pre * (reducer_lv / 100)
            if discount_factor and discount_factor != 0:
                pre = post / discount_factor
            else:
                pre = post
            real = post - pre * (Decimal(reducers[res_i]) / Decimal(100))
            adjusted[res_i] = int(math.ceil(real))
        out[lv] = adjusted
    return out


def _get_buildable_options(session, city, slot_position):
    """Return list of buildable-building dicts for an empty slot.

    Each dict: {building: slug, name: display_name, buildingId: str, type: str}.
    Returns [] if slot is occupied or no buildings are available.
    """
    positions = city.get("position", [])
    if slot_position >= len(positions):
        return []
    slot = positions[slot_position]
    if slot.get("building") != "empty":
        return []
    params = {
        "view": "buildingGround",
        "cityId": city["id"],
        "position": slot_position,
        "backgroundView": "city",
        "currentCityId": city["id"],
        "actionRequest": actionRequest,
        "ajax": "1",
    }
    try:
        resp = session.post(params=params, noIndex=True)
        data = json.loads(resp, strict=False)[1][1]
        if not data:
            return []
        html = data[1]
    except Exception:
        return []
    matches = re.findall(
        r'<li class="building (.+?)">\s*<div class="buildinginfo">\s*'
        r'<div title="(.+?)"\s*class="buildingimg .+?"\s*'
        r'onclick="ajaxHandlerCall\(\'.*?buildingId=(\d+)&',
        html,
    )
    return [
        {
            "building": m[0],
            "name": m[1],
            "buildingId": m[2],
            "type": slot.get("type", ""),
        }
        for m in matches
    ]

# === END CHUNK 2 OF 5 — chunk 3 of 5 inserted below this line ===

# ---------------------------------------------------------------------------
# Chunk 3 of 5: slot renderer + interactive add-to-queue flow
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')


def _visible_len(s):
    """Length excluding ANSI colour escapes — used for column padding."""
    return len(_ANSI_RE.sub('', s))


def _format_slot_line(i, slot, queued):
    """Return the formatted text for a single slot (no leading indent)."""
    name = slot.get("name", "empty")
    if name == "empty":
        slot_type = slot.get("type", "")
        if i == 14:
            slot_type = "wall only"
        elif i == 17:
            slot_type = "pirate fortress"
        build_q = next((q for q in queued if q["action"] == "build"), None)
        if build_q is not None:
            effective_lv = max(q["target_level"] for q in queued)
            label = (
                f"[QUEUED] lv 1→{effective_lv:>2} {build_q['building']} "
                f"({slot_type})"
            )
            return f"{i:>2}  {bcolors.YELLOW}{label}{bcolors.ENDC}"
        return f"{i:>2}  [ -- ]  (empty, {slot_type})"

    lv = slot.get("level", 0)
    queued_targets = [q["target_level"] for q in queued
                      if q["action"] == "upgrade"]
    effective_lv = max(queued_targets + [lv])
    lv_str = (
        f"lv{lv:>2}→{effective_lv}"
        if queued_targets else f"lv{lv:>2}"
    )
    busy_mark = ""
    if slot.get("isBusy"):
        busy_mark = "  *building"
        eta = slot.get("completed")
        if eta:
            busy_mark += f" {getDateTime(int(eta))[8:]}"
    if queued_targets:
        color, note = bcolors.YELLOW, "  [QUEUED]"
    elif slot.get("isMaxLevel"):
        color, note = bcolors.BLACK, "  MAX"
    elif slot.get("canUpgrade"):
        color, note = bcolors.GREEN, ""
    else:
        color, note = bcolors.RED, "  (res missing)"
    return (
        f"{i:>2}  [{lv_str}]  "
        f"{color}{name}{bcolors.ENDC}"
        f"{note}{busy_mark}"
    )


def _render_slot_grid(city, pending_by_slot=None):
    """Print a 2-column slot grid so all 24 positions fit on screen.

    Left column = first half (slots 0..N/2-1), right column = second half.
    Reading top-to-bottom, left-to-right walks the slot numbers in order.

    If *pending_by_slot* is provided ({slot_pos: [{action, building,
    target_level}]}) the grid overlays the queue: empty slots with a
    queued build show the future building + level, occupied slots with
    queued upgrades show ``current → target``, both in yellow + [QUEUED].
    """
    pending_by_slot = pending_by_slot or {}
    positions = city.get("position", [])
    lines = [_format_slot_line(i, slot, pending_by_slot.get(i, []))
             for i, slot in enumerate(positions)]

    n = len(lines)
    half = (n + 1) // 2  # left column gets the extra slot if odd
    col_width = max((_visible_len(l) for l in lines), default=0) + 3
    print("")
    for i in range(half):
        left = lines[i]
        right = lines[i + half] if i + half < n else ""
        pad = " " * max(0, col_width - _visible_len(left))
        print(f"  {left}{pad}{right}")
    print("")


def _gather_pending_for_city(session, city_id):
    """Return {slot_pos: [{action, building, target_level}]} for unfinished
    rows belonging to *city_id*, sorted by queue_id within each slot."""
    pending = {}
    for r in sorted(csv_load(session), key=lambda x: x["queue_id"]):
        if r["city_id"] != city_id:
            continue
        if r["status"] not in ("pending", "shipping", "running"):
            continue
        sp = int(r["slot_position"])
        pending.setdefault(sp, []).append({
            "action":       r["action"],
            "building":     r["building"],
            "target_level": int(r["target_level"]),
        })
    return pending


def _fmt_res(vals):
    """Format a 5-element resource list as a compact string."""
    parts = []
    names = ["Wd", "Wi", "Mb", "Cr", "Su"]
    for n, v in zip(names, vals):
        if v:
            parts.append(f"{n} {addThousandSeparator(v)}")
    return "  ".join(parts) if parts else "0"


def _print_cost_table(header, rows):
    """Print a labelled table of resource amounts.

    *rows* is a list of (label, [w,wi,mb,cr,su]) tuples.
    """
    col_w = 10
    res_names = ["Wood", "Wine", "Marble", "Crystal", "Sulphur"]
    header_line = f"  {'':30}" + "".join(f"{r:>{col_w}}" for r in res_names)
    print(f"\n  {header}")
    print(header_line)
    print("  " + "-" * (30 + col_w * 5))
    for label, vals in rows:
        vals = list(vals) + [0] * (5 - len(vals))
        row_line = f"  {label:<30}" + "".join(
            f"{addThousandSeparator(v):>{col_w}}" for v in vals
        )
        print(row_line)


# ---------------------------------------------------------------------------
# Interactive add-to-queue flow (menu option 1)
# ---------------------------------------------------------------------------

def _prompt_insert_position(pending_rows, city_name):
    """Show numbered pending rows and ask where to insert the new block.

    Returns (insert_idx, None) on success, or (None, 'exit'/'restart').
    insert_idx=0 means before all pending rows (top); insert_idx=len means append.
    """
    n = len(pending_rows)
    print(f"\n  Where to insert? (pending queue for {city_name})\n")
    print(f"    0) [top — run right after in-progress]")
    for i, r in enumerate(pending_rows):
        print(f"    {i + 1}) after  {r['building']} → lv {r['target_level']}")
    print(f"    {n + 1}) [bottom — append after all pending]")
    print(f"\n  Choice [0–{n + 1}, default 0]:")
    raw = read(min=0, max=n + 1, digit=True, empty=True, additionalValues=["'", "="])
    if raw == "'":
        return None, "exit"
    if raw == "=":
        return None, "restart"
    idx = 0 if raw == "" else int(raw)
    # position n+1 (bottom) = insert after all pending = same as len(pending)
    if idx == n + 1:
        idx = n
    return idx, None


def _add_to_queue(session):
    """Interactive: pick a city, pick slots + targets, append rows to CSV."""
    banner()
    print("Add construction(s) to queue\n")
    city = chooseCity(session)
    city_id = city["id"]
    city_name = city.get("cityName") or city.get("name", str(city_id))

    banner()
    print(f"City: {city_name}\n")
    avail = city.get("availableResources", [0] * 5)
    free  = city.get("freeSpaceForResources", [0] * 5)
    cap   = [a + f for a, f in zip(avail, free)]
    _print_cost_table(
        "Current resources",
        [
            ("Available", avail),
            ("Free space", free),
            ("Capacity",  cap),
        ],
    )
    _render_slot_grid(city, _gather_pending_for_city(session, city_id))

    print(
        f"  {bcolors.WARNING if hasattr(bcolors, 'WARNING') else ''}"
        f"Tip: at any prompt below, '=' restarts the current cycle "
        f"(re-prompts for slot), and ' (apostrophe) exits to the main menu."
        f"{bcolors.ENDC}\n"
    )

    # ---- session-default transport mode ----
    print(
        "  Default transport mode for this add session:\n"
        "  (1) jit  — ship resources just in time for each level [default]\n"
        "  (2) bulk — ship all pending costs upfront\n"
        "  (3) none — rely on existing stock only"
    )
    sess_tm = read(
        min=1, max=3, digit=True, empty=True, additionalValues=["'"],
    )
    if sess_tm == "'":
        print("\n  Exited via '. Nothing was added.")
        enter()
        return
    session_default_mode = {1: "jit", 2: "bulk", 3: "none", "": "jit"}.get(
        sess_tm, "jit"
    )
    print(f"  Session default: {session_default_mode}\n")

    positions     = city.get("position", [])
    max_slot      = len(positions) - 1
    slot_targets  = 0          # distinct slot-target picks this session
    added_ids     = []         # queue_ids added; kept for rollback
    cost_cache    = {}         # building_slug → costs dict (per add-session cache)
    exit_to_menu  = False     # set when user presses ' at any prompt

    while not exit_to_menu and slot_targets < ADD_SESSION_SLOT_CAP:
        remaining = ADD_SESSION_SLOT_CAP - slot_targets
        print(
            f"  Enter slot number (0–{max_slot})  |  S=re-show  |  "
            f"'=exit  |  Enter/d/done=finish   [{remaining} left]:"
        )
        raw = read(empty=True).strip()
        if raw == "" or raw.lower() in ("d", "done"):
            break
        if raw == "'":
            exit_to_menu = True
            break
        if raw == "=":
            # No-op at the top of the cycle — equivalent to a re-show.
            _render_slot_grid(
                city, _gather_pending_for_city(session, city_id)
            )
            continue
        if raw.lower() == "s":
            _render_slot_grid(
                city, _gather_pending_for_city(session, city_id)
            )
            continue
        try:
            slot_pos = int(raw)
        except ValueError:
            print(f"  Invalid input — enter a number 0–{max_slot}, 'S', '=', ', or Enter.")
            continue
        if not 0 <= slot_pos <= max_slot:
            print(f"  Slot {slot_pos} out of range (0–{max_slot}).")
            continue
        slot = positions[slot_pos]
        is_empty = slot.get("building") == "empty"

        # Queue-aware: pick up where any prior pending rows for this slot end.
        slot_queue = _gather_pending_for_city(session, city_id).get(slot_pos, [])
        build_q     = next((q for q in slot_queue if q["action"] == "build"), None)
        upgrade_qs  = [q for q in slot_queue if q["action"] == "upgrade"]
        queued_max  = max((q["target_level"] for q in slot_queue), default=0)

        if is_empty and build_q is None:
            # ---- empty slot, nothing queued: pick a building to construct ----
            opts = _get_buildable_options(session, city, slot_pos)
            if not opts:
                print("  No buildings can be constructed in that slot.")
                enter()
                continue
            banner()
            print(f"  Slot {slot_pos} is empty. Choose a building to construct:\n")
            for idx, o in enumerate(opts, 1):
                print(f"  ({idx}) {o['name']}")
            chosen_idx = read(
                min=1, max=len(opts), additionalValues=["'", "="]
            )
            if chosen_idx == "'":
                exit_to_menu = True
                break
            if chosen_idx == "=":
                continue
            chosen = opts[chosen_idx - 1]
            building_slug = chosen["building"]
            building_name = chosen["name"]
            building_id   = chosen["buildingId"]
            target_level = read(
                min=1,
                max=HARD_LEVEL_CAP,
                msg=f"  Build {building_name} to level (1 = just construct): ",
                additionalValues=["'", "="],
            )
            if target_level == "'":
                exit_to_menu = True
                break
            if target_level == "=":
                continue
            rows_to_add = [(1, "build")] + [
                (lv, "upgrade") for lv in range(2, target_level + 1)
            ]

            if building_slug not in cost_cache:
                print("  Fetching cost data…")
                cost_cache[building_slug] = fetch_costs_for_building(
                    session, city, building_slug
                )
            costs_dict = cost_cache[building_slug]
            row_costs = []
            missing_levels = []
            for lv, _ in rows_to_add:
                c = costs_dict.get(lv)
                if c is None:
                    row_costs.append([0] * 5)
                    missing_levels.append(lv)
                else:
                    row_costs.append(c)
            if missing_levels:
                print(
                    f"  {bcolors.YELLOW}Cost data missing for levels "
                    f"{missing_levels}; stored as 0 and will be recomputed "
                    f"before execution.{bcolors.ENDC}"
                )

        elif is_empty and build_q is not None:
            # ---- empty slot, build already queued: extend with more upgrades ----
            if queued_max >= HARD_LEVEL_CAP:
                print(
                    f"  Slot {slot_pos} is already queued to lv {queued_max} "
                    f"(cap is {HARD_LEVEL_CAP}). Nothing more to add."
                )
                enter()
                continue
            building_slug = build_q["building"]
            building_name = building_slug   # CSV doesn't store the friendly name
            building_id   = ""
            print(
                f"\n  Slot {slot_pos}: {building_slug} is already queued to "
                f"reach lv {queued_max}.\n"
                f"  Continuing from lv {queued_max + 1}."
            )
            target_level = read(
                min=queued_max + 1,
                max=HARD_LEVEL_CAP,
                msg=f"  Extend to level: ",
                additionalValues=["'", "="],
            )
            if target_level == "'":
                exit_to_menu = True
                break
            if target_level == "=":
                continue
            rows_to_add = [
                (lv, "upgrade")
                for lv in range(queued_max + 1, target_level + 1)
            ]
            if building_slug not in cost_cache:
                print("  Fetching cost data…")
                cost_cache[building_slug] = fetch_costs_for_building(
                    session, city, building_slug
                )
            costs_dict = cost_cache[building_slug]
            row_costs = []
            missing_levels = []
            for lv, _ in rows_to_add:
                c = costs_dict.get(lv)
                if c is None:
                    row_costs.append([0] * 5)
                    missing_levels.append(lv)
                else:
                    row_costs.append(c)
            if missing_levels:
                print(
                    f"  {bcolors.YELLOW}Cost data missing for levels "
                    f"{missing_levels}; stored as 0 and will be recomputed "
                    f"before execution.{bcolors.ENDC}"
                )

        else:
            # ---- occupied slot: pick a target level to upgrade to ----
            current_lv = slot.get("level", 0)
            if slot.get("isBusy"):
                current_lv += 1          # already building toward this level
            # Effective level = max of in-game level and any queued targets.
            effective_lv = max(current_lv, queued_max)
            building_slug = slot.get("building", "")
            building_name = slot.get("name", building_slug)
            building_id   = ""

            if slot.get("isMaxLevel"):
                print(
                    f"\n  {bcolors.YELLOW}Warning: slot {slot_pos} "
                    f"({building_name}) is already at max level.{bcolors.ENDC}"
                )
                print("  Continue anyway? [y/N]   '=exit  ==restart")
                ans = read(
                    values=["y", "Y", "n", "N", "", "'", "="], default="N"
                )
                if ans == "'":
                    exit_to_menu = True
                    break
                if ans.lower() != "y":
                    continue

            if effective_lv >= HARD_LEVEL_CAP:
                print(
                    f"  Slot {slot_pos} ({building_name}) already queued to "
                    f"lv {effective_lv} (cap is {HARD_LEVEL_CAP}). "
                    f"Nothing more to add."
                )
                enter()
                continue
            if upgrade_qs:
                print(
                    f"\n  Slot {slot_pos}: {building_name} lv {current_lv}, "
                    f"already queued to reach lv {queued_max}.\n"
                    f"  Continuing from lv {effective_lv + 1}."
                )
            else:
                print(
                    f"\n  Slot {slot_pos}: {building_name}  lv {current_lv}  "
                    f"(max allowed: {HARD_LEVEL_CAP})"
                )
            target_level = read(
                min=effective_lv + 1,
                max=HARD_LEVEL_CAP,
                msg=f"  Upgrade to level (effective {effective_lv}): ",
                additionalValues=["'", "="],
            )
            if target_level == "'":
                exit_to_menu = True
                break
            if target_level == "=":
                continue

            # fetch costs for all levels in one shot, cache for this session
            if building_slug not in cost_cache:
                print("  Fetching cost data…")
                cost_cache[building_slug] = fetch_costs_for_building(
                    session, city, building_slug
                )
            costs_dict = cost_cache[building_slug]

            rows_to_add  = [
                (lv, "upgrade")
                for lv in range(effective_lv + 1, target_level + 1)
            ]
            row_costs = []
            missing_levels = []
            for lv, _ in rows_to_add:
                c = costs_dict.get(lv)
                if c is None:
                    row_costs.append([0] * 5)
                    missing_levels.append(lv)
                else:
                    row_costs.append(c)
            if missing_levels:
                print(
                    f"  {bcolors.YELLOW}Cost data missing for levels "
                    f"{missing_levels}; stored as 0 and will be recomputed "
                    f"before execution.{bcolors.ENDC}"
                )

        # ---- transport mode ----
        print(
            f"\n  Transport mode for this slot-target "
            f"(Enter = session default '{session_default_mode}'):\n"
            f"  (1) jit  — ship resources just in time for each level\n"
            f"  (2) bulk — ship all pending costs upfront\n"
            f"  (3) none — rely on existing stock only"
        )
        tm_choice = read(
            min=1, max=3, digit=True, empty=True,
            additionalValues=["'", "="],
        )
        if tm_choice == "'":
            exit_to_menu = True
            break
        if tm_choice == "=":
            continue
        transport_mode = {
            1: "jit", 2: "bulk", 3: "none", "": session_default_mode,
        }.get(tm_choice, session_default_mode)

        # ---- build row dicts (qid placeholder 0; assigned by _csv_insert_at) ----
        now_ts = int(time.time())
        row_total = [0] * 5
        new_row_dicts = []
        for (lv, action_type), cost in zip(rows_to_add, row_costs):
            notes_val = ""
            if action_type == "upgrade" and costs_dict.get(lv) is None:
                notes_val = "cost unknown at add-time; will be recomputed before execution"
            new_row_dicts.append({
                "queue_id":             0,
                "city_id":              city_id,
                "city_name":            city_name,
                "slot_position":        slot_pos,
                "action":               action_type,
                "building":             building_slug,
                "building_id":          building_id if action_type == "build" else "",
                "target_level":         lv,
                "wood":                 cost[0],
                "wine":                 cost[1],
                "marble":               cost[2],
                "crystal":              cost[3],
                "sulphur":              cost[4],
                "transport_mode":       transport_mode,
                "auto_transport_enabled": "no" if transport_mode == "none" else "yes",
                "status":               "pending",
                "expected_finish":      "",
                "added_at":             now_ts,
                "notes":                notes_val,
                "schema_version":       SCHEMA_VERSION,
            })
            for j in range(5):
                row_total[j] += cost[j]

        # ---- position prompt ----
        pending_city_rows = sorted(
            [r for r in csv_load(session)
             if r["city_id"] == city_id and r["status"] == "pending"],
            key=lambda r: r["queue_id"],
        )
        if pending_city_rows:
            insert_idx, pos_action = _prompt_insert_position(pending_city_rows, city_name)
            if pos_action == "exit":
                exit_to_menu = True
                break
            if pos_action == "restart":
                continue
        else:
            insert_idx = 0

        # ---- write to CSV atomically ----
        assigned_qids = _csv_insert_at(session, city_id, new_row_dicts, insert_idx)
        added_ids.extend(assigned_qids)

        slot_targets += 1

        # ---- per-slot summary ----
        lvl_range = (
            f"lv {current_lv}→{target_level}"
            if not is_empty
            else "new (lv 1)"
        )
        n_rows = len(rows_to_add)
        print(
            f"\n  Added: {city_name} · slot {slot_pos} · "
            f"{building_name} · {lvl_range} · "
            f"{n_rows} row(s) · cost = {_fmt_res(row_total)} · mode={transport_mode}"
        )

        if slot_targets >= ADD_SESSION_SLOT_CAP:
            print(f"\n  Slot-target cap ({ADD_SESSION_SLOT_CAP}) reached for this session.")
            break

    if not added_ids:
        if exit_to_menu:
            print("\n  Exited via '. Nothing was added.")
        else:
            print("\n  Nothing added.")
        enter()
        return

    # If user exited via ', skip the slow cross-city analysis but still
    # offer rollback so an accidental ' doesn't lock in unwanted rows.
    if exit_to_menu:
        banner()
        print(
            f"  Exited via '. {len(added_ids)} row(s) staged for "
            f"{city_name}.\n"
            "  [C] Confirm and keep   [R] Rollback and discard"
        )
        choice = read(values=["C", "c", "R", "r"])
        if choice.upper() == "R":
            for qid in added_ids:
                csv_delete(session, qid)
            print("  Rolled back — no rows saved.")
        else:
            print(f"  Confirmed. {len(added_ids)} row(s) queued.")
        enter()
        return

    # ---- queue summary for this city ----
    banner()
    print(f"  Session summary for {city_name}\n")

    city = fetch_city(session, city_id)   # re-fetch for fresh stock
    avail = city.get("availableResources", [0] * 5)

    # Total cost across the *whole* pending queue for this city (not just the
    # rows added this session) — gives an accurate picture of what still has
    # to be shipped or produced before the queue clears.
    all_pending = sorted(
        (r for r in csv_load(session)
         if r["city_id"] == city_id and r["status"] == "pending"),
        key=lambda r: r["queue_id"],
    )
    queue_total = [0] * 5
    for r in all_pending:
        for i, k in enumerate(RESOURCE_COLS):
            queue_total[i] += int(r.get(k, 0) or 0)

    _print_cost_table(
        f"Resources for {len(all_pending)} pending row(s)",
        [
            ("Total queue cost", queue_total),
            ("In city now",      avail),
        ],
    )

    # Simple green/red flag: can the next pending row run right now?
    if all_pending:
        next_cost = [int(all_pending[0].get(k, 0) or 0) for k in RESOURCE_COLS]
        if all(avail[i] >= next_cost[i] for i in range(5)):
            print(
                f"\n  {bcolors.GREEN}✓ Resources are ready for at least one "
                f"upgrade.{bcolors.ENDC}"
            )
        else:
            print(
                f"\n  {bcolors.RED}✗ Not enough resources for the next "
                f"upgrade — auto-transport will ship if enabled."
                f"{bcolors.ENDC}"
            )

    # ---- confirm or rollback ----
    print(
        f"\n  {len(added_ids)} row(s) staged in CSV.\n"
        "  [C] Confirm and keep   [R] Rollback and discard"
    )
    choice = read(values=["C", "c", "R", "r"])
    if choice.upper() == "R":
        for qid in added_ids:
            csv_delete(session, qid)
        print("  Rolled back — no rows saved.")
    else:
        print(f"  Confirmed. {len(added_ids)} row(s) queued for {city_name}.")
    enter()

# === END CHUNK 3 OF 5 — chunk 4 of 5 inserted below this line ===

# ---------------------------------------------------------------------------
# Chunk 4 of 5: view queue, edit queue, stop worker, main menu entry point
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# View queue (menu option 2)
# ---------------------------------------------------------------------------

def _view_queue(session):
    banner()
    rows = csv_load(session)
    if not rows:
        print("  Queue is empty.")
        enter()
        return

    by_city = {}
    for r in rows:
        by_city.setdefault(r["city_id"], []).append(r)

    for cid, city_rows in sorted(by_city.items()):
        city_name = city_rows[0]["city_name"]
        print(f"\n  City: {city_name}  (id {cid})\n")
        print(
            f"  {'QID':>5}  {'Slot':>4}  {'Building':<18}  "
            f"{'→Lv':>4}  {'Mode':>5}  {'Status':<10}  ETA"
        )
        print("  " + "-" * 72)
        for r in sorted(city_rows, key=lambda x: x["queue_id"]):
            eta = r.get("expected_finish", "")
            eta_str = getDateTime(int(eta))[8:] if eta else "--"
            status = r["status"]
            if status == "running":
                s_col = f"{bcolors.GREEN}{status:<10}{bcolors.ENDC}"
            elif status == "skipped":
                s_col = f"{bcolors.RED}{status:<10}{bcolors.ENDC}"
            elif status == "shipping":
                s_col = f"{bcolors.YELLOW}{status:<10}{bcolors.ENDC}"
            else:
                s_col = f"{status:<10}"
            print(
                f"  {r['queue_id']:>5}  {r['slot_position']:>4}  "
                f"{r['building']:<18}  {r['target_level']:>4}  "
                f"{r['transport_mode']:>5}  {s_col}  {eta_str}"
            )
            cost = [r.get(k, 0) for k in RESOURCE_COLS]
            notes = r.get("notes", "")
            if any(cost) or notes:
                cost_str = _fmt_res(cost) if any(cost) else "0"
                suffix = f"  notes: {notes}" if notes else ""
                print(f"         cost: {cost_str}{suffix}")
    print("")
    enter()


# ---------------------------------------------------------------------------
# Edit queue (menu option 3)
# ---------------------------------------------------------------------------

def _build_slot_groups(rows):
    """Group pending rows by (city_id, city_name, slot_position, building).

    Returns a list of group dicts, ordered by city_id then min queue_id:
      {
        "city_id", "city_name", "slot_position", "building",
        "next_level": lowest pending target_level,
        "max_level":  highest pending target_level,
        "pending_qids": [sorted list of pending queue_ids],
        "all_qids":     [all queue_ids for this slot, any status],
      }
    """
    from collections import defaultdict
    key_to_pending = defaultdict(list)
    key_to_all    = defaultdict(list)
    for r in sorted(rows, key=lambda x: x["queue_id"]):
        key = (r["city_id"], r.get("city_name", ""), r["slot_position"], r["building"])
        key_to_all[key].append(r)
        if r["status"] == "pending":
            key_to_pending[key].append(r)

    groups = []
    for key in sorted(key_to_all.keys(), key=lambda k: (k[0], min(r["queue_id"] for r in key_to_all[k]))):
        pending = key_to_pending.get(key, [])
        if not pending:
            continue
        cid, cname, slot, building = key
        levels = [r["target_level"] for r in pending]
        groups.append({
            "city_id":    cid,
            "city_name":  cname,
            "slot_position": slot,
            "building":   building,
            "next_level": min(levels),
            "max_level":  max(levels),
            "pending_qids": [r["queue_id"] for r in pending],
            "all_qids":   [r["queue_id"] for r in key_to_all[key]],
        })
    return groups


def _edit_queue(session):
    while True:
        banner()
        rows = csv_load(session)
        if not rows:
            print("  Queue is empty.")
            enter()
            return

        # ---- Build and display grouped view --------------------------------
        groups = _build_slot_groups(rows)
        if not groups:
            print("  No pending rows to edit.")
            enter()
            return

        print("  Edit queue — pending buildings by slot:\n")
        current_city = None
        for i, g in enumerate(groups, 1):
            if g["city_id"] != current_city:
                current_city = g["city_id"]
                print(f"  {g['city_name']}:")
            lv_range = (f"lv {g['next_level']}→{g['max_level']}"
                        if g["next_level"] != g["max_level"]
                        else f"lv {g['next_level']}")
            print(
                f"    ({i}) slot {g['slot_position']:>2}  {g['building']:<20}  "
                f"{lv_range}  [{len(g['pending_qids'])} row(s)]"
            )
        print(f"\n  (R) Reorder  (T) Transport mode  (B) Back")

        raw = read(
            min=1, max=len(groups), digit=True, empty=True,
            additionalValues=["r", "R", "t", "T", "b", "B"],
        )
        if raw in ("", None):
            continue
        if isinstance(raw, str) and raw.upper() == "B":
            return

        # ---- Reorder within city ------------------------------------------
        if isinstance(raw, str) and raw.upper() == "R":
            rows = csv_load(session)
            city_ids = sorted({r["city_id"] for r in rows})
            if not city_ids:
                print("  No cities in queue.")
                enter()
                continue
            print("\n  Choose city to reorder (by city_id):\n")
            for i, cid in enumerate(city_ids, 1):
                cname = next(r["city_name"] for r in rows if r["city_id"] == cid)
                print(f"  ({i}) {cname}  (id {cid})")
            idx = read(min=1, max=len(city_ids))
            target_cid = city_ids[idx - 1]
            city_rows = sorted(
                [r for r in rows if r["city_id"] == target_cid],
                key=lambda x: x["queue_id"],
            )
            if len(city_rows) < 2:
                print("  Only one row for that city — nothing to reorder.")
                enter()
                continue
            print(f"\n  Current order:\n")
            for r in city_rows:
                print(f"    {r['queue_id']:>5}  {r['building']:<18}  → lv {r['target_level']}")
            print(
                f"\n  New order (queue_ids separated by spaces, "
                f"e.g. '{' '.join(str(r['queue_id']) for r in reversed(city_rows))}'):"
            )
            raw2 = read(empty=True).strip()
            if not raw2:
                continue
            try:
                new_order = [int(x) for x in raw2.split()]
            except ValueError:
                print("  Invalid input — must be space-separated queue_ids.")
                enter()
                continue
            existing_qids = [r["queue_id"] for r in city_rows]
            if sorted(new_order) != sorted(existing_qids):
                print(f"  Must contain exactly these queue_ids: {sorted(existing_qids)}")
                enter()
                continue
            sorted_pool = sorted(existing_qids)
            qid_map = {new_order[i]: sorted_pool[i] for i in range(len(new_order))}
            def _do_reorder(rows, _m=qid_map):
                for r in rows:
                    if r["queue_id"] in _m:
                        r["queue_id"] = _m[r["queue_id"]]
            _csv_modify(session, _do_reorder)
            print(f"  Reordered {len(city_rows)} rows.")
            enter()
            continue

        # ---- Transport mode bulk-set for city ------------------------------
        if isinstance(raw, str) and raw.upper() == "T":
            rows = csv_load(session)
            city_ids = sorted({r["city_id"] for r in rows})
            if not city_ids:
                print("  No cities in queue.")
                enter()
                continue
            print("\n  Choose city to update transport mode:\n")
            for i, cid in enumerate(city_ids, 1):
                cname = next(r["city_name"] for r in rows if r["city_id"] == cid)
                print(f"  ({i}) {cname}  (id {cid})")
            idx = read(min=1, max=len(city_ids))
            target_cid = city_ids[idx - 1]
            print("\n  New transport mode for ALL rows in this city:")
            print("  (1) jit   (2) bulk   (3) none")
            tm_choice = read(min=1, max=3)
            new_tm = {1: "jit", 2: "bulk", 3: "none"}[tm_choice]
            count = 0
            for r in rows:
                if r["city_id"] == target_cid:
                    csv_update(session, r["queue_id"], transport_mode=new_tm)
                    count += 1
            print(f"  Set transport_mode={new_tm} on {count} row(s).")
            enter()
            continue

        # ---- Group selected — show slot actions ----------------------------
        g = groups[int(raw) - 1]
        banner()
        lv_range = (f"lv {g['next_level']}→{g['max_level']}"
                    if g["next_level"] != g["max_level"]
                    else f"lv {g['next_level']}")
        print(
            f"  {g['city_name']} · slot {g['slot_position']} · "
            f"{g['building']} {lv_range}\n"
        )
        print("  (1) Delete this entire slot queue")
        print(f"  (2) Trim — change the target from lv {g['max_level']} to a lower level")
        print("  (3) Modify a single row")
        print("  (B) Back")
        slot_action = read(min=1, max=3, digit=True, additionalValues=["b", "B"])
        if isinstance(slot_action, str) and slot_action.upper() == "B":
            continue

        if slot_action == 1:
            # Delete all pending rows for this slot
            print(
                f"\n  Delete all {len(g['pending_qids'])} pending row(s) for "
                f"{g['building']} slot {g['slot_position']}? [Y/n]"
            )
            if read(values=["Y", "y", "N", "n", ""], default="Y").lower() != "n":
                for qid in g["pending_qids"]:
                    csv_delete(session, qid)
                print(f"  Deleted {len(g['pending_qids'])} row(s).")
            enter()

        elif slot_action == 2:
            # Trim: keep rows up to the new target, delete the rest
            print(
                f"\n  Current target: lv {g['max_level']}. "
                f"Trim to level (must be ≥ {g['next_level']} and < {g['max_level']}):"
            )
            try:
                new_max = int(read(min=g["next_level"], max=g["max_level"] - 1))
            except (TypeError, ValueError):
                continue
            rows = csv_load(session)
            # Pending rows for this slot sorted by target_level
            slot_pending = sorted(
                [r for r in rows
                 if r["city_id"] == g["city_id"]
                 and r["slot_position"] == g["slot_position"]
                 and r["status"] == "pending"],
                key=lambda x: x["target_level"],
            )
            to_del = [r["queue_id"] for r in slot_pending if r["target_level"] > new_max]
            if not to_del:
                print("  Nothing to trim.")
                enter()
                continue
            print(f"  Deleting {len(to_del)} row(s) above lv {new_max}.")
            for qid in to_del:
                csv_delete(session, qid)
            print(f"  Done. Slot now targets lv {g['next_level']}→{new_max}.")
            enter()

        elif slot_action == 3:
            # Modify a single row — show the rows in this slot first
            rows = csv_load(session)
            slot_rows = sorted(
                [r for r in rows
                 if r["city_id"] == g["city_id"]
                 and r["slot_position"] == g["slot_position"]],
                key=lambda x: x["queue_id"],
            )
            print(f"\n  Rows for this slot:\n")
            for r in slot_rows:
                print(
                    f"    QID {r['queue_id']:>5}  → lv {r['target_level']:>3}  "
                    f"{r['status']:<10}  mode={r['transport_mode']}"
                )
            print("\n  Enter queue_id to modify:")
            try:
                qid = int(read())
            except (TypeError, ValueError):
                continue
            row = next((r for r in slot_rows if r["queue_id"] == qid), None)
            if row is None:
                print(f"  queue_id {qid} not found in this slot.")
                enter()
                continue
            print(
                f"\n  Modifying row {qid}: {row['building']} → lv {row['target_level']}\n"
            )
            print("  (1) target_level")
            print("  (2) transport_mode")
            print("  (3) notes")
            print("  (B) Cancel")
            field_choice = read(values=["1", "2", "3", "B", "b"])
            if field_choice.upper() == "B":
                continue
            if field_choice == "1":
                print(f"  Current target_level: {row['target_level']}")
                print("  New target_level:")
                try:
                    new_lv = int(read(min=1, max=HARD_LEVEL_CAP))
                except (TypeError, ValueError):
                    continue
                csv_update(session, qid, target_level=new_lv)
                print(f"  Updated target_level to {new_lv}.")
            elif field_choice == "2":
                print(
                    f"  Current transport_mode: {row['transport_mode']}\n"
                    "  (1) jit   (2) bulk   (3) none"
                )
                tm_choice = read(min=1, max=3)
                new_tm = {1: "jit", 2: "bulk", 3: "none"}[tm_choice]
                csv_update(session, qid, transport_mode=new_tm)
                print(f"  Updated transport_mode to {new_tm}.")
            elif field_choice == "3":
                print(f"  Current notes: {row.get('notes', '')}")
                print("  New notes (Enter to clear):")
                new_notes = read(empty=True)
                csv_update(session, qid, notes=new_notes)
                print("  Updated notes.")
            enter()


# ---------------------------------------------------------------------------
# Construction-module banner + notification config
# (mirrors RTM's get_notification_config so the user sees the right header)
# ---------------------------------------------------------------------------

def _print_module_banner(page_title=None):
    title = f"        CONSTRUCTION MANAGER v{__version__}"
    print("\n")
    print("╔" + "═" * 58 + "╗")
    print("║" + title.ljust(58) + "║")
    print("╚" + "═" * 58 + "╝")
    if page_title:
        print(f"\n{page_title}")
        print("─" * 58)
    print("")


def _get_notification_config(telegram_enabled, event):
    """Construction-module-branded notification prompt.

    Same return shape as RTM's get_notification_config:
      {"level": "partial"|"all"|"none", "telegram": bool} or None on back-out.
    """
    if telegram_enabled is False:
        _print_module_banner()
        print("Telegram notifications are not configured.")
        print("Do you want to continue without notifications? [Y/n]")
        rta = read(values=["y", "Y", "n", "N", ""])
        if rta.lower() == "n":
            event.set()
            return None
        return {"level": "none", "telegram": False}

    _print_module_banner("Notification Preferences")
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


# ---------------------------------------------------------------------------
# Worker status helpers
# ---------------------------------------------------------------------------

def _worker_lock_is_fresh(wlock):
    """True if *wlock* is held by a live process and recently heartbeaten."""
    try:
        with open(wlock, "r") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return False
    try:
        held_at = float(data.get("timestamp", 0) or 0)
    except (TypeError, ValueError):
        return False
    if time.time() - held_at > WORKER_LOCK_STALE_SECONDS:
        return False
    # A crashed worker's lock is dead immediately — don't report RUNNING (or
    # block a restart) for the whole staleness window.
    try:
        pid = int(data.get("pid"))
    except (TypeError, ValueError):
        return True  # no usable pid; trust the timestamp
    try:
        return psutil.pid_exists(pid)
    except Exception:
        return True


def _is_worker_running(session):
    """True if a fresh (non-stale) worker lock exists for this account."""
    return _worker_lock_is_fresh(worker_lock_path(session))


def _worker_lock_heartbeat(wlock, token):
    """Refresh the worker lock's timestamp so it never looks stale while alive.

    The worker sleeps for hours between build timers, so without this the lock
    aged past WORKER_LOCK_STALE_SECONDS and a second worker could be started
    for the same account — two workers issuing duplicate builds and shipments.
    """
    try:
        with open(wlock, "r") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = None

    if data is not None:
        owned = (data.get("token") == token
                 or data.get("pid") == os.getpid())
        if not owned:
            return  # another worker owns it now; don't stomp on it

    payload = json.dumps({
        "pid": os.getpid(),
        "timestamp": time.time(),
        "token": token,
    })
    tmp = f"{wlock}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w") as f:
            f.write(payload)
        os.replace(tmp, wlock)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


def _worker_status_line(session):
    """Return a coloured one-line worker status string."""
    if _is_worker_running(session):
        status = f"{bcolors.GREEN}RUNNING{bcolors.ENDC}"
    else:
        status = f"{bcolors.WARNING}STOPPED{bcolors.ENDC}"
    return f"Worker: {status}"


# ---------------------------------------------------------------------------
# Cancel all pending (x on main menu)
# ---------------------------------------------------------------------------

def _cancel_all_pending(session):
    """Delete every pending row from the CSV across all cities."""
    rows = csv_load(session)
    pending = [r for r in rows if r["status"] == "pending"]
    if not pending:
        print("  No pending rows to cancel.")
        enter()
        return
    print(f"\n  This will delete all {len(pending)} pending row(s) across all cities.")
    print(f"  Running/shipping rows are unaffected.")
    print(f"  Type {bcolors.BOLD}yes{bcolors.ENDC} to confirm:")
    confirm = read(msg="  > ", empty=True, additionalValues=["yes", "Yes", "YES"])
    if str(confirm).lower() != "yes":
        print("  Cancelled.")
        enter()
        return
    for r in pending:
        csv_delete(session, r["queue_id"])
    print(f"  Deleted {len(pending)} pending row(s).")
    enter()


# ---------------------------------------------------------------------------
# Stop worker
# ---------------------------------------------------------------------------

def _stop_worker(session):
    flag = stop_flag_path(session)
    wlock = worker_lock_path(session)
    if not os.path.exists(wlock):
        print("  No construction worker appears to be running.")
        enter()
        return
    pathlib.Path(flag).touch()
    print(
        f"  Stop flag written. The scheduler will exit within "
        f"{TICK_BUDGET_SECONDS} s after finishing any active shipments."
    )
    enter()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def constructionManager(session, event, stdin_fd, predetermined_input):
    """Entry point called by the ikabot menu.

    Parameters
    ----------
    session : ikabot.web.session.Session
    event : multiprocessing.Event
    stdin_fd : int
    predetermined_input : multiprocessing.managers.SyncManager.list
    """
    sys.stdin = os.fdopen(stdin_fd)
    config.predetermined_input = predetermined_input
    interactive = predetermined_input is None or len(predetermined_input) == 0

    if not migrate_legacy_account_files(session):
        enter()
        event.set()
        return

    if not enforce_schema_or_abort(session):
        enter()
        event.set()
        return

    try:
        while True:
            banner()
            pending = csv_count_pending(session)
            n_cities = csv_count_cities_with_work(session)
            print(
                f"Construction Manager v{__version__}\n"
                f"  CSV: {csv_path(session)}\n"
                f"  {_worker_status_line(session)}\n"
                f"  {pending} pending row(s) across {n_cities} city/cities\n"
            )
            print(f"  {bcolors.BOLD}(s){bcolors.ENDC} Start worker   "
                  f"{bcolors.BOLD}(o){bcolors.ENDC} Stop worker   "
                  f"{bcolors.BOLD}(x){bcolors.ENDC} Cancel all pending\n")
            print("(1) Add construction(s) to queue        [interactive only]")
            print("(2) View queue")
            print("(3) Edit queue (modify / delete / reorder) [interactive only]")
            print("(') Back")
            choice = read(min=1, max=3,
                          additionalValues=["'", "s", "S", "o", "O", "x", "X"])

            if isinstance(choice, str):
                letter = choice.lower()
                if letter == "'":
                    break
                if letter == "s":
                    _activate_worker(session, event)
                    return
                if letter == "o":
                    _stop_worker(session)
                elif letter == "x":
                    _cancel_all_pending(session)
                continue

            if choice in (1, 3) and not interactive:
                print(
                    f"  Option {choice} requires an interactive session "
                    f"(predetermined_input is set). Skipping."
                )
                enter()
                continue

            if choice == 1:
                _add_to_queue(session)
            elif choice == 2:
                _view_queue(session)
            elif choice == 3:
                _edit_queue(session)
    except KeyboardInterrupt:
        pass
    finally:
        event.set()

# === END CHUNK 4 OF 6 — chunk 5 of 6 inserted below this line ===

# ---------------------------------------------------------------------------
# Chunk 5 of 6: worker helpers
# (WORKER_PREFS, post_build_or_upgrade, supplier list, spawn_shipment,
#  state helpers, shortage event relay)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Module-level worker state — written by parent before fork, read by child
# ---------------------------------------------------------------------------

WORKER_PREFS = {}          # {"notif_config": ..., "log_path": ...}

# Thread-safe queue for daemon shipment threads to post shortage events back
# to the scheduler thread without touching city_state directly.
shortage_event_queue = queue.Queue()


# ---------------------------------------------------------------------------
# Build / upgrade POST  (plan §10)
# ---------------------------------------------------------------------------

def post_build_or_upgrade(session, city, row):
    """Issue the build or upgrade POST to the game server.

    Does NOT verify success — the caller re-fetches the city after the
    BUILD_POST_VERIFY_DELAY_SECONDS settle period to confirm isBusy.

    NOTE: as of Ikariam v17 the URL shape has been simplified.
    OLD: action=CityScreen&function=upgradeBuilding&...&ajax=1
    NEW: action=UpgradeExistingBuilding&...
    The old action no longer exists and the server silently rejects with
    a custom/reload-to-city response. Confirmed working via the
    constructionDiagnostic Variant H run.
    """
    cid = city["id"]
    pos = int(row["slot_position"])

    if row["action"] == "build":
        # TODO: the new-build URL has likely changed too (legacy used
        #   action=CityScreen&function=build). Capture a real "Build"
        #   click via the ikabot webserver to confirm the new action
        #   name (probably action=BuildNewBuilding or similar).
        params = {
            "action": "CityScreen",
            "function": "build",
            "cityId": cid,
            "position": pos,
            "building": row["building_id"],
            "backgroundView": "city",
            "currentCityId": cid,
            "templateView": "buildingGround",
            "actionRequest": actionRequest,
            "ajax": "1",
        }
        resp = session.post(params=params, noIndex=True)
    else:
        # Upgrade: pass the slot's *current* level — the game increments
        # to current+1.  If isBusy, level already reflects the in-progress
        # target so we use it directly.
        slot = city["position"][pos]
        current_lv = slot.get("level", 0)
        params = {
            "action": "UpgradeExistingBuilding",
            "actionRequest": actionRequest,
            "cityId": cid,
            "position": pos,
            "level": current_lv,
        }
        resp = session.post(params=params)
    try:
        msg = json.loads(resp, strict=False)[3][1][0]["text"]
        if msg:
            sendToBotDebug(
                session,
                f"build/upgrade POST response (city {cid} slot {pos}): {msg}",
                True,
            )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Supplier list with short-lived ID cache  (plan §8)
# ---------------------------------------------------------------------------

_supplier_id_cache = {}   # {dest_city_id: (timestamp, [city_id_str, ...])}
_supplier_cache_lock = threading.Lock()


def _get_supplier_ids(session, dest_city_id):
    now = time.time()
    with _supplier_cache_lock:
        cached = _supplier_id_cache.get(dest_city_id)
        if cached and now - cached[0] < SUPPLIER_LIST_TTL_SECONDS:
            return cached[1]
    html = session.get()
    ids = re.findall(r'<option value="(\d+)" class="cityowntown"', html)
    ids = [cid for cid in ids if str(cid) != str(dest_city_id)]
    with _supplier_cache_lock:
        _supplier_id_cache[dest_city_id] = (now, ids)
    return ids


def build_supplier_list(session, dest_city_id):
    """Return list of freshly-fetched city dicts for all cities except dest."""
    supplier_ids = _get_supplier_ids(session, dest_city_id)
    suppliers = []
    for cid in supplier_ids:
        try:
            suppliers.append(fetch_city(session, cid))
        except Exception:
            continue
    return suppliers


# ---------------------------------------------------------------------------
# Auto-transport thread  (plan §8)
# ---------------------------------------------------------------------------

def spawn_shipment(session, city_id, row, cost):
    """Launch a non-daemon thread that ships the needed resources to city_id.

    Thread is daemon=False so the child process waits for it to finish before
    exiting — a shipment is never aborted mid-flight (plan §12).
    Returns the Thread, or None when transport_mode is 'none'.
    """
    mode = row["transport_mode"]
    if mode == "none":
        return None

    # Snapshot queue_id so the closure doesn't race with row mutations.
    queue_id = int(row["queue_id"])

    def run():
        try:
            rtm = load_rtm()
            city = fetch_city(session, city_id)
            stock = city.get("availableResources", [0] * 5)

            if mode == "jit":
                need = [max(0, c - s) for c, s in zip(cost, stock)]
            else:  # bulk: ship sum of all pending rows' shortfalls
                all_rows = csv_load(session)
                pending = [
                    r for r in all_rows
                    if r["city_id"] == city_id
                    and r["status"] in ("pending", "shipping")
                ]
                total = [0] * 5
                for r in pending:
                    for i, k in enumerate(RESOURCE_COLS):
                        total[i] += int(r.get(k, 0) or 0)
                need = [max(0, t - s) for t, s in zip(total, stock)]

            # Don't over-ship past free warehouse space.
            free = city.get("freeSpaceForResources", [0] * 5)
            need = [min(n, f) for n, f in zip(need, free)]
            if not any(need):
                return

            suppliers = build_supplier_list(session, city_id)
            island    = fetch_island(session, city)
            routes    = rtm.allocate_from_suppliers(need, suppliers, city, island)

            if routes is None:
                csv_update(
                    session, queue_id,
                    status="skipped",
                    notes="insufficient supply across all cities",
                )
                also = csv_skip_rest_of_slot(
                    session, city_id, row["slot_position"], queue_id,
                    "lower-level row skipped — insufficient supply",
                )
                shortage_event_queue.put({
                    "city_id": city_id,
                    "row_id":  queue_id,
                    "msg": (
                        f"City {city.get('cityName', city_id)}: "
                        f"total shortage — row {queue_id} skipped"
                        + (f"; also skipped same-slot row(s) {also}"
                           if also else "")
                    ),
                })
                return

            island_coords = f"[{island['x']}:{island['y']}]"
            dest_player   = city.get("ownerName", "")
            for route in routes:
                rtm.send_shipment(
                    session,
                    route,
                    useFreighters=False,
                    notif_config=WORKER_PREFS.get("notif_config"),
                    log_path=WORKER_PREFS.get("log_path"),
                    mode_name="construction",
                    dest_island_coords=island_coords,
                    dest_player=dest_player,
                    max_lock_retries=3,
                )
        except Exception:
            try:
                sendToBotDebug(
                    session,
                    f"construction shipment thread error:\n{traceback.format_exc()}",
                    True,
                )
            except Exception:
                pass

    t = threading.Thread(target=run, daemon=False)
    t.start()
    return t


# ---------------------------------------------------------------------------
# Small state-machine helpers
# ---------------------------------------------------------------------------

def fresh_state():
    return {
        "phase":                  "idle",
        "current_queue_id":       None,
        "expected_finish":        None,
        "ship_deadline":          None,
        "shipment_thread":        None,
        "next_check":             0,
        "last_shortage_notify_ts": None,
    }


def stock_covers(city, cost):
    """True if city's available resources already cover every element of cost."""
    avail = city.get("availableResources", [0] * 5)
    return all(avail[i] >= cost[i] for i in range(5))


def first_busy_slot(city):
    """Return the first position dict that is currently busy, or None."""
    for slot in city.get("position", []):
        if slot.get("isBusy") and "completed" in slot:
            return slot
    return None


def next_pending_row(rows):
    """Return the pending row with the smallest queue_id, or None."""
    pending = [r for r in rows if r["status"] == "pending"]
    if not pending:
        return None
    return min(pending, key=lambda r: r["queue_id"])


def find_row(rows, queue_id):
    """Return the row dict matching queue_id, or None."""
    return next((r for r in rows if r["queue_id"] == int(queue_id)), None)


def reconcile_rows_with_city(session, city, rows):
    """Drop/skip pending rows the city has already moved past.

    Builds done by hand in the game don't touch the queue, so a slot can sit
    above the level a pending row targets.  Each row is one upgrade — not
    "reach level N" — so leaving those rows in place would build past the
    level the user asked for (queue 5→10, two manual builds, and the last two
    rows would push the slot to 12).  Dropping them re-aligns the queue so the
    next row is always exactly current level + 1, which also keeps the cost
    lookup and the shipped amounts correct.

    A slot that now holds a different building is marked skipped rather than
    deleted, since that's a layout change the user probably wants to see.

    Returns (surviving_rows, dropped_rows, skipped_rows).
    """
    positions = city.get("position", [])
    survivors, dropped, skipped = [], [], []

    for r in rows:
        if r["status"] != "pending":
            survivors.append(r)
            continue

        slot_pos = int(r["slot_position"])
        if slot_pos >= len(positions):
            survivors.append(r)   # bounds are reported downstream
            continue

        slot          = positions[slot_pos]
        slot_building = str(slot.get("building", "") or "")
        row_building  = str(r["building"] or "")
        try:
            slot_level = int(slot.get("level", 0) or 0)
        except (TypeError, ValueError):
            slot_level = 0

        if (slot_building not in ("", "empty")
                and slot_building.lower() != row_building.lower()):
            csv_update(
                session, r["queue_id"],
                status="skipped",
                notes=f"slot now holds {slot_building}, not {row_building}",
            )
            skipped.append(r)
            continue

        # Only compare levels once we know it's the same building — an empty
        # slot reports level 0, so a pending "build" row survives correctly.
        if (slot_building.lower() == row_building.lower()
                and int(r["target_level"]) <= slot_level):
            csv_delete(session, r["queue_id"])
            dropped.append(r)
            continue

        survivors.append(r)

    return survivors, dropped, skipped


def cost_tuple(row):
    """Return the row's resource costs as a plain list of ints."""
    return [int(row.get(k, 0) or 0) for k in RESOURCE_COLS]


# ---------------------------------------------------------------------------
# Telegram rate-limiting + shortage event relay  (plan §7)
# ---------------------------------------------------------------------------

def maybe_notify_shortage(session, city_id, st, message, now):
    """Send a Telegram shortage alert at most once per cooldown window."""
    last = st.get("last_shortage_notify_ts") or 0
    if now - last >= SHORTAGE_COOLDOWN_SECONDS:
        try:
            sendToBot(session, message)
        except Exception:
            pass
        st["last_shortage_notify_ts"] = now
    st["phase"]      = "skip-cooldown"
    st["next_check"] = now + SHORTAGE_COOLDOWN_SECONDS


def drain_shortage_events(session, city_state, now):
    """Pop all queued shortage events from daemon threads and apply cooldowns.

    Daemon shipment threads must NOT touch city_state directly — they post
    events here and the scheduler thread processes them on the next tick.
    """
    while True:
        try:
            ev = shortage_event_queue.get_nowait()
        except queue.Empty:
            break
        cid = ev["city_id"]
        st  = city_state.setdefault(cid, fresh_state())
        maybe_notify_shortage(session, cid, st, ev["msg"], now)

# === END CHUNK 5 OF 6 — chunk 6 of 6 inserted below this line ===

# ---------------------------------------------------------------------------
# Chunk 6 of 6: scheduler + worker activation
# (issue_and_confirm, service_city, reconcile_on_startup, scheduler_loop,
#  _activate_worker)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# issue_and_confirm  (plan §7)
# ---------------------------------------------------------------------------

def issue_and_confirm(session, city_id, st, row, city, cost):
    """POST the build/upgrade, settle, verify isBusy, schedule next wake-up.

    On verified start: set phase=running, expected_finish=eta, kick a JIT
    prefetch shipment for the next pending row if applicable.
    On verify failure: mark row skipped and enter skip-cooldown.
    """
    qid = int(row["queue_id"])
    csv_update(session, qid, status="running")

    try:
        post_build_or_upgrade(session, city, row)
    except Exception:
        sendToBotDebug(
            session,
            f"construction POST error city {city_id} row {qid}:\n{traceback.format_exc()}",
            True,
        )
        csv_update(session, qid, status="skipped", notes="POST exception")
        st["phase"]      = "skip-cooldown"
        st["next_check"] = int(time.time()) + SHIP_RETRY_SECONDS
        return

    # Server-side propagation: give the game a moment to flip isBusy and
    # write the slot's `completed` timestamp (plan §10).
    time.sleep(BUILD_POST_VERIFY_DELAY_SECONDS)

    try:
        city = fetch_city(session, city_id)
    except Exception:
        st["phase"]      = "skip-cooldown"
        st["next_check"] = int(time.time()) + SHIP_RETRY_SECONDS
        return

    positions = city.get("position", [])
    slot_pos = int(row["slot_position"])
    if slot_pos >= len(positions):
        csv_update(session, qid, status="skipped",
                   notes=f"slot {slot_pos} out of range after POST")
        st["phase"]      = "skip-cooldown"
        st["next_check"] = int(time.time()) + SHIP_RETRY_SECONDS
        return
    slot = positions[slot_pos]
    if not slot.get("isBusy") or "completed" not in slot:
        csv_update(
            session, qid,
            status="skipped",
            notes="POST did not start construction",
        )
        try:
            sendToBot(
                session,
                f"City {city.get('cityName', city_id)} slot "
                f"{row['slot_position']}: build did not start",
            )
        except Exception:
            pass
        st["phase"]      = "skip-cooldown"
        st["next_check"] = int(time.time()) + SHIP_RETRY_SECONDS
        return

    eta = int(slot["completed"])
    csv_update(session, qid, status="running", expected_finish=eta)
    st["phase"]            = "running"
    st["current_queue_id"] = qid
    st["expected_finish"]  = eta
    st["next_check"]       = eta + ETA_FUDGE_SECONDS

    # JIT prefetch: launch a shipment for the next pending row in this city
    # so resources are warming while the current build runs.
    if row["transport_mode"] == "jit":
        all_rows = csv_load(session)
        next_row = None
        for r in sorted(all_rows, key=lambda x: x["queue_id"]):
            if (r["city_id"] == city_id
                    and r["queue_id"] > qid
                    and r["status"] == "pending"):
                next_row = r
                break
        if next_row is not None:
            t = spawn_shipment(session, city_id, next_row, cost_tuple(next_row))
            if t is not None:
                st["shipment_thread"] = t


# ---------------------------------------------------------------------------
# service_city — per-city state machine  (plan §7)
# ---------------------------------------------------------------------------

def service_city(session, city_id, st, rows, stop_event):
    now = int(time.time())

    # ---- IDLE ----------------------------------------------------------
    if st["phase"] == "idle":
        if not rows:
            st["next_check"] = now + TICK_BUDGET_SECONDS
            return
        row = next_pending_row(rows)
        if row is None:
            st["next_check"] = now + TICK_BUDGET_SECONDS
            return

        try:
            city = fetch_city(session, city_id)
        except Exception:
            st["next_check"] = now + SHIP_RETRY_SECONDS
            return

        # Catch up on anything built by hand since the queue was made, so the
        # next row is always current level + 1.
        rows, dropped, resolved_skips = reconcile_rows_with_city(
            session, city, rows
        )
        if dropped or resolved_skips:
            try:
                sendToBotDebug(
                    session,
                    f"city {city_id}: queue re-aligned with in-game state — "
                    f"dropped rows {[r['queue_id'] for r in dropped]} "
                    f"(already built), skipped rows "
                    f"{[r['queue_id'] for r in resolved_skips]} "
                    f"(building changed)",
                    True,
                )
            except Exception:
                pass
            row = next_pending_row(rows)
            if row is None:
                st["next_check"] = now + TICK_BUDGET_SECONDS
                return

        # If ANY slot in this city is busy, the game won't accept a new build.
        busy = first_busy_slot(city)
        if busy is not None and busy["position"] != int(row["slot_position"]):
            st["next_check"] = int(busy["completed"]) + ETA_FUDGE_SECONDS
            return
        if busy is not None and busy["position"] == int(row["slot_position"]):
            # The slot we want is already busy — adopt it as our running row.
            eta = int(busy["completed"])
            csv_update(
                session, row["queue_id"],
                status="running", expected_finish=eta,
            )
            st["phase"]            = "running"
            st["current_queue_id"] = int(row["queue_id"])
            st["expected_finish"]  = eta
            st["next_check"]       = eta + ETA_FUDGE_SECONDS
            return

        # Bounds check: city layout may differ from what was queued (Bug 8).
        positions = city.get("position", [])
        slot_pos = int(row["slot_position"])
        if slot_pos >= len(positions):
            csv_update(
                session, row["queue_id"],
                status="skipped",
                notes=f"slot {slot_pos} out of range for city {city_id}",
            )
            st["phase"]      = "skip-cooldown"
            st["next_check"] = now + SHIP_RETRY_SECONDS
            return

        # Pre-execution cost recompute (plan §2b).
        all_costs = fetch_costs_for_building(session, city, row["building"])
        cost = all_costs.get(int(row["target_level"])) if all_costs else None
        if not cost:
            csv_update(
                session, row["queue_id"],
                status="skipped",
                notes=f"no ikipedia cost row for level {row['target_level']}",
            )
            try:
                sendToBot(
                    session,
                    f"City {city.get('cityName', city_id)} slot "
                    f"{row['slot_position']}: cannot determine cost for "
                    f"{row['building']} lv {row['target_level']}",
                )
            except Exception:
                pass
            st["phase"]      = "skip-cooldown"
            st["next_check"] = now + SHIP_RETRY_SECONDS
            return
        # csv_update returns the saved row, so the fresh costs come back
        # without a second lock cycle.
        row = csv_update(
            session, row["queue_id"],
            wood=cost[0], wine=cost[1], marble=cost[2],
            crystal=cost[3], sulphur=cost[4],
        ) or row

        if row["transport_mode"] == "none" or stock_covers(city, cost):
            # canUpgrade=False means the game won't accept the POST yet
            # (insufficient citizens, wine/happiness, or game-side resource check).
            # Retry after a short wait rather than wasting a POST.
            if positions[slot_pos].get("canUpgrade") is False:
                st["next_check"] = now + SHIP_RETRY_SECONDS
                return
            issue_and_confirm(session, city_id, st, row, city, cost)
            return

        # Need to ship — kick a daemon thread, sit in `shipping` phase.
        csv_update(session, row["queue_id"], status="shipping")
        st["phase"]            = "shipping"
        st["current_queue_id"] = int(row["queue_id"])
        st["ship_deadline"]    = now + SHIP_TIMEOUT_SECONDS
        st["next_check"]       = now + SHIP_RETRY_SECONDS
        st["shipment_thread"]  = spawn_shipment(session, city_id, row, cost)
        return

    # ---- SHIPPING ------------------------------------------------------
    if st["phase"] == "shipping":
        try:
            city = fetch_city(session, city_id)
        except Exception:
            st["next_check"] = now + SHIP_RETRY_SECONDS
            return
        row = find_row(rows, st["current_queue_id"])
        if row is None:
            # Row vanished (deleted from CSV mid-ship). Reset to idle.
            st["phase"]            = "idle"
            st["current_queue_id"] = None
            st["next_check"]       = now
            return
        cost = cost_tuple(row)
        if stock_covers(city, cost):
            issue_and_confirm(session, city_id, st, row, city, cost)
            return
        if now >= (st.get("ship_deadline") or 0):
            csv_update(
                session, row["queue_id"],
                status="skipped",
                notes="resources did not arrive in time",
            )
            also = csv_skip_rest_of_slot(
                session, city_id, row["slot_position"], row["queue_id"],
                "lower-level row skipped — shipment timeout",
            )
            try:
                sendToBot(
                    session,
                    f"City {city.get('cityName', city_id)}: shipment timeout — "
                    f"row {row['queue_id']} skipped"
                    + (f"; also skipped same-slot row(s) {also}"
                       if also else ""),
                )
            except Exception:
                pass
            st["phase"]      = "skip-cooldown"
            st["next_check"] = now + SHIP_RETRY_SECONDS
            return
        st["next_check"] = now + SHIP_RETRY_SECONDS
        return

    # ---- RUNNING -------------------------------------------------------
    if st["phase"] == "running":
        # The scheduler has slept until expected_finish + ETA_FUDGE so the
        # server has had time to commit completion. Verify and advance.
        try:
            city = fetch_city(session, city_id)
        except Exception:
            st["next_check"] = now + SHIP_RETRY_SECONDS
            return
        row = find_row(rows, st["current_queue_id"])
        if row is None:
            # Edge case: row was deleted while running. Walk away.
            st["phase"]            = "idle"
            st["current_queue_id"] = None
            st["expected_finish"]  = None
            st["next_check"]       = now
            return
        positions = city.get("position", [])
        slot_pos = int(row["slot_position"])
        if slot_pos >= len(positions):
            # City layout changed while running — reset to idle.
            st["phase"]            = "idle"
            st["current_queue_id"] = None
            st["expected_finish"]  = None
            st["next_check"]       = now
            return
        slot = positions[slot_pos]
        if slot.get("isBusy") and "completed" in slot:
            # Still running — an in-game event extended the timer (pirate
            # raid, attack pause, etc).  Adopt the new ETA and sleep again.
            new_eta = int(slot["completed"])
            csv_update(
                session, st["current_queue_id"],
                expected_finish=new_eta,
                notes="eta extended by in-game event",
            )
            st["expected_finish"] = new_eta
            st["next_check"]      = new_eta + ETA_FUDGE_SECONDS
            return
        # Slot is no longer busy → row is complete. Delete and re-enter idle.
        csv_delete(session, st["current_queue_id"])
        st["phase"]            = "idle"
        st["current_queue_id"] = None
        st["expected_finish"]  = None
        st["next_check"]       = now
        return

    # ---- SKIP-COOLDOWN -------------------------------------------------
    if st["phase"] == "skip-cooldown":
        # Cooldown duration was set by whoever entered this phase; once
        # next_check has elapsed we reset to idle and try the next pending row.
        st["phase"]      = "idle"
        st["next_check"] = now
        return


# ---------------------------------------------------------------------------
# reconcile_on_startup  (plan §7a)
# ---------------------------------------------------------------------------

def reconcile_on_startup(session, city_state):
    """Walk every city with CSV rows once at boot to recover from a crash."""
    rows = csv_load(session)
    by_city = {}
    for r in rows:
        by_city.setdefault(r["city_id"], []).append(r)

    now = int(time.time())
    for cid, city_rows in by_city.items():
        st = city_state.setdefault(cid, fresh_state())
        try:
            city = fetch_city(session, cid)
        except Exception:
            st["next_check"] = now + SHIP_RETRY_SECONDS
            continue

        # Same catch-up as the idle phase: a restart may follow manual builds.
        city_rows, _dropped, _skipped = reconcile_rows_with_city(
            session, city, city_rows
        )

        running = next(
            (r for r in city_rows if r["status"] == "running"),
            None,
        )
        if running is not None:
            positions = city.get("position", [])
            slot_pos = int(running["slot_position"])
            if slot_pos >= len(positions):
                csv_delete(session, running["queue_id"])
                running = None
            else:
                slot = positions[slot_pos]
        if running is not None:
            if slot.get("isBusy") and "completed" in slot:
                eta = int(slot["completed"])
                csv_update(session, running["queue_id"], expected_finish=eta)
                st["phase"]            = "running"
                st["current_queue_id"] = int(running["queue_id"])
                st["expected_finish"]  = eta
                st["next_check"]       = eta + ETA_FUDGE_SECONDS
                continue
            else:
                # Crashed mid-build but slot is no longer busy → completed.
                csv_delete(session, running["queue_id"])
                # Fall through to busy-slot / idle handling.

        busy = first_busy_slot(city)
        if busy is not None:
            # Pre-existing in-game build (or another tool's). Wait it out
            # before attempting our own pending row for this city.
            st["phase"]      = "idle"
            st["next_check"] = int(busy["completed"]) + ETA_FUDGE_SECONDS
        else:
            st["phase"]      = "idle"
            st["next_check"] = now


# ---------------------------------------------------------------------------
# scheduler_loop  (plan §7)
# ---------------------------------------------------------------------------

def scheduler_loop(session, stop_event, worker_token=None):
    city_state = {}
    # Stamp ownership with this process before the (possibly slow) startup
    # reconcile, so the lock reflects the live worker from the outset.
    if worker_token is not None:
        _worker_lock_heartbeat(worker_lock_path(session), worker_token)
    try:
        reconcile_on_startup(session, city_state)
    except Exception:
        sendToBotDebug(
            session,
            f"reconcile_on_startup error:\n{traceback.format_exc()}",
            True,
        )

    while not stop_event.is_set():
        # Honour the menu's stop flag.
        if os.path.exists(stop_flag_path(session)):
            stop_event.set()
            break

        # Prove we're alive before doing any work: the lock must never look
        # stale while this process is running, or a second worker could start.
        if worker_token is not None:
            _worker_lock_heartbeat(worker_lock_path(session), worker_token)

        now = int(time.time())
        drain_shortage_events(session, city_state, now)

        # Hot-reload the CSV every tick so menu edits take effect immediately.
        # A lock-acquisition failure here must NOT kill the worker — log it,
        # skip this tick, and try again; the lock helpers break stale/orphaned
        # locks on their own within seconds.
        try:
            rows = csv_load(session)
        except RuntimeError:
            sendToBotDebug(
                session,
                "scheduler: CSV lock busy, skipping this tick:\n"
                + traceback.format_exc(),
                True,
            )
            stop_event.wait(15)
            continue
        rows_by_city = {}
        for r in rows:
            rows_by_city.setdefault(r["city_id"], []).append(r)

        all_cids = set(rows_by_city.keys()) | set(city_state.keys())
        for cid in sorted(all_cids):
            st = city_state.setdefault(cid, fresh_state())
            if st["next_check"] > now:
                continue
            try:
                service_city(session, cid, st, rows_by_city.get(cid, []), stop_event)
            except Exception:
                sendToBotDebug(
                    session,
                    f"scheduler service_city({cid}) crashed:\n{traceback.format_exc()}",
                    True,
                )
                # Push next_check out so we don't tight-loop on a persistent error.
                st["next_check"] = now + SHIP_RETRY_SECONDS

        # Sleep until the soonest next_check, capped at TICK_BUDGET_SECONDS.
        soonest = min(
            (s["next_check"] for s in city_state.values()),
            default=now + TICK_BUDGET_SECONDS,
        )
        sleep_for = max(1, min(TICK_BUDGET_SECONDS, soonest - int(time.time())))
        try:
            session.setStatus(
                f"Construction worker: sleeping {sleep_for}s "
                f"(next wake at {getDateTime(int(time.time()) + sleep_for)[8:]})"
            )
        except Exception:
            pass
        stop_event.wait(sleep_for)

    # ---- cleanup ------------------------------------------------------
    # Drain every active shipment thread (no timeout — never abort).
    for st in city_state.values():
        t = st.get("shipment_thread")
        if t is not None and t.is_alive():
            try:
                t.join()
            except Exception:
                pass

    # Remove stop flag (might not exist; ignore failure).
    try:
        os.remove(stop_flag_path(session))
    except OSError:
        pass

    # Release worker lock.
    _lock_release(worker_lock_path(session), worker_token)


# ---------------------------------------------------------------------------
# _activate_worker  (menu option 4)
# ---------------------------------------------------------------------------

def _activate_worker(session, event):
    """Capture interactive prefs, acquire worker lock, fork, run scheduler."""
    rtm = load_rtm()

    banner()
    print("Activate construction worker\n")

    try:
        telegram_enabled = checkTelegramData(session)
    except Exception:
        telegram_enabled = False

    notif_config = _get_notification_config(telegram_enabled, event)
    if notif_config is None:
        # User backed out; get_notification_config has already set the event.
        return

    log_path = rtm.get_log_path(session)

    # Refuse if another worker for this account is already running.
    wlock = worker_lock_path(session)
    worker_token = _new_lock_token()
    if not _lock_acquire(wlock, timeout=1,
                         stale_after=WORKER_LOCK_STALE_SECONDS,
                         token=worker_token):
        print(
            f"  {bcolors.RED}Another construction worker is already running "
            f"for this account.{bcolors.ENDC}"
        )
        print(f"  Lock file: {wlock}")
        print("  Press (o) in the menu to stop it, or remove the lock file "
              "if you're sure no worker is running.")
        enter()
        event.set()
        return

    WORKER_PREFS["notif_config"] = notif_config
    WORKER_PREFS["log_path"]     = log_path

    # Clear any leftover stop flag from a prior run.
    try:
        os.remove(stop_flag_path(session))
    except OSError:
        pass

    # Hand the menu back to the user; we keep running in the background.
    set_child_mode(session)
    event.set()

    info = (
        f"\nConstruction Manager worker\n"
        f"  CSV: {csv_path(session)}\n"
        f"  Stop via menu option (o) (touches {stop_flag_path(session)})\n"
    )
    setInfoSignal(session, info)

    stop_event = threading.Event()
    try:
        scheduler_loop(session, stop_event, worker_token=worker_token)
    except Exception:
        try:
            sendToBot(
                session,
                f"Construction worker crashed:\n{traceback.format_exc()}",
            )
        except Exception:
            pass
    finally:
        # scheduler_loop already releases the worker lock on graceful exit;
        # this is a defensive safety net if it crashed before that.
        _lock_release(wlock, worker_token)
        try:
            session.logout()
        except Exception:
            pass

# === END CHUNK 6 OF 6 — module complete ===
