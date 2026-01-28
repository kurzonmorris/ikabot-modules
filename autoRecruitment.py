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

Author: Custom Module
Version: 1.08 - Enter/return treated as 0 when entering unit quantities
"""

import sys
import json
import re
import traceback
import time
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

# User-facing order for units (barracks) - maps to game IDs 301-315
UNITS_ORDER = [
    {'name': 'Hoplite', 'game_index': 303},
    {'name': 'Swordsman', 'game_index': 302},
    {'name': 'Steam Giant', 'game_index': 308},
    {'name': 'Sulphur Carabineer', 'game_index': 304},
    {'name': 'Mortar', 'game_index': 305},
    {'name': 'Gyrocopter', 'game_index': 312},
    {'name': 'Balloon-Bombardier', 'game_index': 309},
    {'name': 'Doctor', 'game_index': 311},
    {'name': 'Cook', 'game_index': 310},
    {'name': 'Battering Ram', 'game_index': 307},
    {'name': 'Spearman', 'game_index': 315},
    {'name': 'Slinger', 'game_index': 301},
    {'name': 'Archer', 'game_index': 313},
    {'name': 'Catapult', 'game_index': 306},
]

# User-facing order for ships (shipyard) - game IDs 201-220
SHIPS_ORDER = [
    {'name': 'Ram Ship', 'game_index': 210},
    {'name': 'Steam Ram', 'game_index': 216},
    {'name': 'Rocket Ship', 'game_index': 217},
    {'name': 'Diving Boat', 'game_index': 212},
    {'name': 'Paddle Speedboat', 'game_index': 218},
    {'name': 'Balloon Carrier', 'game_index': 219},
    {'name': 'Tender', 'game_index': 220},
    {'name': 'Fire Ship', 'game_index': 211},
    {'name': 'Ballista Ship', 'game_index': 213},
    {'name': 'Catapult Ship', 'game_index': 214},
    {'name': 'Mortar Ship', 'game_index': 215},
]

RESOURCE_NAMES = ['Wood', 'Wine', 'Marble', 'Crystal', 'Sulfur']


# =============================================================================
# CITIZEN GROWTH RATE FUNCTIONS
# =============================================================================

def get_citizen_growth_rate(session, city_id):
    """
    Fetch the citizen growth rate per hour from the town hall.
    
    Returns the growth rate as a float (citizens per hour).
    Returns 0 if unable to fetch.
    """
    try:
        # Fetch town hall page (position 0 is always town hall)
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
        
        # Look for population growth rate in the HTML
        # Pattern: "Population growth: X.XX citizens/h" or similar
        # The game shows this in the town hall overview
        
        # Try multiple patterns as game HTML may vary
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
        
        # Alternative: Look in updateTemplateData for growth info
        for item in response_data:
            if isinstance(item, list) and len(item) >= 2 and item[0] == "updateTemplateData":
                template_data = item[1]
                if isinstance(template_data, dict):
                    # Look for growth-related keys
                    for key, value in template_data.items():
                        if 'growth' in key.lower() or 'population' in key.lower():
                            if isinstance(value, dict) and 'text' in value:
                                text = value['text']
                                match = re.search(r'([\d,.]+)', str(text))
                                if match:
                                    rate_str = match.group(1).replace(',', '.')
                                    try:
                                        return float(rate_str)
                                    except:
                                        pass
        
        return 0
        
    except Exception as e:
        return 0


def get_all_city_growth_rates(session, city_ids):
    """
    Fetch citizen growth rates for multiple cities.
    
    Returns dict: {city_id: growth_rate_per_hour}
    """
    growth_rates = {}
    for city_id in city_ids:
        rate = get_citizen_growth_rate(session, city_id)
        growth_rates[city_id] = rate
    return growth_rates


def estimate_recruitment_time(buildings_data, city_resources, city_growth_rates):
    """
    Estimate total recruitment time considering:
    1. Current citizens available
    2. Citizen growth rate
    3. Build time for units
    
    Returns dict with per-city and total estimates.
    """
    estimates = {
        'by_city': {},
        'total_time_seconds': 0,
        'bottleneck': None
    }
    
    for building in buildings_data:
        city_id = building['city_id']
        
        if city_id not in estimates['by_city']:
            estimates['by_city'][city_id] = {
                'city_name': building['city_name'],
                'citizens_needed': 0,
                'citizens_available': city_resources.get(city_id, {}).get('citizens', 0),
                'growth_rate': city_growth_rates.get(city_id, 0),
                'citizen_wait_seconds': 0,
                'build_time_seconds': 0,
                'total_time_seconds': 0
            }
        
        city_est = estimates['by_city'][city_id]
        
        # Calculate citizens needed and build time for this building
        for unit_idx, qty in building.get('assignments', {}).items():
            if unit_idx in building.get('unit_data', {}):
                unit_data = building['unit_data'][unit_idx]
                city_est['citizens_needed'] += unit_data.get('citizens', 0) * qty
                city_est['build_time_seconds'] += unit_data.get('time_seconds', 0) * qty
        
        # Add minimum 10 seconds per order
        if building.get('assignments'):
            city_est['build_time_seconds'] += 10
    
    # Calculate wait times and totals for each city
    max_total = 0
    bottleneck_city = None
    
    for city_id, city_est in estimates['by_city'].items():
        citizens_needed = city_est['citizens_needed']
        citizens_available = city_est['citizens_available']
        growth_rate = city_est['growth_rate']
        
        if citizens_needed <= citizens_available:
            city_est['citizen_wait_seconds'] = 0
        elif growth_rate > 0:
            citizens_deficit = citizens_needed - citizens_available
            # growth_rate is per hour, convert to seconds
            city_est['citizen_wait_seconds'] = (citizens_deficit / growth_rate) * 3600
        else:
            # No growth rate data - estimate based on typical rate or mark as unknown
            city_est['citizen_wait_seconds'] = float('inf')  # Unknown
        
        # Total time is the longer of: citizen wait OR build time
        # Actually, it's citizen wait + build time since you need citizens first
        # But with the 20% threshold, it's more complex - we recruit in batches
        # For simplicity, estimate as: citizen_wait + build_time (conservative)
        if city_est['citizen_wait_seconds'] == float('inf'):
            city_est['total_time_seconds'] = float('inf')
        else:
            # With 20% batching, the actual time is roughly:
            # citizen_wait * 0.8 (since we start recruiting at 20%) + build_time
            effective_citizen_wait = city_est['citizen_wait_seconds'] * 0.8
            city_est['total_time_seconds'] = effective_citizen_wait + city_est['build_time_seconds']
        
        if city_est['total_time_seconds'] > max_total:
            max_total = city_est['total_time_seconds']
            bottleneck_city = city_id
    
    estimates['total_time_seconds'] = max_total
    estimates['bottleneck'] = bottleneck_city
    
    return estimates


# =============================================================================
# MAIN MODULE ENTRY POINT
# =============================================================================

def autoRecruitment(session, event, stdin_fd, predetermined_input):
    """
    Auto Recruitment Module - Main entry point
    """
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
        
        # Step 2: Find all relevant buildings
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
        
        # Step 3: Ask which cities to ignore
        print("=" * 60)
        print("CITY SELECTION")
        print("=" * 60)
        print()
        print("Available cities with " + building_type + ":")
        print()
        
        # List cities with buildings
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
        
        # Filter buildings
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
        
        # Step 4: Fetch detailed building data and check for busy buildings
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
        
        # Step 5: Handle busy buildings
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
            print("2. Wait for all buildings to finish their queues")
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
        
        # Step 6: Ask for unit quantities
        print("=" * 60)
        print("RECRUITMENT ORDER")
        print("=" * 60)
        print()
        print("Enter the quantity for each unit type (0 to skip):")
        print()
        print("(') Back to main menu at any prompt")
        print()
        
        recruitment_order = {}
        units_shown = 0
        
        for unit in unit_list:
            unit_name = unit['name']
            game_idx = unit['game_index']
            
            # Check if this unit is available in any building
            available = any(game_idx in b.get('unit_data', {}) for b in buildings_data)
            
            if not available:
                continue
            
            units_shown += 1
            
            # Get sample cost info from first available building
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
            
            # Treat empty input as 0
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
        
        # Step 7: Calculate distribution
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
        
        # Step 8: Check resources and fetch citizen growth rates
        print("Checking resources in each city...")
        
        resource_check = check_resources(session, distribution, cities)
        
        # Get unique city IDs from distribution
        active_city_ids = list(set(b['city_id'] for b in distribution))
        
        print("Fetching citizen growth rates...")
        print()
        
        city_growth_rates = get_all_city_growth_rates(session, active_city_ids)
        
        # Build city_resources dict for estimate function
        city_resources = {}
        for city_id in active_city_ids:
            city_resources[city_id] = resource_check['available'].get(city_id, {})
        
        # Calculate time estimates
        time_estimates = estimate_recruitment_time(distribution, city_resources, city_growth_rates)
        
        # Step 9: Display plan and confirm
        print("=" * 60)
        print("RECRUITMENT PLAN")
        print("=" * 60)
        print()
        
        display_distribution_plan(distribution, recruitment_order)
        
        print()
        
        # Show resource status
        if resource_check['missing_resources']:
            print(f"{bcolors.WARNING}⚠ RESOURCE SHORTAGES DETECTED:{bcolors.ENDC}")
            print()
            for city_id, missing in resource_check['missing_resources'].items():
                city_name = cities[city_id]['name']
                print(f"  {city_name}:")
                for res_name, amount in missing.items():
                    print(f"    - Missing {addThousandSeparator(amount)} {res_name}")
            print()
        
        if resource_check['missing_citizens']:
            print(f"{bcolors.WARNING}⚠ CITIZEN SHORTAGES DETECTED:{bcolors.ENDC}")
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
        
        # Estimated completion time with breakdown
        print("=" * 60)
        print("TIME ESTIMATES")
        print("=" * 60)
        print()
        
        for city_id, city_est in time_estimates['by_city'].items():
            city_name = city_est['city_name']
            citizens_needed = city_est['citizens_needed']
            citizens_available = city_est['citizens_available']
            growth_rate = city_est['growth_rate']
            citizen_wait = city_est['citizen_wait_seconds']
            build_time = city_est['build_time_seconds']
            total_time = city_est['total_time_seconds']
            
            print(f"  {city_name}:")
            print(f"    Citizens: {addThousandSeparator(citizens_available)} available / {addThousandSeparator(citizens_needed)} needed", end="")
            if growth_rate > 0:
                print(f" (+{growth_rate:.1f}/hr)")
            else:
                print(" (growth rate unknown)")
            
            if citizen_wait == float('inf'):
                print(f"    Citizen wait: Unknown (no growth data)")
            elif citizen_wait > 0:
                print(f"    Citizen wait: ~{format_time(int(citizen_wait))}")
            else:
                print(f"    Citizen wait: None (enough available)")
            
            print(f"    Build time: {format_time(int(build_time))}")
            
            if total_time == float('inf'):
                print(f"    Total estimate: Unknown")
            else:
                print(f"    Total estimate: ~{format_time(int(total_time))}")
            print()
        
        # Overall estimate
        if time_estimates['total_time_seconds'] == float('inf'):
            print(f"Overall estimated completion: Unknown (missing growth rate data)")
        else:
            total_est = time_estimates['total_time_seconds']
            print(f"Overall estimated completion: ~{format_time(int(total_est))}")
            if time_estimates['bottleneck']:
                bottleneck_name = time_estimates['by_city'][time_estimates['bottleneck']]['city_name']
                print(f"Bottleneck city: {bottleneck_name}")
        print()
        
        # Confirm
        print("Proceed with recruitment? [Y/n]")
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
        
        # Step 10: Determine execution mode
        has_shortages = resource_check['missing_resources'] or resource_check['missing_citizens']
        wait_for_busy = any(b.get('is_busy', False) for b in buildings_data)
        
        if has_shortages or wait_for_busy:
            print("Starting background recruitment process...")
            print()
            print("The module will:")
            if wait_for_busy:
                print("  • Wait for busy buildings to finish their queues")
            if resource_check['missing_citizens']:
                print("  • Loop until all citizens are available")
            if resource_check['missing_resources']:
                print("  • Check resources hourly (recruit if ≥20% can be fulfilled)")
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
                    resource_check
                )
            except Exception as e:
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
                print(f"{bcolors.GREEN}✓ Recruitment orders placed successfully!{bcolors.ENDC}")
            else:
                print(f"{bcolors.WARNING}⚠ Some recruitment orders may have failed.{bcolors.ENDC}")
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
        html = session.get(city_url + city_id)
        city_data = getCity(html)
        
        for building in city_data.get('position', []):
            if building.get('building') == building_type:
                buildings.append({
                    'city_id': city_id,
                    'city_name': city['name'],
                    'building_position': building.get('position'),
                    'building_level': building.get('level', 0),
                    'is_busy': building.get('isBusy', False)
                })
    
    return buildings


def fetch_building_data(session, building_info, is_units=True):
    """
    Fetch detailed recruitment data from a barracks or shipyard
    
    The unit data is in updateTemplateData -> js_barracksSlider{N} -> slider -> control_data
    which contains a JSON string with unit_type_id, costs (citizens, wood, sulfur, etc.), 
    and completiontime (build time in seconds)
    """
    city_id = building_info['city_id']
    position = building_info['building_position']
    view_name = 'barracks' if is_units else 'shipyard'
    
    params = f"view={view_name}&cityId={city_id}&position={position}&backgroundView=city&currentCityId={city_id}&ajax=1"
    
    try:
        response = session.post(params)
        response_data = json.loads(response)
    except:
        return None
    
    result = {
        'city_id': city_id,
        'city_name': building_info['city_name'],
        'building_position': position,
        'building_level': building_info['building_level'],
        'is_busy': False,
        'queue_remaining_time': 0,
        'unit_data': {},
        'action_code': None,
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
    
    # Extract unit data from slider control_data
    # Pattern: js_barracksSlider{N} or js_shipyardSlider{N}
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
                'name': control_data.get('local_name', ''),
                'identifier': control_data.get('identifier', ''),
                'citizens': costs.get('citizens', 0),
                'wood': costs.get('wood', 0),
                'wine': costs.get('wine', 0),
                'marble': costs.get('marble', 0),
                'crystal': costs.get('crystal', 0),
                'sulfur': costs.get('sulfur', 0),
                'upkeep': costs.get('upkeep', 0),
                'time_seconds': costs.get('completiontime', 0),
                'max_buildable': slider_info.get('max_value', 0),
            }
            
            result['unit_data'][unit_type_id] = unit_data
            
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    
    # Check if busy - look for training queue in the HTML or check js_noUnitsSelected
    # For simplicity, check if there's a queue via the building info
    result['is_busy'] = building_info.get('is_busy', False)
    
    return result


def format_time(seconds):
    """Format seconds into human readable string"""
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
    to finish within 30 minutes of each other
    """
    if not buildings_data or not recruitment_order:
        return None
    
    for building in buildings_data:
        building['assignments'] = {}
        building['estimated_time'] = 0
    
    for unit_idx, unit_info in recruitment_order.items():
        total_qty = unit_info['quantity']
        
        capable_buildings = []
        for building in buildings_data:
            if unit_idx in building.get('unit_data', {}):
                capable_buildings.append(building)
        
        if not capable_buildings:
            continue
        
        speed_factors = []
        total_speed = 0
        for building in capable_buildings:
            time_per_unit = building['unit_data'][unit_idx]['time_seconds']
            if time_per_unit > 0:
                speed = 1.0 / time_per_unit
            else:
                speed = 1.0
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
            
            if unit_idx not in fastest['assignments']:
                fastest['assignments'][unit_idx] = 0
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
                'citizens': 0,
                'wood': 0,
                'wine': 0,
                'marble': 0,
                'crystal': 0,
                'sulfur': 0
            }
        
        for unit_idx, qty in building.get('assignments', {}).items():
            if unit_idx in building.get('unit_data', {}):
                unit_data = building['unit_data'][unit_idx]
                city_requirements[city_id]['citizens'] += unit_data.get('citizens', 0) * qty
                city_requirements[city_id]['wood'] += unit_data.get('wood', 0) * qty
                city_requirements[city_id]['wine'] += unit_data.get('wine', 0) * qty
                city_requirements[city_id]['marble'] += unit_data.get('marble', 0) * qty
                city_requirements[city_id]['crystal'] += unit_data.get('crystal', 0) * qty
                city_requirements[city_id]['sulfur'] += unit_data.get('sulfur', 0) * qty
    
    for city_id, requirements in city_requirements.items():
        html = session.get(city_url + city_id)
        city_data = getCity(html)
        
        available_res = city_data.get('availableResources', [0, 0, 0, 0, 0])
        if len(available_res) < 5:
            available_res = available_res + [0] * (5 - len(available_res))
        
        citizens_match = re.search(r'id="js_GlobalMenu_citizens">([0-9,]+)', html)
        available_citizens = 0
        if citizens_match:
            available_citizens = int(citizens_match.group(1).replace(',', ''))
        
        result['available'][city_id] = {
            'resources': {
                'wood': available_res[0],
                'wine': available_res[1],
                'marble': available_res[2],
                'crystal': available_res[3],
                'sulfur': available_res[4]
            },
            'citizens': available_citizens
        }
        
        if requirements['citizens'] > available_citizens:
            result['missing_citizens'][city_id] = requirements['citizens'] - available_citizens
            result['can_fulfill'] = False
        
        missing = {}
        if requirements['wood'] > available_res[0]:
            missing['Wood'] = requirements['wood'] - available_res[0]
        if requirements['wine'] > available_res[1]:
            missing['Wine'] = requirements['wine'] - available_res[1]
        if requirements['marble'] > available_res[2]:
            missing['Marble'] = requirements['marble'] - available_res[2]
        if requirements['crystal'] > available_res[3]:
            missing['Crystal'] = requirements['crystal'] - available_res[3]
        if requirements['sulfur'] > available_res[4]:
            missing['Sulfur'] = requirements['sulfur'] - available_res[4]
        
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
        busy_str = " (WAITING)" if building.get('is_busy', False) else ""
        
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
    """Execute recruitment orders for all buildings"""
    success = True
    view_name = 'barracks' if is_units else 'shipyard'
    
    for building in distribution:
        if not building.get('assignments'):
            continue
        
        city_id = building['city_id']
        position = building['building_position']
        action_code = building.get('action_code')
        
        if not action_code:
            params = f"view={view_name}&cityId={city_id}&position={position}&backgroundView=city&currentCityId={city_id}&ajax=1"
            try:
                response = session.post(params)
                response_data = json.loads(response)
                for item in response_data:
                    if isinstance(item, list) and item[0] == "updateGlobalData" and isinstance(item[1], dict):
                        if "actionRequest" in item[1]:
                            action_code = item[1]["actionRequest"]
                            break
            except:
                pass
        
        if not action_code:
            print(f"  {building['city_name']}: Failed to get action code")
            success = False
            continue
        
        # Build recruitment POST data
        # The form submits with action=BuildUnits and unit quantities as name="{unit_id}" value="{qty}"
        recruit_params = {
            'action': 'BuildUnits',
            'actionRequest': action_code,
            'cityId': city_id,
            'position': position,
        }
        
        # Add each unit's quantity
        for unit_idx, qty in building['assignments'].items():
            recruit_params[str(unit_idx)] = qty
        
        try:
            response = session.post(params=recruit_params)
            
            # Count total units for this building
            total_units = sum(building['assignments'].values())
            print(f"  {building['city_name']}: Recruited {total_units} units")
            
        except Exception as e:
            print(f"  {building['city_name']}: Failed - {e}")
            success = False
    
    return success


def execute_recruitment_loop(session, distribution, recruitment_order, cities, is_units, resource_check):
    """
    Execute recruitment with retry logic for shortages.
    
    Uses per-building 20% threshold:
    - For each building, calculate what % of its remaining assignment can be fulfilled
    - If >= 20% can be recruited, recruit as many as possible (up to 100%)
    - If < 20%, skip that building this cycle and wait
    """
    # Track remaining assignments per building
    for building in distribution:
        building['remaining_assignments'] = building.get('assignments', {}).copy()
    
    # Fetch citizen growth rates once at start (they don't change often)
    active_city_ids = list(set(b['city_id'] for b in distribution))
    city_growth_rates = get_all_city_growth_rates(session, active_city_ids)
    
    while True:
        # Calculate total remaining across all buildings
        total_remaining = 0
        total_citizens_needed = 0
        for building in distribution:
            for unit_idx, qty in building.get('remaining_assignments', {}).items():
                total_remaining += qty
                if unit_idx in building.get('unit_data', {}):
                    total_citizens_needed += building['unit_data'][unit_idx].get('citizens', 0) * qty
        
        if total_remaining <= 0:
            session.setStatus("Recruitment complete!")
            sendToBot(session, "✓ Auto Recruitment completed successfully!")
            break
        
        # Refresh resource data for all cities
        city_resources = {}
        total_citizens_available = 0
        for building in distribution:
            city_id = building['city_id']
            if city_id not in city_resources:
                html = session.get(city_url + city_id)
                city_data = getCity(html)
                
                available_res = city_data.get('availableResources', [0, 0, 0, 0, 0])
                if len(available_res) < 5:
                    available_res = available_res + [0] * (5 - len(available_res))
                
                citizens_match = re.search(r'id="js_GlobalMenu_citizens">([0-9,]+)', html)
                available_citizens = 0
                if citizens_match:
                    available_citizens = int(citizens_match.group(1).replace(',', ''))
                
                city_resources[city_id] = {
                    'citizens': available_citizens,
                    'wood': available_res[0],
                    'wine': available_res[1],
                    'marble': available_res[2],
                    'crystal': available_res[3],
                    'sulfur': available_res[4]
                }
                total_citizens_available += available_citizens
        
        # Calculate time estimate for status display
        remaining_citizens_needed = max(0, total_citizens_needed - total_citizens_available)
        total_growth_rate = sum(city_growth_rates.get(cid, 0) for cid in active_city_ids)
        
        if remaining_citizens_needed > 0 and total_growth_rate > 0:
            est_wait_hours = remaining_citizens_needed / total_growth_rate
            est_wait_str = format_time(int(est_wait_hours * 3600))
        else:
            est_wait_str = None
        
        # Calculate what can be recruited per building with 20% threshold
        buildings_to_recruit = []
        total_recruiting_this_cycle = 0
        
        for building in distribution:
            remaining = building.get('remaining_assignments', {})
            if not remaining:
                continue
            
            # Check if building is busy
            if building.get('is_busy', False):
                # Refresh building status
                data = fetch_building_data(session, building, is_units)
                if data:
                    building['is_busy'] = data.get('is_busy', False)
                    building['queue_remaining_time'] = data.get('queue_remaining_time', 0)
                    building['action_code'] = data.get('action_code')
                
                if building.get('is_busy', False):
                    continue  # Skip busy buildings
            
            city_id = building['city_id']
            available = city_resources[city_id].copy()
            
            # Calculate total units remaining for this building
            building_total_remaining = sum(remaining.values())
            threshold = max(1, int(building_total_remaining * 0.20))  # 20% minimum, at least 1
            
            # Calculate how many of each unit can be recruited
            can_recruit_building = {}
            total_can_recruit = 0
            
            for unit_idx, qty_needed in remaining.items():
                if unit_idx not in building.get('unit_data', {}):
                    continue
                
                unit_data = building['unit_data'][unit_idx]
                
                # Calculate max by each resource
                max_possible = qty_needed  # Start with what we need
                
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
                    
                    # Deduct from available for next unit calculation
                    available['citizens'] -= citizens_cost * max_possible
                    available['wood'] -= wood_cost * max_possible
                    available['wine'] -= wine_cost * max_possible
                    available['marble'] -= marble_cost * max_possible
                    available['crystal'] -= crystal_cost * max_possible
                    available['sulfur'] -= sulfur_cost * max_possible
            
            # Check if we meet the 20% threshold for this building
            if total_can_recruit >= threshold:
                # Also deduct from city_resources so other buildings in same city see reduced availability
                for unit_idx, qty in can_recruit_building.items():
                    unit_data = building['unit_data'][unit_idx]
                    city_resources[city_id]['citizens'] -= unit_data.get('citizens', 0) * qty
                    city_resources[city_id]['wood'] -= unit_data.get('wood', 0) * qty
                    city_resources[city_id]['wine'] -= unit_data.get('wine', 0) * qty
                    city_resources[city_id]['marble'] -= unit_data.get('marble', 0) * qty
                    city_resources[city_id]['crystal'] -= unit_data.get('crystal', 0) * qty
                    city_resources[city_id]['sulfur'] -= unit_data.get('sulfur', 0) * qty
                
                buildings_to_recruit.append({
                    'building': building,
                    'to_recruit': can_recruit_building,
                    'total': total_can_recruit,
                    'threshold': threshold,
                    'building_remaining': building_total_remaining
                })
                total_recruiting_this_cycle += total_can_recruit
        
        # If no buildings meet threshold, wait
        if not buildings_to_recruit:
            # Check if any buildings are busy
            any_busy = any(b.get('is_busy', False) for b in distribution if b.get('remaining_assignments'))
            
            if any_busy:
                status_msg = f"Waiting for queues... {total_remaining} units remaining"
                if est_wait_str:
                    status_msg += f" (~{est_wait_str} for citizens)"
                session.setStatus(status_msg)
                wait(300)  # Check every 5 minutes
            else:
                status_msg = f"Waiting for 20% threshold... {total_remaining} units remaining"
                if est_wait_str:
                    status_msg += f" (~{est_wait_str} for citizens)"
                session.setStatus(status_msg)
                wait(300)  # Check every 5 minutes for citizens/resources
            continue
        
        # Execute recruitment for buildings that meet threshold
        status_msg = f"Recruiting {total_recruiting_this_cycle}/{total_remaining} units"
        if est_wait_str and total_remaining > total_recruiting_this_cycle:
            status_msg += f" (~{est_wait_str} remaining)"
        session.setStatus(status_msg)
        
        for recruit_info in buildings_to_recruit:
            building = recruit_info['building']
            to_recruit = recruit_info['to_recruit']
            
            city_id = building['city_id']
            position = building['building_position']
            action_code = building.get('action_code')
            
            # Refresh action code if needed
            if not action_code:
                view_name = 'barracks' if is_units else 'shipyard'
                params = f"view={view_name}&cityId={city_id}&position={position}&backgroundView=city&currentCityId={city_id}&ajax=1"
                try:
                    response = session.post(params)
                    response_data = json.loads(response)
                    for item in response_data:
                        if isinstance(item, list) and item[0] == "updateGlobalData" and isinstance(item[1], dict):
                            if "actionRequest" in item[1]:
                                action_code = item[1]["actionRequest"]
                                building['action_code'] = action_code
                                break
                except:
                    pass
            
            if not action_code:
                print(f"  {building['city_name']}: Failed to get action code")
                continue
            
            # Build recruitment POST data
            recruit_params = {
                'action': 'BuildUnits',
                'actionRequest': action_code,
                'cityId': city_id,
                'position': position,
            }
            
            for unit_idx, qty in to_recruit.items():
                recruit_params[str(unit_idx)] = qty
            
            try:
                response = session.post(params=recruit_params)
                
                total_units = sum(to_recruit.values())
                pct = (total_units / recruit_info['building_remaining']) * 100
                print(f"  {building['city_name']} L{building['building_level']}: Recruited {total_units} units ({pct:.0f}% of remaining)")
                
                # Update remaining assignments
                for unit_idx, qty in to_recruit.items():
                    building['remaining_assignments'][unit_idx] -= qty
                    if building['remaining_assignments'][unit_idx] <= 0:
                        del building['remaining_assignments'][unit_idx]
                
                # Mark as busy since we just added to queue
                building['is_busy'] = True
                
            except Exception as e:
                print(f"  {building['city_name']}: Failed - {e}")
        
        # Short wait before next cycle
        wait(60)



