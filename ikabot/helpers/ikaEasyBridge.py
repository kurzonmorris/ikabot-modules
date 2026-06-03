#! /usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import json
import os
import re
import time

from ikabot.helpers.process import updateProcessList

_HOME = os.path.expanduser("~")

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
