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
