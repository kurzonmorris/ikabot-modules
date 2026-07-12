#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""Resource Production Manager — external module.

WHAT THIS MODULE DOES (in plain language)
-----------------------------------------
Every city in Ikariam produces two kinds of resources:

  * WOOD    — mined at the Saw Mill. Every island produces wood.
  * LUXURY  — the island's special good: Wine, Marble, Crystal or Sulfur.
              (Which one depends on the island the city sits on.)

You assign "workers" (citizens) to each of these. More workers = more
resource per hour. This module lets you set, in bulk, how many workers
each city puts on wood and/or luxury — expressed as a simple percentage
of the maximum the building can hold.

It improves on ikabot's built-in "Modify production" (main-menu option 23)
in four ways:

  1. It remembers each city's island + location so repeat runs skip the
     slow discovery step (island layouts almost never change).
  2. It can keep running in the BACKGROUND and re-apply your settings on a
     schedule (e.g. every 48 hours, or every 5 days 12 hours) so that as
     your population grows and new citizens become available, they are
     automatically put to work.
  3. It sends you a notification (Telegram/Discord/ntfy — whatever you have
     configured) after every run, listing exactly what it changed and where.
  4. It is crash-resistant: a problem in one city no longer aborts the whole
     batch, and a hiccup in one background cycle no longer kills the task.

HOW WORKERS / PERCENTAGES WORK
------------------------------
Each production building has a normal worker capacity (the "100%" point).
By paying gold you can push BEYOND that — this is called "overcharge".

  * Setting 50% puts half the normal capacity to work.
  * Setting 100% fills the building normally (free).
  * Enabling OVERCHARGE fills it beyond 100% (costs gold continuously,
     but produces the most). Overcharge is offered for LUXURY here.

The game will never assign more workers than you actually have free
citizens for, so if you ask for 100% but your city is short on people,
it simply assigns as many as it can. That is exactly why the recurring
option is useful: run it again later and the newly-grown citizens get
assigned automatically.

This file is a standalone ikabot external module. Drop it in your external
modules directory (main-menu option 30) and it appears in the list.
"""

import sys
import os
import json
import re
import traceback

# Mirror the import style of the other modules in this repo. The star imports
# transitively provide `config`, `bcolors`, `read`, `getIdsOfCities`, `wait`,
# `getDateTime`, `sendToBot`, etc., and stay compatible with older ikabot
# installs. `config` is imported explicitly so the entry point can never fail
# with a NameError regardless of how the helpers are wired.
import ikabot.config as config
from ikabot.config import *
from ikabot.helpers.botComm import *
from ikabot.helpers.getJson import getCity
from ikabot.helpers.gui import *
from ikabot.helpers.pedirInfo import *
from ikabot.helpers.process import set_child_mode
from ikabot.helpers.signals import setInfoSignal
from ikabot.helpers.varios import *

# The external-module loader reads these two names.
MODULE_NAME = "Resource Production Manager"
MODULE_ENTRY = "resourceProductionManager"
__version__ = "1.0.1"

# Modes offered on the main menu.
MODE_SCAN = 1
MODE_WOOD = 2
MODE_LUXURY = 3
MODE_WOOD_THEN_LUXURY = 4
MODE_LUXURY_THEN_WOOD = 5

# Human-readable label for each mode (used in prompts and notifications).
MODE_LABELS = {
    MODE_WOOD: "Wood only",
    MODE_LUXURY: "Luxury only",
    MODE_WOOD_THEN_LUXURY: "Wood first, then Luxury",
    MODE_LUXURY_THEN_WOOD: "Luxury first, then Wood",
}

# ikabot uses "resource" for the wood slider and "tradegood" for luxury.
RES_WOOD = "resource"
RES_LUXURY = "tradegood"

# The order in which resources are set, per mode. Order matters in-game:
# whichever resource is filled first gets first claim on free citizens.
MODE_ORDER = {
    MODE_WOOD: [RES_WOOD],
    MODE_LUXURY: [RES_LUXURY],
    MODE_WOOD_THEN_LUXURY: [RES_WOOD, RES_LUXURY],
    MODE_LUXURY_THEN_WOOD: [RES_LUXURY, RES_WOOD],
}

# Never schedule a recurring run faster than this (protects your account
# from hammering the server). One hour is plenty — citizens grow slowly.
MIN_INTERVAL_SECONDS = 3600

# Polite pause between cities so the bot does not fire requests too fast.
WAIT_BETWEEN_CITIES = (2, 4)


# ---------------------------------------------------------------------------
# Per-account memory file
#
# We remember each city's island id, name and luxury type so that repeat
# runs don't have to re-walk every island to rediscover them. Island
# layouts effectively never change, so this is safe and is the main speed
# win over the built-in module. The file is keyed by server + username so
# different accounts never share memory.
# ---------------------------------------------------------------------------

def _safe(value):
    return re.sub(r"[^\w.-]", "_", str(value))


def _memory_path(session):
    return os.path.join(
        os.path.expanduser("~"),
        f".ikabot_resprod_{_safe(session.servidor)}_{_safe(session.username)}.json",
    )


def load_memory(session):
    """Return the remembered data as a dict. Never raises."""
    try:
        with open(_memory_path(session), "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("topology", {})
            data.setdefault("last_settings", {})
            return data
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return {"topology": {}, "last_settings": {}}


def save_memory(session, data):
    """Persist remembered data. Failures are non-fatal (memory is an
    optimisation, not a requirement)."""
    try:
        with open(_memory_path(session), "w") as f:
            json.dump(data, f, indent=2)
    except OSError:
        pass


def _notifications_ready(session):
    """True if the player has a notification channel configured.

    `notificationDataIsValid` is a relatively recent helper; if the running
    ikabot is older and doesn't have it, we assume notifications are ready
    (the actual send is guarded separately) so this never breaks startup.
    """
    try:
        return notificationDataIsValid(session)
    except Exception:
        return True


def remember_city(session, memory, city):
    """Store a city's stable facts (island id, name, luxury type)."""
    memory["topology"][str(city["id"])] = {
        "islandId": str(city["islandId"]),
        "name": city["name"],
    }
    save_memory(session, memory)


# ---------------------------------------------------------------------------
# Recurrence parsing:  "48" / "48h" / "5d" / "5d12h" / "5d 12h"  ->  seconds
# ---------------------------------------------------------------------------

def parse_interval(text):
    """Turn a human duration string into a number of seconds.

    Accepted forms (spaces optional, case-insensitive):
        "0" / "once" / ""   -> None  (run one time only)
        "48"                -> 48 hours   (a bare number means hours)
        "48h"               -> 48 hours
        "5d"                -> 5 days
        "5d12h" / "5d 12h"  -> 5 days and 12 hours

    Returns
    -------
    int   number of seconds, if a recurring interval was given
    None  if the user asked to run only once

    Raises
    ------
    ValueError  with a friendly message if the text can't be understood
                or the interval is below the one-hour minimum.
    """
    t = text.strip().lower().replace(" ", "")
    if t in ("", "0", "o", "once"):
        return None

    if t.isdigit():
        seconds = int(t) * 3600
    else:
        m = re.fullmatch(r"(?:(\d+)d)?(?:(\d+)h)?", t)
        if m is None or (m.group(1) is None and m.group(2) is None):
            raise ValueError(
                "I couldn't understand that. Use a number of hours (e.g. 48), "
                "or days/hours like 5d, 12h, or 5d12h."
            )
        days = int(m.group(1) or 0)
        hours = int(m.group(2) or 0)
        seconds = days * 86400 + hours * 3600

    if seconds == 0:
        return None
    if seconds < MIN_INTERVAL_SECONDS:
        raise ValueError("The shortest allowed interval is 1 hour.")
    return seconds


def format_interval(seconds):
    """Render seconds back into a compact "5d 12h" style string."""
    if seconds is None:
        return "one time only"
    days, rem = divmod(int(seconds), 86400)
    hours = rem // 3600
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    return " ".join(parts) if parts else "less than 1h"


# ---------------------------------------------------------------------------
# City selection:  "all"  or  "1,3,5"  or  "1 3 5"  or  "1, 3 ,5"
# ---------------------------------------------------------------------------

def parse_city_selection(text, count):
    """Parse a city-picker answer into a list of 0-based indices.

    Parameters
    ----------
    text : str    what the user typed
    count : int   how many cities are in the numbered list

    Returns
    -------
    list[int]  sorted, de-duplicated 0-based indices

    Raises
    ------
    ValueError  on empty input, a non-number, or an out-of-range number.
    """
    t = text.strip().lower()
    if t in ("all", "a", "*"):
        return list(range(count))

    indices = []
    for token in re.split(r"[,\s]+", t):
        if token == "":
            continue
        if not token.isdigit():
            raise ValueError(f'"{token}" is not a number. Type "all" or e.g. 1,3,5.')
        n = int(token)
        if n < 1 or n > count:
            raise ValueError(f"{n} is not in the list (pick between 1 and {count}).")
        indices.append(n - 1)

    if not indices:
        raise ValueError('Nothing selected. Type "all" or e.g. 1,3,5.')
    return sorted(set(indices))


def select_cities(session):
    """Show a numbered list of the player's cities and let them choose.

    Returns a list of dicts: {id, name, luxury_name}. Returns None if the
    user backs out with '.
    """
    ids, cities = getIdsOfCities(session)
    # Present in a stable, human order (by city name).
    ordered = sorted(ids, key=lambda cid: cities[cid]["name"].lower())

    banner()
    print("Which cities do you want to change?\n")
    print("Below is a numbered list of your cities. The letter in brackets is")
    print("the island's LUXURY good (Wine / Marble / Crystal / Sulfur).\n")

    for i, cid in enumerate(ordered, start=1):
        good_idx = int(cities[cid]["tradegood"])
        luxury = materials_names[good_idx]
        print(f"  ({i}) {cities[cid]['name']}  [{luxury}]")

    print()
    print('  Type "all" for every city, or list the numbers you want,')
    print("  separated by commas or spaces, e.g.:  1,3,5   or   1 3 5")
    print("  (') Go back")
    print()

    while True:
        answer = read(msg="Your choice: ", additionalValues=["'"])
        if answer == "'":
            return None
        try:
            chosen = parse_city_selection(answer, len(ordered))
        except ValueError as e:
            print(f"{bcolors.RED}{e}{bcolors.ENDC}")
            continue
        break

    selected = []
    for idx in chosen:
        cid = ordered[idx]
        good_idx = int(cities[cid]["tradegood"])
        selected.append(
            {"id": cid, "name": cities[cid]["name"], "luxury_name": materials_names[good_idx]}
        )
    return selected


# ---------------------------------------------------------------------------
# Percentage + overcharge prompts
# ---------------------------------------------------------------------------

def ask_percentage(resource_label):
    """Ask for a 0-100 percentage. Returns the int, or None if backed out."""
    print()
    print(f"What percentage of the {resource_label} workers do you want to assign?")
    print("  100 = fill the building completely (the usual choice)")
    print("   50 = use half of it,  0 = clear all workers")
    print("  (Press Enter for 100%, or ' to go back.)")
    answer = read(min=0, max=100, default=100, additionalValues=["'"])
    if answer == "'":
        return None
    return answer


def ask_overcharge():
    """Ask whether to overcharge luxury. Returns True/False, or None if backed out."""
    print()
    print("Use OVERCHARGE for luxury?")
    print("  Overcharge assigns MORE workers than the building's normal limit,")
    print("  producing extra luxury but costing gold continuously.")
    print("  (y = yes, N = no. Press Enter for no, or ' to go back.)")
    answer = read(values=["y", "Y", "n", "N", "", "'"], additionalValues=["'"])
    if answer == "'":
        return None
    return answer.lower() == "y"


def ask_interval():
    """Ask how often to repeat. Returns (seconds_or_None, backed_out_bool)."""
    print()
    print("How often should this run?")
    print("  Enter 0 (or just press Enter) to apply once now and finish.")
    print("  Or enter a repeat interval to keep it running in the background:")
    print("     48    or  48h   = every 48 hours")
    print("     5d           = every 5 days")
    print("     5d12h  or  5d 12h  = every 5 days and 12 hours")
    print("  Minimum interval is 1 hour.")
    print("  (' to go back.)")
    while True:
        answer = read(msg="Interval: ", empty=True, additionalValues=["'"])
        if answer == "'":
            return None, True
        try:
            return parse_interval(answer), False
        except ValueError as e:
            print(f"{bcolors.RED}{e}{bcolors.ENDC}")


# ---------------------------------------------------------------------------
# Talking to the game: read a production slider, set workers
# ---------------------------------------------------------------------------

def _slider_current_value(slider):
    """Best-effort extraction of the CURRENT worker count from a slider blob,
    so we can report "was X, now Y". The exact key has varied across game
    versions, so we try the common ones and give up gracefully."""
    for key in ("value", "count", "current", "actualWorker", "workers", "amount"):
        if key in slider:
            try:
                return int(slider[key])
            except (TypeError, ValueError):
                continue
    return None


def read_slider(session, island_id, city_id, resource_type):
    """Read a city's production slider. Returns (max_normal, total_max, current)."""
    url = (
        f"view={resource_type}&type={resource_type}&islandId={island_id}"
        f"&cityId={city_id}&backgroundView=island&currentIslandId={island_id}"
        f"&actionRequest={actionRequest}&ajax=1"
    )
    resp = session.post(url)
    resp_json = json.loads(resp, strict=False)
    slider = resp_json[2][1]["js_ResourceSlider"]["slider"]
    max_normal = slider["max_value"]
    total_max = slider["max_value"] + slider["overcharge"]
    return max_normal, total_max, _slider_current_value(slider)


def change_current_city(session, city_id, previous_city_id):
    """Switch the game's "active city" context, mirroring the built-in module."""
    session.post(
        params={
            "action": "header",
            "function": "changeCurrentCity",
            "actionRequest": actionRequest,
            "cityId": city_id,
            "oldView": "city",
            "backgroundView": "city",
            "currentCityId": previous_city_id,
            "ajax": "1",
        }
    )


def set_workers(session, island_id, city_id, resource_type, workers):
    """Assign `workers` to a city's wood or luxury production."""
    worker_key = "rw" if resource_type == RES_WOOD else "tw"
    session.post(
        params={
            "islandId": island_id,
            "cityId": city_id,
            "type": resource_type,
            "screen": resource_type,
            "action": "IslandScreen",
            "function": "workerPlan",
            worker_key: workers,
            "templateView": resource_type,
            "actionRequest": actionRequest,
            "ajax": "1",
        }
    )


# ---------------------------------------------------------------------------
# Resolving a city's island id (from memory if we've seen it before)
# ---------------------------------------------------------------------------

def resolve_island_id(session, memory, city_id):
    """Return the island id for a city, using remembered data when possible.
    Falls back to fetching the city page (and remembers the result)."""
    remembered = memory["topology"].get(str(city_id))
    if remembered and remembered.get("islandId"):
        return remembered["islandId"]
    city = getCity(session.get(city_url + city_id))
    remember_city(session, memory, city)
    return str(city["islandId"])


# ---------------------------------------------------------------------------
# The core: apply the chosen plan to every selected city
# ---------------------------------------------------------------------------

def apply_plan(session, memory, cities, plan):
    """Apply worker settings to every city and collect a per-city report.

    `plan` is a dict with keys:
        order        : list of RES_WOOD / RES_LUXURY in the order to set them
        wood_pct     : int or None
        luxury_pct   : int or None
        overcharge   : bool

    Returns a list of per-city result dicts (used to build the notification).
    """
    results = []

    # The game tracks a "current city"; start from whatever is active now.
    try:
        previous_city_id = getCity(session.get())["id"]
    except Exception:
        previous_city_id = cities[0]["id"]

    for city in cities:
        city_id = city["id"]
        entry = {"name": city["name"], "changes": [], "error": None}
        try:
            island_id = resolve_island_id(session, memory, city_id)
            change_current_city(session, city_id, previous_city_id)
            previous_city_id = city_id

            for resource_type in plan["order"]:
                pct = plan["wood_pct"] if resource_type == RES_WOOD else plan["luxury_pct"]
                if pct is None:
                    continue

                max_normal, total_max, before = read_slider(
                    session, island_id, city_id, resource_type
                )

                use_overcharge = plan["overcharge"] and resource_type == RES_LUXURY
                if pct == 100 and use_overcharge:
                    target = total_max
                else:
                    target = int(max_normal * pct / 100)

                set_workers(session, island_id, city_id, resource_type, target)

                label = "Wood" if resource_type == RES_WOOD else city["luxury_name"]
                entry["changes"].append(
                    {
                        "label": label,
                        "target": target,
                        "before": before,
                        "overcharge": use_overcharge and pct == 100,
                    }
                )
        except Exception:
            entry["error"] = traceback.format_exc(limit=1).strip().splitlines()[-1]

        results.append(entry)
        wait(*WAIT_BETWEEN_CITIES)

    return results


def build_report(mode, plan, results, interval_seconds, next_run=True):
    """Turn the per-city results into a friendly notification message."""
    lines = []
    lines.append("Resource Production Manager")
    lines.append(f"Run finished {getDateTime()}")
    lines.append(f"Mode: {MODE_LABELS.get(mode, 'change production')}")

    bits = []
    if plan["wood_pct"] is not None:
        bits.append(f"Wood {plan['wood_pct']}%")
    if plan["luxury_pct"] is not None:
        oc = " +overcharge" if plan["overcharge"] and plan["luxury_pct"] == 100 else ""
        bits.append(f"Luxury {plan['luxury_pct']}%{oc}")
    lines.append("Target: " + ", ".join(bits))
    lines.append("")

    ok = 0
    for entry in results:
        if entry["error"]:
            lines.append(f"  {entry['name']}: SKIPPED — {entry['error']}")
            continue
        ok += 1
        pieces = []
        for c in entry["changes"]:
            if c["before"] is not None:
                piece = f"{c['label']} {c['before']}→{c['target']}"
            else:
                piece = f"{c['label']} → {c['target']}"
            if c["overcharge"]:
                piece += " (overcharge)"
            pieces.append(piece)
        lines.append(f"  {entry['name']}: " + ", ".join(pieces) if pieces else f"  {entry['name']}: no change")

    lines.append("")
    lines.append(f"{ok} of {len(results)} cities updated.")
    if interval_seconds is not None and next_run:
        lines.append(f"Next run in {format_interval(interval_seconds)}.")
    else:
        lines.append("This was a one-time run.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Scan (read-only): show the current state of production, change nothing
# ---------------------------------------------------------------------------

def run_scan(session, memory):
    cities = select_cities(session)
    if not cities:
        return

    banner()
    print("Scanning current production (nothing will be changed)...\n")

    header = f"  {'City':<20} {'Wood (now/max)':<18} {'Luxury (now/max)':<18}"
    print(header)
    print("  " + "-" * (len(header) - 2))

    for city in cities:
        try:
            island_id = resolve_island_id(session, memory, city["id"])
            w_norm, _, w_cur = read_slider(session, island_id, city["id"], RES_WOOD)
            l_norm, _, l_cur = read_slider(session, island_id, city["id"], RES_LUXURY)
            w_cur_s = "?" if w_cur is None else str(w_cur)
            l_cur_s = "?" if l_cur is None else str(l_cur)
            wood = f"{w_cur_s}/{w_norm}"
            lux = f"{l_cur_s}/{l_norm} ({city['luxury_name']})"
            print(f"  {city['name']:<20} {wood:<18} {lux:<18}")
        except Exception:
            print(f"  {city['name']:<20} {bcolors.RED}could not read{bcolors.ENDC}")
        wait(*WAIT_BETWEEN_CITIES)

    print("\nScan complete. City locations are now remembered for faster runs.")
    enter()


# ---------------------------------------------------------------------------
# Configure one of the change modes, then run it (in the background)
# ---------------------------------------------------------------------------

def configure_and_run(session, event, memory, mode):
    """Gather all settings for a change mode, confirm, then hand off to the
    background. Returns True if a background task was launched (caller must
    then stop touching the terminal), False if the user backed out."""
    order = MODE_ORDER[mode]
    wants_wood = RES_WOOD in order
    wants_luxury = RES_LUXURY in order

    cities = select_cities(session)
    if not cities:
        return False

    banner()
    print(f"Mode: {MODE_LABELS[mode]}\n")

    wood_pct = None
    luxury_pct = None
    overcharge = False

    if wants_wood:
        wood_pct = ask_percentage("WOOD")
        if wood_pct is None:
            return False

    if wants_luxury:
        luxury_pct = ask_percentage("LUXURY")
        if luxury_pct is None:
            return False
        if luxury_pct == 100:
            overcharge = ask_overcharge()
            if overcharge is None:
                return False

    interval_seconds, backed_out = ask_interval()
    if backed_out:
        return False

    plan = {
        "order": order,
        "wood_pct": wood_pct,
        "luxury_pct": luxury_pct,
        "overcharge": overcharge,
    }

    # Remember these settings so the next visit can be re-run quickly.
    memory["last_settings"] = {"mode": mode, "plan": plan}
    save_memory(session, memory)

    # --- Confirmation summary -------------------------------------------
    banner()
    print("Please confirm — here is what will happen:\n")
    print(f"  Mode      : {MODE_LABELS[mode]}")
    print(f"  Cities    : {len(cities)}  ({', '.join(c['name'] for c in cities)})")
    if wood_pct is not None:
        print(f"  Wood      : {wood_pct}%")
    if luxury_pct is not None:
        oc = "  (+ overcharge)" if overcharge and luxury_pct == 100 else ""
        print(f"  Luxury    : {luxury_pct}%{oc}")
    print(f"  Schedule  : {format_interval(interval_seconds)}")
    print()

    if not _notifications_ready(session):
        print(f"{bcolors.WARNING}Note: you have no Telegram/Discord/ntfy notifications set up,")
        print(f"so the completion message won't reach you. Set them up in main-menu")
        print(f"option 21 if you want it.{bcolors.ENDC}")
        print()

    print("Once you confirm, this task moves into the BACKGROUND and does its")
    print("work there. You'll get a notification when it's done listing every")
    print("change it made.")
    print()
    confirm = read(msg="Start now? [Y/n]: ", values=["y", "Y", "n", "N", ""])
    if confirm.lower() == "n":
        return False

    print()
    print("Working in the background. You can keep using ikabot.")
    enter()

    # --- Hand off to the background -------------------------------------
    set_child_mode(session)
    event.set()

    info = (
        f"\nResource Production Manager: {MODE_LABELS[mode]}, "
        f"{len(cities)} cities, {format_interval(interval_seconds)}\n"
    )
    setInfoSignal(session, info)

    try:
        while True:
            try:
                results = apply_plan(session, memory, cities, plan)
                report = build_report(mode, plan, results, interval_seconds)
                if _notifications_ready(session):
                    sendToBot(session, report)
                session.setStatus(f"Ran at {getDateTime()}")
            except KeyboardInterrupt:
                raise
            except Exception:
                # A whole-cycle failure must not kill a recurring task.
                err = (
                    "Resource Production Manager hit a problem this cycle "
                    "but will keep running.\n" + traceback.format_exc()
                )
                traceback.print_exc()
                if _notifications_ready(session):
                    try:
                        sendToBot(session, err)
                    except Exception:
                        traceback.print_exc()

            if interval_seconds is None:
                break
            wait(interval_seconds)
    except KeyboardInterrupt:
        pass
    finally:
        session.logout()

    return True


# ---------------------------------------------------------------------------
# Main menu / entry point
# ---------------------------------------------------------------------------

def resourceProductionManager(session, event, stdin_fd, predetermined_input):
    """
    Parameters
    ----------
    session : ikabot.web.session.Session
    event : multiprocessing.Event
    stdin_fd : int
    predetermined_input : multiprocessing.managers.SyncManager.list
    """
    sys.stdin = os.fdopen(stdin_fd)
    config.predetermined_input = predetermined_input

    try:
        memory = load_memory(session)

        while True:
            banner()
            print(f"Resource Production Manager  v{__version__}")
            print("===========================\n")
            print("Set how many workers each city puts on WOOD and its LUXURY good")
            print("(Wine / Marble / Crystal / Sulfur), in bulk, as a percentage.\n")
            print("Pick what you want to do:\n")
            print("  (1) Scan cities        — just look at current production, change nothing")
            print("  (2) Change WOOD only")
            print("  (3) Change LUXURY only — with the option to overcharge")
            print("  (4) Change WOOD, then LUXURY  — wood gets first pick of citizens")
            print("  (5) Change LUXURY, then WOOD  — luxury gets first pick of citizens")
            print()
            print("  Options 2-5 can run once, or repeat in the background on a schedule")
            print("  (hours or days) and notify you after each run.")
            print()
            print("  (') Go back to the ikabot menu")
            print()

            choice = read(min=1, max=5, digit=True, additionalValues=["'"])
            if choice == "'":
                event.set()
                return

            if choice == MODE_SCAN:
                run_scan(session, memory)
                continue

            # Change modes 2-5. If a background task was launched, we must
            # not return to the menu (the process is now detached).
            launched = configure_and_run(session, event, memory, choice)
            if launched:
                return
            # otherwise the user backed out — loop back to the menu

    except KeyboardInterrupt:
        event.set()
        return

    event.set()
