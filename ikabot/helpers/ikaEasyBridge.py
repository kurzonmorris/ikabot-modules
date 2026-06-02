#! /usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import json
import os
import re

from ikabot.helpers.process import updateProcessList

# Construction Manager and RTM write their data files to the user's home
# directory, not IKABOT_DATA_DIR. These paths must match those modules.
_HOME = os.path.expanduser("~")


def _safe(value):
    return re.sub(r"[^\w.-]", "_", str(value))


def _account_suffix(session):
    return f"{_safe(session.servidor)}_{_safe(session.username)}"


def _cm_csv_path(session):
    return os.path.join(_HOME, f".ikabot_construction_{_account_suffix(session)}.csv")


_CM_INT_COLS = {"city_id", "slot_position", "target_level", "expected_finish"}


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
                for col in _CM_INT_COLS:
                    if row.get(col) not in (None, ""):
                        try:
                            row[col] = int(row[col])
                        except (ValueError, TypeError):
                            row[col] = None
                cities.setdefault(city_id, []).append({
                    "queue_id": row.get("queue_id"),
                    "city_name": row.get("city_name"),
                    "slot_position": row.get("slot_position"),
                    "building": row.get("building"),
                    "target_level": row.get("target_level"),
                    "status": row.get("status"),
                    "expected_finish": row.get("expected_finish"),
                })
    except Exception:
        return {}

    return cities


def get_processes(session):
    out = []
    for p in updateProcessList(session):
        out.append({
            "pid": p.get("pid"),
            "action": p.get("action"),
            "date": p.get("date"),
            "status": p.get("status"),
        })
    return out


def handle(session, request, flask):
    action = request.args.get("ikaeasy", "all")

    if action == "construction":
        payload = {"construction": get_construction_queue(session)}
    elif action == "processes":
        payload = {"processes": get_processes(session)}
    else:
        payload = {
            "construction": get_construction_queue(session),
            "processes": get_processes(session),
            "account": {
                "server": session.servidor,
                "username": session.username,
            },
        }

    return flask.Response(
        json.dumps(payload), 200, {"Content-Type": "application/json"}
    )
