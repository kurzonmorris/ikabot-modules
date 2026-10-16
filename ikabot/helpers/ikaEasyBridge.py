#! /usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import json
import os
import re
import time

from ikabot.helpers.process import updateProcessList

_HOME = os.path.expanduser("~")
_MODULES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "modules")

SCHEDULE_COLUMNS = [
    "schedule_id", "mode", "ship_type",
    "source_city_ids", "dest_city_ids", "resource_config",
    "send_mode", "dest_targets", "source_reserves", "dest_minimums",
    "bulk_csv_path", "bulk_run_column", "ap_max_wait_minutes",
    "min_shipment_threshold", "interval_hours", "notif_level",
    "status", "last_run", "next_run", "total_shipments",
    "created_at", "notes", "schema_version",
]
SCHEDULE_JSON_COLS = {
    "source_city_ids", "dest_city_ids", "resource_config",
    "dest_targets", "source_reserves", "dest_minimums",
}
SCHEDULE_SCHEMA_VERSION = 1
CM_INT_COLS = {"city_id", "slot_position", "target_level", "expected_finish",
               "wood", "wine", "marble", "crystal", "sulphur"}


def _safe(v):
    return re.sub(r"[^\w.-]", "_", str(v))


def _account_suffix(session):
    return f"{_safe(session.servidor)}_{_safe(session.username)}"


# ── Construction Manager ─────────────────────────────────────────────────────

def _cm_csv_path(session):
    return os.path.join(_HOME, f".ikabot_construction_{_account_suffix(session)}.csv")


def get_construction_queue(session):
    path = _cm_csv_path(session)
    cities = {}
    if not os.path.isfile(path):
        return cities

    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                city_id = row.get("city_id")
                if not city_id:
                    continue
                for col in CM_INT_COLS:
                    if row.get(col) not in (None, ""):
                        try:
                            row[col] = int(row[col])
                        except (ValueError, TypeError):
                            row[col] = None
                cities.setdefault(city_id, []).append({
                    "queue_id":       row.get("queue_id"),
                    "city_name":      row.get("city_name"),
                    "slot_position":  row.get("slot_position"),
                    "building":       row.get("building"),
                    "target_level":   row.get("target_level"),
                    "status":         row.get("status"),
                    "expected_finish": row.get("expected_finish"),
                    "wood":           row.get("wood") or 0,
                    "wine":           row.get("wine") or 0,
                    "marble":         row.get("marble") or 0,
                    "crystal":        row.get("crystal") or 0,
                    "sulphur":        row.get("sulphur") or 0,
                })
    except Exception:
        return {}

    return cities


# ── Processes ────────────────────────────────────────────────────────────────

def get_processes(session):
    out = []
    for p in updateProcessList(session):
        out.append({
            "pid":    p.get("pid"),
            "action": p.get("action"),
            "date":   p.get("date"),
            "status": p.get("status"),
        })
    return out


# ── Cities ───────────────────────────────────────────────────────────────────

def get_cities(session):
    """Return a minimal list of the account's cities for the extension."""
    data = session.getSessionData() or {}
    cities_raw = data.get("cities") or {}
    out = []
    for city_id, city in cities_raw.items():
        if not isinstance(city, dict):
            continue
        out.append({
            "id":   city.get("id") or city_id,
            "name": city.get("cityName") or city.get("name") or city_id,
        })
    return out


def get_resources_overview(session):
    """Return every city's five resource amounts and storage capacity.

    Reuses ikabot's own account scan (getStatus.collectData) instead of
    fetching each city again, so the numbers match the account-status view and
    the gather logic stays in one place. The web server restores the browser's
    city on the next GET, so the server-side city switches do not change what
    the user sees.
    """
    from ikabot.function.getStatus import collectData

    try:
        data = collectData(session)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    out = []
    for cid in data.get("own_ids", []):
        cur = (data["city_headers"].get(cid, {}) or {}).get("currentResources", {}) or {}
        info = data["city_info"].get(cid, {}) or {}

        def _int(v):
            try:
                return int(v)
            except (TypeError, ValueError):
                return 0

        out.append({
            "id":      cid,
            "name":    data["city_names"].get(cid, cid),
            "wood":    _int(cur.get("resource")),
            "wine":    _int(cur.get("1")),
            "marble":  _int(cur.get("2")),
            "crystal": _int(cur.get("3")),
            "sulphur": _int(cur.get("4")),
            "storage": info.get("storageCapacity"),
        })
    return {"ok": True, "cities": out}


# ── RTM schedule creation ─────────────────────────────────────────────────────

def _rtm_csv_path(session):
    return os.path.join(
        _HOME,
        f".ikabot_transport_{_account_suffix(session)}.csv",
    )


def _rtm_schema_sidecar(session):
    return _rtm_csv_path(session).replace(".csv", "_schema.json")


def _rtm_next_id(session):
    path = _rtm_csv_path(session)
    if not os.path.isfile(path):
        return 1
    try:
        with open(path, "r", encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        ids = [int(r.get("schedule_id", 0) or 0) for r in rows]
        return max(ids, default=0) + 1
    except Exception:
        return 1


def _coerce_out(row):
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


def create_rtm_schedule(session, body):
    src_id    = int(body.get("source_city_id", 0))
    dest_id   = int(body.get("dest_city_id", 0))
    resources = body.get("resources") or {}
    keeps     = body.get("keeps") or {}
    ship_type = body.get("ship_type", "merchant_ships")
    interval  = max(1, int(body.get("interval_hours", 4)))

    if not src_id or not dest_id:
        return {"ok": False, "error": "Missing source or destination city."}

    # Only include resources where the user actually set an amount.
    resource_config = {k: v for k, v in resources.items() if v and int(v) > 0}
    if not resource_config:
        return {"ok": False, "error": "No resources selected."}

    source_reserves = {k: int(v) for k, v in keeps.items() if v}

    path   = _rtm_csv_path(session)
    exists = os.path.isfile(path)

    row = _coerce_out({
        "schedule_id":           _rtm_next_id(session),
        "mode":                  "autosend",
        "ship_type":             ship_type,
        "source_city_ids":       [src_id],
        "dest_city_ids":         [dest_id],
        "resource_config":       resource_config,
        "send_mode":             "fixed",
        "dest_targets":          {},
        "source_reserves":       source_reserves,
        "dest_minimums":         {},
        "bulk_csv_path":         "",
        "bulk_run_column":       "",
        "ap_max_wait_minutes":   0,
        "min_shipment_threshold": 0,
        "interval_hours":        interval,
        "notif_level":           "errors",
        "status":                "active",
        "last_run":              "",
        "next_run":              "",
        "total_shipments":       0,
        "created_at":            int(time.time()),
        "notes":                 "Created via IkaEasy",
        "schema_version":        SCHEDULE_SCHEMA_VERSION,
    })

    try:
        with open(path, "a", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=SCHEDULE_COLUMNS)
            if not exists:
                writer.writeheader()
            writer.writerow(row)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    # Write schema sidecar if missing (so RTM worker knows the version).
    sidecar = _rtm_schema_sidecar(session)
    if not os.path.isfile(sidecar):
        try:
            with open(sidecar, "w", encoding="utf-8") as f:
                json.dump({"version": SCHEDULE_SCHEMA_VERSION, "columns": SCHEDULE_COLUMNS}, f)
        except Exception:
            pass

    return {"ok": True, "schedule_id": int(row["schedule_id"])}


# ── Construction Manager: add to queue from the building view ────────────────

def _module_path(pattern):
    """Return the path to a versioned module file matching *pattern*, or None."""
    try:
        for name in os.listdir(_MODULES_DIR):
            if re.match(pattern, name):
                return os.path.join(_MODULES_DIR, name)
    except OSError:
        pass
    return None


def _load_module(name, pattern):
    path = _module_path(pattern)
    if not path:
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def get_cm_status(session):
    """Report whether the Construction Manager module is installed and whether
    its build worker is currently running for this account."""
    cm = _load_module("constructionManager", r"constructionManager_v[\d.]+\.py$")
    if cm is None:
        return {"available": False, "worker_running": False}
    try:
        running = bool(cm._is_worker_running(session))
    except Exception:
        running = False
    return {"available": True, "worker_running": running}


def cm_add_building(session, body):
    """Queue *levels* more level-ups for the building at *slot_position* in a
    city, by reusing the Construction Manager's own cost lookup and CSV insert.

    body fields:
      city_id       : int
      slot_position : int (0-15)
      levels        : int — how many levels to add beyond what is already there
                      or already queued
      transport_mode: "jit" | "bulk" | "none"  (default "jit")
      start         : bool — also start the build worker if it is not running
    """
    cm = _load_module("constructionManager", r"constructionManager_v[\d.]+\.py$")
    if cm is None:
        return {"ok": False, "error": "Construction Manager module not installed."}

    try:
        city_id = int(body.get("city_id", 0))
        slot_pos = int(body.get("slot_position", -1))
        levels = int(body.get("levels", 0))
    except (TypeError, ValueError):
        return {"ok": False, "error": "Bad city_id, slot_position or levels."}

    if not city_id or slot_pos < 0:
        return {"ok": False, "error": "Missing city or slot."}
    if levels < 1:
        return {"ok": False, "error": "Enter at least one level."}

    mode = body.get("transport_mode", "jit")
    if mode not in ("jit", "bulk", "none"):
        mode = "jit"

    from ikabot.helpers.getJson import getCity
    from ikabot.config import city_url
    try:
        city = getCity(session.get(city_url + str(city_id)))
    except Exception as e:
        return {"ok": False, "error": f"Could not read city: {e}"}
    if str(city.get("id")) != str(city_id):
        return {"ok": False, "error": "That city is not yours."}

    positions = city.get("position", [])
    if slot_pos >= len(positions):
        return {"ok": False, "error": "Slot out of range."}
    slot = positions[slot_pos]

    city_name = city.get("cityName") or city.get("name", str(city_id))
    is_empty = slot.get("building") == "empty"

    # Pick up where current level / already-queued rows for this slot end.
    slot_queue = cm._gather_pending_for_city(session, city_id).get(slot_pos, [])
    queued_max = max((q["target_level"] for q in slot_queue), default=0)
    build_queued = any(q["action"] == "build" for q in slot_queue)

    if is_empty and not build_queued:
        building_slug = slot.get("building")  # "empty" — resolved below
        return {"ok": False, "error": "This slot is empty. Build it in-game first, "
                                      "then queue upgrades."}
    building_slug = slot_queue[0]["building"] if slot_queue else slot.get("building")
    building_id = ""

    current_lv = int(slot.get("level", 0) or 0)
    start_from = max(current_lv, queued_max)
    target_level = min(start_from + levels, cm.HARD_LEVEL_CAP)
    if target_level <= start_from:
        return {"ok": False, "error": f"Already at or above the level cap "
                                      f"({cm.HARD_LEVEL_CAP})."}

    rows_to_add = [(lv, "upgrade") for lv in range(start_from + 1, target_level + 1)]

    costs = cm.fetch_costs_for_building(session, city, building_slug) or {}
    import time as _time
    now_ts = int(_time.time())
    new_rows = []
    for lv, action_type in rows_to_add:
        c = costs.get(lv)
        cost = c if c is not None else [0, 0, 0, 0, 0]
        new_rows.append({
            "queue_id":               0,
            "city_id":                city_id,
            "city_name":              city_name,
            "slot_position":          slot_pos,
            "action":                 action_type,
            "building":               building_slug,
            "building_id":            building_id,
            "target_level":           lv,
            "wood":                   cost[0],
            "wine":                   cost[1],
            "marble":                 cost[2],
            "crystal":                cost[3],
            "sulphur":                cost[4],
            "transport_mode":         mode,
            "auto_transport_enabled": "no" if mode == "none" else "yes",
            "status":                 "pending",
            "expected_finish":        "",
            "added_at":               now_ts,
            "notes":                  "" if c is not None else
                                      "cost unknown at add-time; recomputed before execution",
            "schema_version":         cm.SCHEMA_VERSION,
        })

    pending_count = sum(
        1 for r in cm.csv_load(session)
        if r["city_id"] == city_id and r["status"] == "pending"
    )
    try:
        qids = cm._csv_insert_at(session, city_id, new_rows, pending_count)
    except Exception as e:
        return {"ok": False, "error": f"Could not add to queue: {e}"}

    result = {"ok": True, "added": len(qids), "target_level": target_level,
              "building": building_slug}

    if body.get("start"):
        result["worker"] = _cm_start_worker(session, cm)

    return result


def _cm_start_worker(session, cm):
    """Start the Construction Manager build worker if it is not already running,
    reusing schedulerMonitor's headless spawn."""
    try:
        if cm._is_worker_running(session):
            return "already running"
    except Exception:
        pass
    sm = _load_module("schedulerMonitor", r"schedulerMonitor_v[\d.]+\.py$")
    if sm is None:
        return "scheduler monitor not installed — start the worker from the ikabot menu"
    try:
        sm._activate(session, "construction")
        return "started"
    except Exception as e:
        return f"could not start worker: {e}"


# ── Tavern Manager ───────────────────────────────────────────────────────────

def _tavern_module_path():
    """Return path to tavernManager_vX.Y.Z.py, or None if not installed."""
    try:
        for name in os.listdir(_MODULES_DIR):
            if re.match(r"tavernManager_v[\d.]+\.py$", name):
                return os.path.join(_MODULES_DIR, name)
    except OSError:
        pass
    return None


def get_tavern_status(session):
    """Return whether the tavern manager module is present."""
    return {"available": _tavern_module_path() is not None}


def apply_tavern(session, body):
    """Apply a tavern mode to all cities with a tavern.

    body fields:
      mode        : "set_pct" | "equilibrium"
      pct         : int 0-100  (required for set_pct)
      interval_hrs: int 1-24   (required for equilibrium; 0 = run once)
    """
    module_path = _tavern_module_path()
    if not module_path:
        return {"ok": False, "error": "tavernManager module not installed."}

    mode = body.get("mode", "")
    if mode not in ("set_pct", "equilibrium"):
        return {"ok": False, "error": f"Unknown mode '{mode}'."}

    # Dynamically load the module so we don't need a hard import.
    import importlib.util
    spec = importlib.util.spec_from_file_location("tavernManager", module_path)
    tm_mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(tm_mod)
    except Exception as e:
        return {"ok": False, "error": f"Failed to load tavernManager: {e}"}

    TavernManager = tm_mod.TavernManager

    from ikabot.helpers.pedirInfo import getIdsOfCities
    try:
        cities_ids, cities = getIdsOfCities(session)
    except Exception as e:
        return {"ok": False, "error": f"Could not fetch city list: {e}"}

    mgr = TavernManager(session, notification_mode=3)

    try:
        if mode == "set_pct":
            pct = int(body.get("pct", 0))
            pct = max(0, min(100, pct))
            results = []
            for city_id in cities_ids:
                city = cities[city_id]
                ok = mgr.set_tavern_pct(city, pct)
                results.append({"city": city.get("name", str(city_id)), "ok": bool(ok)})
            return {"ok": True, "mode": "set_pct", "pct": pct, "results": results}

        else:  # equilibrium
            interval = int(body.get("interval_hrs", 24))
            interval = max(0, min(24, interval))
            results = mgr.process_equilibrium(cities_ids, cities)
            summary = []
            for r in (results or []):
                summary.append({
                    "city":   r.get("name", ""),
                    "action": r.get("action", ""),
                    "note":   r.get("note", ""),
                })
            return {"ok": True, "mode": "equilibrium", "interval_hrs": interval,
                    "results": summary}

    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Resource Production Manager ───────────────────────────────────────────────

def _resprod_module_path():
    """Return path to resourceProductionManager_vX.Y.Z.py, or None if absent."""
    try:
        for name in os.listdir(_MODULES_DIR):
            if re.match(r"resourceProductionManager_v[\d.]+\.py$", name):
                return os.path.join(_MODULES_DIR, name)
    except OSError:
        pass
    return None


def _load_resprod():
    """Dynamically load the Resource Production Manager module, or None."""
    path = _resprod_module_path()
    if not path:
        return None
    import importlib.util
    spec = importlib.util.spec_from_file_location("resourceProductionManager", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None


def get_prod_status(session):
    """Return whether the Resource Production Manager module is present."""
    mod = _load_resprod()
    return {"available": mod is not None,
            "version": getattr(mod, "__version__", None) if mod else None}


def modify_production(session, body):
    """Apply worker percentages by delegating to the Resource Production
    Manager module (resourceProductionManager_vX.Y.Z.py).

    body fields:
      city_ids   : list of int city IDs to apply to
      mode        : "wood" | "luxury" | "wood_then_luxury" | "luxury_then_wood"
      wood_pct    : int 0-100  (used when the mode includes wood)
      luxury_pct  : int 0-100  (used when the mode includes luxury)
      overcharge  : bool — if true and luxury_pct==100, fill luxury beyond 100%

    Backwards compatible with the older UI payload that sent
    `resource_types` + `maximise` instead of `mode` + `overcharge`.
    """
    mod = _load_resprod()
    if mod is None:
        return {"ok": False,
                "error": "Resource Production Manager module not installed."}

    city_ids = body.get("city_ids") or []
    if not city_ids:
        return {"ok": False, "error": "No cities specified."}

    wood_pct   = max(0, min(100, int(body.get("wood_pct", 100))))
    luxury_pct = max(0, min(100, int(body.get("luxury_pct", 100))))
    overcharge = bool(body.get("overcharge", body.get("maximise", False)))

    # Resolve the mode. Prefer the new `mode` field; fall back to the old
    # `resource_types` list so older extension builds keep working.
    mode = body.get("mode")
    if mode not in ("wood", "luxury", "wood_then_luxury", "luxury_then_wood"):
        rtypes = body.get("resource_types") or []
        has_wood = "resource" in rtypes
        has_lux  = "tradegood" in rtypes
        if has_wood and has_lux:
            mode = "wood_then_luxury"
        elif has_lux:
            mode = "luxury"
        else:
            mode = "wood"

    mode_map = {
        "wood":             mod.MODE_WOOD,
        "luxury":           mod.MODE_LUXURY,
        "wood_then_luxury": mod.MODE_WOOD_THEN_LUXURY,
        "luxury_then_wood": mod.MODE_LUXURY_THEN_WOOD,
    }
    mode_const = mode_map[mode]
    order = mod.MODE_ORDER[mode_const]

    plan = {
        "order":      order,
        "wood_pct":   wood_pct   if mod.RES_WOOD   in order else None,
        "luxury_pct": luxury_pct if mod.RES_LUXURY in order else None,
        "overcharge": overcharge,
    }

    # Build the city list the module expects: {id, name, luxury_name}.
    from ikabot.helpers.pedirInfo import getIdsOfCities
    try:
        _all_ids, all_cities = getIdsOfCities(session)
    except Exception as e:
        return {"ok": False, "error": f"Could not fetch city list: {e}"}

    from ikabot.config import materials_names
    wanted = {str(c) for c in city_ids}
    cities = []
    for cid, c in all_cities.items():
        if str(cid) not in wanted and str(c.get("id")) not in wanted:
            continue
        try:
            luxury_name = materials_names[int(c["tradegood"])]
        except (KeyError, ValueError, TypeError):
            luxury_name = ""
        cities.append({"id": cid, "name": c.get("name", str(cid)),
                       "luxury_name": luxury_name})

    if not cities:
        return {"ok": False, "error": "None of the requested cities were found."}

    memory = mod.load_memory(session)

    try:
        raw = mod.apply_plan(session, memory, cities, plan)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    # Translate the module's per-city report into the extension's shape.
    results = []
    for entry in (raw or []):
        results.append({
            "city_name": entry.get("name", ""),
            "ok":        entry.get("error") is None,
            "error":     entry.get("error"),
            "changes":   entry.get("changes", []),
        })

    return {"ok": True, "mode": mode, "results": results}


# ── Main dispatcher ───────────────────────────────────────────────────────────

def handle(session, request, flask):
    # POST — schedule creation.
    if request.method == "POST":
        try:
            body = request.get_json(force=True) or {}
        except Exception:
            body = {}

        action = body.get("ikaeasy_action", "")
        if action == "schedule":
            payload = create_rtm_schedule(session, body)
        elif action == "tavern_apply":
            payload = apply_tavern(session, body)
        elif action == "modify_production":
            payload = modify_production(session, body)
        elif action == "cm_add":
            payload = cm_add_building(session, body)
        else:
            payload = {"ok": False, "error": "Unknown action."}

        return flask.Response(
            json.dumps(payload), 200,
            {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"}
        )

    # GET.
    sub = request.args.get("ikaeasy", "all")

    if sub == "cities":
        payload = {"cities": get_cities(session)}
    elif sub == "construction":
        payload = {"construction": get_construction_queue(session)}
    elif sub == "processes":
        payload = {"processes": get_processes(session)}
    elif sub == "tavern_status":
        payload = get_tavern_status(session)
    elif sub == "prod_status":
        payload = get_prod_status(session)
    elif sub == "resources":
        payload = get_resources_overview(session)
    elif sub == "cm_status":
        payload = get_cm_status(session)
    else:
        payload = {
            "construction": get_construction_queue(session),
            "processes":    get_processes(session),
            "account": {
                "server":   session.servidor,
                "username": session.username,
            },
        }

    return flask.Response(
        json.dumps(payload), 200,
        {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"}
    )
