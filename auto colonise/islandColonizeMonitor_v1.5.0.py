#! /usr/bin/env python3
# -*- coding: utf-8 -*-

MODULE_NAME  = "Island Colonize Monitor"
MODULE_ENTRY = "islandColonizeMonitor"

import csv
import datetime
import json
import math
import multiprocessing
import os
import random
import re
import sys
import time
import traceback

import ikabot.config as config
from ikabot.config import IKABOT_DATA_DIR, city_url, island_url
from ikabot.helpers.botComm import sendToBot, notificationDataIsValid
from ikabot.helpers.getJson import getCity, getIsland
from ikabot.helpers.gui import banner, enter
from ikabot.helpers.naval import getAvailableShips
from ikabot.helpers.pedirInfo import read, getIdsOfCities
from ikabot.helpers.process import set_child_mode, updateProcessList
from ikabot.helpers.signals import setInfoSignal
from ikabot.helpers.varios import wait, addThousandSeparator

try:
    from resourceReservationSystem import (
        reserve as rrs_reserve,
        release as rrs_release,
    )
    RRS_AVAILABLE = True
except ImportError:
    RRS_AVAILABLE = False

# ─── Constants ────────────────────────────────────────────────────────────────

_VERSION       = "1.5.0"
_MOD_TAG       = "IslandColonizeMonitor"

_BASE_CITIZENS = 40
_BASE_GOLD     = 9000
_BASE_WOOD     = 1250
_MIN_SHIPS     = 3
_SHIP_CAPACITY = 500
_MAX_EXTRA     = 2250

_PRIORITY_LABEL  = {1: "HIGH", 2: "MEDIUM", 3: "LOW"}
_TRADEGOOD_LABEL = {0: "Wood", 1: "Wine", 2: "Marble", 3: "Crystal", 4: "Sulfur"}

STATUS_ACTIVE    = "active"
STATUS_PAUSED    = "paused"
STATUS_COMPLETED = "completed"
STATUS_ERROR     = "error"

MONITOR_COLUMNS = [
    "monitor_id", "x", "y", "island_id", "island_name", "tradegood",
    "priority", "max_cities", "cities_sent",
    "origin_city_id", "origin_city_name",
    "accept_premium",
    "extra_wood", "extra_wine", "extra_marble", "extra_crystal", "extra_sulfur",
    "post_cooldown_mins",
    "status", "last_checked", "last_sent", "cooldown_until",
    "notes",
]

_INT_COLS = {
    "monitor_id", "x", "y", "island_id", "tradegood",
    "priority", "max_cities", "cities_sent",
    "origin_city_id", "accept_premium",
    "extra_wood", "extra_wine", "extra_marble", "extra_crystal", "extra_sulfur",
    "post_cooldown_mins",
}
_FLOAT_COLS = {"last_checked", "last_sent", "cooldown_until"}

_DEFAULT_SETTINGS = {
    "interval_minutes":   30,
    "jitter_type":        "small",
    "notif_level":        "colonize",
    "heartbeat_hours":    0,
    "post_cooldown_mins": 20,
}


# ─── ANSI colours ─────────────────────────────────────────────────────────────

def _ansi_supported():
    if os.name == "nt":
        try:
            import ctypes
            k32 = ctypes.windll.kernel32
            h = k32.GetStdHandle(-11)
            m = ctypes.c_ulong()
            if k32.GetConsoleMode(h, ctypes.byref(m)):
                k32.SetConsoleMode(h, m.value | 0x0004)
                return True
        except Exception:
            pass
        return bool(os.environ.get("WT_SESSION"))
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

_USE_C = _ansi_supported()

class C:
    R  = "\033[0m"    if _USE_C else ""
    B  = "\033[1m"    if _USE_C else ""
    D  = "\033[2m"    if _USE_C else ""
    CY = "\033[36m"   if _USE_C else ""
    GR = "\033[32m"   if _USE_C else ""
    YE = "\033[33m"   if _USE_C else ""
    RE = "\033[31m"   if _USE_C else ""
    H  = "\033[1;36m" if _USE_C else ""
    OK = "\033[1;32m" if _USE_C else ""
    WA = "\033[1;33m" if _USE_C else ""
    ER = "\033[1;31m" if _USE_C else ""
    HI = "\033[2;37m" if _USE_C else ""


# ─── File paths (per account) ─────────────────────────────────────────────────

def _safe(v):
    return re.sub(r"[^\w.-]", "_", str(v))

def _sfx(session):
    return f"{_safe(session.servidor)}_{_safe(session.username)}"

def _csv_path(session):
    return os.path.join(IKABOT_DATA_DIR, f"island_monitor_{_sfx(session)}.csv")

def _csv_lock_path(session):
    return os.path.join(IKABOT_DATA_DIR, f"island_monitor_{_sfx(session)}.lock")

def _worker_lock_path(session):
    return os.path.join(IKABOT_DATA_DIR, f"island_monitor_worker_{_sfx(session)}.lock")

def _stop_flag_path(session):
    return os.path.join(IKABOT_DATA_DIR, f"island_monitor_stop_{_sfx(session)}")

def _settings_path(session):
    return os.path.join(IKABOT_DATA_DIR, f"island_monitor_settings_{_sfx(session)}.json")


# ─── Settings ─────────────────────────────────────────────────────────────────

def _load_settings(session):
    try:
        with open(_settings_path(session), "r", encoding="utf-8") as f:
            saved = json.load(f)
        s = dict(_DEFAULT_SETTINGS)
        s.update(saved)
        return s
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(_DEFAULT_SETTINGS)

def _save_settings(session, settings):
    try:
        os.makedirs(IKABOT_DATA_DIR, exist_ok=True)
        with open(_settings_path(session), "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
    except Exception:
        pass


# ─── CSV lock ─────────────────────────────────────────────────────────────────

def _lock_acquire(path, timeout=30, stale_after=60):
    payload = json.dumps({"pid": os.getpid(), "ts": time.time()}).encode()
    start = time.time()
    while time.time() - start < timeout:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                os.write(fd, payload)
            finally:
                os.close(fd)
            return True
        except FileExistsError:
            try:
                with open(path, "r") as f:
                    d = json.load(f)
                if time.time() - d.get("ts", 0) > stale_after:
                    os.remove(path)
                    continue
            except Exception:
                try:
                    os.remove(path)
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(1)
    return False

def _lock_release(path):
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                d = json.load(f)
            if d.get("pid") == os.getpid():
                os.remove(path)
    except Exception:
        pass

class _CsvLock:
    def __init__(self, session):
        self._path = _csv_lock_path(session)
    def __enter__(self):
        if not _lock_acquire(self._path):
            raise RuntimeError("Could not acquire island monitor CSV lock")
        return self
    def __exit__(self, *_):
        _lock_release(self._path)


# ─── CSV CRUD ─────────────────────────────────────────────────────────────────

def _coerce_in(raw):
    row = dict(raw)
    for col in _INT_COLS:
        v = str(row.get(col, "")).strip()
        try:
            row[col] = int(v) if v else 0
        except (ValueError, TypeError):
            row[col] = 0
    for col in _FLOAT_COLS:
        v = str(row.get(col, "")).strip()
        try:
            row[col] = float(v) if v else 0.0
        except (ValueError, TypeError):
            row[col] = 0.0
    if row.get("status") not in (STATUS_ACTIVE, STATUS_PAUSED,
                                  STATUS_COMPLETED, STATUS_ERROR):
        row["status"] = STATUS_ACTIVE
    return row

def _coerce_out(row):
    return {col: str(row.get(col, "")) for col in MONITOR_COLUMNS}

def _write_rows(session, rows):
    path = _csv_path(session)
    tmp  = path + ".tmp"
    os.makedirs(IKABOT_DATA_DIR, exist_ok=True)
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=MONITOR_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(_coerce_out(r))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)

def csv_load(session):
    with _CsvLock(session):
        try:
            with open(_csv_path(session), "r", newline="", encoding="utf-8") as f:
                return [_coerce_in(r) for r in csv.DictReader(f)]
        except FileNotFoundError:
            return []

def csv_save_all(session, rows):
    with _CsvLock(session):
        _write_rows(session, rows)

def _csv_modify(session, fn):
    with _CsvLock(session):
        try:
            with open(_csv_path(session), "r", newline="", encoding="utf-8") as f:
                rows = [_coerce_in(r) for r in csv.DictReader(f)]
        except FileNotFoundError:
            rows = []
        fn(rows)
        _write_rows(session, rows)

def csv_append(session, row):
    _csv_modify(session, lambda rows: rows.append(row))

def csv_update(session, monitor_id, **fields):
    mid = int(monitor_id)
    def _apply(rows):
        for r in rows:
            if r["monitor_id"] == mid:
                r.update(fields)
                break
    _csv_modify(session, _apply)

def csv_delete(session, monitor_id):
    mid = int(monitor_id)
    _csv_modify(session, lambda rows: rows.__setitem__(
        slice(None), [r for r in rows if r["monitor_id"] != mid]
    ))

def _next_id(session):
    rows = csv_load(session)
    return max((r["monitor_id"] for r in rows), default=0) + 1


# ─── Worker lock / stop flag ──────────────────────────────────────────────────

def _worker_lock_write(session):
    os.makedirs(IKABOT_DATA_DIR, exist_ok=True)
    with open(_worker_lock_path(session), "w") as f:
        json.dump({"pid": os.getpid(), "ts": time.time()}, f)

def _worker_lock_refresh(session):
    _worker_lock_write(session)

def _worker_lock_release(session):
    path = _worker_lock_path(session)
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                d = json.load(f)
            if d.get("pid") == os.getpid():
                os.remove(path)
    except Exception:
        pass

def _is_worker_running(session):
    path = _worker_lock_path(session)
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r") as f:
            d = json.load(f)
        if time.time() - d.get("ts", 0) > 600:
            return False
        pid = d.get("pid", 0)
        if not pid:
            return False
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False
    except Exception:
        return False

def _set_stop_flag(session):
    os.makedirs(IKABOT_DATA_DIR, exist_ok=True)
    open(_stop_flag_path(session), "w").close()

def _stop_requested(session):
    return os.path.exists(_stop_flag_path(session))

def _clear_stop_flag(session):
    try:
        os.remove(_stop_flag_path(session))
    except FileNotFoundError:
        pass


# ─── Module banner ────────────────────────────────────────────────────────────

def _banner(page=None):
    bar  = "═" * 58
    rule = "─" * 58
    print("\n")
    print(f"{C.H}╔{bar}╗")
    print(f"║      ISLAND COLONIZE MONITOR v{_VERSION:<27}║")
    print(f"╚{bar}╝{C.R}")
    if page:
        print(f"\n{C.B}{page}{C.R}")
        print(f"{C.D}{rule}{C.R}")
    print("")


# ─── Game response parsing ────────────────────────────────────────────────────

def _parse_json(text):
    try:
        return json.loads(text, strict=False)
    except Exception:
        return None

def _extract_action_request(text):
    data = _parse_json(text)
    if isinstance(data, list):
        for item in data:
            if (isinstance(item, list) and len(item) >= 2
                    and item[0] == "updateGlobalData"):
                ar = (item[1] or {}).get("actionRequest", "")
                if ar:
                    return ar.strip()
    m = re.search(r'"actionRequest"\s*:\s*"([^"]+)"', text)
    return m.group(1) if m else ""

def _extract_background(text):
    data = _parse_json(text)
    if isinstance(data, list):
        for item in data:
            if (isinstance(item, list) and len(item) >= 2
                    and item[0] == "updateGlobalData"):
                bg = (item[1] or {}).get("backgroundData")
                if isinstance(bg, dict):
                    return bg
    return {}

def _colonize_succeeded(text):
    data = _parse_json(text)
    if not isinstance(data, list):
        return False
    for item in data:
        if (isinstance(item, list) and len(item) >= 2
                and item[0] == "provideFeedback"):
            feedbacks = item[1] if isinstance(item[1], list) else []
            for fb in feedbacks:
                if isinstance(fb, dict) and fb.get("type") == 10:
                    return True
    return False


# ─── Island slot detection ────────────────────────────────────────────────────

def _parse_slot_list(cities, accept_premium):
    """Parse a cities/slots list into (free, premium) position lists."""
    free, premium = [], []
    for i, c in enumerate(cities or []):
        if not isinstance(c, dict) or c.get("type") != "buildplace":
            continue
        bp  = (c.get("buildplace_type") or "").strip().lower()
        pos = c.get("position") or c.get("pos")
        if pos is None:
            pos = i + 1
        try:
            pos = int(pos)
        except Exception:
            continue
        if not 1 <= pos <= 17:
            continue
        if bp in ("", "normal"):
            free.append(pos)
        else:
            premium.append(pos)
    free    = sorted(set(free))
    premium = sorted(set(premium))
    if accept_premium:
        return sorted(set(free + premium)), premium
    return free, []

def _get_real_empty_slots(session, island_id, accept_premium):
    """Detect genuinely empty slots using the regular island view.

    The colonize view backgroundData lists ALL island positions as buildplaces
    regardless of whether they are already occupied by other players' cities.
    Using the island view instead returns actual occupancy, preventing false
    positives on full islands.
    """
    html   = session.get(island_url + str(island_id))
    island = getIsland(html)
    return _parse_slot_list(island.get("cities") or [], accept_premium)

def _load_colonize_view(session, island_id, position=1):
    """Fetch colonize view to retrieve the action request token for a POST.
    Not used for slot detection - use _get_real_empty_slots() for that."""
    url = (
        "view=colonize&islandId={}&position={}"
        "&backgroundView=island&currentIslandId={}"
        "&templateView=version&currentTab=tab_version&ajax=1"
    ).format(island_id, position, island_id)
    resp = session.get(url)
    return _extract_action_request(resp), _extract_background(resp)

def _get_island_from_coords(session, x, y):
    """Returns (island_id, island_dict) or (None, None) on failure."""
    try:
        html   = session.get(f"view=island&xcoord={x}&ycoord={y}")
        island = getIsland(html)
        iid    = int(island.get("id") or island.get("islandId") or 0)
        return (iid, island) if iid else (None, None)
    except Exception:
        return None, None


# ─── Ship / resource helpers ──────────────────────────────────────────────────

def _ships_required(extra_wood, extra_wine, extra_marble,
                    extra_crystal, extra_sulfur):
    total = (_BASE_WOOD + extra_wood + extra_wine
             + extra_marble + extra_crystal + extra_sulfur)
    return max(_MIN_SHIPS, math.ceil(total / _SHIP_CAPACITY))

def _get_ships(session, city_id):
    try:
        return getAvailableShips(session, city_id)
    except TypeError:
        try:
            return getAvailableShips(session)
        except Exception:
            return 0
    except Exception:
        return 0

def _check_origin_resources(session, row):
    """Returns (ok, missing_lines). Checks resources only - gold not pre-checkable."""
    try:
        html  = session.get(city_url + str(row["origin_city_id"]))
        city  = getCity(html)
        avail = city["availableResources"]  # [wood, wine, marble, crystal, sulfur]
        need  = [
            _BASE_WOOD + row["extra_wood"],
            row["extra_wine"],
            row["extra_marble"],
            row["extra_crystal"],
            row["extra_sulfur"],
        ]
        res_labels = ["Wood", "Wine", "Marble", "Crystal", "Sulfur"]
        missing = [
            f"{res_labels[i]}: need {addThousandSeparator(n)}, "
            f"have {addThousandSeparator(avail[i])}"
            for i, n in enumerate(need)
            if n > 0 and (i >= len(avail) or avail[i] < n)
        ]
        return len(missing) == 0, missing
    except Exception as e:
        return False, [f"Resource check failed: {e}"]


# ─── RRS helpers (no-op when RRS not installed) ───────────────────────────────

def _rrs_reserve_resources(session, row):
    if not RRS_AVAILABLE:
        return []
    rids = []
    pairs = [
        (0, _BASE_WOOD + row["extra_wood"]),
        (1, row["extra_wine"]),
        (2, row["extra_marble"]),
        (3, row["extra_crystal"]),
        (4, row["extra_sulfur"]),
    ]
    for idx, amount in pairs:
        if amount <= 0:
            continue
        try:
            rid = rrs_reserve(
                session,
                city_id        = int(row["origin_city_id"]),
                city_name      = str(row["origin_city_name"]),
                resource_index = idx,
                amount         = amount,
                module_name    = _MOD_TAG,
                reason         = f"Colonize [{row['x']}:{row['y']}]",
                release_at     = time.time() + 3600,
            )
            if rid:
                rids.append(rid)
        except Exception:
            pass
    return rids

def _rrs_release_list(session, rids):
    if not RRS_AVAILABLE or not rids:
        return
    for rid in rids:
        try:
            rrs_release(session, rid, _MOD_TAG)
        except Exception:
            pass


# ─── Colonize POST ────────────────────────────────────────────────────────────

def _do_colonize(session, island_id, position, origin_city_id, action_request,
                 extra_wood, extra_wine, extra_marble, extra_crystal, extra_sulfur):
    """Send the colonization POST. Returns (success, reason)."""
    ships    = _ships_required(extra_wood, extra_wine, extra_marble,
                                extra_crystal, extra_sulfur)
    total_r  = (_BASE_WOOD + extra_wood + extra_wine
                + extra_marble + extra_crystal + extra_sulfur)
    capacity = math.ceil(total_r / ships) if ships else _SHIP_CAPACITY

    payload = {
        "action":              "transportOperations",
        "function":            "startColonization",
        "islandId":            str(island_id),
        "cargo_people":        str(_BASE_CITIZENS),
        "cargo_gold":          str(_BASE_GOLD),
        "desiredPosition":     str(position),
        "actionRequest":       str(action_request),
        "cargo_resource":      str(_BASE_WOOD + extra_wood),
        "cargo_tradegood1":    str(extra_wine),
        "cargo_tradegood2":    str(extra_marble),
        "cargo_tradegood3":    str(extra_crystal),
        "cargo_tradegood4":    str(extra_sulfur),
        "capacity":            str(capacity),
        "max_capacity":        str(capacity),
        "jetPropulsion":       "0",
        "transporters":        str(ships),
        "position":            str(position),
        "destinationIslandId": str(island_id),
        "backgroundView":      "island",
        "currentIslandId":     str(island_id),
        "templateView":        "colonize",
        "ajax":                "1",
    }

    base_url     = session.urlBase.replace("index.php?", "index.php")
    ships_before = _get_ships(session, origin_city_id)

    response  = session.s.post(
        base_url, data=payload, params={},
        verify=config.do_ssl_verify, timeout=300,
    )
    resp_text = response.text

    if _colonize_succeeded(resp_text):
        return True, "success"

    ships_after = _get_ships(session, origin_city_id)
    if 0 < ships_after < ships_before:
        return True, "ships_decreased"

    return False, "failed"


# ─── Jitter ───────────────────────────────────────────────────────────────────

def _jitter_secs(jitter_type):
    if jitter_type == "small":
        return random.randint(60, 300)
    if jitter_type == "large":
        return random.randint(1800, 3600)
    return 0


# ─── Worker - colonize attempt ────────────────────────────────────────────────

def _attempt_colonize(session, row, pos, settings, notif_col, notif_err, rrs_enabled):
    """Try to colonize `pos`. Returns True if a ship was sent."""
    mid   = row["monitor_id"]
    label = f"[{row['x']}:{row['y']}] {row['island_name']}"
    ships = _ships_required(row["extra_wood"], row["extra_wine"],
                             row["extra_marble"], row["extra_crystal"],
                             row["extra_sulfur"])

    # Ship availability
    ships_avail = _get_ships(session, row["origin_city_id"])
    if ships_avail < ships:
        setInfoSignal(
            session,
            f"{label} - slot found but only {ships_avail}/{ships} ships available"
        )
        if notif_col:
            sendToBot(
                session,
                f"Island Monitor: Open slot found at {label} pos {pos} but "
                f"only {ships_avail}/{ships} ships available in "
                f"{row['origin_city_name']}. Will retry next cycle."
            )
        return False

    # Resource check (warn only - gold cannot be pre-checked)
    ok_res, missing = _check_origin_resources(session, row)
    if not ok_res and missing and notif_col:
        sendToBot(
            session,
            f"Island Monitor: Slot found at {label} but resources may be "
            f"insufficient in {row['origin_city_name']}:\n"
            + "\n".join(missing)
            + f"\nAttempting anyway - ensure {addThousandSeparator(_BASE_GOLD)} "
              "gold is available in that city."
        )

    # Reserve resources via RRS so other modules don't consume them
    rids = _rrs_reserve_resources(session, row) if rrs_enabled else []

    # Final slot check just before sending (uses island view - reliable occupancy)
    setInfoSignal(session, f"{label} - confirming slot {pos} still open...")
    valid2, _ = _get_real_empty_slots(
        session, row["island_id"], bool(row["accept_premium"])
    )
    if pos not in valid2:
        _rrs_release_list(session, rids)
        setInfoSignal(session, f"{label} - slot {pos} no longer available")
        return False

    # Get action request from colonize view for this specific position
    ar2, _ = _load_colonize_view(session, row["island_id"], position=pos)

    setInfoSignal(session, f"{label} - sending colonization to slot {pos}...")
    ok, reason = _do_colonize(
        session, row["island_id"], pos, row["origin_city_id"], ar2,
        row["extra_wood"], row["extra_wine"], row["extra_marble"],
        row["extra_crystal"], row["extra_sulfur"],
    )
    _rrs_release_list(session, rids)

    if ok:
        _record_colonize_sent(session, row, pos, ships, settings, notif_col,
                               label, suffix="")
        return True

    # ── First attempt failed: retry after 30 s ────────────────────────────────
    setInfoSignal(session, f"{label} - colonize failed, retrying in 30 s...")
    wait(30)

    valid3, _ = _get_real_empty_slots(
        session, row["island_id"], bool(row["accept_premium"])
    )
    if pos not in valid3:
        if notif_col:
            sendToBot(
                session,
                f"Island Monitor: Slot at {label} pos {pos} was taken by "
                f"another player before the ship could be sent."
            )
        return False

    # Second attempt - fresh action request
    ar3, _ = _load_colonize_view(session, row["island_id"], position=pos)
    rids2 = _rrs_reserve_resources(session, row) if rrs_enabled else []
    ok2, _ = _do_colonize(
        session, row["island_id"], pos, row["origin_city_id"], ar3,
        row["extra_wood"], row["extra_wine"], row["extra_marble"],
        row["extra_crystal"], row["extra_sulfur"],
    )
    _rrs_release_list(session, rids2)

    if ok2:
        _record_colonize_sent(session, row, pos, ships, settings, notif_col,
                               label, suffix=" (retry)")
        return True

    if notif_err:
        sendToBot(
            session,
            f"Island Monitor: Colonization failed at {label} pos {pos} after "
            f"retry. Will try again next scan cycle."
        )
    return False


def _record_colonize_sent(session, row, pos, ships, settings, notif_col,
                           label, suffix):
    """Update CSV and notify after a successful colonization send."""
    now           = time.time()
    new_sent      = row["cities_sent"] + 1
    cooldown_mins = int(row.get("post_cooldown_mins") or
                        settings.get("post_cooldown_mins", 20))
    csv_update(
        session, row["monitor_id"],
        cities_sent    = new_sent,
        last_sent      = now,
        cooldown_until = now + cooldown_mins * 60,
    )
    if notif_col:
        sendToBot(
            session,
            f"Colonization sent{suffix}!\n"
            f"Island:   {label}\n"
            f"Slot:     {pos}\n"
            f"From:     {row['origin_city_name']}\n"
            f"Ships:    {ships}\n"
            f"Progress: {new_sent}/{row['max_cities']} planted on this island"
        )


# ─── Worker - per-island processing ──────────────────────────────────────────

def _process_island(session, row, settings, notif_col, notif_err, rrs_enabled):
    """Scan one island and send colonization ships if slots are available."""
    now = time.time()
    mid = row["monitor_id"]

    # Skip if in post-colonize cooldown
    if row["cooldown_until"] and float(row["cooldown_until"]) > now:
        remaining_m = max(1, int((float(row["cooldown_until"]) - now) / 60))
        setInfoSignal(
            session,
            f"[{row['x']}:{row['y']}] In cooldown - {remaining_m}m remaining"
        )
        return

    setInfoSignal(session, f"[{row['x']}:{row['y']}] Scanning for open slots...")

    try:
        available, premium = _get_real_empty_slots(
            session, row["island_id"], bool(row["accept_premium"])
        )
        csv_update(session, mid, last_checked=now)

        if not available:
            setInfoSignal(session, f"[{row['x']}:{row['y']}] No open slots found")
            return

        remaining = row["max_cities"] - row["cities_sent"]
        if remaining <= 0:
            csv_update(session, mid, status=STATUS_COMPLETED)
            return

        label = f"[{row['x']}:{row['y']}] {row['island_name']}"
        prem_note = (
            f" ({len(premium)} premium)" if premium and bool(row["accept_premium"])
            else f" ({len(premium)} premium - skipped)" if premium else ""
        )
        setInfoSignal(
            session,
            f"{label} - {len(available)} slot(s) open{prem_note}, "
            f"need {remaining} more"
        )

        for pos in available[:remaining]:
            if _stop_requested(session):
                return
            # Reload row so cities_sent is current after each attempt
            fresh = csv_load(session)
            row   = next((r for r in fresh if r["monitor_id"] == mid), row)
            if row["cities_sent"] >= row["max_cities"]:
                break
            _attempt_colonize(session, row, pos, settings,
                              notif_col, notif_err, rrs_enabled)

        # Check if all cities planted
        fresh = csv_load(session)
        row   = next((r for r in fresh if r["monitor_id"] == mid), row)
        if row["cities_sent"] >= row["max_cities"]:
            csv_update(session, mid, status=STATUS_COMPLETED)
            if notif_col:
                sendToBot(
                    session,
                    f"Island Monitor complete!\n"
                    f"All {row['max_cities']} colonization(s) sent to "
                    f"{label}.\n"
                    f"Island removed from active watch list."
                )

    except Exception:
        err = traceback.format_exc()
        csv_update(session, mid, status=STATUS_ERROR, notes=err[:240])
        if notif_err:
            sendToBot(
                session,
                f"Island Monitor error on [{row['x']}:{row['y']}]:\n{err}"
            )


# ─── Worker main loop ─────────────────────────────────────────────────────────

def _worker(session, settings, rrs_enabled):
    set_child_mode(session)
    updateProcessList(session)
    _worker_lock_write(session)
    _clear_stop_flag(session)
    setInfoSignal(session, "Island Colonize Monitor running")

    notif_enabled   = notificationDataIsValid(session)
    notif_level     = settings.get("notif_level", "colonize")
    notif_col       = notif_enabled and notif_level in ("all", "colonize")
    notif_err       = notif_enabled
    notif_heartbeat = notif_enabled and notif_level == "all"
    hb_secs         = settings.get("heartbeat_hours", 0) * 3600
    last_heartbeat  = 0.0
    first_run       = True

    try:
        while not _stop_requested(session):
            _worker_lock_refresh(session)

            # Re-read CSV every cycle - picks up manual edits
            rows        = csv_load(session)
            active_rows = sorted(
                [r for r in rows if r["status"] == STATUS_ACTIVE],
                key=lambda r: r["priority"],
            )
            total_active = len(active_rows)
            total_sent   = sum(r["cities_sent"] for r in rows)

            setInfoSignal(
                session,
                f"Island Monitor - watching {total_active} island(s), "
                f"{total_sent} colonization(s) sent total"
            )

            # Heartbeat
            now = time.time()
            if notif_heartbeat and hb_secs > 0 and now - last_heartbeat >= hb_secs:
                detail = "\n".join(
                    f"  [{r['x']}:{r['y']}] {r['island_name']} - "
                    f"{r['cities_sent']}/{r['max_cities']} planted"
                    for r in active_rows
                ) or "  (none)"
                sendToBot(
                    session,
                    f"Island Monitor running\n"
                    f"Watching: {total_active} island(s)\n"
                    f"Total colonizations sent: {total_sent}\n"
                    f"Active islands:\n{detail}"
                )
                last_heartbeat = now

            # Process each active island (priority order)
            for row in active_rows:
                if _stop_requested(session):
                    break
                _process_island(session, row, settings, notif_col, notif_err,
                                rrs_enabled)

            # No wait on first run - check immediately, then start the interval
            if not first_run:
                interval_secs = settings.get("interval_minutes", 30) * 60
                jitter_secs   = _jitter_secs(settings.get("jitter_type", "none"))
                total_wait    = interval_secs + jitter_secs
                wait_m        = total_wait // 60

                setInfoSignal(
                    session,
                    f"Island Monitor - next check in ~{wait_m} minute(s)"
                )
                wait(total_wait)

            first_run = False

    except Exception:
        err = traceback.format_exc()
        if notif_err:
            try:
                sendToBot(session, f"Island Monitor crashed:\n{err}")
            except Exception:
                pass
    finally:
        _worker_lock_release(session)
        session.logout()


# ─── Start / stop the worker ──────────────────────────────────────────────────

def _activate_monitor(session, event):
    if _is_worker_running(session):
        print(f"\n  {C.WA}Monitor is already running.{C.R}")
        enter()
        event.set()
        return

    rows   = csv_load(session)
    active = [r for r in rows if r["status"] == STATUS_ACTIVE]
    if not active:
        print(f"\n  {C.WA}No active islands in the watch list. "
              f"Add islands first, then start the monitor.{C.R}")
        enter()
        event.set()
        return

    settings    = _load_settings(session)
    rrs_enabled = RRS_AVAILABLE
    _clear_stop_flag(session)

    proc = multiprocessing.Process(
        target = _worker,
        args   = (session, settings, rrs_enabled),
        daemon = False,
    )
    proc.start()

    event.set()
    print(f"\n  {C.OK}Monitor started - watching {len(active)} island(s).{C.R}")
    if rrs_enabled:
        print(f"  {C.HI}RRS detected - resources will be reserved before each "
              f"colonization attempt.{C.R}")
    interval = settings.get("interval_minutes", 30)
    jitter   = settings.get("jitter_type", "none")
    jitter_desc = {"small": " + 1–5 min jitter", "large": " + 30–60 min jitter",
                   "none": ""}.get(jitter, "")
    print(f"  {C.D}Check interval: every {interval} minute(s){jitter_desc}.{C.R}")
    print(f"  {C.D}Initial check runs immediately.{C.R}")
    enter()


def _stop_monitor(session):
    if not _is_worker_running(session):
        print(f"\n  {C.D}Monitor is not currently running.{C.R}")
    else:
        _set_stop_flag(session)
        print(f"\n  {C.OK}Stop signal sent. The monitor will finish its current "
              f"scan and then exit.{C.R}")
    enter()


# ─── City picker ──────────────────────────────────────────────────────────────

def _pick_city(session):
    ids, cities = getIdsOfCities(session)
    if not ids:
        print(f"  {C.WA}No cities found.{C.R}")
        return None, None
    print("")
    print(f"  {'#':<4} {'City name':<22} {'Island':<9} {'Resource'}")
    print(f"  {'-'*4} {'-'*22} {'-'*9} {'-'*10}")
    for i, cid in enumerate(ids, 1):
        c   = cities[cid]
        x_c = c.get("islandX") or c.get("x", "?")
        y_c = c.get("islandY") or c.get("y", "?")
        tg  = _TRADEGOOD_LABEL.get(int(c.get("tradegood", 0) or 0), "")
        print(f"  {C.B}({i}){C.R}  {c['name']:<22} [{x_c}:{y_c}]  {tg}")
    choice = read(min=1, max=len(ids), digit=True)
    cid = ids[choice - 1]
    return int(cities[cid]["id"]), cities[cid]["name"]


# ─── Add Island wizard (7 steps) ─────────────────────────────────────────────

def _add_island(session):

    # ── Step 1: Coordinates ───────────────────────────────────────────────────
    banner()
    _banner("Add Island  -  Step 1 of 7: Island Coordinates")
    print(f"  {C.D}Enter the world map coordinates of the island you want to monitor.{C.R}\n")
    x = read(min=0, max=100, digit=True, msg="  X coordinate: ")
    y = read(min=0, max=100, digit=True, msg="  Y coordinate: ")

    print(f"\n  {C.D}Looking up island [{x}:{y}]...{C.R}")
    island_id, island = _get_island_from_coords(session, x, y)
    if island_id is None:
        print(f"\n  {C.ER}Could not find an island at [{x}:{y}]. "
              f"Check coordinates and try again.{C.R}")
        enter()
        return

    tg          = int(island.get("tradegood", 0))
    island_name = island.get("name", f"Island {island_id}")
    tg_label    = _TRADEGOOD_LABEL.get(tg, "Unknown")
    print(f"\n  {C.OK}Found: {island_name} [{x}:{y}] - {tg_label} island (ID {island_id}){C.R}")

    existing = csv_load(session)
    for r in existing:
        if r["island_id"] == island_id and r["status"] != STATUS_COMPLETED:
            print(f"\n  {C.WA}This island is already in the watch list "
                  f"(entry #{r['monitor_id']}, status: {r['status']}).{C.R}")
            enter()
            return

    # ── Step 2: Priority ──────────────────────────────────────────────────────
    banner()
    _banner("Add Island  -  Step 2 of 7: Priority")
    print(f"  Island: {C.CY}{island_name} [{x}:{y}] - {tg_label}{C.R}\n")
    print(f"  {C.D}Higher-priority islands are checked first in each scan cycle.{C.R}",)
    print(f"  {C.D}Use High for islands you want to colonize as soon as possible.{C.R}\n")
    print(f"  {C.B}(1){C.R} High   - checked first every cycle")
    print(f"  {C.B}(2){C.R} Medium - checked after High islands")
    print(f"  {C.B}(3){C.R} Low    - checked last")
    priority = read(min=1, max=3, digit=True)

    # ── Step 3: Cities to plant ───────────────────────────────────────────────
    banner()
    _banner("Add Island  -  Step 3 of 7: How Many Cities to Plant")
    print(f"  Island: {C.CY}{island_name} [{x}:{y}]{C.R}\n")
    print(f"  {C.D}How many of this island's city slots do you want to colonize?{C.R}")
    print(f"  {C.D}The monitor keeps watching until this many ships have been sent,{C.R}")
    print(f"  {C.D}then stops monitoring this island automatically.{C.R}")
    print(f"  {C.HI}Enter 1 if you only want to plant one city here.{C.R}\n")
    max_cities = read(min=1, max=10, digit=True, msg="  Number of cities to plant: ")

    # ── Step 4: Premium spots ─────────────────────────────────────────────────
    banner()
    _banner("Add Island  -  Step 4 of 7: Premium Spots")
    print(f"  Island: {C.CY}{island_name} [{x}:{y}]{C.R}\n")
    print(f"  {C.D}Some island slots are marked as 'premium' positions.{C.R}")
    print(f"  {C.D}Colonizing a premium slot costs Ambrosia (real money) or a special item.{C.R}")
    print(f"  {C.D}Free slots have no extra cost.{C.R}\n")
    print(f"  {C.B}(1){C.R} Free slots only - {C.OK}safe, no Ambrosia spent{C.R}")
    print(f"  {C.B}(2){C.R} Accept premium slots - {C.WA}WARNING: costs Ambrosia or an item{C.R}")
    prem_choice    = read(min=1, max=2, digit=True)
    accept_premium = 1 if prem_choice == 2 else 0

    # ── Step 5: Origin city ───────────────────────────────────────────────────
    banner()
    _banner("Add Island  -  Step 5 of 7: Origin City")
    print(f"  Island: {C.CY}{island_name} [{x}:{y}]{C.R}\n")
    print(f"  {C.D}Which of your cities will send the colonization ships to this island?{C.R}")
    print(f"  {C.HI}Tip: choose the city closest to the target island for faster travel.{C.R}")
    origin_city_id, origin_city_name = _pick_city(session)
    if origin_city_id is None:
        print(f"\n  {C.ER}No city available to send from. "
              f"Wizard cancelled - nothing was added.{C.R}")
        enter()
        return

    # ── Step 6: Extra resources ───────────────────────────────────────────────
    banner()
    _banner("Add Island  -  Step 6 of 7: Extra Resources")
    print(f"  Island: {C.CY}{island_name} [{x}:{y}]{C.R}\n")
    print(f"  {C.D}Every colonization ship always carries these mandatory items:{C.R}")
    print(f"    {C.B}Wood:{C.R}     {addThousandSeparator(_BASE_WOOD)}  (base requirement)")
    print(f"    {C.B}Gold:{C.R}     {addThousandSeparator(_BASE_GOLD)}  (from your treasury)")
    print(f"    {C.B}Citizens:{C.R} {_BASE_CITIZENS}")
    print(f"    {C.B}Ships:{C.R}    {_MIN_SHIPS} minimum\n")
    print(f"  {C.D}You can optionally send extra resources alongside the colonists.{C.R}")
    print(f"  {C.D}These give the new city a head start. Each extra {addThousandSeparator(_SHIP_CAPACITY)}{C.R}")
    print(f"  {C.D}resources requires one additional ship.{C.R}")
    print(f"  {C.D}Maximum extra per resource: {addThousandSeparator(_MAX_EXTRA)}{C.R}")
    print(f"  {C.HI}Press Enter to skip (send none extra for that resource).{C.R}\n")

    extras = []
    extra_labels = [
        (f"Extra Wood   (0–{addThousandSeparator(_MAX_EXTRA)}, "
         f"total wood will be {addThousandSeparator(_BASE_WOOD)} + extra)"),
        f"Extra Wine   (0–{addThousandSeparator(_MAX_EXTRA)})",
        f"Extra Marble (0–{addThousandSeparator(_MAX_EXTRA)})",
        f"Extra Crystal(0–{addThousandSeparator(_MAX_EXTRA)})",
        f"Extra Sulfur (0–{addThousandSeparator(_MAX_EXTRA)})",
    ]
    for label in extra_labels:
        while True:
            raw = read(msg=f"  {label}: ", empty=True, additionalValues=["'"])
            if raw == "'":
                print(f"\n  {C.WA}Wizard cancelled - no island was added.{C.R}")
                enter()
                return
            if raw == "":
                extras.append(0)
                break
            cleaned = raw.replace(",", "").replace(".", "").strip()
            if cleaned.isdigit():
                val = min(int(cleaned), _MAX_EXTRA)
                extras.append(val)
                if val > 0:
                    print(f"  → {addThousandSeparator(val)}")
                break
            print(f"  {C.HI}Enter a whole number between 0 and "
                  f"{addThousandSeparator(_MAX_EXTRA)}, or press Enter to skip.{C.R}")

    extra_wood, extra_wine, extra_marble, extra_crystal, extra_sulfur = extras
    ships_total = _ships_required(extra_wood, extra_wine, extra_marble,
                                   extra_crystal, extra_sulfur)
    total_wood  = _BASE_WOOD + extra_wood

    # ── Step 7: Post-colonize cooldown ────────────────────────────────────────
    banner()
    _banner("Add Island  -  Step 7 of 7: Cooldown After Sending")
    print(f"  Island: {C.CY}{island_name} [{x}:{y}]{C.R}\n")
    print(f"  {C.D}After a colonization ship is sent to this island, the monitor will{C.R}")
    print(f"  {C.D}skip it for this many minutes before scanning again.{C.R}")
    print(f"  {C.D}This prevents a second ship being sent while the first is still travelling.{C.R}")
    settings    = _load_settings(session)
    default_cd  = settings.get("post_cooldown_mins", 20)
    print(f"  {C.HI}Press Enter to use the default ({default_cd} minutes).{C.R}\n")
    raw_cd       = read(min=1, max=1440, digit=True, empty=True,
                        msg=f"  Cooldown in minutes (default {default_cd}): ")
    post_cooldown = (int(str(raw_cd).strip())
                     if str(raw_cd).strip().isdigit() else default_cd)

    # ── Confirmation ──────────────────────────────────────────────────────────
    banner()
    _banner("Add Island  -  Confirm")

    res_parts = []
    for lbl, val in zip(
        ["Wood+", "Wine", "Marble", "Crystal", "Sulfur"],
        [extra_wood, extra_wine, extra_marble, extra_crystal, extra_sulfur],
    ):
        if val > 0:
            res_parts.append(f"{lbl}: +{addThousandSeparator(val)}")
    res_summary = ", ".join(res_parts) if res_parts else "None (base requirement only)"

    print(f"  {C.B}Island:{C.R}            {island_name} [{x}:{y}] - {tg_label}")
    print(f"  {C.B}Priority:{C.R}          {_PRIORITY_LABEL[priority]}")
    print(f"  {C.B}Cities to plant:{C.R}   {max_cities}")
    print(f"  {C.B}Premium slots:{C.R}     "
          f"{'Accept  (WARNING: costs Ambrosia)' if accept_premium else 'Ignore (free slots only)'}")
    print(f"  {C.B}Origin city:{C.R}       {origin_city_name}")
    print(f"  {C.B}Wood sent:{C.R}         {addThousandSeparator(total_wood)} "
          f"({addThousandSeparator(_BASE_WOOD)} mandatory + {addThousandSeparator(extra_wood)} extra)")
    print(f"  {C.B}Extra resources:{C.R}   {res_summary}")
    print(f"  {C.B}Ships per send:{C.R}    {ships_total}")
    print(f"  {C.B}Post cooldown:{C.R}     {post_cooldown} minutes")
    print(f"\n  {C.B}(1){C.R} Confirm - add this island to the watch list")
    print(f"  {C.B}(0){C.R} Cancel")
    if read(min=0, max=1, digit=True) == 0:
        return

    new_row = {
        "monitor_id":       _next_id(session),
        "x":                x,
        "y":                y,
        "island_id":        island_id,
        "island_name":      island_name,
        "tradegood":        tg,
        "priority":         priority,
        "max_cities":       max_cities,
        "cities_sent":      0,
        "origin_city_id":   origin_city_id,
        "origin_city_name": origin_city_name,
        "accept_premium":   accept_premium,
        "extra_wood":       extra_wood,
        "extra_wine":       extra_wine,
        "extra_marble":     extra_marble,
        "extra_crystal":    extra_crystal,
        "extra_sulfur":     extra_sulfur,
        "post_cooldown_mins": post_cooldown,
        "status":           STATUS_ACTIVE,
        "last_checked":     0.0,
        "last_sent":        0.0,
        "cooldown_until":   0.0,
        "notes":            "",
    }
    csv_append(session, new_row)
    print(f"\n  {C.OK}Island [{x}:{y}] added to watch list.{C.R}")
    if not _is_worker_running(session):
        print(f"  {C.HI}Remember to start the monitor with (s) from the main menu.{C.R}")
    enter()


# ─── Manage islands ───────────────────────────────────────────────────────────

def _manage_islands(session):
    while True:
        banner()
        _banner("Manage Islands")
        rows = csv_load(session)
        if not rows:
            print(f"  {C.D}The watch list is empty. Add islands first.{C.R}")
            enter()
            return

        sc_map = {
            STATUS_ACTIVE:    C.OK,
            STATUS_PAUSED:    C.YE,
            STATUS_COMPLETED: C.D,
            STATUS_ERROR:     C.ER,
        }
        print(f"  {'#':<4} {'Coords':<9} {'Island':<16} {'Type':<8} "
              f"{'Pri':<7} {'Planted':<9} {'Status':<11} Origin")
        print(f"  {'-'*4} {'-'*9} {'-'*16} {'-'*8} {'-'*7} {'-'*9} {'-'*11} {'-'*15}")
        for r in rows:
            sc   = sc_map.get(r["status"], "")
            crd  = f"[{r['x']}:{r['y']}]"
            name = r["island_name"][:16]
            tg   = _TRADEGOOD_LABEL.get(r["tradegood"], "?")[:8]
            pri  = _PRIORITY_LABEL.get(r["priority"], "?")[:3]
            prog = f"{r['cities_sent']}/{r['max_cities']}"
            print(
                f"  {r['monitor_id']:<4} {crd:<9} {name:<16} {tg:<8} "
                f"{pri:<7} {prog:<9} "
                f"{sc}{r['status']:<11}{C.R} {r['origin_city_name'][:15]}"
            )

        all_ids = [r["monitor_id"] for r in rows]
        print(f"\n  {C.B}Enter island #{C.R} to manage,  {C.B}(0){C.R} Back:")
        choice = read(min=0, max=max(all_ids), digit=True)
        if choice == 0:
            return
        if choice not in all_ids:
            continue
        row = next(r for r in rows if r["monitor_id"] == choice)
        _manage_single_island(session, row)


def _manage_single_island(session, row):
    while True:
        banner()
        lbl = f"[{row['x']}:{row['y']}] {row['island_name']}"
        _banner(f"Island: {lbl}")

        sc  = {STATUS_ACTIVE: C.OK, STATUS_PAUSED: C.YE,
               STATUS_COMPLETED: C.D, STATUS_ERROR: C.ER}.get(row["status"], "")
        tg  = _TRADEGOOD_LABEL.get(row["tradegood"], "?")
        pri = _PRIORITY_LABEL.get(row["priority"], "?")
        ships = _ships_required(row["extra_wood"], row["extra_wine"],
                                 row["extra_marble"], row["extra_crystal"],
                                 row["extra_sulfur"])

        print(f"  Status:           {sc}{row['status']}{C.R}")
        print(f"  Island type:      {tg}")
        print(f"  Priority:         {pri}")
        print(f"  Cities planted:   {row['cities_sent']} / {row['max_cities']}")
        print(f"  Origin city:      {row['origin_city_name']}")
        print(f"  Premium slots:    {'Accepted' if row['accept_premium'] else 'Ignored'}")
        print(f"  Ships per send:   {ships}")
        print(f"  Post cooldown:    {row['post_cooldown_mins']} minutes")

        if row["last_checked"]:
            lc = datetime.datetime.fromtimestamp(
                float(row["last_checked"])).strftime("%H:%M:%S  %d/%m/%Y")
            print(f"  Last scanned:     {lc}")
        if row["last_sent"]:
            ls = datetime.datetime.fromtimestamp(
                float(row["last_sent"])).strftime("%H:%M:%S  %d/%m/%Y")
            print(f"  Last ship sent:   {ls}")
        if row["cooldown_until"] and float(row["cooldown_until"]) > time.time():
            rem = max(1, int((float(row["cooldown_until"]) - time.time()) / 60))
            print(f"  Cooldown active:  {rem} minute(s) remaining")
        if row["notes"]:
            print(f"\n  {C.ER}Error note: {row['notes'][:100]}{C.R}")

        pause_label = "Pause" if row["status"] == STATUS_ACTIVE else "Resume"
        print(f"\n  {C.B}(1){C.R} {pause_label} monitoring this island")
        print(f"  {C.B}(2){C.R} Change priority")
        print(f"  {C.B}(3){C.R} Change origin city")
        print(f"  {C.B}(4){C.R} Reset planted counter - re-arm this island")
        print(f"  {C.B}(5){C.R} Remove from watch list")
        print(f"  {C.B}(0){C.R} Back")
        choice = read(min=0, max=5, digit=True)

        if choice == 0:
            return

        elif choice == 1:
            new_st = STATUS_PAUSED if row["status"] == STATUS_ACTIVE else STATUS_ACTIVE
            csv_update(session, row["monitor_id"], status=new_st)
            row["status"] = new_st
            print(f"\n  {C.OK}Status set to {new_st}.{C.R}")
            enter()

        elif choice == 2:
            print(f"\n  {C.B}(1){C.R} High   "
                  f"{C.B}(2){C.R} Medium   "
                  f"{C.B}(3){C.R} Low")
            p = read(min=1, max=3, digit=True)
            csv_update(session, row["monitor_id"], priority=p)
            row["priority"] = p
            print(f"  {C.OK}Priority → {_PRIORITY_LABEL[p]}.{C.R}")
            enter()

        elif choice == 3:
            print(f"\n  {C.D}Choose new origin city:{C.R}")
            cid, cname = _pick_city(session)
            if cid:
                csv_update(session, row["monitor_id"],
                           origin_city_id=cid, origin_city_name=cname)
                row["origin_city_id"]   = cid
                row["origin_city_name"] = cname
                print(f"  {C.OK}Origin city → {cname}.{C.R}")
            enter()

        elif choice == 4:
            print(f"\n  {C.WA}Reset planted count to 0 and reactivate this island?{C.R} [Y/n]")
            ans = read(values=["y", "Y", "n", "N", ""])
            if ans.lower() != "n":
                csv_update(session, row["monitor_id"],
                           cities_sent=0, status=STATUS_ACTIVE,
                           cooldown_until=0.0, notes="")
                row.update({"cities_sent": 0, "status": STATUS_ACTIVE,
                            "cooldown_until": 0.0, "notes": ""})
                print(f"  {C.OK}Reset. Island will be scanned again next cycle.{C.R}")
            enter()

        elif choice == 5:
            print(f"\n  {C.ER}Permanently remove [{row['x']}:{row['y']}] "
                  f"from the watch list?{C.R} [Y/n]")
            ans = read(values=["y", "Y", "n", "N", ""])
            if ans.lower() != "n":
                csv_delete(session, row["monitor_id"])
                print(f"  {C.OK}Removed.{C.R}")
                enter()
                return
            enter()


# ─── Settings screen ──────────────────────────────────────────────────────────

def _settings_screen(session):
    settings = _load_settings(session)
    banner()
    _banner("Settings")

    cur_int  = settings.get("interval_minutes", 30)
    cur_jit  = settings.get("jitter_type", "small")
    cur_cd   = settings.get("post_cooldown_mins", 20)
    cur_nl   = settings.get("notif_level", "colonize")
    cur_hb   = settings.get("heartbeat_hours", 0)
    print(f"  {C.D}Current settings:{C.R}")
    print(f"    Interval:       {cur_int} minute(s)")
    print(f"    Jitter:         {cur_jit}")
    print(f"    Cooldown:       {cur_cd} minute(s)")
    print(f"    Notifications:  {cur_nl}")
    print(f"    Heartbeat:      {cur_hb}h (0 = off)")
    print(f"\n  {C.B}(1){C.R} Update settings")
    print(f"  {C.B}(0){C.R} Back without changing\n")
    if read(min=0, max=1, digit=True) == 0:
        return

    # Check interval
    print(f"  {C.H}── Check Interval ──{C.R}\n")
    print(f"  {C.D}How often should the monitor scan all islands for open slots?{C.R}")
    print(f"  {C.D}Enter hours AND minutes - the values are added together.{C.R}")
    print(f"  {C.WA}  Example: 1 hour + 30 min = checks every 1 hour 30 minutes.{C.R}")
    print(f"  {C.WA}  Example: 0 hours + 30 min = checks every 30 minutes.{C.R}\n")
    h = read(min=0, max=24, digit=True, msg="  Hours   (0–24):  ")
    m = read(min=0, max=59, digit=True, msg="  Minutes (0–59):  ")
    total_m = h * 60 + m
    if total_m < 1:
        total_m = 30
        print(f"  {C.HI}Minimum 1 minute - defaulting to 30 minutes.{C.R}")
    settings["interval_minutes"] = total_m
    print(f"  → Islands will be scanned every {total_m} minute(s).")

    # Jitter
    print(f"\n  {C.H}── Random Jitter ──{C.R}\n")
    print(f"  {C.D}Adds a random extra delay to the check interval each cycle.{C.R}")
    print(f"  {C.D}Makes the bot's scan pattern harder to predict - recommended.{C.R}\n")
    print(f"  {C.B}(1){C.R} Small jitter - adds 1 to 5 minutes randomly each cycle")
    print(f"  {C.B}(2){C.R} Large jitter - adds 30 to 60 minutes randomly each cycle")
    print(f"  {C.B}(3){C.R} No jitter    - scans on the exact interval every time")
    j = read(min=1, max=3, digit=True)
    settings["jitter_type"] = {1: "small", 2: "large", 3: "none"}[j]

    # Default cooldown
    print(f"\n  {C.H}── Default Post-Send Cooldown ──{C.R}\n")
    print(f"  {C.D}After a colonization ship is dispatched, this island is skipped{C.R}")
    print(f"  {C.D}for this many minutes before the next scan. Prevents double-sending.{C.R}")
    print(f"  {C.D}Individual islands can override this with their own cooldown.{C.R}")
    default_cd = settings.get("post_cooldown_mins", 20)
    print(f"  {C.HI}Press Enter to keep the current default ({default_cd} minutes).{C.R}\n")
    raw = read(min=1, max=1440, digit=True, empty=True,
               msg=f"  Minutes (default {default_cd}): ")
    settings["post_cooldown_mins"] = (
        int(str(raw).strip()) if str(raw).strip().isdigit() else default_cd
    )

    # Notification level
    print(f"\n  {C.H}── Notifications ──{C.R}\n")
    print(f"  {C.D}When should notifications be sent to your configured apps?{C.R}\n")
    print(f"  {C.B}(1){C.R} All events   - colonization sent, heartbeat status, and errors")
    print(f"  {C.B}(2){C.R} Colonize + errors only - silent unless a city is sent or "
          f"something breaks")
    print(f"  {C.B}(3){C.R} Errors only  - completely silent unless something goes wrong")
    nl = read(min=1, max=3, digit=True)
    settings["notif_level"] = {1: "all", 2: "colonize", 3: "errors"}[nl]

    # Heartbeat
    print(f"\n  {C.H}── Heartbeat Notifications ──{C.R}\n")
    print(f"  {C.D}Sends a regular 'still running' message to your notification app.{C.R}")
    print(f"  {C.D}If the messages stop arriving, the monitor may have crashed.{C.R}")
    print(f"  {C.D}This is optional - set to 0 to disable.{C.R}\n")
    print(f"  {C.B}(0){C.R} Disabled - no heartbeat messages")
    print(f"  {C.B}(N){C.R} Send a heartbeat every N hours (e.g. 1, 2, 4, 6, 12, 24)")
    hb = read(min=0, max=48, digit=True, msg="  Hours between heartbeats (0 = off): ")
    settings["heartbeat_hours"] = hb

    _save_settings(session, settings)
    print(f"\n  {C.OK}Settings saved.{C.R}")
    if _is_worker_running(session):
        print(f"  {C.HI}The monitor is currently running with the previous settings.{C.R}")
        print(f"  {C.HI}Stop (o) and restart (s) from the main menu to apply changes.{C.R}")
    enter()


# ─── Status line ─────────────────────────────────────────────────────────────

def _status_line(session):
    running = _is_worker_running(session)
    rows    = csv_load(session)
    active  = sum(1 for r in rows if r["status"] == STATUS_ACTIVE)
    total   = len(rows)
    sent    = sum(r["cities_sent"] for r in rows)
    status  = f"{C.OK}RUNNING{C.R}" if running else f"{C.WA}STOPPED{C.R}"
    line    = f"Monitor: {status}"
    if total > 0:
        line += (f"  |  {active} active of {total} island(s)"
                 f"  |  {sent} colonization(s) sent")
    else:
        line += "  |  No islands in watch list"
    return line


# ─── Clear all ────────────────────────────────────────────────────────────────

def _clear_all_islands(session):
    rows = csv_load(session)
    if not rows:
        print(f"\n  {C.D}Watch list is already empty.{C.R}")
        enter()
        return
    if _is_worker_running(session):
        print(f"\n  {C.WA}Warning: the monitor is currently running.{C.R}")
        print(f"  {C.WA}Stop it first with (o) before clearing, otherwise the{C.R}")
        print(f"  {C.WA}worker process will keep running until its current scan ends.{C.R}")
        print(f"  {C.WA}Continue anyway?{C.R}\n")
    print(f"  {C.ER}This will permanently remove all {len(rows)} island(s) "
          f"from the watch list.{C.R}")
    print(f"  Type {C.B}yes{C.R} to confirm, or press Enter to cancel:")
    ans = read(msg="  > ", additionalValues=["yes", "Yes", "YES",
                                             "no", "No", "NO", "n", "N", "y", "Y", ""])
    if str(ans).lower() != "yes":
        print("  Cancelled.")
        enter()
        return
    csv_save_all(session, [])
    print(f"  {C.OK}Watch list cleared.{C.R}")
    enter()


# ─── Entry point ─────────────────────────────────────────────────────────────

def islandColonizeMonitor(session, event, stdin_fd, predetermined_input):
    sys.stdin = os.fdopen(stdin_fd)
    config.predetermined_input = predetermined_input

    try:
        banner()
        _banner()

        print(f"  {_status_line(session)}\n")

        running = _is_worker_running(session)
        if not running:
            rows   = csv_load(session)
            active = [r for r in rows if r["status"] == STATUS_ACTIVE]
            if active:
                print(f"  {C.WA}Active islands exist but the monitor is stopped. "
                      f"Press (s) to start it.{C.R}")

        print(f"\n  {C.B}(s){C.R} Start monitor   "
              f"{C.B}(o){C.R} Stop monitor   "
              f"{C.B}(x){C.R} Clear all islands")

        print(f"\n  {C.H}── Island Management ──{C.R}\n")
        print(f"  {C.B}(1){C.R} Add island to watch list")
        print(f"  {C.D}    Set up a new island to monitor and colonize automatically.{C.R}")
        print(f"  {C.B}(2){C.R} Manage islands")
        print(f"  {C.D}    View, pause, edit, reset, or remove watched islands.{C.R}")
        print(f"  {C.B}(3){C.R} Settings")
        print(f"  {C.D}    Check interval, jitter, cooldown, and notification preferences.{C.R}")
        print(f"\n  {C.B}('){C.R} Back to main menu")

        choice = read(min=1, max=3, digit=True,
                      additionalValues=["'", "s", "S", "o", "O", "x", "X"])

        if choice == "'":
            return

        if isinstance(choice, str):
            letter = choice.lower()
            if letter == "s":
                _activate_monitor(session, event)
                return
            elif letter == "o":
                _stop_monitor(session)
                return
            elif letter == "x":
                _clear_all_islands(session)
                return

        # Choices 1–3: interactive management
        if choice == 1:
            _add_island(session)
        elif choice == 2:
            _manage_islands(session)
        elif choice == 3:
            _settings_screen(session)

    except KeyboardInterrupt:
        pass
    except Exception:
        traceback.print_exc()
    finally:
        try:
            event.set()
        except Exception:
            pass
