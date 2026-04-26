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
BUILD_POST_VERIFY_DELAY_SECONDS = 2
SUPPLIER_LIST_TTL_SECONDS = 120

# Order matches `materials_names_tec` in ikabot.config:
# ["wood", "wine", "marble", "glass", "sulfur"]
RESOURCE_FILENAMES = ("wood", "wine", "marble", "glass", "sulfur")

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
    sulphur].  Returns {} if the building has no ikipedia entry (wonders,
    palace variants, embassy variants, etc.) so callers can gracefully skip
    rather than crashing.

    Column-to-resource mapping is derived from the <th> image filenames
    in the cost table — NOT assumed positional — because many buildings only
    show a subset of the five resources (carpentering = 1 col, barracks = 2,
    town hall = 3, etc.).  A positional assumption would write marble costs
    into the wine slot for a barracks row, which would break cost math and
    over-ship the wrong resources.
    """
    # Step 1: load ikipedia listing page
    detail_url = (
        "view=buildingDetail&buildingId=0&helpId=1&backgroundView=city"
        "&currentCityId={cid}&templateView=ikipedia"
        "&actionRequest={ar}&ajax=1"
    ).format(cid=city["id"], ar=actionRequest)
    try:
        resp = session.post(detail_url)
        building_html = json.loads(resp, strict=False)[1][1][1]
    except Exception:
        return {}

    # Step 2: find the per-building ajaxHandlerCall URL in the ikipedia HTML.
    # Guard: wonders / palace / museum slugs have no `button_building` entry
    # here; the regex will return None, and we return {} so the scheduler's
    # existing `cost is None → skip` branch handles it cleanly.
    pat = (
        r'<div class="(?:selected)? *button_building '
        + re.escape(building_slug)
        + r'"\s*onmouseover=.*?onclick="ajaxHandlerCall\(\'\?(.*?)\'\);'
    )
    match = re.search(pat, building_html, re.DOTALL)
    if match is None:
        return {}
    cost_url = (
        match.group(1)
        + "&backgroundView=city&currentCityId={cid}"
          "&templateView=buildingDetail&actionRequest={ar}&ajax=1"
    ).format(cid=city["id"], ar=actionRequest)

    # Step 3: per-building cost table
    try:
        resp2 = session.post(cost_url)
        html_costs = json.loads(resp2, strict=False)[1][1][1]
    except Exception:
        return {}

    # Step 4: derive column → resource-index from th image filename.
    # RESOURCE_FILENAMES = ("wood","wine","marble","glass","sulfur") matches
    # materials_names_tec.  We do NOT hash PNG images; the URL filename already
    # carries the identity (e.g. ".../wood.png" → index 0).
    th_srcs = re.findall(r'<th class="costs"><img src="(.*?)\.png"', html_costs)
    # The last th is usually a clock icon for build time — drop it if it
    # doesn't match any known resource filename.
    col_to_res = []
    for src in th_srcs:
        fname = src.rstrip("/").split("/")[-1].lower()
        try:
            idx = RESOURCE_FILENAMES.index(fname)
        except ValueError:
            idx = -1  # unknown (e.g. time icon); values for this col ignored
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
