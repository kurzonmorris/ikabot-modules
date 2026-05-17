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
- Resource shortage handling with 20% threshold batching
- Busy building detection with smart wait / include options
- CSV-backed persistence: survives crashes and restarts
- Live queue management: add / remove units while running
- Periodic Telegram progress reports (global bar + per-city breakdown)
- Optional resource import via ResourceTransportManager CSV scheduler

Version: 2.0.0
"""

import csv
import glob
import importlib.util
import json
import os
import re
import sys
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

RECRUIT_COLUMNS = [
    "recruit_id",
    "batch_id",         # integer grouping all rows from the same "add" call
    "session_id",
    "city_id",
    "city_name",
    "building_position",
    "building_level",
    "is_units",         # "True" = barracks, "False" = shipyard
    "unit_type_id",
    "unit_name",
    "qty_total",
    "qty_remaining",    # units still to be placed with the game (not yet POSTed)
    "priority",         # 1 = highest; lower number = more urgent
    "status",           # pending | partial | ordered | cancelled
    "created_at",
    "last_updated",
]

RECRUIT_INT_COLS = {
    "recruit_id", "batch_id", "city_id", "building_position", "building_level",
    "unit_type_id", "qty_total", "qty_remaining", "priority",
    "created_at", "last_updated",
}
RECRUIT_VALID_STATUSES = ("pending", "partial", "ordered", "cancelled")


def _safe(value):
    return re.sub(r'[^\w.-]', '_', str(value))


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
        row["status"] = "pending"
    return row


def _coerce_recruit_row_out(row):
    out = {}
    for col in RECRUIT_COLUMNS:
        v = row.get(col, "")
        out[col] = "" if v is None else str(v)
    return out


def csv_load_orders(session, is_units):
    path = _csv_path(session, is_units)
    try:
        with open(path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            return [_coerce_recruit_row(r) for r in reader]
    except FileNotFoundError:
        return []


def csv_save_orders(session, is_units, rows):
    path = _csv_path(session, is_units)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=RECRUIT_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow(_coerce_recruit_row_out(r))
    os.replace(tmp, path)


def csv_update_order(session, is_units, recruit_id, **fields):
    rows = csv_load_orders(session, is_units)
    for r in rows:
        if r["recruit_id"] == recruit_id:
            r.update(fields)
            break
    csv_save_orders(session, is_units, rows)


def csv_delete_orders(session, is_units):
    path = _csv_path(session, is_units)
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def csv_next_recruit_id(rows):
    if not rows:
        return 1
    return max(r["recruit_id"] for r in rows) + 1


def csv_next_batch_id(rows):
    if not rows:
        return 1
    return max(r["batch_id"] for r in rows) + 1


def csv_has_active_orders(session, is_units):
    rows = csv_load_orders(session, is_units)
    return any(r["status"] in ("pending", "partial") for r in rows)


# =============================================================================
# CONFIG MANAGEMENT
# =============================================================================

_DEFAULT_CONFIG = {
    "report_enabled":         False,
    "report_interval_hours":  4,
    "resource_import_enabled": False,
    "last_report_time":       0,
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


def _build_progress_report(all_rows):
    """
    Build a progress report string from all CSV rows (both units and ships).

    Returns a plain-text string suitable for Telegram.
    """
    now_str = time.strftime("%d %b %Y %H:%M", time.localtime())
    lines = [f"AUTO RECRUITMENT MANAGER — {now_str}", ""]

    # Separate units and ships
    unit_rows = [r for r in all_rows if r["is_units"]]
    ship_rows = [r for r in all_rows if not r["is_units"]]

    def _active(rows):
        return [r for r in rows if r["status"] != "cancelled"]

    def _totals(rows):
        total = sum(r["qty_total"] for r in rows)
        remaining = sum(r["qty_remaining"] for r in rows)
        ordered = total - remaining
        return ordered, total

    all_active = _active(all_rows)
    g_ordered, g_total = _totals(all_active)

    if g_total == 0:
        lines.append("No active orders.")
        return "\n".join(lines)

    lines.append(f"OVERALL {_ascii_bar(g_ordered, g_total)} {g_ordered:,}/{g_total:,} ({_pct(g_ordered, g_total)}%)")

    u_active = _active(unit_rows)
    s_active = _active(ship_rows)
    if u_active:
        uo, ut = _totals(u_active)
        lines.append(f"  Troops : {_ascii_bar(uo, ut, 10)} {uo:,}/{ut:,}")
    if s_active:
        so, st = _totals(s_active)
        lines.append(f"  Ships  : {_ascii_bar(so, st, 10)} {so:,}/{st:,}")

    lines.append("")
    lines.append("=" * 36)

    # Per-city, per-type breakdown
    cities_seen = []
    city_order = {}
    for r in all_active:
        cname = r["city_name"]
        if cname not in city_order:
            city_order[cname] = len(city_order)
            cities_seen.append(cname)

    for city_name in cities_seen:
        city_unit_rows = [r for r in u_active if r["city_name"] == city_name]
        city_ship_rows = [r for r in s_active if r["city_name"] == city_name]

        if not city_unit_rows and not city_ship_rows:
            continue

        lines.append(f"\n{city_name}")

        for label, rows in [("Troops", city_unit_rows), ("Ships", city_ship_rows)]:
            if not rows:
                continue
            co, ct = _totals(rows)
            done_mark = " DONE" if co >= ct else ""
            lines.append(f"  {label} {_ascii_bar(co, ct, 12)} {co:,}/{ct:,}{done_mark}")
            # Per-unit breakdown
            unit_totals = {}
            for r in rows:
                name = r["unit_name"] or f"Unit {r['unit_type_id']}"
                if name not in unit_totals:
                    unit_totals[name] = [0, 0]
                unit_totals[name][0] += r["qty_total"] - r["qty_remaining"]
                unit_totals[name][1] += r["qty_total"]
            for uname, (uo2, ut2) in unit_totals.items():
                done2 = " *" if uo2 >= ut2 else ""
                lines.append(f"    {uname:<20} {uo2:>5,}/{ut2:,}{done2}")

    lines.append("\n" + "=" * 36)
    return "\n".join(lines)


def send_progress_report(session, is_units):
    """Send a combined units+ships progress report to the bot."""
    unit_rows = csv_load_orders(session, True)
    ship_rows = csv_load_orders(session, False)
    all_rows = unit_rows + ship_rows

    if not all_rows:
        return

    report = _build_progress_report(all_rows)
    sendToBot(session, report)


def _maybe_send_report(session, is_units, cfg):
    """Send a progress report if the interval has elapsed. Updates config."""
    if not cfg.get("report_enabled"):
        return cfg
    interval_secs = cfg.get("report_interval_hours", 4) * 3600
    now = time.time()
    if now - cfg.get("last_report_time", 0) >= interval_secs:
        try:
            send_progress_report(session, is_units)
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
                notes="autoRecruitmentManager resource import",
            )
            rtm.transport_csv_append(session, row)
            for i in range(5):
                remaining[i] = max(0, remaining[i] - to_req[i])
            res_desc = ", ".join(
                f"{RESOURCE_NAMES[i]} {to_req[i]:,}"
                for i in range(5) if to_req[i] > 0
            )
            requests.append(f"{sup['city_name']} → {res_desc}")
        except Exception:
            pass

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

    slider_prefix = f"js_{view_name}Slider"
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
            result['unit_data'][uid] = {
                'name':          cd.get('local_name', ''),
                'identifier':    cd.get('identifier', ''),
                'citizens':      costs.get('citizens', 0),
                'wood':          costs.get('wood', 0),
                'wine':          costs.get('wine', 0),
                'marble':        costs.get('marble', 0),
                'crystal':       costs.get('crystal', 0),
                'sulfur':        costs.get('sulfur', 0),
                'upkeep':        costs.get('upkeep', 0),
                'time_seconds':  costs.get('completiontime', 0),
                'max_buildable': slider_info.get('max_value', 0),
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

        available_citizens = 0
        for pat in (r'id="js_GlobalMenu_citizens">([0-9,]+)', r'"citizens"\s*:\s*(\d+)'):
            m = re.search(pat, html)
            if m:
                available_citizens = int(m.group(1).replace(',', ''))
                break

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
# QUEUE MANAGEMENT UI  (available while session is running)
# =============================================================================

def _show_queue_status(session, is_units):
    """Print the current queue, grouped by batch and city."""
    rows = csv_load_orders(session, is_units)
    active = [r for r in rows if r["status"] not in ("ordered", "cancelled")]
    if not active:
        print("  (no active orders)")
        return

    print(f"{'ID':>4}  {'Bat':>3}  {'Pri':>3}  {'City':<16}  {'Unit':<22}  {'Remaining':>12}  Status")
    print("  " + "-" * 80)
    last_batch = None
    for r in sorted(active, key=lambda x: (x["priority"], x["batch_id"], x["city_name"], x["unit_name"])):
        if r["batch_id"] != last_batch:
            if last_batch is not None:
                print()
            last_batch = r["batch_id"]
        ordered = r["qty_total"] - r["qty_remaining"]
        remaining_str = f"{r['qty_remaining']:,} / {r['qty_total']:,}"
        print(f"  {r['recruit_id']:>4}  {r['batch_id']:>3}  {r['priority']:>3}  "
              f"{r['city_name']:<16}  {r['unit_name']:<22}  {remaining_str:>12}  {r['status']}")


def _add_to_queue_ui(session, is_units, cities_ids, cities):
    """
    Interactive: pick a building, choose units and quantities,
    pick a priority, append to the CSV.
    Returns True if rows were added, False if user backed out.
    """
    building_type = "barracks" if is_units else "shipyard"
    print(f"\nScanning for available {building_type}(s)...")
    all_buildings = find_all_buildings(session, cities_ids, cities, building_type)
    if not all_buildings:
        print(f"  {bcolors.RED}No {building_type} found.{bcolors.ENDC}")
        return False

    print(f"\nAvailable {building_type}(s):\n")
    building_list = []
    for b in all_buildings:
        busy_str = " [BUSY]" if b['is_busy'] else ""
        print(f"  {len(building_list) + 1:>2}. {b['city_name']} - L{b['building_level']}{busy_str}")
        building_list.append(b)

    print()
    sel = read(msg="Select building (number) or ' to cancel: ",
               min=1, max=len(building_list), digit=True, additionalValues=["'"])
    if sel == "'":
        return False

    chosen_building = building_list[sel - 1]
    print(f"\nFetching data for {chosen_building['city_name']} {building_type}...")
    data = fetch_building_data(session, chosen_building, is_units)
    if data is None:
        print(f"  {bcolors.RED}Failed to fetch building data.{bcolors.ENDC}")
        return False

    unit_list = UNITS_ORDER if is_units else SHIPS_ORDER
    order = {}
    print("\nEnter quantity for each unit (0 or Enter to skip):\n")
    for unit in unit_list:
        uid = unit['game_index']
        if uid not in data.get('unit_data', {}):
            continue
        ud = data['unit_data'][uid]
        costs = []
        if ud.get('citizens', 0): costs.append(f"{ud['citizens']} cit")
        if ud.get('wood', 0):     costs.append(f"{ud['wood']} wood")
        if ud.get('wine', 0):     costs.append(f"{ud['wine']} wine")
        if ud.get('marble', 0):   costs.append(f"{ud['marble']} marble")
        if ud.get('crystal', 0):  costs.append(f"{ud['crystal']} crystal")
        if ud.get('sulfur', 0):   costs.append(f"{ud['sulfur']} sulfur")
        cost_str = f" [{', '.join(costs)}]" if costs else ""
        qty = read(msg=f"  {unit['name']}{cost_str}: ", min=0, digit=True, additionalValues=["'", ""])
        if qty == "'":
            return False
        if qty == "":
            qty = 0
        if qty > 0:
            order[uid] = {'name': unit['name'], 'quantity': qty}

    if not order:
        print("  No units entered.")
        return False

    # Ask for priority
    existing = csv_load_orders(session, is_units)
    existing_priorities = sorted({r["priority"] for r in existing if r["status"] not in ("ordered","cancelled")})
    if existing_priorities:
        print(f"\n  Existing priority levels: {existing_priorities}")
    pri_raw = read(msg="  Priority for this batch (1=highest, default 1): ",
                   min=1, digit=True, additionalValues=["'", ""])
    if pri_raw == "'":
        return False
    priority = pri_raw if isinstance(pri_raw, int) else 1

    # Build temp distribution for CSV rows
    temp_buildings = [data]
    calculate_distribution(temp_buildings, order)

    now = int(time.time())
    session_id = str(now)
    rows = existing[:]
    next_id = csv_next_recruit_id(rows)
    batch_id = csv_next_batch_id(rows)

    for uid, qty in data.get('assignments', {}).items():
        rows.append({
            "recruit_id":       next_id,
            "batch_id":         batch_id,
            "session_id":       session_id,
            "city_id":          data["city_id"],
            "city_name":        data["city_name"],
            "building_position": data["building_position"],
            "building_level":   data["building_level"],
            "is_units":         is_units,
            "unit_type_id":     uid,
            "unit_name":        order.get(uid, {}).get('name', data['unit_data'].get(uid, {}).get('name', str(uid))),
            "qty_total":        qty,
            "qty_remaining":    qty,
            "priority":         priority,
            "status":           "pending",
            "created_at":       now,
            "last_updated":     now,
        })
        next_id += 1

    csv_save_orders(session, is_units, rows)
    total_added = sum(data.get('assignments', {}).values())
    print(f"\n  {bcolors.GREEN}Added {total_added} units (batch {batch_id}, priority {priority}){bcolors.ENDC}")
    return True


def _remove_from_queue_ui(session, is_units):
    """Interactive: reduce or cancel units in the queue."""
    rows = csv_load_orders(session, is_units)
    active = [r for r in rows if r["status"] in ("pending", "partial")]
    if not active:
        print("  No pending orders to remove.")
        return

    _show_queue_status(session, is_units)
    print()

    raw = read(msg="Enter recruit ID to modify (or ' to cancel): ", additionalValues=["'"])
    if raw == "'":
        return

    try:
        rid = int(str(raw).strip())
    except ValueError:
        print("  Invalid ID.")
        return

    target = next((r for r in active if r["recruit_id"] == rid), None)
    if target is None:
        print(f"  ID {rid} not found or already ordered.")
        return

    print(f"\n  {target['unit_name']}: {target['qty_remaining']:,} remaining of {target['qty_total']:,} total")
    print(f"  Enter new remaining quantity (0 to cancel), or ' to abort:")

    new_qty_raw = read(msg="  New quantity: ", min=0, digit=True, additionalValues=["'"])
    if new_qty_raw == "'":
        return
    new_qty = int(new_qty_raw)

    if new_qty == 0:
        csv_update_order(session, is_units, rid,
                         qty_remaining=0, status="cancelled",
                         last_updated=int(time.time()))
        print(f"  {bcolors.WARNING}Cancelled order #{rid}.{bcolors.ENDC}")
    elif new_qty < target["qty_remaining"]:
        csv_update_order(session, is_units, rid,
                         qty_remaining=new_qty,
                         qty_total=target["qty_total"] - (target["qty_remaining"] - new_qty),
                         status="partial" if target["status"] == "pending" else target["status"],
                         last_updated=int(time.time()))
        print(f"  Updated order #{rid}: {new_qty:,} remaining.")
    else:
        print(f"  No change (entered quantity >= current remaining).")


def _adjust_priority_ui(session, is_units):
    """Interactive: change the priority of an entire batch."""
    rows = csv_load_orders(session, is_units)
    active = [r for r in rows if r["status"] in ("pending", "partial")]
    if not active:
        print("  No pending orders.")
        return

    batch_ids = sorted({r["batch_id"] for r in active})
    print("\n  Active batches:")
    for bid in batch_ids:
        batch_rows = [r for r in active if r["batch_id"] == bid]
        pri = batch_rows[0]["priority"] if batch_rows else "?"
        total = sum(r["qty_remaining"] for r in batch_rows)
        cities = ", ".join({r["city_name"] for r in batch_rows})
        print(f"    Batch {bid:>3}  Priority {pri}  {total:,} units  [{cities}]")

    print()
    bid_raw = read(msg="Enter batch ID to reprioritise (or '): ", additionalValues=["'"])
    if bid_raw == "'":
        return
    try:
        bid = int(str(bid_raw).strip())
    except ValueError:
        print("  Invalid batch ID.")
        return

    if bid not in batch_ids:
        print(f"  Batch {bid} not found.")
        return

    new_pri_raw = read(msg="  New priority (1=highest): ", min=1, digit=True, additionalValues=["'"])
    if new_pri_raw == "'":
        return

    now = int(time.time())
    all_rows = csv_load_orders(session, is_units)
    for r in all_rows:
        if r["batch_id"] == bid:
            r["priority"] = int(new_pri_raw)
            r["last_updated"] = now
    csv_save_orders(session, is_units, all_rows)
    print(f"  {bcolors.GREEN}Batch {bid} priority set to {int(new_pri_raw)}.{bcolors.ENDC}")


def _manage_running_session(session, event, is_units):
    """
    Top-level menu when a session is already running.
    All changes are written to CSV; the background loop picks them up.
    Returns True if the caller should exit (user stopped session or returned to menu).
    """
    cities_ids, cities = getIdsOfCities(session)

    while True:
        banner()
        print("=" * 60)
        print("  AUTO RECRUITMENT MANAGER — RUNNING SESSION")
        print("=" * 60)
        print()
        _show_queue_status(session, is_units)
        print()
        print("Options:")
        print("  1. Add units to queue")
        print("  2. Remove / reduce units in queue")
        print("  3. Adjust priority of a batch")
        print("  4. Send progress report now")
        print("  5. Stop recruitment (cancel all pending orders)")
        print()
        print("  (') Return to main menu")
        print()

        choice = read(msg="Select: ", min=1, max=5, digit=True, additionalValues=["'"])
        print()

        if choice == "'":
            event.set()
            return True

        if choice == 1:
            _add_to_queue_ui(session, is_units, cities_ids, cities)
            enter()

        elif choice == 2:
            _remove_from_queue_ui(session, is_units)
            enter()

        elif choice == 3:
            _adjust_priority_ui(session, is_units)
            enter()

        elif choice == 4:
            try:
                send_progress_report(session, is_units)
                print(f"  {bcolors.GREEN}Progress report sent.{bcolors.ENDC}")
            except Exception as e:
                print(f"  {bcolors.RED}Failed to send report: {e}{bcolors.ENDC}")
            enter()

        elif choice == 5:
            confirm = read(msg="Stop all pending recruitment? [Y/n]: ",
                           values=["y", "Y", "n", "N", ""])
            if confirm.lower() != "n":
                # Create stop flag; the loop will detect it and exit cleanly
                try:
                    open(_stop_flag_path(session), 'w').close()
                except Exception:
                    pass
                # Also mark all pending rows as cancelled
                all_rows = csv_load_orders(session, is_units)
                now = int(time.time())
                for r in all_rows:
                    if r["status"] in ("pending", "partial"):
                        r["status"] = "cancelled"
                        r["last_updated"] = now
                csv_save_orders(session, is_units, all_rows)
                print(f"  {bcolors.WARNING}Recruitment stopped.{bcolors.ENDC}")
                enter()
                event.set()
                return True


# =============================================================================
# RECRUITMENT EXECUTION
# =============================================================================

def execute_recruitment(session, distribution, is_units=True):
    """Immediate mode: place all orders at once, no retry loop."""
    success = True
    for b in distribution:
        if not b.get('assignments'):
            continue
        action_code = _fetch_fresh_action_code(session, b, is_units)
        if not action_code:
            print(f"  {b['city_name']}: Failed to get action code")
            success = False
            continue
        params = {
            'action': 'BuildUnits',
            'actionRequest': action_code,
            'cityId': b['city_id'],
            'position': b['building_position'],
        }
        for uid, qty in b['assignments'].items():
            params[str(uid)] = qty
        try:
            session.post(params=params)
            print(f"  {b['city_name']}: Ordered {sum(b['assignments'].values())} units")
        except Exception as e:
            print(f"  {b['city_name']}: Failed — {e}")
            success = False
    return success


def execute_recruitment_loop(session, distribution, recruitment_order, cities,
                             is_units, resource_check, saved_rows, cfg):
    """
    Background recruitment loop.

    Each cycle:
    1. Reload active rows from CSV (picks up any live queue changes)
    2. Refresh city data: resources + live isBusy per building slot
    3. Sort buildings by their priority
    4. For each (non-busy) building, calculate how many units can be placed;
       if >= 20% threshold, POST BuildUnits
    5. Units are considered DONE once posted (mark row as 'ordered' in CSV)
    6. Handle resource import requests when import is enabled and resources short
    7. Send periodic progress reports if configured

    Crash-safe: all state lives in the CSV, so the session can be resumed.
    """
    session_id = str(int(time.time()))
    all_city_ids = list(set(b['city_id'] for b in distribution))

    # Ensure every building in distribution has unit_data
    bld_map = {(b['city_id'], b['building_position']): b for b in distribution}

    # Import cooldown: track when we last requested imports per city
    import_requested_at = {}
    IMPORT_COOLDOWN_SECS = 2 * 3600  # re-request at most every 2 hours

    consecutive_errors = 0
    MAX_ERRORS = 10

    while True:
        # --- Check stop flag ---
        if os.path.exists(_stop_flag_path(session)):
            try:
                os.remove(_stop_flag_path(session))
            except OSError:
                pass
            session.setStatus("Auto Recruitment Manager: stopped by user")
            break

        # --- Reload CSV each cycle to pick up live queue changes ---
        all_rows = csv_load_orders(session, is_units)
        active_rows = [r for r in all_rows if r["status"] in ("pending", "partial")]

        if not active_rows:
            session.setStatus("Auto Recruitment complete!")
            sendToBot(session, "Auto Recruitment: all orders placed successfully.")
            csv_delete_orders(session, is_units)
            delete_config(session)
            break

        # Rebuild remaining_assignments for each building from live CSV data
        for b in distribution:
            b['remaining_assignments'] = {}
        for r in active_rows:
            key = (r['city_id'], r['building_position'])
            b = bld_map.get(key)
            if b is None:
                # New building added via queue management — create an entry
                b = {
                    'city_id':           r['city_id'],
                    'city_name':         r['city_name'],
                    'building_position': r['building_position'],
                    'building_level':    r['building_level'],
                    'is_busy':           False,
                    'queue_remaining_time': 0,
                    'unit_data':         {},
                    'action_code':       None,
                    'remaining_assignments': {},
                    'assignments':       {},
                }
                bld_map[key] = b
                distribution.append(b)
            b['remaining_assignments'].setdefault(r['unit_type_id'], 0)
            b['remaining_assignments'][r['unit_type_id']] = max(
                b['remaining_assignments'][r['unit_type_id']], r['qty_remaining']
            )
            # If this unit_type is not in unit_data, refresh the building
            if r['unit_type_id'] not in b.get('unit_data', {}):
                fresh = fetch_building_data(session, b, is_units)
                if fresh:
                    b['unit_data'] = fresh['unit_data']

        # --- Build priority map: (city_id, pos) -> min priority ---
        priority_map = {}
        for r in active_rows:
            key = (r['city_id'], r['building_position'])
            priority_map[key] = min(priority_map.get(key, 9999), r['priority'])

        # --- Refresh city resources + update building busy status ---
        city_resources = {}
        city_data_cache = {}

        for b in distribution:
            cid = b['city_id']
            if cid in city_data_cache:
                continue
            try:
                html = session.get(city_url + str(cid))
                city_data = getCity(html)
                city_data_cache[cid] = (html, city_data)

                avail = city_data.get('availableResources', [0] * 5)
                if len(avail) < 5:
                    avail = avail + [0] * (5 - len(avail))

                citizens = 0
                for pat in (r'id="js_GlobalMenu_citizens">([0-9,]+)', r'"citizens"\s*:\s*(\d+)'):
                    m = re.search(pat, html)
                    if m:
                        citizens = int(m.group(1).replace(',', ''))
                        break

                # Apply RRS snapshot so we don't count resources reserved by
                # other modules.  Citizens have no RRS resource index — kept raw.
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
                consecutive_errors = 0
            except Exception as e:
                consecutive_errors += 1
                if consecutive_errors >= MAX_ERRORS:
                    msg = f"Auto Recruitment: {consecutive_errors} consecutive network errors, aborting.\n{e}"
                    sendToBot(session, msg)
                    raise RuntimeError(msg)
                session.setStatus(f"Network error ({consecutive_errors}/{MAX_ERRORS}), retrying...")
                wait(60)
                continue

        # Update building busy flags from fresh city data
        for b in distribution:
            if not b.get('remaining_assignments'):
                continue
            cid = b['city_id']
            pos = b['building_position']
            _, city_data = city_data_cache.get(cid, (None, {}))
            for slot in city_data.get('position', []):
                if slot.get('position') == pos:
                    b['is_busy'] = bool(slot.get('isBusy', False))
                    eta = slot.get('completed')
                    b['queue_remaining_time'] = max(0, int(eta) - int(time.time())) if eta else 0
                    break

        # --- Sort buildings by priority ---
        active_buildings = [
            b for b in distribution
            if b.get('remaining_assignments') and b['city_id'] in city_resources
        ]
        active_buildings.sort(
            key=lambda b: priority_map.get((b['city_id'], b['building_position']), 9999)
        )

        # --- Determine what to recruit this cycle ---
        buildings_to_recruit = []
        total_can_recruit = 0
        total_remaining = sum(sum(b.get('remaining_assignments', {}).values()) for b in distribution)

        for b in active_buildings:
            remaining = b.get('remaining_assignments', {})
            if not remaining or b.get('is_busy', False):
                continue

            cid = b['city_id']
            avail = city_resources[cid].copy()
            bldg_total = sum(remaining.values())
            threshold = max(1, int(bldg_total * 0.20))

            can_recruit = {}
            cycle_total = 0

            for uid, qty_needed in remaining.items():
                ud = b.get('unit_data', {}).get(uid)
                if ud is None:
                    continue
                max_p = qty_needed
                for res_key, avail_key in (
                    ('citizens','citizens'), ('wood','wood'), ('wine','wine'),
                    ('marble','marble'), ('crystal','crystal'), ('sulfur','sulfur'),
                ):
                    cost = ud.get(res_key, 0)
                    if cost > 0:
                        max_p = min(max_p, avail[avail_key] // cost)

                if max_p > 0:
                    can_recruit[uid] = max_p
                    cycle_total += max_p
                    for res_key in ('citizens','wood','wine','marble','crystal','sulfur'):
                        avail[res_key] -= ud.get(res_key, 0) * max_p

            if cycle_total >= threshold:
                # Commit deduction to shared city pool
                for uid, qty in can_recruit.items():
                    ud = b['unit_data'][uid]
                    for res_key in ('citizens','wood','wine','marble','crystal','sulfur'):
                        city_resources[cid][res_key] -= ud.get(res_key, 0) * qty

                buildings_to_recruit.append({
                    'building':  b,
                    'to_recruit': can_recruit,
                    'cycle_total': cycle_total,
                    'bldg_total':  bldg_total,
                })
                total_can_recruit += cycle_total

        # --- Handle resource import if enabled and nothing can be recruited ---
        if not buildings_to_recruit and cfg.get("resource_import_enabled"):
            now_ts = int(time.time())
            for b in active_buildings:
                if b.get('is_busy'):
                    continue
                cid = b['city_id']
                if now_ts - import_requested_at.get(cid, 0) < IMPORT_COOLDOWN_SECS:
                    continue
                # Calculate deficit for this city
                deficit = [0, 0, 0, 0, 0]
                for uid, qty in b.get('remaining_assignments', {}).items():
                    ud = b.get('unit_data', {}).get(uid, {})
                    for i, key in enumerate(('wood','wine','marble','crystal','sulfur')):
                        cost = ud.get(key, 0) * qty
                        have = city_resources.get(cid, {}).get(key, 0)
                        deficit[i] = max(deficit[i], max(0, cost - have))
                if any(d > 0 for d in deficit):
                    requests = _request_resource_import(
                        session, cid, deficit, all_city_ids, cities
                    )
                    if requests:
                        import_requested_at[cid] = now_ts
                        msg = f"Resource import requested for {b['city_name']}:\n" + "\n".join(f"  {r}" for r in requests)
                        sendToBot(session, msg)

        # --- Wait if nothing to recruit ---
        if not buildings_to_recruit:
            any_busy = any(b.get('is_busy') for b in active_buildings if b.get('remaining_assignments'))

            soonest_queue = None
            for b in active_buildings:
                qt = b.get('queue_remaining_time', 0)
                if b.get('is_busy') and qt > 0:
                    soonest_queue = min(soonest_queue or qt, qt)

            if any_busy:
                # Wake up just after the soonest queue finishes; cap at 30 min
                sleep_secs = min((soonest_queue + 30) if soonest_queue else 300, 1800)
            else:
                # Resource shortage: honour RRS retry_hours if available
                if RRS_AVAILABLE:
                    retry_secs = int(rrs_get_config(session)["retry_hours"] * 3600)
                else:
                    retry_secs = 300
                sleep_secs = min(retry_secs, 1800)

            label = "Waiting for queues..." if any_busy else "Waiting for resources..."
            session.setStatus(f"{label} {total_remaining:,} units remaining")
            cfg = _maybe_send_report(session, is_units, cfg)
            wait(sleep_secs)
            continue

        # --- Execute recruitment ---
        session.setStatus(f"Recruiting {total_can_recruit:,}/{total_remaining:,} units...")

        for info in buildings_to_recruit:
            b = info['building']
            to_recruit = info['to_recruit']
            cid = b['city_id']
            pos = b['building_position']

            action_code = _fetch_fresh_action_code(session, b, is_units)
            if not action_code:
                print(f"  {b['city_name']}: Failed to get action code, skipping")
                continue

            # --- Reserve resources with RRS before touching city stocks ---
            # release_at gives the game 5 minutes to process the POST;
            # release_all_for_module in the finally block covers any edge case.
            reservation_ids = []
            if RRS_AVAILABLE:
                _RES_KEYS = ('wood', 'wine', 'marble', 'crystal', 'sulfur')
                for uid, qty in to_recruit.items():
                    ud = b.get('unit_data', {}).get(uid, {})
                    unit_label = ud.get('name', str(uid))
                    for res_idx, res_key in enumerate(_RES_KEYS):
                        cost = ud.get(res_key, 0) * qty
                        if cost <= 0:
                            continue
                        rid = rrs_reserve(
                            session,
                            city_id=cid,
                            city_name=b['city_name'],
                            resource_index=res_idx,
                            amount=cost,
                            module_name=MODULE_NAME,
                            reason=f"Recruit {unit_label} x{qty}",
                            release_at=time.time() + 300,
                        )
                        reservation_ids.append(rid)

            params = {
                'action':        'BuildUnits',
                'actionRequest': action_code,
                'cityId':        cid,
                'position':      pos,
            }
            for uid, qty in to_recruit.items():
                params[str(uid)] = qty

            try:
                session.post(params=params)
                placed = sum(to_recruit.values())
                pct = placed / info['bldg_total'] * 100
                print(f"  {b['city_name']} L{b['building_level']}: "
                      f"Placed {placed:,} units ({pct:.0f}% of remaining)")

                # Resources consumed by the game — release reservations
                if RRS_AVAILABLE:
                    for rid in reservation_ids:
                        result = rrs_release(session, rid, MODULE_NAME)
                        # True = released OK, None = wrong owner (shouldn't happen),
                        # False = already expired (fine, POST took <5 min)

                # Update in-memory remaining
                for uid, qty in to_recruit.items():
                    b['remaining_assignments'][uid] = max(
                        0, b['remaining_assignments'].get(uid, 0) - qty
                    )
                    if b['remaining_assignments'][uid] == 0:
                        del b['remaining_assignments'][uid]

                # Persist to CSV — mark as ordered once qty_remaining hits 0
                now_ts = int(time.time())
                updated_rows = csv_load_orders(session, is_units)
                for r in updated_rows:
                    if r['city_id'] != cid or r['building_position'] != pos:
                        continue
                    uid = r['unit_type_id']
                    if uid not in to_recruit:
                        continue
                    new_remaining = max(0, r['qty_remaining'] - to_recruit[uid])
                    new_status = "ordered" if new_remaining == 0 else "partial"
                    r['qty_remaining'] = new_remaining
                    r['status'] = new_status
                    r['last_updated'] = now_ts
                csv_save_orders(session, is_units, updated_rows)

            except Exception as e:
                print(f"  {b['city_name']}: POST failed — {e}")
                # POST failed — release reservations so resources aren't locked
                if RRS_AVAILABLE:
                    for rid in reservation_ids:
                        rrs_release(session, rid, MODULE_NAME)

        # Brief pause, then check progress reports
        wait(30)
        cfg = _maybe_send_report(session, is_units, cfg)


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def autoRecruitmentManager(session, event, stdin_fd, predetermined_input):
    """Auto Recruitment Manager — Main entry point"""
    sys.stdin = os.fdopen(stdin_fd)
    config.predetermined_input = predetermined_input

    try:
        banner()
        print("=" * 60)
        print("         AUTO RECRUITMENT MANAGER")
        print("=" * 60)
        print()

        # Choose units or ships
        print("What do you want to recruit?")
        print()
        print("1. Military Units (Barracks)")
        print("2. Ships (Shipyard)")
        print()
        print("(') Back to main menu")

        recruit_type = read(msg="Select (1 or 2): ", min=1, max=2, digit=True, additionalValues=["'"])
        if recruit_type == "'":
            event.set()
            return

        is_units = (recruit_type == 1)
        building_type = "barracks" if is_units else "shipyard"
        unit_list = UNITS_ORDER if is_units else SHIPS_ORDER

        print()

        # If an active session exists, show the management menu
        if csv_has_active_orders(session, is_units):
            saved_rows = csv_load_orders(session, is_units)
            active = [r for r in saved_rows if r["status"] in ("pending", "partial")]
            total_rem = sum(r["qty_remaining"] for r in active)
            unique_blds = len({(r["city_id"], r["building_position"]) for r in active})

            print(f"{bcolors.WARNING}Active recruitment session found:{bcolors.ENDC}")
            print(f"  {total_rem:,} units remaining across {unique_blds} building(s)")
            print()
            print("1. Manage running session (add/remove units, view progress)")
            print("2. Resume running session in background (re-attach)")
            print("3. Start a new session (discards previous)")
            print()
            print("(') Back to main menu")

            session_choice = read(msg="Select (1-3): ", min=1, max=3, digit=True, additionalValues=["'"])
            if session_choice == "'":
                event.set()
                return

            if session_choice == 1:
                print()
                _manage_running_session(session, event, is_units)
                return

            if session_choice == 2:
                print()
                print("Resuming previous session in background...")
                distribution, recruitment_order = _load_distribution_from_csv(session, is_units, saved_rows)
                if not distribution:
                    print(f"{bcolors.RED}Could not restore session. Start a new one.{bcolors.ENDC}")
                    print()
                    csv_delete_orders(session, is_units)
                else:
                    print()
                    enter()
                    cfg = load_config(session)
                    set_child_mode(session)
                    event.set()
                    info = f"\nAuto Recruitment (resume): {total_rem:,} units\n"
                    setInfoSignal(session, info)
                    try:
                        execute_recruitment_loop(
                            session, distribution, recruitment_order,
                            {}, is_units, {}, saved_rows, cfg,
                        )
                    except Exception:
                        sendToBot(session, f"Error in:\n{info}\n{traceback.format_exc()}")
                    finally:
                        session.logout()
                    return

            # session_choice == 3: fall through to fresh start
            csv_delete_orders(session, is_units)
            delete_config(session)
            print()

        # Fresh session setup
        print(f"Scanning for {building_type}...")
        print()
        cities_ids, cities = getIdsOfCities(session)
        all_buildings = find_all_buildings(session, cities_ids, cities, building_type)

        if not all_buildings:
            print(f"{bcolors.RED}No {building_type} found in any city!{bcolors.ENDC}")
            print()
            enter()
            event.set()
            return

        # City selection
        print(f"Found {len(all_buildings)} {building_type}(s) across "
              f"{len({b['city_id'] for b in all_buildings})} cities")
        print()
        print("=" * 60)
        print("CITY SELECTION")
        print("=" * 60)
        print()

        city_building_counts = {}
        for b in all_buildings:
            cid = b['city_id']
            if cid not in city_building_counts:
                city_building_counts[cid] = {'name': b['city_name'], 'count': 0, 'levels': []}
            city_building_counts[cid]['count'] += 1
            city_building_counts[cid]['levels'].append(b['building_level'])

        city_list = list(city_building_counts.keys())
        for idx, cid in enumerate(city_list, 1):
            info = city_building_counts[cid]
            lvl = ', '.join(f"L{l}" for l in sorted(info['levels']))
            print(f"  {idx}. {info['name']} — {info['count']} {building_type}(s) [{lvl}]")

        print()
        print("Enter city numbers to IGNORE (comma-separated), or Enter for none:")
        print("(') Back to main menu")

        ignore_input = read(msg="Ignore cities: ", additionalValues=["'", ""])
        if ignore_input == "'":
            event.set()
            return

        ignored_city_ids = set()
        if ignore_input and ignore_input.strip():
            try:
                for num in [int(x.strip()) for x in ignore_input.split(',') if x.strip()]:
                    if 1 <= num <= len(city_list):
                        ignored_city_ids.add(city_list[num - 1])
            except ValueError:
                print(f"{bcolors.WARNING}Invalid input, ignoring no cities{bcolors.ENDC}")

        active_buildings = [b for b in all_buildings if b['city_id'] not in ignored_city_ids]
        if not active_buildings:
            print(f"{bcolors.RED}No {building_type} remaining after exclusions!{bcolors.ENDC}")
            print()
            enter()
            event.set()
            return

        print()
        print(f"Using {len(active_buildings)} {building_type}(s) in "
              f"{len({b['city_id'] for b in active_buildings})} cities")
        print()

        # Fetch building data
        print("=" * 60)
        print("FETCHING BUILDING DATA")
        print("=" * 60)
        print()

        buildings_data = []
        busy_buildings = []

        for b in active_buildings:
            print(f"  {b['city_name']} - L{b['building_level']}...", end=" ")
            data = fetch_building_data(session, b, is_units)
            if data is None:
                print(f"{bcolors.RED}FAILED{bcolors.ENDC}")
                continue
            if data.get('is_busy'):
                print(f"{bcolors.WARNING}BUSY{bcolors.ENDC} ({len(data['unit_data'])} unit types)")
                busy_buildings.append(data)
            else:
                print(f"{bcolors.GREEN}OK{bcolors.ENDC} ({len(data['unit_data'])} unit types)")
                buildings_data.append(data)

        print()

        if busy_buildings:
            print("=" * 60)
            print("BUSY BUILDINGS")
            print("=" * 60)
            print()
            for bb in busy_buildings:
                qt = bb.get('queue_remaining_time', 0)
                print(f"  • {bb['city_name']} L{bb['building_level']} — queue done in: "
                      f"{format_time(qt) if qt else 'unknown'}")
            print()
            print("1. Ignore busy buildings")
            print("2. Include them (used once their queue finishes)")
            print()
            print("(') Back to main menu")

            busy_choice = read(msg="Select (1 or 2): ", min=1, max=2, digit=True, additionalValues=["'"])
            if busy_choice == "'":
                event.set()
                return
            if busy_choice == 2:
                buildings_data.extend(busy_buildings)
            print()

        if not buildings_data:
            print(f"{bcolors.RED}No available {building_type}!{bcolors.ENDC}")
            print()
            enter()
            event.set()
            return

        # Unit quantities
        print("=" * 60)
        print("RECRUITMENT ORDER")
        print("=" * 60)
        print()
        print("Enter quantity for each unit (0 or Enter to skip):")
        print("(') Back to main menu at any prompt")
        print()

        recruitment_order = {}
        units_shown = 0

        for unit in unit_list:
            uid = unit['game_index']
            if not any(uid in b.get('unit_data', {}) for b in buildings_data):
                continue
            units_shown += 1

            sample = next((b['unit_data'][uid] for b in buildings_data if uid in b.get('unit_data', {})), None)
            cost_str = ""
            if sample:
                parts = []
                for res in ('citizens','wood','wine','marble','crystal','sulfur'):
                    if sample.get(res, 0):
                        parts.append(f"{sample[res]} {res}")
                t = format_time(int(sample.get('time_seconds', 0)))
                cost_str = f" [{', '.join(parts)}, {t}/unit]" if parts else f" [{t}/unit]"

            qty = read(msg=f"  {unit['name']}{cost_str}: ", min=0, digit=True, additionalValues=["'", ""])
            if qty == "'":
                event.set()
                return
            if qty == "":
                qty = 0
            if qty > 0:
                recruitment_order[uid] = {'name': unit['name'], 'quantity': qty}

        if units_shown == 0:
            print(f"\n{bcolors.RED}ERROR: No units parsed from buildings!{bcolors.ENDC}\n")
            enter()
            event.set()
            return

        if not recruitment_order:
            print(f"\n{bcolors.WARNING}No units selected.{bcolors.ENDC}\n")
            enter()
            event.set()
            return

        print()

        # Priority for this batch
        print("=" * 60)
        print("PRIORITY")
        print("=" * 60)
        print()
        print("Set priority for this order (1 = highest).")
        print("Additional orders placed later can have their own independent priority.")
        print()

        priority_raw = read(msg="Priority [default 1]: ", min=1, digit=True, additionalValues=["'", ""])
        if priority_raw == "'":
            event.set()
            return
        priority = priority_raw if isinstance(priority_raw, int) else 1
        print()

        # Distribution
        print("=" * 60)
        print("CALCULATING DISTRIBUTION")
        print("=" * 60)
        print()

        distribution = calculate_distribution(buildings_data, recruitment_order)
        if not distribution:
            print(f"{bcolors.RED}Could not calculate distribution!{bcolors.ENDC}")
            print()
            enter()
            event.set()
            return

        # Resource check
        print("Checking resources...")
        resource_check = check_resources(session, distribution, cities)
        print()

        # Report configuration
        print("=" * 60)
        print("PROGRESS REPORTS")
        print("=" * 60)
        print()

        report_raw = read(msg="Send periodic progress reports to Telegram? [Y/n]: ",
                          values=["y", "Y", "n", "N", ""])
        report_enabled = report_raw.lower() != "n"
        report_interval = 4

        if report_enabled:
            interval_raw = read(
                msg="Report interval in hours (default 4): ",
                min=1, digit=True, additionalValues=["'", ""]
            )
            if interval_raw == "'":
                event.set()
                return
            report_interval = interval_raw if isinstance(interval_raw, int) else 4
            print()

        # Resource import option
        print("=" * 60)
        print("RESOURCE IMPORT")
        print("=" * 60)
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
                print(f"  {bcolors.GREEN}Resource import enabled (30% buffer kept in source cities){bcolors.ENDC}")
                print("  Note: reservation module integration coming soon.")
        else:
            print(f"  {bcolors.WARNING}ResourceTransportManager not found — resource import unavailable.{bcolors.ENDC}")
        print()

        # Display plan and confirm
        print("=" * 60)
        print("RECRUITMENT PLAN")
        print("=" * 60)
        print()
        display_distribution_plan(distribution, recruitment_order)
        print()

        if resource_check['missing_resources']:
            print(f"{bcolors.WARNING}WARNING — RESOURCE SHORTAGES:{bcolors.ENDC}")
            for cid, missing in resource_check['missing_resources'].items():
                city_name = cities[cid]['name']
                for res, amt in missing.items():
                    print(f"  {city_name}: missing {addThousandSeparator(amt)} {res}")
            print()

        if resource_check['missing_citizens']:
            print(f"{bcolors.WARNING}WARNING — CITIZEN SHORTAGES:{bcolors.ENDC}")
            for cid, missing in resource_check['missing_citizens'].items():
                print(f"  {cities[cid]['name']}: missing {addThousandSeparator(missing)} citizens")
            print()

        confirm = read(msg="Proceed? [Y/n]: ", values=["y", "Y", "n", "N", "", "'"])
        if confirm == "'" or confirm.lower() == "n":
            print("\nCancelled.\n")
            enter()
            event.set()
            return

        print()

        # Build and save config
        cfg = {
            "report_enabled":         report_enabled,
            "report_interval_hours":  report_interval,
            "resource_import_enabled": resource_import_enabled,
            "last_report_time":       0,
        }
        save_config(session, cfg)

        has_shortages = resource_check['missing_resources'] or resource_check['missing_citizens']
        wait_for_busy = any(b.get('is_busy') for b in buildings_data)

        if has_shortages or wait_for_busy:
            # Background mode
            print("Starting background recruitment process...")
            print()
            if wait_for_busy:
                print("  • Will wait for busy buildings to free up")
            if resource_check['missing_citizens']:
                print("  • Will wait for citizens to grow")
            if resource_check['missing_resources']:
                print("  • Will recruit in batches as resources allow (20% threshold)")
            if report_enabled:
                print(f"  • Progress reports every {report_interval}h via Telegram")
            if resource_import_enabled:
                print("  • Will request resource imports when cities run short")
            print()
            print("  Progress saved to disk — can be resumed if interrupted.")
            print()
            enter()

            session_id = str(int(time.time()))
            csv_rows = _build_orders_from_distribution(distribution, is_units, session_id, priority, None)
            csv_save_orders(session, is_units, csv_rows)

            set_child_mode(session)
            event.set()

            total_units = sum(r['quantity'] for r in recruitment_order.values())
            unit_type_str = "units" if is_units else "ships"
            info = f"\nAuto Recruitment: {total_units:,} {unit_type_str} / priority {priority}\n"
            setInfoSignal(session, info)

            try:
                execute_recruitment_loop(
                    session, distribution, recruitment_order,
                    cities, is_units, resource_check, None, cfg,
                )
            except Exception:
                sendToBot(session, f"Error in:\n{info}\n{traceback.format_exc()}")
            finally:
                if RRS_AVAILABLE:
                    try:
                        release_all_for_module(session, MODULE_NAME)
                    except Exception:
                        pass
                session.logout()

        else:
            # Immediate mode
            print("Executing recruitment immediately...")
            print()
            success = execute_recruitment(session, distribution, is_units)
            print()
            total_units = sum(r['quantity'] for r in recruitment_order.values())
            if success:
                print(f"{bcolors.GREEN}Recruitment orders placed ({total_units:,} units).{bcolors.ENDC}")
                sendToBot(session, f"Auto Recruitment: {total_units:,} units placed successfully.")
            else:
                print(f"{bcolors.WARNING}Some orders may have failed.{bcolors.ENDC}")
            print()
            enter()
            event.set()

    except KeyboardInterrupt:
        event.set()
        return
