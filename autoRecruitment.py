#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Auto Recruitment Module for Ikabot
===================================
Automates recruitment of military units and ships across multiple barracks/shipyards.

Features:
- Recruit units (barracks) or ships (shipyards)
- Distribute recruitment across multiple buildings for synchronized completion
- Dynamic fetching of build times and costs (server-specific)
- Resource shortage handling with intelligent retry logic
- Busy building detection with wait or ignore options
- CSV-backed persistence: survives crashes and restarts
- Telegram notifications on completion and errors

Version: 2.0
"""

import csv
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
    "session_id",
    "city_id",
    "city_name",
    "building_position",
    "building_level",
    "is_units",
    "unit_type_id",
    "unit_name",
    "qty_total",
    "qty_remaining",
    "status",
    "created_at",
    "last_updated",
]

RECRUIT_INT_COLS = {
    "recruit_id", "city_id", "building_position", "building_level",
    "unit_type_id", "qty_total", "qty_remaining", "created_at", "last_updated",
}
RECRUIT_VALID_STATUSES = ("pending", "partial", "done", "failed")


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
        if v is None:
            out[col] = ""
        elif isinstance(v, bool):
            out[col] = str(v)
        else:
            out[col] = str(v)
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


def csv_has_active_orders(session, is_units):
    rows = csv_load_orders(session, is_units)
    return any(r["status"] in ("pending", "partial") for r in rows)


def _build_orders_from_distribution(distribution, is_units, session_id, existing_rows=None):
    """
    Build a fresh list of CSV order rows from a distribution plan.
    If existing_rows is provided (resume case), carry over qty_remaining from them.
    """
    existing_map = {}
    if existing_rows:
        for r in existing_rows:
            key = (r["city_id"], r["building_position"], r["unit_type_id"])
            existing_map[key] = r

    rows = []
    next_id = csv_next_recruit_id(existing_rows or [])
    now = int(time.time())

    for building in distribution:
        for unit_type_id, qty_total in building.get("assignments", {}).items():
            key = (building["city_id"], building["building_position"], unit_type_id)
            if key in existing_map:
                existing = existing_map[key]
                rows.append({
                    "recruit_id":       existing["recruit_id"],
                    "session_id":       session_id,
                    "city_id":          building["city_id"],
                    "city_name":        building["city_name"],
                    "building_position": building["building_position"],
                    "building_level":   building["building_level"],
                    "is_units":         is_units,
                    "unit_type_id":     unit_type_id,
                    "unit_name":        building.get("unit_data", {}).get(unit_type_id, {}).get("name", str(unit_type_id)),
                    "qty_total":        existing["qty_total"],
                    "qty_remaining":    existing["qty_remaining"],
                    "status":           existing["status"],
                    "created_at":       existing["created_at"],
                    "last_updated":     existing["last_updated"],
                })
            else:
                rows.append({
                    "recruit_id":       next_id,
                    "session_id":       session_id,
                    "city_id":          building["city_id"],
                    "city_name":        building["city_name"],
                    "building_position": building["building_position"],
                    "building_level":   building["building_level"],
                    "is_units":         is_units,
                    "unit_type_id":     unit_type_id,
                    "unit_name":        building.get("unit_data", {}).get(unit_type_id, {}).get("name", str(unit_type_id)),
                    "qty_total":        qty_total,
                    "qty_remaining":    qty_total,
                    "status":           "pending",
                    "created_at":       now,
                    "last_updated":     now,
                })
                next_id += 1

    return rows


def _load_distribution_from_csv(session, is_units, rows):
    """
    Reconstruct a partial distribution list from saved CSV rows.
    Re-fetches building data (unit costs, action codes) from the API.
    Returns (distribution, recruitment_order) or (None, None) on failure.
    """
    # Group rows by (city_id, building_position)
    building_keys = {}
    for r in rows:
        if r["status"] in ("done", "failed"):
            continue
        key = (r["city_id"], r["building_position"])
        if key not in building_keys:
            building_keys[key] = {
                "city_id":          r["city_id"],
                "city_name":        r["city_name"],
                "building_position": r["building_position"],
                "building_level":   r["building_level"],
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

        # Apply remaining assignments from CSV
        data["assignments"] = {}
        data["remaining_assignments"] = {}
        for r in rows:
            if r["city_id"] != binfo["city_id"] or r["building_position"] != binfo["building_position"]:
                continue
            if r["status"] in ("done", "failed"):
                continue
            qty = r["qty_remaining"]
            if qty > 0:
                data["assignments"][r["unit_type_id"]] = qty
                data["remaining_assignments"][r["unit_type_id"]] = qty

        if not data["assignments"]:
            print(f"{bcolors.GREEN}already done{bcolors.ENDC}")
            continue

        print(f"{bcolors.GREEN}OK{bcolors.ENDC}")
        distribution.append(data)

    if not distribution:
        return None, None

    # Reconstruct recruitment_order from CSV rows (for display purposes)
    recruitment_order = {}
    for r in rows:
        uid = r["unit_type_id"]
        if uid not in recruitment_order:
            recruitment_order[uid] = {"name": r["unit_name"], "quantity": r["qty_total"]}

    return distribution, recruitment_order


# =============================================================================
# CITIZEN GROWTH RATE FUNCTIONS
# =============================================================================

def get_citizen_growth_rate(session, city_id):
    """
    Fetch the citizen growth rate per hour from the town hall.
    Returns the growth rate as a float (citizens per hour), 0 if unavailable.
    """
    try:
        params = f"view=townHall&cityId={city_id}&position=0&backgroundView=city&currentCityId={city_id}&ajax=1"
        response = session.post(params)
        response_data = json.loads(response)

        html_content = None
        for item in response_data:
            if isinstance(item, list) and len(item) >= 2 and item[0] == "changeView":
                if isinstance(item[1], list) and len(item[1]) >= 2:
                    html_content = item[1][1]
                    break

        if not html_content:
            return 0

        patterns = [
            r'population[_\s]?growth[^0-9]*?([\d,.]+)\s*(?:citizens?)?\s*/\s*h',
            r'growth[^0-9]*?([\d,.]+)\s*(?:citizens?)?\s*/\s*h',
            r'citizens?\s*/\s*h[^0-9]*?([\d,.]+)',
            r'(\d+[.,]?\d*)\s*citizens?\s*/\s*h',
        ]

        for pattern in patterns:
            match = re.search(pattern, html_content, re.IGNORECASE)
            if match:
                rate_str = match.group(1).replace(',', '.')
                return float(rate_str)

        for item in response_data:
            if isinstance(item, list) and len(item) >= 2 and item[0] == "updateTemplateData":
                template_data = item[1]
                if isinstance(template_data, dict):
                    for key, value in template_data.items():
                        if 'growth' in key.lower() or 'population' in key.lower():
                            if isinstance(value, dict) and 'text' in value:
                                text = value['text']
                                match = re.search(r'([\d,.]+)', str(text))
                                if match:
                                    rate_str = match.group(1).replace(',', '.')
                                    try:
                                        return float(rate_str)
                                    except Exception:
                                        pass

        return 0

    except Exception:
        return 0


def get_all_city_growth_rates(session, city_ids):
    growth_rates = {}
    for city_id in city_ids:
        rate = get_citizen_growth_rate(session, city_id)
        growth_rates[city_id] = rate
    return growth_rates


# =============================================================================
# MAIN MODULE ENTRY POINT
# =============================================================================

def autoRecruitment(session, event, stdin_fd, predetermined_input):
    """Auto Recruitment Module - Main entry point"""
    sys.stdin = os.fdopen(stdin_fd)
    config.predetermined_input = predetermined_input

    try:
        banner()

        print("=" * 60)
        print("           AUTO RECRUITMENT MODULE")
        print("=" * 60)
        print()

        # Step 1: Choose units or ships
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

        # Step 2: Check for a saved session to resume
        if csv_has_active_orders(session, is_units):
            saved_rows = csv_load_orders(session, is_units)
            active_rows = [r for r in saved_rows if r["status"] in ("pending", "partial")]
            total_remaining = sum(r["qty_remaining"] for r in active_rows)
            unique_buildings = len({(r["city_id"], r["building_position"]) for r in active_rows})

            print(f"{bcolors.WARNING}A previous recruitment session was found:{bcolors.ENDC}")
            print(f"  {total_remaining} units remaining across {unique_buildings} building(s)")
            print()
            print("1. Resume previous session")
            print("2. Start a new session (discards previous)")
            print()
            print("(') Back to main menu")

            resume_choice = read(msg="Select (1 or 2): ", min=1, max=2, digit=True, additionalValues=["'"])

            if resume_choice == "'":
                event.set()
                return

            if resume_choice == 1:
                print()
                print("Resuming previous session...")
                print()
                distribution, recruitment_order = _load_distribution_from_csv(session, is_units, saved_rows)

                if not distribution:
                    print(f"{bcolors.RED}Could not restore session. Starting fresh.{bcolors.ENDC}")
                    print()
                    csv_delete_orders(session, is_units)
                else:
                    # Jump straight to execution
                    print()
                    print(f"Resumed: {sum(sum(b['remaining_assignments'].values()) for b in distribution)} units remaining")
                    print()
                    enter()

                    set_child_mode(session)
                    event.set()

                    unit_type_str = "units" if is_units else "ships"
                    total_units = sum(r["qty_remaining"] for r in active_rows)
                    info = f"\nAuto Recruitment (resume): {total_units} {unit_type_str}\n"
                    setInfoSignal(session, info)

                    try:
                        execute_recruitment_loop(
                            session,
                            distribution,
                            recruitment_order,
                            {},
                            is_units,
                            {},
                            saved_rows,
                        )
                    except Exception:
                        msg = f"Error in:\n{info}\nCause:\n{traceback.format_exc()}"
                        sendToBot(session, msg)
                    finally:
                        session.logout()
                    return
            else:
                csv_delete_orders(session, is_units)
                print()

        # Step 3: Find all relevant buildings
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

        print(f"Found {len(all_buildings)} {building_type}(s) across {len(set(b['city_id'] for b in all_buildings))} cities")
        print()

        # Step 4: Ask which cities to ignore
        print("=" * 60)
        print("CITY SELECTION")
        print("=" * 60)
        print()
        print("Available cities with " + building_type + ":")
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
            levels_str = ', '.join(f"L{l}" for l in sorted(info['levels']))
            print(f"  {idx}. {info['name']} - {info['count']} {building_type}(s) [{levels_str}]")

        print()
        print("Enter city numbers to IGNORE (comma-separated), or press Enter for none:")
        print()
        print("(') Back to main menu")

        ignore_input = read(msg="Ignore cities: ", additionalValues=["'", ""])

        if ignore_input == "'":
            event.set()
            return

        ignored_city_ids = set()
        if ignore_input and ignore_input.strip():
            try:
                ignore_nums = [int(x.strip()) for x in ignore_input.split(',') if x.strip()]
                for num in ignore_nums:
                    if 1 <= num <= len(city_list):
                        ignored_city_ids.add(city_list[num - 1])
            except ValueError:
                print(f"{bcolors.WARNING}Invalid input, not ignoring any cities{bcolors.ENDC}")

        active_buildings = [b for b in all_buildings if b['city_id'] not in ignored_city_ids]

        if not active_buildings:
            print(f"{bcolors.RED}No {building_type} remaining after exclusions!{bcolors.ENDC}")
            print()
            enter()
            event.set()
            return

        print()
        print(f"Using {len(active_buildings)} {building_type}(s) in {len(set(b['city_id'] for b in active_buildings))} cities")
        print()

        # Step 5: Fetch detailed building data and check for busy buildings
        print("=" * 60)
        print("FETCHING BUILDING DATA")
        print("=" * 60)
        print()
        print("Fetching recruitment times and costs from each building...")
        print("(This may take a moment)")
        print()

        buildings_data = []
        busy_buildings = []

        for building in active_buildings:
            print(f"  Checking {building['city_name']} - {building_type} L{building['building_level']}...", end=" ")

            data = fetch_building_data(session, building, is_units)

            if data is None:
                print(f"{bcolors.RED}FAILED{bcolors.ENDC}")
                continue

            unit_count = len(data.get('unit_data', {}))

            if data.get('is_busy', False):
                print(f"{bcolors.WARNING}BUSY{bcolors.ENDC} ({unit_count} unit types)")
                busy_buildings.append(data)
            else:
                print(f"{bcolors.GREEN}OK{bcolors.ENDC} ({unit_count} unit types)")
                buildings_data.append(data)

        print()

        # Step 6: Handle busy buildings
        if busy_buildings:
            print("=" * 60)
            print("BUSY BUILDINGS DETECTED")
            print("=" * 60)
            print()
            print(f"{len(busy_buildings)} building(s) currently have units in queue:")
            print()
            for bb in busy_buildings:
                queue_time = bb.get('queue_remaining_time', 0)
                time_str = format_time(queue_time) if queue_time > 0 else "Unknown"
                print(f"  • {bb['city_name']} L{bb['building_level']} - Queue finishes in: {time_str}")
            print()
            print("How do you want to handle busy buildings?")
            print()
            print("1. Ignore busy buildings (proceed without them)")
            print("2. Include them (they will be used once their queue finishes)")
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
            print(f"{bcolors.RED}No available {building_type} to use!{bcolors.ENDC}")
            print()
            enter()
            event.set()
            return

        # Step 7: Ask for unit quantities
        print("=" * 60)
        print("RECRUITMENT ORDER")
        print("=" * 60)
        print()
        print("Enter the quantity for each unit type (0 or Enter to skip):")
        print()
        print("(') Back to main menu at any prompt")
        print()

        recruitment_order = {}
        units_shown = 0

        for unit in unit_list:
            unit_name = unit['name']
            game_idx = unit['game_index']

            available = any(game_idx in b.get('unit_data', {}) for b in buildings_data)
            if not available:
                continue

            units_shown += 1

            sample_cost = None
            for b in buildings_data:
                if game_idx in b.get('unit_data', {}):
                    sample_cost = b['unit_data'][game_idx]
                    break

            cost_str = ""
            if sample_cost:
                costs = []
                if sample_cost.get('citizens', 0) > 0:
                    costs.append(f"{sample_cost['citizens']} cit")
                if sample_cost.get('wood', 0) > 0:
                    costs.append(f"{sample_cost['wood']} wood")
                if sample_cost.get('wine', 0) > 0:
                    costs.append(f"{sample_cost['wine']} wine")
                if sample_cost.get('marble', 0) > 0:
                    costs.append(f"{sample_cost['marble']} marble")
                if sample_cost.get('crystal', 0) > 0:
                    costs.append(f"{sample_cost['crystal']} crystal")
                if sample_cost.get('sulfur', 0) > 0:
                    costs.append(f"{sample_cost['sulfur']} sulfur")
                time_str = format_time(int(sample_cost.get('time_seconds', 0)))
                cost_str = f" [{', '.join(costs)}, {time_str}/unit]"

            qty_input = read(msg=f"  {unit_name}{cost_str}: ", min=0, digit=True, additionalValues=["'", ""])

            if qty_input == "'":
                event.set()
                return

            if qty_input == "":
                qty_input = 0

            if qty_input > 0:
                recruitment_order[game_idx] = {
                    'name': unit_name,
                    'quantity': qty_input
                }

        if units_shown == 0:
            print()
            print(f"{bcolors.RED}ERROR: No units could be parsed from buildings!{bcolors.ENDC}")
            print()
            enter()
            event.set()
            return

        if not recruitment_order:
            print()
            print(f"{bcolors.WARNING}No units selected for recruitment.{bcolors.ENDC}")
            print()
            enter()
            event.set()
            return

        print()

        # Step 8: Calculate distribution
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

        # Step 9: Check resources and citizen growth rates
        print("Checking resources in each city...")

        resource_check = check_resources(session, distribution, cities)

        active_city_ids = list(set(b['city_id'] for b in distribution))

        print("Fetching citizen growth rates...")
        print()

        city_growth_rates = get_all_city_growth_rates(session, active_city_ids)

        city_resources = {}
        for city_id in active_city_ids:
            city_resources[city_id] = resource_check['available'].get(city_id, {})

        # Step 10: Display plan and confirm
        print("=" * 60)
        print("RECRUITMENT PLAN")
        print("=" * 60)
        print()

        display_distribution_plan(distribution, recruitment_order)

        print()

        if resource_check['missing_resources']:
            print(f"{bcolors.WARNING}WARNING: RESOURCE SHORTAGES DETECTED:{bcolors.ENDC}")
            print()
            for city_id, missing in resource_check['missing_resources'].items():
                city_name = cities[city_id]['name']
                print(f"  {city_name}:")
                for res_name, amount in missing.items():
                    print(f"    - Missing {addThousandSeparator(amount)} {res_name}")
            print()

        if resource_check['missing_citizens']:
            print(f"{bcolors.WARNING}WARNING: CITIZEN SHORTAGES DETECTED:{bcolors.ENDC}")
            print()
            for city_id, missing in resource_check['missing_citizens'].items():
                city_name = cities[city_id]['name']
                growth_rate = city_growth_rates.get(city_id, 0)
                if growth_rate > 0:
                    wait_hours = missing / growth_rate
                    wait_str = format_time(int(wait_hours * 3600))
                    print(f"  {city_name}: Missing {addThousandSeparator(missing)} citizens (~{wait_str} at {growth_rate:.1f}/hr)")
                else:
                    print(f"  {city_name}: Missing {addThousandSeparator(missing)} citizens")
            print()

        print("Confirm? [Y/n]")
        print()
        print("(') Back to main menu")

        confirm = read(values=["y", "Y", "n", "N", "", "'"])

        if confirm == "'" or confirm.lower() == "n":
            print()
            print("Recruitment cancelled.")
            print()
            enter()
            event.set()
            return

        print()

        # Step 11: Determine execution mode
        has_shortages = resource_check['missing_resources'] or resource_check['missing_citizens']
        wait_for_busy = any(b.get('is_busy', False) for b in buildings_data)

        if has_shortages or wait_for_busy:
            print("Starting background recruitment process...")
            print()
            print("The module will:")
            if wait_for_busy:
                print("  • Wait for busy buildings to finish their queues")
            if resource_check['missing_citizens']:
                print("  • Wait until citizens become available")
            if resource_check['missing_resources']:
                print("  • Recruit in batches as resources become available (20% threshold)")
            print()
            print("Progress is saved to disk — recruitment will resume if restarted.")
            print()
            enter()

            set_child_mode(session)
            event.set()

            total_units = sum(r['quantity'] for r in recruitment_order.values())
            unit_type_str = "units" if is_units else "ships"
            info = f"\nAuto Recruitment: {total_units} {unit_type_str} across {len(distribution)} {building_type}(s)\n"
            setInfoSignal(session, info)

            try:
                execute_recruitment_loop(
                    session,
                    distribution,
                    recruitment_order,
                    cities,
                    is_units,
                    resource_check,
                    None,
                )
            except Exception:
                msg = f"Error in:\n{info}\nCause:\n{traceback.format_exc()}"
                sendToBot(session, msg)
            finally:
                session.logout()

        else:
            print("Executing recruitment...")
            print()

            success = execute_recruitment(session, distribution, is_units)

            print()
            if success:
                print(f"{bcolors.GREEN}Recruitment orders placed successfully!{bcolors.ENDC}")
                sendToBot(session, f"Auto Recruitment: {total_units if 'total_units' in dir() else '?'} units placed successfully.")
            else:
                print(f"{bcolors.WARNING}Some recruitment orders may have failed.{bcolors.ENDC}")
            print()
            enter()
            event.set()

    except KeyboardInterrupt:
        event.set()
        return


# =============================================================================
# BUILDING DISCOVERY FUNCTIONS
# =============================================================================

def find_all_buildings(session, cities_ids, cities, building_type):
    """Find all barracks or shipyards across all cities"""
    buildings = []

    for city_id in cities_ids:
        city = cities[city_id]
        try:
            html = session.get(city_url + str(city_id))
            city_data = getCity(html)
        except Exception:
            continue

        for building in city_data.get('position', []):
            if building.get('building') == building_type:
                buildings.append({
                    'city_id':          city_id,
                    'city_name':        city['name'],
                    'building_position': building.get('position'),
                    'building_level':   building.get('level', 0),
                    'is_busy':          building.get('isBusy', False),
                })

    return buildings


def fetch_building_data(session, building_info, is_units=True):
    """
    Fetch detailed recruitment data from a barracks or shipyard.

    Busy status is determined from the city JSON (`isBusy` field) which is
    provided by the caller. The caller should refresh city data before calling
    this if it needs an up-to-date busy status.
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
            if "actionRequest" in item[1]:
                result['action_code'] = item[1]["actionRequest"]

        elif item[0] == "updateTemplateData" and isinstance(item[1], dict):
            template_data = item[1]

    if not template_data:
        return None

    slider_prefix = f"js_{view_name}Slider"

    for key, value in template_data.items():
        if not key.startswith(slider_prefix):
            continue

        if not isinstance(value, dict):
            continue

        slider_info = value.get('slider', {})
        if not isinstance(slider_info, dict):
            continue

        control_data_str = slider_info.get('control_data', '')
        if not control_data_str:
            continue

        try:
            control_data = json.loads(control_data_str)

            unit_type_id = control_data.get('unit_type_id')
            if not unit_type_id:
                continue

            costs = control_data.get('costs', {})

            unit_data = {
                'name':          control_data.get('local_name', ''),
                'identifier':    control_data.get('identifier', ''),
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

            result['unit_data'][unit_type_id] = unit_data

        except (json.JSONDecodeError, KeyError, TypeError):
            continue

    return result


def _refresh_building_busy_status(session, building, is_units):
    """
    Re-fetch the city JSON for this building's city and update the building's
    `is_busy` field from the live `isBusy` value in that city's slot data.

    This is the authoritative busy check — avoids the stale-flag bug where
    a cached `is_busy=True` was never cleared after a queue finished.
    """
    city_id = building['city_id']
    position = building['building_position']
    try:
        html = session.get(city_url + str(city_id))
        city_data = getCity(html)
        for slot in city_data.get('position', []):
            if slot.get('position') == position:
                building['is_busy'] = bool(slot.get('isBusy', False))
                # Try to extract queue completion time if available
                completed = slot.get('completed')
                if completed:
                    remaining = int(completed) - int(time.time())
                    building['queue_remaining_time'] = max(0, remaining)
                else:
                    building['queue_remaining_time'] = 0
                return True
    except Exception:
        pass
    return False


def _fetch_fresh_action_code(session, building, is_units):
    """
    Always fetch a fresh action code from the building view before POSTing.
    Action codes expire and must not be reused across sessions or after long waits.
    """
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
    """Format seconds into human-readable string"""
    if seconds <= 0:
        return "0s"

    seconds = int(seconds)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    parts = []
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0 or not parts:
        parts.append(f"{secs}s")

    return ' '.join(parts)


# =============================================================================
# DISTRIBUTION CALCULATION
# =============================================================================

def calculate_distribution(buildings_data, recruitment_order):
    """
    Calculate how to distribute unit recruitment across buildings
    to finish within 30 minutes of each other.
    """
    if not buildings_data or not recruitment_order:
        return None

    for building in buildings_data:
        building['assignments'] = {}
        building['estimated_time'] = 0

    for unit_idx, unit_info in recruitment_order.items():
        total_qty = unit_info['quantity']

        capable_buildings = [b for b in buildings_data if unit_idx in b.get('unit_data', {})]

        if not capable_buildings:
            continue

        speed_factors = []
        total_speed = 0
        for building in capable_buildings:
            time_per_unit = building['unit_data'][unit_idx]['time_seconds']
            speed = (1.0 / time_per_unit) if time_per_unit > 0 else 1.0
            speed_factors.append(speed)
            total_speed += speed

        if total_speed == 0:
            per_building = total_qty // len(capable_buildings)
            remainder = total_qty % len(capable_buildings)
            for i, building in enumerate(capable_buildings):
                qty = per_building + (1 if i < remainder else 0)
                if qty > 0:
                    building['assignments'][unit_idx] = qty
        else:
            remaining = total_qty
            for i, building in enumerate(capable_buildings):
                if i == len(capable_buildings) - 1:
                    qty = remaining
                else:
                    qty = int(total_qty * (speed_factors[i] / total_speed))
                    remaining -= qty
                if qty > 0:
                    building['assignments'][unit_idx] = qty

    for building in buildings_data:
        total_time = 0
        for unit_idx, qty in building.get('assignments', {}).items():
            if unit_idx in building.get('unit_data', {}):
                time_per_unit = building['unit_data'][unit_idx]['time_seconds']
                total_time += time_per_unit * qty
        if building.get('is_busy', False):
            total_time += building.get('queue_remaining_time', 0)
        if building.get('assignments'):
            total_time += 10 * len(building['assignments'])
        building['estimated_time'] = total_time

    buildings_data = balance_distribution(buildings_data, recruitment_order)

    return buildings_data


def balance_distribution(buildings_data, recruitment_order, tolerance=1800):
    """Adjust distribution to balance completion times within tolerance"""
    max_iterations = 100
    iteration = 0

    while iteration < max_iterations:
        iteration += 1

        times = []
        for building in buildings_data:
            total_time = 0
            for unit_idx, qty in building.get('assignments', {}).items():
                if unit_idx in building.get('unit_data', {}):
                    time_per_unit = building['unit_data'][unit_idx]['time_seconds']
                    total_time += time_per_unit * qty
            if building.get('is_busy', False):
                total_time += building.get('queue_remaining_time', 0)
            if building.get('assignments'):
                total_time += 10 * len(building['assignments'])
            building['estimated_time'] = total_time
            times.append(total_time)

        if not times:
            break

        max_time = max(times)
        min_time = min(times)

        if max_time - min_time <= tolerance:
            break

        fastest_idx = times.index(min_time)
        slowest_idx = times.index(max_time)

        fastest = buildings_data[fastest_idx]
        slowest = buildings_data[slowest_idx]

        moved = False
        for unit_idx in list(slowest.get('assignments', {}).keys()):
            if unit_idx not in fastest.get('unit_data', {}):
                continue

            current_qty = slowest['assignments'].get(unit_idx, 0)
            if current_qty <= 10:
                continue

            move_qty = min(10, current_qty - 1)

            slowest['assignments'][unit_idx] -= move_qty
            if slowest['assignments'][unit_idx] <= 0:
                del slowest['assignments'][unit_idx]

            fastest['assignments'].setdefault(unit_idx, 0)
            fastest['assignments'][unit_idx] += move_qty

            moved = True
            break

        if not moved:
            break

    return buildings_data


# =============================================================================
# RESOURCE CHECKING
# =============================================================================

def check_resources(session, distribution, cities):
    """Check if each city has enough resources and citizens"""
    result = {
        'can_fulfill': True,
        'missing_resources': {},
        'missing_citizens': {},
        'available': {}
    }

    city_requirements = {}
    for building in distribution:
        city_id = building['city_id']
        if city_id not in city_requirements:
            city_requirements[city_id] = {
                'citizens': 0, 'wood': 0, 'wine': 0,
                'marble': 0, 'crystal': 0, 'sulfur': 0,
            }

        for unit_idx, qty in building.get('assignments', {}).items():
            if unit_idx in building.get('unit_data', {}):
                unit_data = building['unit_data'][unit_idx]
                city_requirements[city_id]['citizens'] += unit_data.get('citizens', 0) * qty
                city_requirements[city_id]['wood']     += unit_data.get('wood', 0) * qty
                city_requirements[city_id]['wine']     += unit_data.get('wine', 0) * qty
                city_requirements[city_id]['marble']   += unit_data.get('marble', 0) * qty
                city_requirements[city_id]['crystal']  += unit_data.get('crystal', 0) * qty
                city_requirements[city_id]['sulfur']   += unit_data.get('sulfur', 0) * qty

    for city_id, requirements in city_requirements.items():
        try:
            html = session.get(city_url + str(city_id))
            city_data = getCity(html)
        except Exception:
            continue

        available_res = city_data.get('availableResources', [0, 0, 0, 0, 0])
        if len(available_res) < 5:
            available_res = available_res + [0] * (5 - len(available_res))

        # Try multiple patterns for citizens count
        available_citizens = 0
        for citizens_pattern in [
            r'id="js_GlobalMenu_citizens">([0-9,]+)',
            r'"citizens"\s*:\s*(\d+)',
            r'class="[^"]*citizens[^"]*"[^>]*>([0-9,]+)',
        ]:
            citizens_match = re.search(citizens_pattern, html)
            if citizens_match:
                available_citizens = int(citizens_match.group(1).replace(',', ''))
                break

        result['available'][city_id] = {
            'resources': {
                'wood': available_res[0], 'wine': available_res[1],
                'marble': available_res[2], 'crystal': available_res[3],
                'sulfur': available_res[4],
            },
            'citizens': available_citizens,
        }

        if requirements['citizens'] > available_citizens:
            result['missing_citizens'][city_id] = requirements['citizens'] - available_citizens
            result['can_fulfill'] = False

        missing = {}
        if requirements['wood']    > available_res[0]: missing['Wood']    = requirements['wood']    - available_res[0]
        if requirements['wine']    > available_res[1]: missing['Wine']    = requirements['wine']    - available_res[1]
        if requirements['marble']  > available_res[2]: missing['Marble']  = requirements['marble']  - available_res[2]
        if requirements['crystal'] > available_res[3]: missing['Crystal'] = requirements['crystal'] - available_res[3]
        if requirements['sulfur']  > available_res[4]: missing['Sulfur']  = requirements['sulfur']  - available_res[4]

        if missing:
            result['missing_resources'][city_id] = missing
            result['can_fulfill'] = False

    return result


# =============================================================================
# DISPLAY FUNCTIONS
# =============================================================================

def display_distribution_plan(distribution, recruitment_order):
    """Display the recruitment plan in a readable format"""
    print("Order Summary:")
    print("-" * 40)
    for unit_idx, unit_info in recruitment_order.items():
        print(f"  {unit_info['name']}: {addThousandSeparator(unit_info['quantity'])}")
    print()

    print("Distribution by Building:")
    print("-" * 40)

    for building in distribution:
        if not building.get('assignments'):
            continue

        time_str = format_time(int(building.get('estimated_time', 0)))
        busy_str = " (WAITING FOR QUEUE)" if building.get('is_busy', False) else ""

        print(f"\n  {building['city_name']} - L{building['building_level']}{busy_str}")
        print(f"  Estimated time: {time_str}")
        print("  Units:")

        for unit_idx, qty in building['assignments'].items():
            unit_name = f"Unit {unit_idx}"
            if unit_idx in recruitment_order:
                unit_name = recruitment_order[unit_idx]['name']
            elif unit_idx in building.get('unit_data', {}):
                unit_name = building['unit_data'][unit_idx].get('name', unit_name)

            print(f"    • {unit_name}: {addThousandSeparator(qty)}")


# =============================================================================
# RECRUITMENT EXECUTION
# =============================================================================

def execute_recruitment(session, distribution, is_units=True):
    """Execute recruitment orders for all buildings (immediate, no loop)"""
    success = True
    view_name = 'barracks' if is_units else 'shipyard'

    for building in distribution:
        if not building.get('assignments'):
            continue

        city_id = building['city_id']
        position = building['building_position']

        action_code = _fetch_fresh_action_code(session, building, is_units)

        if not action_code:
            print(f"  {building['city_name']}: Failed to get action code")
            success = False
            continue

        recruit_params = {
            'action':        'BuildUnits',
            'actionRequest': action_code,
            'cityId':        city_id,
            'position':      position,
        }

        for unit_idx, qty in building['assignments'].items():
            recruit_params[str(unit_idx)] = qty

        try:
            session.post(params=recruit_params)
            total_units = sum(building['assignments'].values())
            print(f"  {building['city_name']}: Recruited {total_units} units")

        except Exception as e:
            print(f"  {building['city_name']}: Failed - {e}")
            success = False

    return success


def execute_recruitment_loop(session, distribution, recruitment_order, cities, is_units, resource_check, saved_rows):
    """
    Execute recruitment with retry logic for shortages.

    Uses per-building 20% threshold:
    - Refresh city data each cycle to get live `isBusy` and resource counts
    - For each non-busy building, calculate what fraction of remaining units can be recruited
    - If >= 20% can be recruited, recruit as many as possible
    - If < 20% or building busy, skip and wait
    - Always fetches a fresh action code before each POST

    Progress is saved to a CSV file so the session can be resumed after a crash.
    """
    session_id = str(int(time.time()))

    # Initialize remaining_assignments from distribution
    for building in distribution:
        if 'remaining_assignments' not in building:
            building['remaining_assignments'] = building.get('assignments', {}).copy()

    # Save initial state to CSV
    csv_rows = _build_orders_from_distribution(distribution, is_units, session_id, saved_rows)
    csv_save_orders(session, is_units, csv_rows)

    active_city_ids = list(set(b['city_id'] for b in distribution))
    city_growth_rates = get_all_city_growth_rates(session, active_city_ids)

    consecutive_errors = 0
    MAX_CONSECUTIVE_ERRORS = 10

    while True:
        # Count total remaining units
        total_remaining = sum(
            qty
            for b in distribution
            for qty in b.get('remaining_assignments', {}).values()
        )

        if total_remaining <= 0:
            session.setStatus("Auto Recruitment complete!")
            sendToBot(session, "Auto Recruitment completed successfully!")
            csv_delete_orders(session, is_units)
            break

        # --- Refresh city data (resources + busy status for each building) ---
        city_resources = {}
        city_data_cache = {}

        for b in distribution:
            city_id = b['city_id']
            if city_id in city_data_cache:
                continue
            try:
                html = session.get(city_url + str(city_id))
                city_data = getCity(html)
                city_data_cache[city_id] = (html, city_data)

                available_res = city_data.get('availableResources', [0, 0, 0, 0, 0])
                if len(available_res) < 5:
                    available_res = available_res + [0] * (5 - len(available_res))

                available_citizens = 0
                for citizens_pattern in [
                    r'id="js_GlobalMenu_citizens">([0-9,]+)',
                    r'"citizens"\s*:\s*(\d+)',
                ]:
                    m = re.search(citizens_pattern, html)
                    if m:
                        available_citizens = int(m.group(1).replace(',', ''))
                        break

                city_resources[city_id] = {
                    'citizens': available_citizens,
                    'wood':     available_res[0],
                    'wine':     available_res[1],
                    'marble':   available_res[2],
                    'crystal':  available_res[3],
                    'sulfur':   available_res[4],
                }
                consecutive_errors = 0

            except Exception as e:
                consecutive_errors += 1
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    msg = f"Auto Recruitment: too many consecutive network errors ({consecutive_errors}), aborting.\nLast error: {e}"
                    sendToBot(session, msg)
                    raise RuntimeError(msg)
                session.setStatus(f"Network error, retrying... ({consecutive_errors}/{MAX_CONSECUTIVE_ERRORS})")
                wait(60)
                continue

        # Update each building's busy status from the fresh city data
        for building in distribution:
            if not building.get('remaining_assignments'):
                continue
            city_id = building['city_id']
            position = building['building_position']
            _, city_data = city_data_cache.get(city_id, (None, {}))
            for slot in city_data.get('position', []):
                if slot.get('position') == position:
                    building['is_busy'] = bool(slot.get('isBusy', False))
                    completed = slot.get('completed')
                    if completed:
                        building['queue_remaining_time'] = max(0, int(completed) - int(time.time()))
                    else:
                        building['queue_remaining_time'] = 0
                    break

        # --- Calculate citizen wait estimate for status display ---
        total_citizens_needed = sum(
            building['unit_data'].get(uid, {}).get('citizens', 0) * qty
            for building in distribution
            for uid, qty in building.get('remaining_assignments', {}).items()
            if uid in building.get('unit_data', {})
        )
        total_citizens_available = sum(
            city_resources.get(b['city_id'], {}).get('citizens', 0)
            for b in distribution
        )
        remaining_citizens_needed = max(0, total_citizens_needed - total_citizens_available)
        total_growth_rate = sum(city_growth_rates.get(cid, 0) for cid in active_city_ids)

        est_wait_str = None
        if remaining_citizens_needed > 0 and total_growth_rate > 0:
            est_wait_str = format_time(int((remaining_citizens_needed / total_growth_rate) * 3600))

        # --- Determine which buildings can recruit this cycle ---
        buildings_to_recruit = []
        total_recruiting_this_cycle = 0

        for building in distribution:
            remaining = building.get('remaining_assignments', {})
            if not remaining:
                continue

            if building.get('is_busy', False):
                continue

            city_id = building['city_id']
            if city_id not in city_resources:
                continue

            available = city_resources[city_id].copy()
            building_total_remaining = sum(remaining.values())
            threshold = max(1, int(building_total_remaining * 0.20))

            can_recruit_building = {}
            total_can_recruit = 0

            for unit_idx, qty_needed in remaining.items():
                if unit_idx not in building.get('unit_data', {}):
                    continue

                unit_data = building['unit_data'][unit_idx]
                max_possible = qty_needed

                citizens_cost = unit_data.get('citizens', 0)
                if citizens_cost > 0:
                    max_possible = min(max_possible, available['citizens'] // citizens_cost)

                wood_cost = unit_data.get('wood', 0)
                if wood_cost > 0:
                    max_possible = min(max_possible, available['wood'] // wood_cost)

                wine_cost = unit_data.get('wine', 0)
                if wine_cost > 0:
                    max_possible = min(max_possible, available['wine'] // wine_cost)

                marble_cost = unit_data.get('marble', 0)
                if marble_cost > 0:
                    max_possible = min(max_possible, available['marble'] // marble_cost)

                crystal_cost = unit_data.get('crystal', 0)
                if crystal_cost > 0:
                    max_possible = min(max_possible, available['crystal'] // crystal_cost)

                sulfur_cost = unit_data.get('sulfur', 0)
                if sulfur_cost > 0:
                    max_possible = min(max_possible, available['sulfur'] // sulfur_cost)

                if max_possible > 0:
                    can_recruit_building[unit_idx] = max_possible
                    total_can_recruit += max_possible

                    # Deduct from available so subsequent units see the correct totals
                    available['citizens'] -= citizens_cost * max_possible
                    available['wood']     -= wood_cost * max_possible
                    available['wine']     -= wine_cost * max_possible
                    available['marble']   -= marble_cost * max_possible
                    available['crystal']  -= crystal_cost * max_possible
                    available['sulfur']   -= sulfur_cost * max_possible

            if total_can_recruit >= threshold:
                # Commit the deduction to city_resources so sibling buildings see it
                for unit_idx, qty in can_recruit_building.items():
                    unit_data = building['unit_data'][unit_idx]
                    city_resources[city_id]['citizens'] -= unit_data.get('citizens', 0) * qty
                    city_resources[city_id]['wood']     -= unit_data.get('wood', 0) * qty
                    city_resources[city_id]['wine']     -= unit_data.get('wine', 0) * qty
                    city_resources[city_id]['marble']   -= unit_data.get('marble', 0) * qty
                    city_resources[city_id]['crystal']  -= unit_data.get('crystal', 0) * qty
                    city_resources[city_id]['sulfur']   -= unit_data.get('sulfur', 0) * qty

                buildings_to_recruit.append({
                    'building':          building,
                    'to_recruit':        can_recruit_building,
                    'total':             total_can_recruit,
                    'threshold':         threshold,
                    'building_remaining': building_total_remaining,
                })
                total_recruiting_this_cycle += total_can_recruit

        # --- If nothing meets threshold, wait ---
        if not buildings_to_recruit:
            any_busy = any(b.get('is_busy', False) for b in distribution if b.get('remaining_assignments'))

            # Calculate a smarter sleep time based on queue completion
            soonest_queue_done = None
            for b in distribution:
                if b.get('is_busy') and b.get('queue_remaining_time', 0) > 0:
                    remaining_time = b['queue_remaining_time']
                    if soonest_queue_done is None or remaining_time < soonest_queue_done:
                        soonest_queue_done = remaining_time

            if soonest_queue_done and soonest_queue_done > 0:
                # Wake up just after the soonest queue finishes, capped at 30 min
                sleep_secs = min(soonest_queue_done + 30, 1800)
            else:
                sleep_secs = 300

            if any_busy:
                status_msg = f"Waiting for queues to clear... {total_remaining} remaining"
            else:
                status_msg = f"Waiting for resources... {total_remaining} remaining"

            if est_wait_str:
                status_msg += f" (~{est_wait_str} for citizens)"
            session.setStatus(status_msg)
            wait(sleep_secs)
            continue

        # --- Execute recruitment for eligible buildings ---
        status_msg = f"Recruiting {total_recruiting_this_cycle}/{total_remaining} units..."
        if est_wait_str and total_remaining > total_recruiting_this_cycle:
            status_msg += f" (~{est_wait_str} remaining)"
        session.setStatus(status_msg)

        for recruit_info in buildings_to_recruit:
            building = recruit_info['building']
            to_recruit = recruit_info['to_recruit']
            city_id = building['city_id']
            position = building['building_position']

            # Always fetch a fresh action code — they expire
            action_code = _fetch_fresh_action_code(session, building, is_units)

            if not action_code:
                print(f"  {building['city_name']}: Failed to get action code, skipping")
                continue

            recruit_params = {
                'action':        'BuildUnits',
                'actionRequest': action_code,
                'cityId':        city_id,
                'position':      position,
            }

            for unit_idx, qty in to_recruit.items():
                recruit_params[str(unit_idx)] = qty

            try:
                session.post(params=recruit_params)

                total_units = sum(to_recruit.values())
                pct = (total_units / recruit_info['building_remaining']) * 100
                print(f"  {building['city_name']} L{building['building_level']}: "
                      f"Recruited {total_units} units ({pct:.0f}% of remaining)")

                # Update in-memory remaining assignments
                for unit_idx, qty in to_recruit.items():
                    building['remaining_assignments'][unit_idx] -= qty
                    if building['remaining_assignments'][unit_idx] <= 0:
                        del building['remaining_assignments'][unit_idx]

                # Persist progress to CSV
                now = int(time.time())
                for recruit_id_row in csv_load_orders(session, is_units):
                    if (recruit_id_row["city_id"] == city_id
                            and recruit_id_row["building_position"] == position
                            and recruit_id_row["unit_type_id"] in to_recruit):
                        unit_idx = recruit_id_row["unit_type_id"]
                        new_remaining = recruit_id_row["qty_remaining"] - to_recruit[unit_idx]
                        new_status = "done" if new_remaining <= 0 else "partial"
                        csv_update_order(
                            session, is_units,
                            recruit_id_row["recruit_id"],
                            qty_remaining=max(0, new_remaining),
                            status=new_status,
                            last_updated=now,
                        )

            except Exception as e:
                print(f"  {building['city_name']}: POST failed - {e}")

        # Brief pause before next cycle to let the game process the orders
        wait(30)
