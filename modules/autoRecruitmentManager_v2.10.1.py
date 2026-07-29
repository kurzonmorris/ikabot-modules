#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Auto Recruitment Manager for Ikabot
=====================================
Automates recruitment of military units and ships across multiple barracks/shipyards.

Features:
- Recruit units (barracks) or ships (shipyards)
- Distribute recruitment across multiple buildings (speed-weighted, balanced)
- Dynamic fetching of build times and costs (server-specific)
- Priority queue: each order batch has an independent priority level
- Configurable batch size: each order builds N% of a unit type's remaining
- Resource shortage handling: each idle building places whatever it can afford
- Busy building detection with smart wait / include options
- CSV-backed persistence: survives crashes and restarts
- Live queue management: add / remove units while running
- Periodic Telegram progress reports (global bar + per-city breakdown)
- Optional resource import via ResourceTransportManager CSV scheduler

Version: 2.10.1
"""

MODULE_VERSION = "2.10.1"

import csv
import glob
import importlib.util
import json
import os
import pathlib
import re
import sys
import threading
import time
import traceback
import math
from ikabot.config import *
from ikabot.helpers.botComm import *
from ikabot.helpers.getJson import getCity
from ikabot.helpers.gui import *
from ikabot.helpers.pedirInfo import *
from ikabot.helpers.process import set_child_mode
from ikabot.helpers.signals import setInfoSignal
from ikabot.helpers.varios import *

# =============================================================================
# RESOURCE RESERVATION SYSTEM (RRS) — optional integration
# =============================================================================

MODULE_NAME = "autoRecruitmentManager"

try:
    from resourceReservationSystem import (
        is_city_excluded,
        get_excluded_cities,
        get_summary        as rrs_get_summary,
        get_reservation_snapshot,
        reserve            as rrs_reserve,
        release            as rrs_release,
        release_all_for_module,
        update_reservation as rrs_update_reservation,
        rrs_get_config,
    )
    RRS_AVAILABLE = True
except ImportError:
    RRS_AVAILABLE = False

# =============================================================================
# UNIT AND SHIP DEFINITIONS
# =============================================================================

UNITS_ORDER = [
    {'name': 'Hoplite',            'game_index': 303},
    {'name': 'Swordsman',          'game_index': 302},
    {'name': 'Steam Giant',        'game_index': 308},
    {'name': 'Sulphur Carabineer', 'game_index': 304},
    {'name': 'Mortar',             'game_index': 305},
    {'name': 'Gyrocopter',         'game_index': 312},
    {'name': 'Balloon-Bombardier', 'game_index': 309},
    {'name': 'Doctor',             'game_index': 311},
    {'name': 'Cook',               'game_index': 310},
    {'name': 'Battering Ram',      'game_index': 307},
    {'name': 'Spearman',           'game_index': 315},
    {'name': 'Slinger',            'game_index': 301},
    {'name': 'Archer',             'game_index': 313},
    {'name': 'Catapult',           'game_index': 306},
]

SHIPS_ORDER = [
    {'name': 'Ram Ship',         'game_index': 210},
    {'name': 'Steam Ram',        'game_index': 216},
    {'name': 'Rocket Ship',      'game_index': 217},
    {'name': 'Diving Boat',      'game_index': 212},
    {'name': 'Paddle Speedboat', 'game_index': 218},
    {'name': 'Balloon Carrier',  'game_index': 219},
    {'name': 'Tender',           'game_index': 220},
    {'name': 'Fire Ship',        'game_index': 211},
    {'name': 'Ballista Ship',    'game_index': 213},
    {'name': 'Catapult Ship',    'game_index': 214},
    {'name': 'Mortar Ship',      'game_index': 215},
]

RESOURCE_NAMES = ['Wood', 'Wine', 'Marble', 'Crystal', 'Sulfur']


# =============================================================================
# CSV PERSISTENCE
# =============================================================================

# Goals CSV: one row per unit-type target.
# qty_remaining is decremented as build orders are POSTed to the game.
# When qty_remaining reaches 0 the row is marked "completed".
RECRUIT_COLUMNS = [
    "request_id",
    "unit_type_id",
    "unit_name",
    "is_units",         # "True" = barracks, "False" = shipyard
    "qty_total",        # original amount requested
    "qty_remaining",    # still to be POSTed to the game
    "priority",         # 1 = highest
    "status",           # active | completed | cancelled
    "created_at",
    "last_updated",
    "notes",
]

RECRUIT_INT_COLS = {
    "request_id", "unit_type_id",
    "qty_total", "qty_remaining", "priority",
    "created_at", "last_updated",
}
RECRUIT_VALID_STATUSES = ("active", "completed", "cancelled")

# Old column name used to detect v1 CSV format during migration
_OLD_CSV_SENTINEL = "batch_id"


def _safe(value):
    return re.sub(r'[^\w.-]', '_', str(value))


def _to_num(value):
    """Coerce a game-supplied value to int/float. The Ikariam JSON sometimes
    returns numeric fields as strings (e.g. '1000', '71.0'). Returns 0 on
    anything unparseable so downstream arithmetic never crashes."""
    if isinstance(value, (int, float)):
        return value
    try:
        s = str(value).replace(',', '').strip()
        if s == "":
            return 0
        f = float(s)
        return int(f) if f.is_integer() else f
    except (ValueError, TypeError):
        return 0


def _account_suffix(session):
    return f"{_safe(session.servidor)}_{_safe(session.username)}"


def _csv_path(session, is_units):
    kind = "units" if is_units else "ships"
    return os.path.join(
        os.path.expanduser("~"),
        f".ikabot_recruitment_{kind}_{_account_suffix(session)}.csv",
    )


def _config_path(session):
    return os.path.join(
        os.path.expanduser("~"),
        f".ikabot_recruitment_cfg_{_account_suffix(session)}.json",
    )


def _stop_flag_path(session):
    return os.path.join(
        os.path.expanduser("~"),
        f".ikabot_recruitment_stop_{_account_suffix(session)}",
    )


WORKER_LOCK_STALE_SECONDS = 120  # worker renews every ~30 s; stale after 4 missed cycles
WAKE_POLL_SECONDS = 2            # granularity for interruptible sleep


def _worker_lock_path(session, is_units):
    kind = "units" if is_units else "ships"
    return os.path.join(
        os.path.expanduser("~"),
        f".ikabot_recruitment_worker_{kind}_{_account_suffix(session)}.lock",
    )


def _wake_flag_path(session, is_units):
    kind = "units" if is_units else "ships"
    return os.path.join(
        os.path.expanduser("~"),
        f".ikabot_recruitment_wake_{kind}_{_account_suffix(session)}",
    )


# =============================================================================
# BUILDING CACHE  — static list of barracks/shipyards, refreshed on demand
# =============================================================================

_BUILDINGS_CACHE_VERSION = 1


def _buildings_cache_path(session, is_units):
    kind = "units" if is_units else "ships"
    return os.path.join(
        os.path.expanduser("~"),
        f".ikabot_recruit_bldcache_{kind}_{_account_suffix(session)}.json",
    )


def _load_buildings_cache(session, is_units):
    """Returns cached static building list, or None if missing/invalid."""
    try:
        with open(_buildings_cache_path(session, is_units)) as f:
            data = json.load(f)
        if data.get("version") == _BUILDINGS_CACHE_VERSION and data.get("buildings"):
            return data["buildings"]
    except (OSError, json.JSONDecodeError, KeyError):
        pass
    return None


def _save_buildings_cache(session, is_units, buildings):
    path = _buildings_cache_path(session, is_units)
    tmp = path + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump({"version": _BUILDINGS_CACHE_VERSION,
                       "scanned_at": int(time.time()),
                       "buildings": buildings}, f)
        os.replace(tmp, path)
    except OSError:
        pass


def _clear_buildings_cache(session, is_units):
    try:
        os.remove(_buildings_cache_path(session, is_units))
    except OSError:
        pass


def _scan_buildings(session, is_units, all_city_ids, cities):
    """Scan every city for barracks/shipyards; returns static building list.

    Only needs to run once (or after a level-up / new barracks).  The loop
    then calls this result for city data each cycle to get resources and
    live busy state without re-enumerating all position slots.
    """
    building_type = "barracks" if is_units else "shipyard"
    buildings = []
    for cid in all_city_ids:
        city = cities.get(cid, {})
        try:
            html = session.get(city_url + str(cid))
            city_data = getCity(html)
            for slot in city_data.get('position', []):
                if slot.get('building') == building_type:
                    buildings.append({
                        'city_id':           cid,
                        'city_name':         city.get('name', str(cid)),
                        'building_position': slot.get('position'),
                        'building_level':    slot.get('level', 0),
                    })
        except Exception:
            continue
    return buildings


def _is_worker_running(session, is_units):
    """True if a fresh (non-stale) worker lock exists for this account/type."""
    wlock = _worker_lock_path(session, is_units)
    try:
        with open(wlock, "r") as f:
            data = json.load(f)
    except (IOError, json.JSONDecodeError):
        return False
    return time.time() - data.get("timestamp", 0) <= WORKER_LOCK_STALE_SECONDS


def _worker_status_line(session, is_units):
    """Return a coloured one-line worker status string."""
    kind = "Units" if is_units else "Ships"
    if _is_worker_running(session, is_units):
        status = f"{bcolors.GREEN}RUNNING{bcolors.ENDC}"
    else:
        status = f"{bcolors.WARNING}STOPPED{bcolors.ENDC}"
    return f"{kind} Worker: {status}"


def _touch_wake_flag(session, is_units):
    """Signal the running worker to skip its current sleep and re-check now."""
    try:
        with open(_wake_flag_path(session, is_units), "w") as f:
            f.write(str(int(time.time())))
    except OSError:
        pass


def _consume_wake_flag(session, is_units):
    """Remove the wake flag if present; return True if it existed."""
    path = _wake_flag_path(session, is_units)
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass
        return True
    return False


def _renew_worker_lock(session, is_units):
    """Overwrite the worker lock with the current timestamp (heartbeat)."""
    wlock = _worker_lock_path(session, is_units)
    try:
        with open(wlock, "w") as f:
            json.dump({"pid": os.getpid(), "timestamp": time.time()}, f)
    except OSError:
        pass


def _release_worker_lock(session, is_units):
    try:
        os.remove(_worker_lock_path(session, is_units))
    except OSError:
        pass


def _wait_or_wake(session, is_units, stop_event, total_seconds):
    """Sleep for up to total_seconds, returning early on wake flag or stop."""
    elapsed = 0
    while elapsed < total_seconds:
        if stop_event is not None and stop_event.is_set():
            return
        chunk = min(WAKE_POLL_SECONDS, total_seconds - elapsed)
        if stop_event is not None:
            stop_event.wait(chunk)
        else:
            time.sleep(chunk)
        elapsed += chunk
        if _consume_wake_flag(session, is_units):
            return


def _coerce_recruit_row(raw):
    row = dict(raw)
    for col in RECRUIT_INT_COLS:
        v = row.get(col, "")
        try:
            row[col] = int(v) if str(v).strip() != "" else 0
        except (TypeError, ValueError):
            row[col] = 0
    row["is_units"] = str(row.get("is_units", "True")).lower() in ("true", "1", "yes")
    if row.get("status", "") not in RECRUIT_VALID_STATUSES:
        row["status"] = "active"
    return row


def _coerce_recruit_row_out(row):
    out = {}
    for col in RECRUIT_COLUMNS:
        v = row.get(col, "")
        out[col] = "" if v is None else str(v)
    return out


def recruit_csv_load(session, is_units):
    """Load and return all rows from the goals CSV for this account/type."""
    path = _csv_path(session, is_units)
    try:
        with open(path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            rows = list(reader)
        if _OLD_CSV_SENTINEL in fieldnames:
            return _migrate_old_csv(session, is_units, rows)
        return [_coerce_recruit_row(r) for r in rows]
    except FileNotFoundError:
        return []


def recruit_csv_save(session, is_units, rows):
    """Atomically write all rows to the goals CSV."""
    path = _csv_path(session, is_units)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=RECRUIT_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(_coerce_recruit_row_out(r))
    os.replace(tmp, path)


def recruit_csv_has_active(session, is_units):
    return any(r["status"] == "active" for r in recruit_csv_load(session, is_units))


def recruit_csv_next_id(rows):
    if not rows:
        return 1
    return max(r["request_id"] for r in rows) + 1


def recruit_csv_add(session, is_units, unit_type_id, unit_name, qty, priority, notes=""):
    """Append a new goal row; merge with existing active row for same unit type."""
    rows = recruit_csv_load(session, is_units)
    now = int(time.time())
    for r in rows:
        if r["unit_type_id"] == unit_type_id and r["status"] == "active":
            r["qty_total"] += qty
            r["qty_remaining"] += qty
            r["priority"] = min(r["priority"], priority)
            r["last_updated"] = now
            recruit_csv_save(session, is_units, rows)
            return r["request_id"]
    new_id = recruit_csv_next_id(rows)
    rows.append({
        "request_id":   new_id,
        "unit_type_id": unit_type_id,
        "unit_name":    unit_name,
        "is_units":     is_units,
        "qty_total":    qty,
        "qty_remaining": qty,
        "priority":     priority,
        "status":       "active",
        "created_at":   now,
        "last_updated": now,
        "notes":        notes,
    })
    recruit_csv_save(session, is_units, rows)
    return new_id


def recruit_csv_deduct(session, is_units, request_id, qty_placed):
    """Subtract qty_placed from qty_remaining; mark completed when zero."""
    rows = recruit_csv_load(session, is_units)
    now = int(time.time())
    for r in rows:
        if r["request_id"] == request_id:
            r["qty_remaining"] = max(0, r["qty_remaining"] - qty_placed)
            r["last_updated"]  = now
            if r["qty_remaining"] == 0:
                r["status"] = "completed"
            break
    recruit_csv_save(session, is_units, rows)


def recruit_csv_update(session, is_units, request_id, **kwargs):
    rows = recruit_csv_load(session, is_units)
    for r in rows:
        if r["request_id"] == request_id:
            r.update(kwargs)
            r["last_updated"] = int(time.time())
            break
    recruit_csv_save(session, is_units, rows)


def recruit_csv_clear(session, is_units):
    path = _csv_path(session, is_units)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _migrate_old_csv(session, is_units, raw_rows):
    """
    Convert v1 per-building rows (with batch_id, city_id, etc.) to v2 goals rows.
    Groups pending/partial rows by unit_type_id, summing qty_remaining.
    Called automatically the first time an old CSV is opened.
    """
    from collections import defaultdict
    groups = defaultdict(lambda: {"qty_total": 0, "qty_remaining": 0, "priority": 9999, "unit_name": ""})
    now = int(time.time())

    for r in raw_rows:
        status = r.get("status", "pending")
        if status in ("ordered", "cancelled"):
            continue
        uid = r.get("unit_type_id", "")
        try:
            uid = int(uid)
        except (ValueError, TypeError):
            continue
        try:
            qty_rem = int(r.get("qty_remaining", 0))
            qty_tot = int(r.get("qty_total", 0))
            pri = int(r.get("priority", 1))
        except (ValueError, TypeError):
            continue
        g = groups[uid]
        g["qty_total"]     += qty_tot
        g["qty_remaining"] += qty_rem
        g["priority"]       = min(g["priority"], pri)
        if not g["unit_name"]:
            g["unit_name"] = r.get("unit_name", str(uid))

    new_rows = []
    for idx, (uid, g) in enumerate(groups.items(), start=1):
        if g["qty_remaining"] <= 0:
            continue
        new_rows.append({
            "request_id":    idx,
            "unit_type_id":  uid,
            "unit_name":     g["unit_name"],
            "is_units":      is_units,
            "qty_total":     g["qty_total"],
            "qty_remaining": g["qty_remaining"],
            "priority":      g["priority"],
            "status":        "active",
            "created_at":    now,
            "last_updated":  now,
            "notes":         "migrated from v1",
        })

    recruit_csv_save(session, is_units, new_rows)
    print(f"  {bcolors.GREEN}Migrated old CSV format -> {len(new_rows)} goal(s){bcolors.ENDC}")
    return [_coerce_recruit_row(r) for r in new_rows]


# ---------------------------------------------------------------------------
# Compatibility shims — used by progress report functions
# ---------------------------------------------------------------------------
def csv_load_orders(session, is_units):
    """Legacy alias for recruit_csv_load."""
    return recruit_csv_load(session, is_units)


def csv_has_active_orders(session, is_units):
    return recruit_csv_has_active(session, is_units)


# =============================================================================
# CONFIG MANAGEMENT
# =============================================================================

_DEFAULT_CONFIG = {
    "report_enabled":           False,
    "report_interval_hours":    4,
    "report_group_completed":   False,
    "resource_import_enabled":  False,
    "last_report_time":         0,
    # Batch size: each production order builds this fraction of the unit type's
    # CURRENT remaining (0.20 = 20%). A goal steps down over successive orders,
    # e.g. 1500 -> 300 -> 240 -> 192 ... which also spreads it across buildings.
    # 0 disables batching (each order uses the max the building can afford).
    "min_batch_pct":            0.20,
}


def load_config(session):
    try:
        with open(_config_path(session), "r", encoding="utf-8") as f:
            data = json.load(f)
        cfg = dict(_DEFAULT_CONFIG)
        cfg.update(data)
        return cfg
    except (FileNotFoundError, json.JSONDecodeError):
        return dict(_DEFAULT_CONFIG)


def save_config(session, cfg):
    with open(_config_path(session), "w", encoding="utf-8") as f:
        json.dump(cfg, f)


def delete_config(session):
    try:
        os.remove(_config_path(session))
    except FileNotFoundError:
        pass


# =============================================================================
# PROGRESS REPORTING
# =============================================================================

def _ascii_bar(current, total, width=16):
    """Return a text progress bar like [████████░░░░░░░░]"""
    if total <= 0:
        return '[' + '░' * width + ']'
    filled = min(width, int(width * current / total))
    return '[' + '█' * filled + '░' * (width - filled) + ']'


def _pct(current, total):
    if total <= 0:
        return "0.0"
    return f"{100 * current / total:.1f}"


def _parse_growth_from_townhall(text):
    """
    Extract hourly citizen growth from a Town Hall response.

    The growth value ONLY appears in the Town Hall popup HTML:
      <li id="js_TownHallPopulationGrowth" class="growth_positive">
        Growth: <span id="js_TownHallPopulationGrowthValue">59.49 </span> per Hour
      </li>
    The <li> class carries the sign (growth_negative when below zero); the
    <span> holds the magnitude. Returns a float (0.0 is valid) or None.
    """
    # The town hall arrives as an ajax JSON envelope; json.loads decodes the
    # escaped quotes back to real ones so the HTML regex below matches. Fall
    # back to the raw text if it isn't JSON.
    haystacks = []
    try:
        data = json.loads(text)
        stack = [data]
        while stack:
            o = stack.pop()
            if isinstance(o, str):
                haystacks.append(o)
            elif isinstance(o, dict):
                stack.extend(o.values())
            elif isinstance(o, list):
                stack.extend(o)
    except (ValueError, TypeError):
        haystacks = [text]

    for s in haystacks:
        val_m = re.search(
            r'id="js_TownHallPopulationGrowthValue"[^>]*>\s*([-\d.,]+)', s)
        if not val_m:
            continue
        try:
            magnitude = float(val_m.group(1).replace(',', ''))
        except ValueError:
            continue
        sign_m = re.search(
            r'id="js_TownHallPopulationGrowth"[^>]*class="([^"]*)"', s)
        negative = bool(sign_m) and "growth_negative" in sign_m.group(1)
        return -magnitude if negative else magnitude
    return None


def _fetch_citizen_growth(session, city_id):
    """
    Return (free_citizens, growth_per_hour) for a city.

    Free citizens come from the plain city view. The hourly growth rate is
    NOT present there — it is rendered only inside the Town Hall popup — so we
    open the Town Hall (always position 0, but resolved from city data to be
    safe) and parse its HTML. growth_per_hour is None only if that fetch fails.
    """
    try:
        html = session.get(city_url + str(city_id))

        # Free citizens — use getCity's parser rather than a local regex (a
        # loose fallback used to match a unit COST instead of the real count).
        free = 0

        # Resolve the Town Hall position (it is position 0 in every city, but
        # read it from the city data in case that ever changes).
        th_pos = 0
        try:
            city_data = getCity(html)
            free = int(city_data.get('freeCitizens', 0) or 0)
            for slot in city_data.get('position', []):
                if slot.get('building') == 'townHall':
                    th_pos = slot.get('position', 0)
                    break
        except Exception:
            pass

        # Open the Town Hall popup (ajax) and parse the growth value from it.
        growth = None
        try:
            params = (
                f"view=townHall&cityId={city_id}&position={th_pos}"
                f"&backgroundView=city&currentCityId={city_id}&ajax=1"
            )
            resp = session.post(params)
            growth = _parse_growth_from_townhall(resp)
        except Exception:
            growth = None

        return free, growth
    except Exception:
        return 0, None


def _calculate_recruitment_eta(session, all_rows):
    """
    Estimate when the full recruitment run will be done.

    Two-component model:
      ETA = citizen_wait_hours + build_time_hours

    citizen_wait: time until enough citizens accumulate to cover remaining units
                  (0 if citizens are already sufficient)
    build_time:   total unit-seconds across all goals, divided by the number of
                  parallel buildings (avg spu per unit type, weighted by qty)

    Both components come from the same barracks AJAX response already used by
    fetch_building_data: costs.citizens and costs.completiontime (stored as
    time_seconds).

    Returns a dict, or None if there are no active goals.
    """
    active = [r for r in all_rows if r["status"] == "active" and r["qty_remaining"] > 0]
    if not active:
        return None

    is_units = active[0]["is_units"]
    building_type = "barracks" if is_units else "shipyard"
    needed_uids = {r["unit_type_id"] for r in active}

    try:
        cities_ids, cities = getIdsOfCities(session)
    except Exception:
        return None

    # Building count — use the cache so we get ALL buildings, not just the first
    # city that happens to cover all needed unit types.
    cached_blds = _load_buildings_cache(session, is_units)
    num_buildings = len(cached_blds) if cached_blds else 0

    # Scan buildings for unit stats (citizens + spu per unit type).
    # We break early once every needed unit type has been sampled — that's fine
    # for stats, because the same unit type has the same citizen cost everywhere
    # and spu only varies with building level (averaged across sampled buildings).
    unit_stats = {}
    all_city_ids = set()

    for cid in cities_ids:
        try:
            html = session.get(city_url + str(cid))
            city_data = getCity(html)
        except Exception:
            continue
        for slot in city_data.get('position', []):
            if slot.get('building') != building_type:
                continue
            stub = {
                "city_id":           cid,
                "city_name":         cities[cid].get('name', str(cid)),
                "building_position": slot.get('position'),
                "building_level":    slot.get('level', 0),
                "is_busy":           bool(slot.get('isBusy', False)),
            }
            try:
                data = fetch_building_data(session, stub, is_units)
                if not data:
                    continue
                all_city_ids.add(cid)
                for uid, ud in data.get("unit_data", {}).items():
                    if uid not in needed_uids:
                        continue
                    if uid not in unit_stats:
                        unit_stats[uid] = {
                            "citizens":  ud.get("citizens", 0),
                            "spu_sum":   0.0,
                            "spu_count": 0,
                        }
                    spu = ud.get("time_seconds", 0)
                    if spu > 0:
                        unit_stats[uid]["spu_sum"]   += spu
                        unit_stats[uid]["spu_count"] += 1
            except Exception:
                pass
        if needed_uids <= set(unit_stats.keys()):
            break  # have cost/spu data for all needed unit types

    # --- Citizen bottleneck ---
    citizens_needed = sum(
        unit_stats.get(r["unit_type_id"], {}).get("citizens", 0) * r["qty_remaining"]
        for r in active
    )

    total_free        = 0
    known_growth_ph   = 0.0
    growth_ok_count   = 0
    growth_failed     = []  # city_ids whose growth couldn't be read
    city_ids_to_check = list(all_city_ids) or list(cities_ids)

    for cid in city_ids_to_check:
        free, growth = _fetch_citizen_growth(session, cid)
        total_free += free
        if growth is not None:
            known_growth_ph += growth
            growth_ok_count += 1
        else:
            growth_failed.append(cid)

    # Degrade gracefully: if SOME cities reported growth, estimate the total by
    # scaling the known average across all cities rather than blanking the ETA.
    # Only treat growth as fully unknown when EVERY city failed.
    growth_unknown = (growth_ok_count == 0)
    if growth_unknown:
        total_growth_ph = 0.0
    elif growth_failed:
        avg = known_growth_ph / growth_ok_count
        total_growth_ph = avg * len(city_ids_to_check)  # extrapolate missing
    else:
        total_growth_ph = known_growth_ph

    deficit = max(0, citizens_needed - total_free)
    citizen_wait_h = None
    if deficit == 0:
        citizen_wait_h = 0.0
    elif not growth_unknown and total_growth_ph > 0:
        citizen_wait_h = deficit / total_growth_ph

    # --- Build-time component ---
    # Total unit-seconds = sum(qty * avg_spu), parallelised across num_buildings.
    # avg_spu is averaged across all buildings that can train that unit type,
    # so buildings at different levels contribute naturally.
    total_build_s = 0.0
    build_unknown = False
    avg_spus = {}

    for r in active:
        uid   = r["unit_type_id"]
        stats = unit_stats.get(uid, {})
        count = stats.get("spu_count", 0)
        if count > 0:
            avg_spu = stats["spu_sum"] / count
        else:
            build_unknown = True
            avg_spu = 0.0
        avg_spus[uid] = avg_spu
        total_build_s += r["qty_remaining"] * avg_spu

    build_time_h = None
    if not build_unknown and num_buildings > 0:
        build_time_h = total_build_s / (3600.0 * num_buildings)

    # --- Combined ETA ---
    # citizen_wait (time before building can start) + build_time (parallel build)
    combined_eta_h = None
    if build_time_h is not None:
        cw = citizen_wait_h if citizen_wait_h is not None else 0.0
        if citizen_wait_h is None and growth_unknown:
            combined_eta_h = None  # can't estimate citizen component
        else:
            combined_eta_h = cw + build_time_h

    return {
        "citizens_needed":    citizens_needed,
        "citizens_available": total_free,
        "citizen_deficit":    deficit,
        "growth_per_hour":    total_growth_ph if not growth_unknown else None,
        "citizen_wait_hours": citizen_wait_h,
        "build_time_hours":   build_time_h,
        "num_buildings":      num_buildings,
        "combined_eta_hours": combined_eta_h,
        "growth_unknown":     growth_unknown,
        "growth_cities_ok":   growth_ok_count,
        "growth_cities_failed": len(growth_failed),
        "build_unknown":      build_unknown,
    }


def _format_hours(h):
    """Format a float number of hours as e.g. '2d 14h' or '3h 30m'."""
    h = max(0.0, h)
    days  = int(h // 24)
    hrs   = int(h % 24)
    mins  = int((h * 60) % 60)
    if days > 0:
        return f"{days}d {hrs}h"
    if hrs > 0:
        return f"{hrs}h {mins}m"
    return f"{mins}m"


def _build_progress_report(all_rows, group_completed=False, eta=None):
    """
    Build a progress report string from goals CSV rows (units and/or ships).
    Uses ASCII only to avoid encoding issues on Windows cp1252.
    """
    now_str = time.strftime("%d %b %Y %H:%M", time.localtime())
    lines = [f"AUTO RECRUITMENT MANAGER v{MODULE_VERSION} - {now_str}", ""]

    unit_rows = [r for r in all_rows if r["is_units"]  and r["status"] != "cancelled"]
    ship_rows = [r for r in all_rows if not r["is_units"] and r["status"] != "cancelled"]

    g_total = sum(r["qty_total"]     for r in unit_rows + ship_rows)
    g_rem   = sum(r["qty_remaining"] for r in unit_rows + ship_rows)
    g_ord   = g_total - g_rem

    if g_total == 0:
        lines.append("No active goals.")
        return "\n".join(lines)

    lines.append(
        f"OVERALL {_ascii_bar(g_ord, g_total)} {g_ord:,}/{g_total:,} "
        f"({_pct(g_ord, g_total)}%)"
    )
    lines.append("=" * 44)

    for label, rows in [("TROOPS", unit_rows), ("SHIPS", ship_rows)]:
        if not rows:
            continue
        sec_tot = sum(r["qty_total"]     for r in rows)
        sec_rem = sum(r["qty_remaining"] for r in rows)
        sec_ord = sec_tot - sec_rem
        lines.append(f"\n{label}  {_ascii_bar(sec_ord, sec_tot, 12)} {sec_ord:,}/{sec_tot:,}")

        completed = []
        active    = []
        for r in sorted(rows, key=lambda x: (x["priority"], x["unit_name"])):
            if r["status"] == "completed" or r["qty_remaining"] == 0:
                completed.append(r)
            else:
                active.append(r)

        for r in active:
            placed = r["qty_total"] - r["qty_remaining"]
            bar = _ascii_bar(placed, r["qty_total"], 10)
            pct = _pct(placed, r["qty_total"])
            lines.append(
                f"  {r['unit_name']:<22} {bar} "
                f"{placed:,}/{r['qty_total']:,} ({pct}%)  P{r['priority']}"
            )

        if group_completed and completed:
            names = ", ".join(r["unit_name"] for r in completed)
            lines.append(f"  COMPLETED: {names}")
        elif completed:
            for r in completed:
                lines.append(f"  {r['unit_name']:<22} DONE  {r['qty_total']:,}/{r['qty_total']:,}")

    lines.append("\n" + "=" * 44)

    # ETA block
    if eta:
        lines.append("\nESTIMATED COMPLETION")

        # Citizen component
        cn  = eta.get("citizens_needed", 0)
        ca  = eta.get("citizens_available", 0)
        def_ = eta.get("citizen_deficit", 0)
        cw_h = eta.get("citizen_wait_hours")
        gph  = eta.get("growth_per_hour")

        if cn > 0:
            lines.append(f"  Citizens : {ca:,} available / {cn:,} needed"
                         + (f"  ({def_:,} short)" if def_ > 0 else "  [sufficient]"))
            if def_ > 0:
                failed = eta.get("growth_cities_failed", 0)
                partial = "  [partial: {} cit(y/ies) unread]".format(failed) if failed else ""
                if eta.get("growth_unknown"):
                    lines.append("  Cit. wait: unknown (growth rate unavailable"
                                 " — could not read any Town Hall)")
                elif gph and gph > 0:
                    lines.append(f"  Cit. wait: ~{_format_hours(cw_h)}"
                                 f"  (growth ~{gph:,.0f}/hr){partial}")
                else:
                    lines.append("  Cit. wait: unknown (growth rate is 0)")
            else:
                lines.append("  Cit. wait: none")

        # Build-time component
        bt_h  = eta.get("build_time_hours")
        n_bld = eta.get("num_buildings", 0)
        if eta.get("build_unknown"):
            lines.append("  Build time: unknown (spu data unavailable)")
        elif bt_h is not None:
            lines.append(f"  Build time: ~{_format_hours(bt_h)}"
                         f"  ({n_bld} building{'s' if n_bld != 1 else ''} in parallel)")

        # Combined
        eta_h = eta.get("combined_eta_hours")
        if eta_h is not None:
            lines.append(f"  >> Total ETA: ~{_format_hours(eta_h)} from now")
        elif bt_h is not None and cw_h is None:
            lines.append("  >> Total ETA: ~" + _format_hours(bt_h)
                         + " + citizen wait (unknown)")
        else:
            lines.append("  >> Total ETA: cannot estimate")

        lines.append("  (estimate only - growth rate and load change over time)")

    return "\n".join(lines)


def send_progress_report(session, is_units, group_completed=False):
    """Send a combined units+ships progress report to Telegram."""
    unit_rows = recruit_csv_load(session, True)
    ship_rows = recruit_csv_load(session, False)
    all_rows  = unit_rows + ship_rows

    if not all_rows:
        return

    # ETA must be single-type: it scans one building type, so mixing unit and
    # ship goals would corrupt the citizen/build-time maths. Scope it to the
    # worker's own type (the one whose rows we can actually cost out).
    eta = None
    try:
        eta_rows = [r for r in all_rows if r["is_units"] == is_units]
        eta = _calculate_recruitment_eta(session, eta_rows)
    except Exception:
        pass

    report = _build_progress_report(all_rows, group_completed=group_completed, eta=eta)
    sendToBot(session, report)


def _maybe_send_report(session, is_units, cfg):
    """Send a progress report if the interval has elapsed. Updates config."""
    if not cfg.get("report_enabled"):
        return cfg
    interval_secs = cfg.get("report_interval_hours", 4) * 3600
    now = time.time()
    if now - cfg.get("last_report_time", 0) >= interval_secs:
        try:
            send_progress_report(
                session, is_units,
                group_completed=cfg.get("report_group_completed", False),
            )
        except Exception:
            pass
        cfg["last_report_time"] = int(now)
        try:
            save_config(session, cfg)
        except Exception:
            pass
    return cfg


# =============================================================================
# RTM INTEGRATION (resource import)
# =============================================================================

_rtm_module = None


def _try_load_rtm():
    """Locate and import resourceTransportManager*.py. Returns None if not found."""
    global _rtm_module
    if _rtm_module is not None:
        return _rtm_module
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = glob.glob(os.path.join(here, "resourceTransportManager*.py"))
    if not candidates:
        return None

    def _ver(fn):
        m = re.search(r'_v(\d+)\.(\d+)\.py$', fn)
        return (int(m.group(1)), int(m.group(2))) if m else None

    unversioned = [c for c in candidates if os.path.basename(c) == "resourceTransportManager.py"]
    if unversioned:
        target = unversioned[0]
    else:
        versioned = [(c, _ver(c)) for c in candidates if _ver(c)]
        target = sorted(versioned, key=lambda x: x[1])[-1][0] if versioned else candidates[-1]

    try:
        spec = importlib.util.spec_from_file_location("_recruit_rtm", target)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _rtm_module = mod
        return mod
    except Exception:
        return None


def _find_resource_surpluses(session, dest_city_id, needed, all_city_ids, cities):
    """
    Return a list of {city_id, city_name, can_send: [w,wi,mb,cr,su]}
    for cities that can spare resources needed by dest_city.

    With RRS: uses get_summary() once (not per-city get_available()), respects
    excluded cities, and honours minimum-ship-capacity from config.
    Without RRS: keeps a 30% local buffer as a conservative fallback.
    """
    # Fetch RRS state once for the whole scan — avoids repeated per-city API calls
    if RRS_AVAILABLE:
        rrs_summary  = rrs_get_summary(session)          # {city_id: {res_idx: reserved}}
        excluded     = {e["city_id"] for e in get_excluded_cities(session)}
        min_take     = rrs_get_config(session)["min_ship_capacity"]
    else:
        rrs_summary, excluded, min_take = {}, set(), 500

    surpluses = []
    for cid in all_city_ids:
        if cid == dest_city_id:
            continue
        if cid in excluded:
            continue
        try:
            html = session.get(city_url + str(cid))
            city_data = getCity(html)
        except Exception:
            continue
        avail = city_data.get('availableResources', [0] * 5)
        if len(avail) < 5:
            avail = avail + [0] * (5 - len(avail))

        can_send = []
        for i in range(5):
            if RRS_AVAILABLE:
                reserved = rrs_summary.get(cid, {}).get(i, 0)
                free = max(0, avail[i] - reserved)
            else:
                # 30% local buffer when RRS is unavailable
                free = int(avail[i] * 0.70)
            can_send.append(free)

        # Skip cities where no resource meets the minimum-take threshold
        if not any(can_send[i] >= min_take and needed[i] > 0 for i in range(5)):
            continue

        surpluses.append({
            'city_id':   cid,
            'city_name': cities[cid]['name'],
            'can_send':  can_send,
        })
    return surpluses


def _request_resource_import(session, dest_city_id, needed_res, all_city_ids, cities):
    """
    Create one-time RTM transport entries to supply *needed_res* to dest_city.
    Returns list of human-readable request descriptions, or [] on failure/unavailable.
    """
    rtm = _try_load_rtm()
    if rtm is None:
        return []

    surpluses = _find_resource_surpluses(session, dest_city_id, needed_res, all_city_ids, cities)
    if not surpluses:
        return []

    requests = []
    remaining = list(needed_res)

    for sup in surpluses:
        if all(r <= 0 for r in remaining):
            break
        to_req = [min(remaining[i], sup['can_send'][i]) for i in range(5)]
        if all(r <= 0 for r in to_req):
            continue
        try:
            rows = rtm.transport_csv_load(session)
            new_id = rtm.next_schedule_id(rows) if rows else 1
            row = rtm.build_schedule_row(
                schedule_id=new_id,
                mode="distribute",
                source_city_ids=[sup['city_id']],
                dest_city_ids=[dest_city_id],
                resource_config=to_req,
                send_mode="na",
                interval_hours=0,
                notes=f"{MODULE_NAME} resource import",
            )
            rtm.transport_csv_append(session, row)
            for i in range(5):
                remaining[i] = max(0, remaining[i] - to_req[i])
            res_desc = ", ".join(
                f"{RESOURCE_NAMES[i]} {to_req[i]:,}"
                for i in range(5) if to_req[i] > 0
            )
            requests.append(f"{sup['city_name']} -> {res_desc}")
        except AttributeError as e:
            print(f"  RTM interface mismatch (function not found): {e}")
        except Exception as e:
            print(f"  RTM schedule append failed for {sup.get('city_name', '?')}: {e}")

    return requests


# =============================================================================
# BUILDING DISCOVERY
# =============================================================================

def find_all_buildings(session, cities_ids, cities, building_type):
    """Find all barracks or shipyards across all cities."""
    buildings = []
    for city_id in cities_ids:
        city = cities[city_id]
        try:
            html = session.get(city_url + str(city_id))
            city_data = getCity(html)
        except Exception:
            continue
        for slot in city_data.get('position', []):
            if slot.get('building') == building_type:
                buildings.append({
                    'city_id':           city_id,
                    'city_name':         city['name'],
                    'building_position': slot.get('position'),
                    'building_level':    slot.get('level', 0),
                    'is_busy':           bool(slot.get('isBusy', False)),
                    'queue_remaining_time': 0,
                })
    return buildings


def fetch_building_data(session, building_info, is_units=True):
    """
    Fetch slider/cost data from a barracks or shipyard view.
    busy status is taken from building_info (caller must provide fresh city data).
    """
    city_id = building_info['city_id']
    position = building_info['building_position']
    view_name = 'barracks' if is_units else 'shipyard'
    params = (
        f"view={view_name}&cityId={city_id}&position={position}"
        f"&backgroundView=city&currentCityId={city_id}&ajax=1"
    )
    try:
        response = session.post(params)
        response_data = json.loads(response)
    except Exception:
        return None

    result = {
        'city_id':              city_id,
        'city_name':            building_info['city_name'],
        'building_position':    position,
        'building_level':       building_info['building_level'],
        'is_busy':              building_info.get('is_busy', False),
        'queue_remaining_time': building_info.get('queue_remaining_time', 0),
        'unit_data':            {},
        'action_code':          None,
    }

    template_data = None
    for item in response_data:
        if not isinstance(item, list) or len(item) < 2:
            continue
        if item[0] == "updateGlobalData" and isinstance(item[1], dict):
            result['action_code'] = item[1].get("actionRequest")
        elif item[0] == "updateTemplateData" and isinstance(item[1], dict):
            template_data = item[1]

    if not template_data:
        return None

    slider_prefix = "js_barracksSlider"  # game reuses this key name for shipyard too
    for key, value in template_data.items():
        if not key.startswith(slider_prefix) or not isinstance(value, dict):
            continue
        slider_info = value.get('slider', {})
        if not isinstance(slider_info, dict):
            continue
        control_data_str = slider_info.get('control_data', '')
        if not control_data_str:
            continue
        try:
            cd = json.loads(control_data_str)
            uid = cd.get('unit_type_id')
            if not uid:
                continue
            costs = cd.get('costs', {})
            # The game returns some numeric fields as strings (e.g. max_value,
            # and occasionally costs). Coerce everything used in arithmetic.
            result['unit_data'][uid] = {
                'name':          cd.get('local_name', ''),
                'identifier':    cd.get('identifier', ''),
                'citizens':      _to_num(costs.get('citizens', 0)),
                'wood':          _to_num(costs.get('wood', 0)),
                'wine':          _to_num(costs.get('wine', 0)),
                'marble':        _to_num(costs.get('marble', 0)),
                'crystal':       _to_num(costs.get('crystal', 0)),
                'sulfur':        _to_num(costs.get('sulfur', 0)),
                'upkeep':        _to_num(costs.get('upkeep', 0)),
                'time_seconds':  _to_num(costs.get('completiontime', 0)),
                'max_buildable': _to_num(slider_info.get('max_value', 0)),
            }
        except (json.JSONDecodeError, KeyError, TypeError):
            continue

    return result


def _fetch_fresh_action_code(session, building, is_units):
    """Always fetch a live action code before POSTing — codes expire."""
    city_id = building['city_id']
    position = building['building_position']
    view_name = 'barracks' if is_units else 'shipyard'
    params = (
        f"view={view_name}&cityId={city_id}&position={position}"
        f"&backgroundView=city&currentCityId={city_id}&ajax=1"
    )
    try:
        response = session.post(params)
        response_data = json.loads(response)
        for item in response_data:
            if isinstance(item, list) and item[0] == "updateGlobalData" and isinstance(item[1], dict):
                code = item[1].get("actionRequest")
                if code:
                    building['action_code'] = code
                    return code
    except Exception:
        pass
    return None


def format_time(seconds):
    if seconds <= 0:
        return "0s"
    seconds = int(seconds)
    parts = []
    if seconds // 3600:
        parts.append(f"{seconds // 3600}h")
    if (seconds % 3600) // 60:
        parts.append(f"{(seconds % 3600) // 60}m")
    if seconds % 60 or not parts:
        parts.append(f"{seconds % 60}s")
    return ' '.join(parts)


# =============================================================================
# DISTRIBUTION CALCULATION
# =============================================================================

def calculate_distribution(buildings_data, recruitment_order):
    """Distribute units across buildings proportional to training speed."""
    if not buildings_data or not recruitment_order:
        return None

    for building in buildings_data:
        building['assignments'] = {}
        building['estimated_time'] = 0

    for unit_idx, unit_info in recruitment_order.items():
        total_qty = unit_info['quantity']
        capable = [b for b in buildings_data if unit_idx in b.get('unit_data', {})]
        if not capable:
            continue

        speeds = []
        total_speed = 0
        for b in capable:
            t = b['unit_data'][unit_idx]['time_seconds']
            s = (1.0 / t) if t > 0 else 1.0
            speeds.append(s)
            total_speed += s

        remaining = total_qty
        for i, b in enumerate(capable):
            if i == len(capable) - 1:
                qty = remaining
            else:
                qty = int(total_qty * (speeds[i] / total_speed)) if total_speed > 0 else (total_qty // len(capable))
                remaining -= qty
            if qty > 0:
                b['assignments'][unit_idx] = qty

    for building in buildings_data:
        t = sum(
            building['unit_data'][uid]['time_seconds'] * qty
            for uid, qty in building.get('assignments', {}).items()
            if uid in building.get('unit_data', {})
        )
        if building.get('is_busy', False):
            t += building.get('queue_remaining_time', 0)
        if building.get('assignments'):
            t += 10 * len(building['assignments'])
        building['estimated_time'] = t

    return balance_distribution(buildings_data, tolerance=1800)


def balance_distribution(buildings_data, tolerance=1800):
    """Iteratively move units from the slowest building to the fastest."""
    for _ in range(100):
        times = []
        for b in buildings_data:
            t = sum(
                b['unit_data'][uid]['time_seconds'] * qty
                for uid, qty in b.get('assignments', {}).items()
                if uid in b.get('unit_data', {})
            )
            if b.get('is_busy', False):
                t += b.get('queue_remaining_time', 0)
            if b.get('assignments'):
                t += 10 * len(b['assignments'])
            b['estimated_time'] = t
            times.append(t)

        if not times or max(times) - min(times) <= tolerance:
            break

        fi = times.index(min(times))
        si = times.index(max(times))
        fastest, slowest = buildings_data[fi], buildings_data[si]

        moved = False
        for uid in list(slowest.get('assignments', {}).keys()):
            if uid not in fastest.get('unit_data', {}):
                continue
            qty = slowest['assignments'].get(uid, 0)
            if qty <= 10:
                continue
            move = min(10, qty - 1)
            slowest['assignments'][uid] -= move
            if slowest['assignments'][uid] <= 0:
                del slowest['assignments'][uid]
            fastest['assignments'].setdefault(uid, 0)
            fastest['assignments'][uid] += move
            moved = True
            break

        if not moved:
            break

    return buildings_data


# =============================================================================
# RESOURCE CHECKING
# =============================================================================

def check_resources(session, distribution, cities):
    """Check if each city has enough resources and citizens for the full order."""
    result = {'can_fulfill': True, 'missing_resources': {}, 'missing_citizens': {}, 'available': {}}

    city_req = {}
    for b in distribution:
        cid = b['city_id']
        city_req.setdefault(cid, {'citizens': 0, 'wood': 0, 'wine': 0, 'marble': 0, 'crystal': 0, 'sulfur': 0})
        for uid, qty in b.get('assignments', {}).items():
            ud = b.get('unit_data', {}).get(uid, {})
            for res in ('citizens', 'wood', 'wine', 'marble', 'crystal', 'sulfur'):
                city_req[cid][res] += ud.get(res, 0) * qty

    for cid, req in city_req.items():
        try:
            html = session.get(city_url + str(cid))
            city_data = getCity(html)
        except Exception:
            continue
        avail = city_data.get('availableResources', [0] * 5)
        if len(avail) < 5:
            avail = avail + [0] * (5 - len(avail))

        # getCity's parser — not a local regex (see note in the main loop).
        available_citizens = int(city_data.get('freeCitizens', 0) or 0)

        result['available'][cid] = {
            'resources': dict(zip(('wood','wine','marble','crystal','sulfur'), avail)),
            'citizens':  available_citizens,
        }

        if req['citizens'] > available_citizens:
            result['missing_citizens'][cid] = req['citizens'] - available_citizens
            result['can_fulfill'] = False

        missing = {
            name: req[key] - avail[i]
            for i, (name, key) in enumerate(zip(RESOURCE_NAMES, ('wood','wine','marble','crystal','sulfur')))
            if req[key] > avail[i]
        }
        if missing:
            result['missing_resources'][cid] = missing
            result['can_fulfill'] = False

    return result


# =============================================================================
# DISPLAY HELPERS
# =============================================================================

def display_distribution_plan(distribution, recruitment_order):
    print("Order Summary:")
    print("-" * 40)
    for uid, info in recruitment_order.items():
        print(f"  {info['name']}: {addThousandSeparator(info['quantity'])}")
    print()
    print("Distribution by Building:")
    print("-" * 40)
    for b in distribution:
        if not b.get('assignments'):
            continue
        busy_str = " (WAITING FOR QUEUE)" if b.get('is_busy') else ""
        print(f"\n  {b['city_name']} - L{b['building_level']}{busy_str}")
        print(f"  Est. time: {format_time(int(b.get('estimated_time', 0)))}")
        for uid, qty in b['assignments'].items():
            name = recruitment_order.get(uid, {}).get('name') \
                   or b.get('unit_data', {}).get(uid, {}).get('name') \
                   or f"Unit {uid}"
            print(f"    • {name}: {addThousandSeparator(qty)}")


# =============================================================================
# QUEUE BUILD FROM DISTRIBUTION
# =============================================================================

def _build_orders_from_distribution(distribution, is_units, session_id, priority, existing_rows=None):
    """
    Create CSV rows from a distribution plan.
    *existing_rows* lets resume carry over saved qty_remaining values.
    """
    existing_map = {}
    if existing_rows:
        for r in existing_rows:
            existing_map[(r["city_id"], r["building_position"], r["unit_type_id"])] = r

    rows = []
    next_id = csv_next_recruit_id(existing_rows or [])
    batch_id = csv_next_batch_id(existing_rows or []) if not existing_rows else (
        csv_next_batch_id(existing_rows)
    )
    now = int(time.time())

    for b in distribution:
        for uid, qty in b.get("assignments", {}).items():
            key = (b["city_id"], b["building_position"], uid)
            if key in existing_map:
                ex = existing_map[key]
                rows.append({**ex, "session_id": session_id, "last_updated": now})
            else:
                rows.append({
                    "recruit_id":       next_id,
                    "batch_id":         batch_id,
                    "session_id":       session_id,
                    "city_id":          b["city_id"],
                    "city_name":        b["city_name"],
                    "building_position": b["building_position"],
                    "building_level":   b["building_level"],
                    "is_units":         is_units,
                    "unit_type_id":     uid,
                    "unit_name":        b.get("unit_data", {}).get(uid, {}).get("name", str(uid)),
                    "qty_total":        qty,
                    "qty_remaining":    qty,
                    "priority":         priority,
                    "status":           "pending",
                    "created_at":       now,
                    "last_updated":     now,
                })
                next_id += 1

    return rows


def _load_distribution_from_csv(session, is_units, rows):
    """
    Reconstruct a distribution list from saved CSV rows by re-fetching building data.
    Returns (distribution, recruitment_order) or (None, None) on failure.
    """
    building_keys = {}
    for r in rows:
        if r["status"] in ("ordered", "cancelled"):
            continue
        key = (r["city_id"], r["building_position"])
        if key not in building_keys:
            building_keys[key] = {
                "city_id":           r["city_id"],
                "city_name":         r["city_name"],
                "building_position": r["building_position"],
                "building_level":    r["building_level"],
            }

    if not building_keys:
        return None, None

    print("Re-fetching building data for resume...")
    distribution = []
    building_type = "barracks" if is_units else "shipyard"

    for key, binfo in building_keys.items():
        print(f"  {binfo['city_name']} - {building_type} L{binfo['building_level']}...", end=" ")
        data = fetch_building_data(session, binfo, is_units)
        if data is None:
            print(f"{bcolors.RED}FAILED{bcolors.ENDC}")
            continue
        data["assignments"] = {}
        for r in rows:
            if r["city_id"] != binfo["city_id"] or r["building_position"] != binfo["building_position"]:
                continue
            if r["status"] in ("ordered", "cancelled") or r["qty_remaining"] <= 0:
                continue
            data["assignments"][r["unit_type_id"]] = r["qty_remaining"]
        if not data["assignments"]:
            print(f"{bcolors.GREEN}done{bcolors.ENDC}")
            continue
        print(f"{bcolors.GREEN}OK{bcolors.ENDC}")
        distribution.append(data)

    if not distribution:
        return None, None

    recruitment_order = {}
    for r in rows:
        if r["unit_type_id"] not in recruitment_order:
            recruitment_order[r["unit_type_id"]] = {"name": r["unit_name"], "quantity": r["qty_total"]}

    return distribution, recruitment_order


# =============================================================================
# QUEUE / GOALS MANAGEMENT UI
# =============================================================================

def _show_queue_status(session, is_units):
    """Print goals progress table (active goals only)."""
    kind = "Units" if is_units else "Ships"
    rows = recruit_csv_load(session, is_units)
    active    = [r for r in rows if r["status"] == "active"]
    completed = [r for r in rows if r["status"] == "completed"]

    if not active and not completed:
        print(f"  No {kind.lower()} goals set.")
        return

    if not active:
        n = len(completed)
        total = sum(r["qty_total"] for r in completed)
        print(f"  All goals placed ({n} goal{'s' if n != 1 else ''}, "
              f"{total:,} {kind.lower()} in build queue).")
        return

    print(f"  {'ID':>4}  {'Pri':>3}  {'Unit':<22}  {'Placed':>8}  {'Goal':>8}  {'%':>6}  {'':8}")
    print("  " + "-" * 68)
    for r in sorted(active, key=lambda x: (x["priority"], x["unit_name"])):
        placed = r["qty_total"] - r["qty_remaining"]
        pct = _pct(placed, r["qty_total"])
        bar = _ascii_bar(placed, r["qty_total"], 8)
        print(f"  {r['request_id']:>4}  {r['priority']:>3}  {r['unit_name']:<22}  "
              f"{placed:>8,}  {r['qty_total']:>8,}  {pct:>5}%  {bar}")

    if completed:
        names = ", ".join(r["unit_name"] for r in completed)
        print(f"\n  Completed: {names}")


def _view_building_status(session, is_units):
    """
    Fetch live game queue status for every building of this type and show:
    - whether it's busy (and time remaining)
    - what the loop would assign next from the goals CSV
    """
    building_type = "barracks" if is_units else "shipyard"
    kind = "Units" if is_units else "Ships"

    print(f"\n  Fetching building status for {kind.lower()}...")
    try:
        cities_ids, cities = getIdsOfCities(session)
    except Exception as e:
        print(f"  {bcolors.RED}Could not fetch cities: {e}{bcolors.ENDC}")
        return

    # Load active goals sorted by priority for "next to build" prediction
    goals = sorted(
        [r for r in recruit_csv_load(session, is_units) if r["status"] == "active" and r["qty_remaining"] > 0],
        key=lambda r: (r["priority"], r["request_id"]),
    )

    print()
    total_buildings = 0
    total_idle = 0
    total_busy = 0

    for cid in cities_ids:
        city_name = cities[cid]['name']
        city_buildings = []
        try:
            html = session.get(city_url + str(cid))
            city_data = getCity(html)
        except Exception:
            continue
        for slot in city_data.get('position', []):
            if slot.get('building') != building_type:
                continue
            city_buildings.append({
                'city_id':           cid,
                'city_name':         city_name,
                'building_position': slot.get('position'),
                'building_level':    slot.get('level', 0),
                'is_busy':           bool(slot.get('isBusy', False)),
            })

        if not city_buildings:
            continue

        # Citizen growth diagnostic (read from the Town Hall). Shows whether
        # growth is readable per city so a failing city is easy to spot.
        free_cit, growth = _fetch_citizen_growth(session, cid)
        if growth is None:
            growth_str = f"{bcolors.RED}growth: unreadable{bcolors.ENDC}"
        else:
            growth_str = f"growth: {growth:+.2f}/hr"
        print(f"  {bcolors.BOLD}{city_name}{bcolors.ENDC} "
              f"({len(city_buildings)} {building_type})  |  "
              f"{free_cit:,} free citizens, {growth_str}")

        for b in sorted(city_buildings, key=lambda x: -x['building_level']):
            total_buildings += 1
            if b['is_busy']:
                total_busy += 1
                # Fetch full data for queue time
                data = fetch_building_data(session, b, is_units)
                qt = data.get('queue_remaining_time', 0) if data else 0
                qt_str = f"  queue ~{format_time(qt)}" if qt else "  (queue time unknown)"
                print(f"    L{b['building_level']:>2}  {bcolors.WARNING}BUILDING{bcolors.ENDC}{qt_str}")
            else:
                total_idle += 1
                # Fetch unit_data to find what we'd build next
                data = fetch_building_data(session, b, is_units)
                next_unit = "(no goals match this building)"
                if data and goals:
                    for g in goals:
                        if g['unit_type_id'] in data.get('unit_data', {}):
                            next_unit = (f"{g['unit_name']}  "
                                         f"{g['qty_remaining']:,} remaining  P{g['priority']}")
                            break
                elif not goals:
                    next_unit = "(no active goals)"
                print(f"    L{b['building_level']:>2}  {bcolors.GREEN}IDLE{bcolors.ENDC}"
                      f"  -> {next_unit}")

        print()

    print(f"  Summary: {total_buildings} buildings total  |  "
          f"{bcolors.GREEN}{total_idle} idle{bcolors.ENDC}  |  "
          f"{bcolors.WARNING}{total_busy} building{bcolors.ENDC}")
    if goals:
        total_rem = sum(g["qty_remaining"] for g in goals)
        print(f"  Goals remaining: {total_rem:,} units across {len(goals)} goal(s)")
    else:
        print(f"  No active goals — add some with (1) Add to goals")


def _add_to_goals_ui(session, is_units):
    """
    Add one or more unit-type goals to the goals CSV.
    Scans buildings once to find available unit types, then asks quantities.
    """
    building_type = "barracks" if is_units else "shipyard"
    unit_list = UNITS_ORDER if is_units else SHIPS_ORDER

    print(f"\n  Scanning for available {building_type}s...")
    try:
        cities_ids, cities = getIdsOfCities(session)
    except Exception as e:
        print(f"  {bcolors.RED}Could not fetch cities: {e}{bcolors.ENDC}")
        return False

    all_buildings = find_all_buildings(session, cities_ids, cities, building_type)
    if not all_buildings:
        print(f"  {bcolors.RED}No {building_type} found.{bcolors.ENDC}")
        return False

    # Collect available unit types from a sampling of buildings
    unit_info_map = {}  # uid → {name, time_seconds, citizens, ...}
    for b in all_buildings[:3]:  # sample first 3 to find unit types quickly
        data = fetch_building_data(session, b, is_units)
        if data:
            for uid, ud in data.get('unit_data', {}).items():
                if uid not in unit_info_map:
                    unit_info_map[uid] = ud

    if not unit_info_map:
        print(f"  {bcolors.RED}Could not fetch unit data.{bcolors.ENDC}")
        return False

    print()
    print("  Enter quantity for each unit type (0 or Enter to skip):")
    print("  (') Cancel at any prompt")
    print()

    added = 0
    for unit in unit_list:
        uid = unit['game_index']
        if uid not in unit_info_map:
            continue
        ud = unit_info_map[uid]
        costs = []
        for res in ('citizens', 'wood', 'wine', 'marble', 'crystal', 'sulfur'):
            if ud.get(res, 0):
                costs.append(f"{ud[res]} {res}")
        t = format_time(int(ud.get('time_seconds', 0)))
        cost_str = f" [{', '.join(costs)}, {t}/unit]" if costs else f" [{t}/unit]"

        qty = read(msg=f"  {unit['name']}{cost_str}: ",
                   min=0, digit=True, additionalValues=["'", ""])
        if qty == "'":
            return False
        if qty == "" or qty == 0:
            continue

        existing = [r for r in recruit_csv_load(session, is_units)
                    if r["status"] == "active"]
        existing_pris = sorted({r["priority"] for r in existing})
        if existing_pris:
            print(f"    Existing priorities: {existing_pris}")

        pri_raw = read(msg="  Priority (1=highest, default 1): ",
                       min=1, digit=True, additionalValues=["'", ""])
        if pri_raw == "'":
            return False
        priority = pri_raw if isinstance(pri_raw, int) else 1

        request_id = recruit_csv_add(session, is_units, uid, unit['name'], qty, priority)
        print(f"  {bcolors.GREEN}Added {qty:,} {unit['name']} (goal #{request_id}).{bcolors.ENDC}")
        added += 1

    if added == 0:
        print("  No units entered.")
    return added > 0


def _remove_goal_ui(session, is_units):
    """Remove or reduce an active goal."""
    rows = recruit_csv_load(session, is_units)
    active = [r for r in rows if r["status"] == "active"]
    if not active:
        print(f"\n  {bcolors.WARNING}No active goals to remove.{bcolors.ENDC}")
        return

    print()
    _show_queue_status(session, is_units)
    print()
    goal_ids = [r["request_id"] for r in active]
    sel = read(msg="  Enter goal ID to remove/reduce (or ' to cancel): ",
               min=1, digit=True, additionalValues=["'"])
    if sel == "'":
        return
    if sel not in goal_ids:
        print(f"  {bcolors.RED}ID {sel} not found.{bcolors.ENDC}")
        return

    goal = next(r for r in active if r["request_id"] == sel)
    print(f"\n  Goal: {goal['unit_name']}  {goal['qty_remaining']:,} remaining")
    print(f"  (1) Cancel completely")
    print(f"  (2) Reduce quantity")
    print(f"  (') Back")
    action = read(msg="  Select: ", min=1, max=2, digit=True, additionalValues=["'"])
    if action == "'":
        return

    if action == 1:
        recruit_csv_update(session, is_units, sel, status="cancelled")
        print(f"  {bcolors.WARNING}Goal cancelled.{bcolors.ENDC}")
    elif action == 2:
        reduce_raw = read(
            msg=f"  Reduce by how many (max {goal['qty_remaining']:,})? ",
            min=1, max=goal['qty_remaining'], digit=True, additionalValues=["'"]
        )
        if reduce_raw == "'":
            return
        new_rem = max(0, goal['qty_remaining'] - reduce_raw)
        new_tot = max(0, goal['qty_total'] - reduce_raw)
        if new_rem == 0:
            recruit_csv_update(session, is_units, sel, qty_remaining=0,
                               qty_total=new_tot, status="completed")
            print(f"  {bcolors.GREEN}Goal marked complete.{bcolors.ENDC}")
        else:
            recruit_csv_update(session, is_units, sel, qty_remaining=new_rem,
                               qty_total=new_tot)
            print(f"  {bcolors.GREEN}Reduced to {new_rem:,} remaining.{bcolors.ENDC}")


def _adjust_priority_ui(session, is_units):
    """Adjust the priority of a goal."""
    rows = recruit_csv_load(session, is_units)
    active = [r for r in rows if r["status"] == "active"]
    if not active:
        print(f"\n  {bcolors.WARNING}No active goals.{bcolors.ENDC}")
        return

    print()
    _show_queue_status(session, is_units)
    print()
    goal_ids = [r["request_id"] for r in active]
    sel = read(msg="  Enter goal ID to reprioritise (or ' to cancel): ",
               min=1, digit=True, additionalValues=["'"])
    if sel == "'":
        return
    if sel not in goal_ids:
        print(f"  {bcolors.RED}ID {sel} not found.{bcolors.ENDC}")
        return

    goal = next(r for r in active if r["request_id"] == sel)
    new_pri = read(msg=f"  New priority for {goal['unit_name']} (1=highest): ",
                   min=1, digit=True, additionalValues=["'"])
    if new_pri == "'":
        return
    recruit_csv_update(session, is_units, sel, priority=new_pri)
    print(f"  {bcolors.GREEN}Priority updated to {new_pri}.{bcolors.ENDC}")


# ---------------------------------------------------------------------------
# Legacy shim wrappers used by old code paths still in _activate_worker etc.
# ---------------------------------------------------------------------------
def _add_to_queue_ui(session, is_units, cities_ids=None, cities=None):
    return _add_to_goals_ui(session, is_units)



# =============================================================================
# RECRUITMENT EXECUTION
# =============================================================================

def execute_recruitment_loop(session, is_units, cfg, stop_event=None):
    """
    Background recruitment loop (goals-based, dynamic building assignment).

    Each cycle:
    1. Reload goals CSV — picks up any live changes
    2. Per-city pass: fetch resources + enumerate all barracks/shipyards
    3. For each idle building: match the highest-priority active goal that
       this building can train, calculate affordable quantity (>= 1 unit),
       POST BuildUnits, then decrement the goals CSV
    4. Request resource imports if enabled and nothing could be placed
    5. Sleep (interruptible by wake flag or stop_event)
    """
    building_type = "barracks" if is_units else "shipyard"
    building_noun = "units" if is_units else "ships"

    # Fetch city list once — cities rarely change mid-session
    cities_ids, cities = getIdsOfCities(session)
    all_city_ids = list(cities_ids)

    # Load cached building list; scan once if missing
    cached_buildings = _load_buildings_cache(session, is_units)
    if not cached_buildings:
        session.setStatus(f"Scanning {building_type}s across all cities (one-time)...")
        cached_buildings = _scan_buildings(session, is_units, all_city_ids, cities)
        if cached_buildings:
            _save_buildings_cache(session, is_units, cached_buildings)
        else:
            sendToBot(session, f"Auto Recruitment: no {building_type}s found — aborting.")
            return

    # (city_id, building_position) -> unit_data dict; cleared on busy→idle transition
    bld_unit_data_cache = {}
    bld_was_busy = {}

    import_requested_at = {}
    IMPORT_COOLDOWN_SECS = 2 * 3600

    consecutive_errors = 0
    MAX_ERRORS = 10

    last_order_time = time.time()
    stall_warned = False
    STALL_WARN_SECS = 24 * 3600
    idle_notified = False  # avoid spamming "all goals placed" each idle cycle
    first_report_sent = False  # hold the very first report until troops are
                               # ordered — a maxed-out city reads 0 growth until
                               # production starts consuming its population, so
                               # the first ETA must be taken AFTER ordering.

    while True:
        # --- Check stop flag ---
        if os.path.exists(_stop_flag_path(session)):
            try:
                os.remove(_stop_flag_path(session))
            except OSError:
                pass
            session.setStatus("Auto Recruitment Manager: stopped by user")
            break

        # --- Reload config each cycle so settings changes (batch %, reports,
        #     import) apply live without restarting the worker. ---
        cfg = load_config(session)
        try:
            batch_pct = float(cfg.get("min_batch_pct", 0.20))
        except (TypeError, ValueError):
            batch_pct = 0.20
        batch_pct = max(0.0, min(1.0, batch_pct))

        # --- Reload goals CSV each cycle ---
        all_rows = recruit_csv_load(session, is_units)
        active_goals = [r for r in all_rows
                        if r["status"] == "active" and r["qty_remaining"] > 0]

        if not active_goals:
            # All goals placed — stay alive so new goals added to the CSV are
            # picked up automatically without restarting the worker.
            if not idle_notified:
                session.setStatus("Auto Recruitment: all goals placed — idle")
                sendToBot(session,
                    "Auto Recruitment: all goals placed into build queue.\n"
                    "Worker staying alive — add new goals or stop with (o).")
                idle_notified = True
            _wait_or_wake(session, is_units, stop_event, 300)
            _renew_worker_lock(session, is_units)
            continue

        idle_notified = False  # reset when goals are present again

        # --- Per-city pass: resources + live slot data (one HTTP call per city) ---
        # Building positions come from the cache; we only need the slot map for
        # resources and busy/queue state — no re-enumeration needed.
        city_resources = {}
        city_pos_map = {}   # cid -> {position -> slot dict}
        fetch_error = False

        for cid in all_city_ids:
            try:
                html = session.get(city_url + str(cid))
                city_data = getCity(html)

                avail = city_data.get('availableResources', [0] * 5)
                if len(avail) < 5:
                    avail = avail + [0] * (5 - len(avail))

                # Use getCity's own freeCitizens parser. Do NOT re-parse with a
                # local regex: the old fallback r'"citizens"\s*:\s*(\d+)' matched
                # the first "citizens": in the page, which is a unit COST (1-2),
                # not the free-citizen count — so citizens//cost came out as 1
                # and only 1-2 troops were ever ordered.
                citizens = int(city_data.get('freeCitizens', 0) or 0)

                if RRS_AVAILABLE:
                    res_avail = []
                    for i in range(5):
                        snap, _ = get_reservation_snapshot(session, cid, i, avail[i])
                        res_avail.append(snap)
                else:
                    res_avail = list(avail[:5])

                city_resources[cid] = {
                    'citizens': citizens,
                    'wood':    res_avail[0], 'wine':    res_avail[1],
                    'marble':  res_avail[2], 'crystal': res_avail[3],
                    'sulfur':  res_avail[4],
                }

                city_pos_map[cid] = {
                    slot.get('position'): slot
                    for slot in city_data.get('position', [])
                    if slot.get('position') is not None
                }

                consecutive_errors = 0
            except Exception as e:
                consecutive_errors += 1
                if consecutive_errors >= MAX_ERRORS:
                    msg = (f"Auto Recruitment: {consecutive_errors} consecutive "
                           f"network errors, aborting.\n{e}")
                    sendToBot(session, msg)
                    raise RuntimeError(msg)
                session.setStatus(
                    f"Network error ({consecutive_errors}/{MAX_ERRORS}), retrying...")
                fetch_error = True
                break

        if fetch_error:
            _wait_or_wake(session, is_units, stop_event, 60)
            _renew_worker_lock(session, is_units)
            continue

        # Assemble all_buildings from the static cache + live busy/queue state
        all_buildings = []
        for b_static in cached_buildings:
            cid = b_static['city_id']
            if cid not in city_pos_map:
                continue  # city fetch failed this cycle
            slot = city_pos_map[cid].get(b_static['building_position'], {})
            is_b = bool(slot.get('isBusy', False))
            eta = slot.get('completed')
            try:
                queue_time = max(0, int(eta) - int(time.time())) if eta else 0
            except (TypeError, ValueError):
                queue_time = 0
            all_buildings.append({
                **b_static,
                'is_busy':              is_b,
                'queue_remaining_time': queue_time,
            })

        # Detect busy→idle transitions; evict stale unit_data cache entries
        for b in all_buildings:
            key = (b['city_id'], b['building_position'])
            was = bld_was_busy.get(key, False)
            now_busy = b['is_busy']
            if was and not now_busy:
                bld_unit_data_cache.pop(key, None)
            bld_was_busy[key] = now_busy

        # Sort goals by priority (lower = higher priority), then by request_id
        active_goals.sort(key=lambda r: (r['priority'], r['request_id']))

        # goals lookup: unit_type_id -> sorted goal list
        goals_by_unit = {}
        for g in active_goals:
            goals_by_unit.setdefault(g['unit_type_id'], []).append(g)

        buildings_recruited = 0
        total_placed = 0

        for b in all_buildings:
            if b['is_busy']:
                continue
            cid = b['city_id']
            if cid not in city_resources:
                continue
            key = (cid, b['building_position'])

            # Lazy-load unit_data for this building (cached across cycles)
            if key not in bld_unit_data_cache:
                bld_data = fetch_building_data(session, b, is_units)
                if not bld_data or not bld_data.get('unit_data'):
                    continue
                bld_unit_data_cache[key] = bld_data['unit_data']

            unit_data = bld_unit_data_cache[key]
            res = city_resources[cid]

            # Walk goals in priority order and build the FIRST one this building
            # can both train AND currently afford (>= 1 unit). Falling through to
            # a lower-priority affordable goal keeps the building working when the
            # top-priority goal is momentarily unaffordable (no citizens / short
            # on a resource) instead of leaving it idle.
            chosen_goal = None
            chosen_uid = None
            ud = None
            max_p = 0
            for g in active_goals:
                uid = g['unit_type_id']
                if uid not in unit_data:
                    continue
                cand = unit_data[uid]

                # Order size = batch_pct of the goal's CURRENT remaining (so the
                # goal steps down: 1500 -> 300 -> 240 ...), then capped by the
                # building's slider max, the goal, and what the city can afford.
                cap = g['qty_remaining']
                if batch_pct > 0:
                    batch = max(1, math.ceil(g['qty_remaining'] * batch_pct))
                    cap = min(cap, batch)
                # NOTE: deliberately NOT capped by unit_data['max_buildable'].
                # That is the slider's max at the moment the building was
                # fetched, i.e. a snapshot of THEN-available resources. Because
                # unit_data is cached across cycles, a stale low value (taken
                # when the city was poor) kept throttling every later order.
                # ikabot's own trainArmy never uses it either — the live
                # resource maths below is the correct and sufficient limit.
                for res_key in ('citizens', 'wood', 'wine', 'marble', 'crystal', 'sulfur'):
                    cost = cand.get(res_key, 0)
                    if cost > 0:
                        cap = min(cap, res[res_key] // cost)

                if cap >= 1:
                    chosen_goal = g
                    chosen_uid = uid
                    ud = cand
                    max_p = cap
                    break

            if chosen_goal is None:
                continue

            # Reserve resources with RRS before POSTing
            reservation_ids = []
            if RRS_AVAILABLE:
                for res_idx, res_key in enumerate(('wood','wine','marble','crystal','sulfur')):
                    cost = ud.get(res_key, 0) * max_p
                    if cost <= 0:
                        continue
                    rid = rrs_reserve(
                        session, city_id=cid, city_name=b['city_name'],
                        resource_index=res_idx, amount=cost,
                        module_name=MODULE_NAME,
                        reason=f"Recruit {ud.get('name', chosen_uid)} x{max_p}",
                        release_at=time.time() + 300,
                    )
                    if rid:
                        reservation_ids.append(rid)

            action_code = _fetch_fresh_action_code(session, b, is_units)
            if not action_code:
                if RRS_AVAILABLE:
                    for rid in reservation_ids:
                        rrs_release(session, rid, MODULE_NAME)
                continue

            # Match ikabot's canonical trainArmy payload. Barracks and shipyard
            # use DIFFERENT functions: buildUnits vs buildShips. Sending the
            # wrong one (the old 'action':'BuildUnits' for both) let troop
            # orders work but silently no-op'd ship orders — the goal was still
            # deducted, so ship goals falsely completed within a cycle.
            params = {
                'action':         'CityScreen',
                'function':       'buildUnits' if is_units else 'buildShips',
                'actionRequest':  action_code,
                'cityId':         cid,
                'position':       b['building_position'],
                'backgroundView': 'city',
                'currentCityId':  cid,
                'templateView':   'barracks' if is_units else 'shipyard',
                'ajax':           '1',
                str(chosen_uid):  max_p,
            }

            try:
                session.post(params=params)
                unit_name = ud.get('name', str(chosen_uid))
                print(f"  {b['city_name']} L{b['building_level']}: "
                      f"Placed {max_p:,} {unit_name} "
                      f"(goal #{chosen_goal['request_id']})")

                # Decrement the goals CSV
                recruit_csv_deduct(session, is_units, chosen_goal['request_id'], max_p)

                # Deduct from city resource pool to prevent double-spend this cycle
                for res_key in ('citizens','wood','wine','marble','crystal','sulfur'):
                    city_resources[cid][res_key] = max(
                        0, city_resources[cid][res_key] - ud.get(res_key, 0) * max_p
                    )

                # Mark building as busy so later buildings in this cycle skip it
                b['is_busy'] = True
                bld_was_busy[key] = True

                if RRS_AVAILABLE:
                    for rid in reservation_ids:
                        rrs_release(session, rid, MODULE_NAME)

                buildings_recruited += 1
                total_placed += max_p
                last_order_time = time.time()
                stall_warned = False

                # Reload goals to reflect the deduction for subsequent buildings
                all_rows = recruit_csv_load(session, is_units)
                active_goals = [r for r in all_rows
                                if r["status"] == "active" and r["qty_remaining"] > 0]
                active_goals.sort(key=lambda r: (r['priority'], r['request_id']))
                goals_by_unit = {}
                for g in active_goals:
                    goals_by_unit.setdefault(g['unit_type_id'], []).append(g)

            except Exception as e:
                print(f"  {b['city_name']}: POST failed — {e}")
                if RRS_AVAILABLE:
                    for rid in reservation_ids:
                        rrs_release(session, rid, MODULE_NAME)

        # --- Resource import if nothing was placed this cycle ---
        if buildings_recruited == 0 and cfg.get("resource_import_enabled"):
            now_ts = int(time.time())
            for b in all_buildings:
                if b['is_busy']:
                    continue
                cid = b['city_id']
                if now_ts - import_requested_at.get(cid, 0) < IMPORT_COOLDOWN_SECS:
                    continue
                key = (cid, b['building_position'])
                unit_data = bld_unit_data_cache.get(key, {})
                deficit = [0, 0, 0, 0, 0]
                for uid, ud in unit_data.items():
                    if uid not in goals_by_unit:
                        continue
                    goal = goals_by_unit[uid][0]
                    qty = goal['qty_remaining']
                    for i, res_key in enumerate(('wood','wine','marble','crystal','sulfur')):
                        cost = ud.get(res_key, 0) * qty
                        have = city_resources.get(cid, {}).get(res_key, 0)
                        deficit[i] = max(deficit[i], max(0, cost - have))
                if any(d > 0 for d in deficit):
                    requests = _request_resource_import(
                        session, cid, deficit, all_city_ids, cities
                    )
                    if requests:
                        import_requested_at[cid] = now_ts
                        msg = (f"Resource import requested for {b['city_name']}:\n"
                               + "\n".join(f"  {r}" for r in requests))
                        sendToBot(session, msg)

        # --- Status line and periodic report ---
        any_busy = any(b['is_busy'] for b in all_buildings)
        total_remaining = sum(r['qty_remaining'] for r in active_goals)

        if buildings_recruited > 0:
            label = f"Placed {total_placed:,} {building_noun} this cycle"
        elif any_busy:
            label = "Waiting for building queues..."
        else:
            label = "Waiting for resources..."

        session.setStatus(
            f"Auto Recruitment: {label}  |  {total_remaining:,} remaining")

        # First report: wait until troops have actually been ordered across the
        # cities. Ordering frees population in maxed-out cities, so the growth
        # rate read now reflects reality instead of a misleading 0/hr. After
        # this one, fall back to the normal interval-based reporting.
        if not first_report_sent:
            if buildings_recruited > 0:
                if cfg.get("report_enabled"):
                    try:
                        send_progress_report(
                            session, is_units,
                            group_completed=cfg.get("report_group_completed", False),
                        )
                    except Exception:
                        pass
                    cfg["last_report_time"] = int(time.time())
                    try:
                        save_config(session, cfg)
                    except Exception:
                        pass
                first_report_sent = True
            # else: nothing ordered yet — hold the first report back.
        else:
            cfg = _maybe_send_report(session, is_units, cfg)

        # Stall guard: warn after 24 h with no successful order
        if not stall_warned and (time.time() - last_order_time) >= STALL_WARN_SECS:
            stall_warned = True
            sendToBot(
                session,
                "Auto Recruitment Manager: no units ordered in the last 24 h.\n"
                "Check that buildings are reachable and resources are available.\n"
                "Use the queue manager to cancel orders if needed.",
            )

        # --- Sleep duration ---
        if any_busy:
            soonest = None
            for b in all_buildings:
                qt = b.get('queue_remaining_time', 0)
                if b['is_busy'] and qt > 0:
                    soonest = min(soonest if soonest is not None else qt, qt)
            sleep_secs = min((soonest + 30) if soonest is not None else 300, 1800)
        elif RRS_AVAILABLE:
            sleep_secs = min(int(rrs_get_config(session)["retry_hours"] * 3600), 1800)
        else:
            sleep_secs = 300

        _wait_or_wake(session, is_units, stop_event, sleep_secs)
        _renew_worker_lock(session, is_units)


# =============================================================================
# WORKER LIFECYCLE  (start / stop / refresh)
# =============================================================================

def _activate_worker(session, event, is_units):
    """Acquire lock, fork into background, run execute_recruitment_loop."""
    kind = "units" if is_units else "ships"

    if not csv_has_active_orders(session, is_units):
        print(f"\n  {bcolors.WARNING}No active {kind} goals to work on.{bcolors.ENDC}")
        print(f"  Add goals first using option (1).")
        enter()
        return False  # caller stays in menu

    wlock = _worker_lock_path(session, is_units)
    if _is_worker_running(session, is_units):
        print(f"\n  {bcolors.WARNING}A worker for {kind} is already running.{bcolors.ENDC}")
        print(f"  Use (o) to stop it first, or (r) to force a check now.")
        enter()
        return False

    # Acquire worker lock
    try:
        with open(wlock, "w") as f:
            json.dump({"pid": os.getpid(), "timestamp": time.time()}, f)
    except OSError as e:
        print(f"\n  {bcolors.RED}Could not write worker lock: {e}{bcolors.ENDC}")
        enter()
        return False

    # Clear any leftover stop / wake flags
    try:
        os.remove(_stop_flag_path(session))
    except OSError:
        pass
    _consume_wake_flag(session, is_units)

    all_rows = recruit_csv_load(session, is_units)
    cfg = load_config(session)

    total_rem = sum(r["qty_remaining"] for r in all_rows
                    if r["status"] == "active" and r["qty_remaining"] > 0)
    unit_type_str = "units" if is_units else "ships"
    info = f"\nAuto Recruitment Manager ({unit_type_str}): {total_rem:,} remaining\n"

    set_child_mode(session)
    event.set()

    setInfoSignal(session, info)

    stop_event = threading.Event()
    try:
        execute_recruitment_loop(session, is_units, cfg, stop_event=stop_event)
    except Exception:
        try:
            sendToBot(session, f"Auto Recruitment Manager crashed:\n{traceback.format_exc()}")
        except Exception:
            pass
    finally:
        _release_worker_lock(session, is_units)
        _consume_wake_flag(session, is_units)
        try:
            os.remove(_stop_flag_path(session))
        except OSError:
            pass
        if RRS_AVAILABLE:
            try:
                release_all_for_module(session, MODULE_NAME)
            except Exception:
                pass
        try:
            session.logout()
        except Exception:
            pass
    return True  # never reached after set_child_mode, but satisfies callers


def _stop_worker_cmd(session, is_units):
    """Touch stop flag so the running loop exits, and wake it from any sleep."""
    kind = "units" if is_units else "ships"
    if not _is_worker_running(session, is_units):
        print(f"\n  {bcolors.WARNING}No {kind} worker appears to be running.{bcolors.ENDC}")
        enter()
        return
    pathlib.Path(_stop_flag_path(session)).touch()
    # Wake the worker so it doesn't wait out a long sleep (up to 30 min)
    # before noticing the stop flag at the top of its next cycle.
    _touch_wake_flag(session, is_units)
    print(
        f"\n  {bcolors.GREEN}Stop signal sent.{bcolors.ENDC} "
        f"The worker will finish its current step and exit within "
        f"~{WAKE_POLL_SECONDS} s (a cycle already mid-fetch may take longer)."
    )
    enter()


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def _setup_worker_config(session, is_units):
    """
    Ask the user for reporting / import preferences before starting the worker.
    Returns a config dict, or None if the user backed out.
    """
    kind = "units" if is_units else "ships"
    print()
    print("=" * 60)
    print("WORKER SETTINGS")
    print("=" * 60)
    print()

    report_raw = read(msg="Send periodic progress reports to Telegram? [Y/n]: ",
                      values=["y", "Y", "n", "N", ""])
    report_enabled = report_raw.lower() != "n"
    report_interval = 4
    report_group_completed = False

    if report_enabled:
        interval_raw = read(
            msg="Report interval in hours (default 4): ",
            min=1, digit=True, additionalValues=["'", ""]
        )
        if interval_raw == "'":
            return None
        report_interval = interval_raw if isinstance(interval_raw, int) else 4

        group_raw = read(
            msg="Collapse completed cities in reports? [y/N]: ",
            values=["y", "Y", "n", "N", ""]
        )
        report_group_completed = group_raw.lower() == "y"
        print()

    rtm_available = _try_load_rtm() is not None
    resource_import_enabled = False
    if rtm_available:
        ri_raw = read(
            msg="Auto-import resources from other cities if short? [Y/n]: ",
            values=["y", "Y", "n", "N", ""]
        )
        resource_import_enabled = ri_raw.lower() != "n"
        if resource_import_enabled:
            print(f"  {bcolors.GREEN}Resource import enabled.{bcolors.ENDC}")
    print()

    # Batch size (preserve any existing value; default 20%)
    existing_pct = load_config(session).get("min_batch_pct", 0.20)
    print("Batch size: each order builds this % of a unit type's remaining count")
    print("(e.g. 20%: 1500 -> 300 -> 240 ...). 0 = build max affordable per order.")
    pct_raw = read(
        msg=f"Batch percentage (1-100, 0 to disable, default {existing_pct*100:.0f}): ",
        min=0, max=100, digit=True, additionalValues=["'", ""]
    )
    if pct_raw == "'":
        return None
    min_batch_pct = (pct_raw / 100.0) if isinstance(pct_raw, int) else existing_pct
    print()

    return {
        "report_enabled":          report_enabled,
        "report_interval_hours":   report_interval,
        "report_group_completed":  report_group_completed,
        "resource_import_enabled": resource_import_enabled,
        "min_batch_pct":           min_batch_pct,
        "last_report_time":        0,
    }


def _set_batch_pct_ui(session):
    """Standalone quick-edit for the batch percentage (shared units+ships)."""
    cfg = load_config(session)
    current = cfg.get("min_batch_pct", 0.20)
    print()
    print("=" * 60)
    print("  BATCH SIZE")
    print("=" * 60)
    if current > 0:
        print(f"\n  Current: {current*100:.0f}% of each unit type's remaining per order.")
    else:
        print("\n  Current: batching OFF (each order builds the max affordable).")
    print("  Each production order builds this % of what's left for that unit")
    print("  type, so a goal steps down over successive orders. Example at 20%:")
    print("    1500 remaining -> order 300 -> 1200 left -> order 240 -> 960 ...")
    print("  A building that can't afford the full batch builds what it can, so")
    print("  it never sits idle. Enter 0 to disable batching entirely.")
    print("  Applies live to a running worker within one cycle.")
    print()
    raw = read(msg="  New batch percentage (0-100, or ' to cancel): ",
               min=0, max=100, digit=True, additionalValues=["'"])
    if raw == "'":
        return
    cfg["min_batch_pct"] = raw / 100.0
    save_config(session, cfg)
    if raw == 0:
        print(f"\n  {bcolors.GREEN}Batching disabled — orders use max affordable.{bcolors.ENDC}")
    else:
        print(f"\n  {bcolors.GREEN}Batch size set to {raw}%.{bcolors.ENDC}")


def _edit_queue_menu(session, is_units):
    """Sub-menu to remove a goal or adjust a goal's priority."""
    while True:
        banner()
        print("=" * 60)
        print("  EDIT GOALS")
        print("=" * 60)
        print()
        _show_queue_status(session, is_units)
        print()
        print("  (1) Remove / reduce a goal")
        print("  (2) Adjust a goal's priority")
        print()
        print("  (') Back")
        print()
        choice = read(msg="Select: ", min=1, max=2, digit=True, additionalValues=["'"])
        if choice == "'":
            return
        print()
        if choice == 1:
            _remove_goal_ui(session, is_units)
            enter()
        elif choice == 2:
            _adjust_priority_ui(session, is_units)
            enter()


def autoRecruitmentManager(session, event, stdin_fd, predetermined_input):
    """Auto Recruitment Manager — Main entry point"""
    sys.stdin = os.fdopen(stdin_fd)
    config.predetermined_input = predetermined_input

    try:
        # Outer loop: unit-type selection
        while True:
            banner()
            print("=" * 60)
            print("         AUTO RECRUITMENT MANAGER")
            print(f"                  v{MODULE_VERSION}")
            print("=" * 60)
            print()
            print("  What do you want to manage?")
            print()
            print("  1. Military Units (Barracks)")
            print("  2. Ships (Shipyard)")
            print()
            print("  (') Back to main menu")
            print()

            recruit_type = read(msg="Select: ", min=1, max=2, digit=True,
                                additionalValues=["'"])
            if recruit_type == "'":
                return

            is_units = (recruit_type == 1)
            kind = "Units" if is_units else "Ships"
            cities_ids = None
            cities = None

            # Inner loop: persistent management menu for this type
            while True:
                banner()
                print("=" * 60)
                print(f"  AUTO RECRUITMENT MANAGER v{MODULE_VERSION} — {kind}")
                print("=" * 60)
                print()
                print(f"  {_worker_status_line(session, is_units)}")

                all_rows = recruit_csv_load(session, is_units)
                active = [r for r in all_rows
                          if r["status"] == "active" and r["qty_remaining"] > 0]
                if active:
                    total_rem = sum(r["qty_remaining"] for r in active)
                    n_goals = len(active)
                    print(f"  Goals: {total_rem:,} {kind.lower()} remaining "
                          f"across {n_goals} active goal(s)")
                else:
                    print(f"  Goals: empty")

                _batch_pct = load_config(session).get("min_batch_pct", 0.20)
                if _batch_pct > 0:
                    print(f"  Batch size: {_batch_pct*100:.0f}% of remaining per order")
                else:
                    print(f"  Batch size: OFF (max affordable per order)")
                print()

                worker_running = _is_worker_running(session, is_units)
                if not worker_running and active:
                    print(f"  {bcolors.WARNING}Queue has orders but worker is stopped"
                          f" — press (s) to start it.{bcolors.ENDC}")
                    print()

                print(f"  {bcolors.BOLD}(s){bcolors.ENDC} Start worker   "
                      f"{bcolors.BOLD}(o){bcolors.ENDC} Stop worker   "
                      f"{bcolors.BOLD}(r){bcolors.ENDC} Force-check now")
                print()
                print("  (1) Add to goals")
                print("  (2) View goals")
                print("  (3) Building status  (what each building is doing)")
                print("  (4) Edit goals  (remove / adjust priority)")
                print("  (5) Send progress report now")
                print("  (6) Refresh building list  (rescan after upgrades)")
                print("  (7) Worker settings  (reports / resource import)")
                print("  (8) Batch size  (units built per order)")
                print()
                print("  (') Back to type selection")
                print()

                choice = read(msg="Select: ", min=1, max=8, digit=True,
                              additionalValues=["'", "s", "S", "o", "O", "r", "R"])

                if isinstance(choice, str):
                    letter = choice.lower()
                    if letter == "'":
                        break  # back to outer (type-selection) loop

                    if letter == "s":
                        # First run for this account: no config file yet, so ask
                        # for reporting / import settings. load_config() always
                        # returns defaults, so check the file itself.
                        if not os.path.exists(_config_path(session)):
                            cfg = _setup_worker_config(session, is_units)
                            if cfg is None:
                                continue  # user backed out of settings
                            save_config(session, cfg)
                        started = _activate_worker(session, event, is_units)
                        if started is False:
                            # Couldn't start (no orders / already running);
                            # message already printed — stay in menu.
                            continue
                        # Worker started: loop is running in this process.
                        # After set_child_mode the process is the background
                        # worker; exit the menu function cleanly.
                        return

                    if letter == "o":
                        _stop_worker_cmd(session, is_units)
                        continue

                    if letter == "r":
                        if not worker_running:
                            print(f"\n  {bcolors.WARNING}No worker is running — "
                                  f"start one with (s) first.{bcolors.ENDC}")
                            enter()
                        else:
                            _touch_wake_flag(session, is_units)
                            print(f"\n  {bcolors.GREEN}Force-check signal sent.{bcolors.ENDC} "
                                  f"The worker will re-check goals and buildings within "
                                  f"{WAKE_POLL_SECONDS} s.")
                            enter()
                        continue

                    continue  # unrecognised letter

                # Numeric options
                if choice == 1:
                    _add_to_goals_ui(session, is_units)
                    enter()
                elif choice == 2:
                    banner()
                    _show_queue_status(session, is_units)
                    enter()
                elif choice == 3:
                    _view_building_status(session, is_units)
                    enter()
                elif choice == 4:
                    _edit_queue_menu(session, is_units)
                elif choice == 5:
                    try:
                        send_progress_report(session, is_units)
                        print(f"\n  {bcolors.GREEN}Progress report sent.{bcolors.ENDC}")
                    except Exception as e:
                        print(f"\n  {bcolors.RED}Failed to send report: {e}{bcolors.ENDC}")
                    enter()
                elif choice == 6:
                    btype = "barracks" if is_units else "shipyards"
                    print(f"\n  Scanning all {btype} across all cities...")
                    _clear_buildings_cache(session, is_units)
                    cids, cits = getIdsOfCities(session)
                    fresh = _scan_buildings(session, is_units, list(cids), cits)
                    if fresh:
                        _save_buildings_cache(session, is_units, fresh)
                        print(f"  {bcolors.GREEN}Found {len(fresh)} {btype}. "
                              f"Cache updated.{bcolors.ENDC}")
                    else:
                        print(f"  {bcolors.WARNING}No {btype} found.{bcolors.ENDC}")
                    enter()
                elif choice == 7:
                    new_cfg = _setup_worker_config(session, is_units)
                    if new_cfg is not None:
                        save_config(session, new_cfg)
                        print(f"\n  {bcolors.GREEN}Settings saved.{bcolors.ENDC}")
                        if _is_worker_running(session, is_units):
                            print("  A running worker applies the new settings "
                                  "within one cycle — no restart needed.")
                    enter()
                elif choice == 8:
                    _set_batch_pct_ui(session)
                    if _is_worker_running(session, is_units):
                        print("  A running worker applies this within one cycle.")
                    enter()

    except KeyboardInterrupt:
        pass
    finally:
        event.set()
