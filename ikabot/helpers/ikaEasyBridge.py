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
CM_INT_COLS = {"city_id", "slot_position", "target_level", "expected_finish"}


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
            for city_id, city in zip(cities_ids, cities):
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


# ── Modify Production ────────────────────────────────────────────────────────

def modify_production(session, body):
    """Apply worker percentages to resource/tradegood buildings.

    body fields:
      city_ids        : list of int city IDs to apply to
      resource_types  : list containing "resource", "tradegood", or both
      wood_pct        : int 0-100  (used when "resource" is in resource_types)
      luxury_pct      : int 0-100  (used when "tradegood" is in resource_types)
      maximise        : bool — if true and pct==100, includes overcharge workers
    """
    from ikabot.helpers.getJson import getCity, getIsland
    from ikabot.config import actionRequest, island_url, city_url
    import time as _time

    city_ids       = body.get("city_ids") or []
    resource_types = body.get("resource_types") or []
    wood_pct       = max(0, min(100, int(body.get("wood_pct", 100))))
    luxury_pct     = max(0, min(100, int(body.get("luxury_pct", 100))))
    maximise       = bool(body.get("maximise", False))

    if not city_ids:
        return {"ok": False, "error": "No cities specified."}
    if not resource_types:
        return {"ok": False, "error": "No resource types specified."}

    results = []

    # Group cities by island so we only fetch each island once.
    island_cache = {}
    cities_data  = {}
    for cid in city_ids:
        try:
            html = session.get(city_url + str(cid))
            city = getCity(html)
            cities_data[cid] = city
            iid = city.get("islandId")
            if iid and iid not in island_cache:
                island_cache[iid] = getIsland(session.get(island_url + str(iid)))
        except Exception as e:
            results.append({"city_id": cid, "ok": False, "error": str(e)})

    current_city_id = getCity(session.get()).get("id")

    for cid in city_ids:
        city = cities_data.get(cid)
        if not city:
            continue
        island_id = city.get("islandId")
        island    = island_cache.get(island_id, {})

        # Switch session context to this city.
        try:
            session.post(params={
                "action": "header", "function": "changeCurrentCity",
                "actionRequest": actionRequest, "cityId": cid,
                "oldView": "city", "backgroundView": "city",
                "currentCityId": current_city_id, "ajax": "1",
            })
            current_city_id = cid
        except Exception:
            pass

        city_result = {"city_id": cid, "city_name": city.get("name", str(cid)), "changes": []}

        for rtype in resource_types:
            pct = wood_pct if rtype == "resource" else luxury_pct
            try:
                url = (f"view={rtype}&type={rtype}&islandId={island_id}"
                       f"&cityId={cid}&backgroundView=island"
                       f"&currentIslandId={island_id}&actionRequest={actionRequest}&ajax=1")
                resp      = session.post(url)
                resp_json = json.loads(resp, strict=False)
                slider    = resp_json[2][1][f"js_ResourceSlider"]["slider"]
                max_norm  = slider["max_value"]
                overcharge = slider.get("overcharge", 0)

                if pct == 100 and maximise:
                    final = max_norm + overcharge
                else:
                    final = int(max_norm * pct / 100)

                worker_key = "rw" if rtype == "resource" else "tw"
                session.post(params={
                    "islandId": island_id, "cityId": cid,
                    "type": rtype, "screen": rtype,
                    "action": "IslandScreen", "function": "workerPlan",
                    worker_key: final, "templateView": rtype,
                    "actionRequest": actionRequest, "ajax": "1",
                })
                city_result["changes"].append({
                    "type": rtype, "pct": pct, "workers": final, "ok": True,
                })
            except Exception as e:
                city_result["changes"].append({
                    "type": rtype, "pct": pct, "ok": False, "error": str(e),
                })

            _time.sleep(1)

        city_result["ok"] = all(c["ok"] for c in city_result["changes"])
        results.append(city_result)

    return {"ok": True, "results": results}


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
