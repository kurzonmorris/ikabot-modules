#! /usr/bin/env python3
# -*- coding: utf-8 -*-


import hashlib
import json
import math
import random
import re
import threading
import time
import traceback
from decimal import *

import requests
from functools import cache

from ikabot.config import *
from ikabot.helpers.botComm import *
from ikabot.helpers.getJson import getCity
from ikabot.helpers.gui import *
from ikabot.helpers.pedirInfo import *
from ikabot.helpers.planRoutes import *
from ikabot.helpers.process import set_child_mode
from ikabot.helpers.resources import getAvailableResources
from ikabot.helpers.signals import setInfoSignal
from ikabot.helpers.varios import *
from ikabot.web.session import normal_get

# Safety caps and intervals for polling loops
_MAX_CONSTRUCTION_WAIT_SECS = 7 * 24 * 3600   # 7 days
_MAX_RESOURCE_WAIT_SECS     = 7 * 24 * 3600   # 7 days (queue retries indefinitely up to this)
_RESOURCE_RETRY_INTERVAL    = 30 * 60         # 30 minutes between resource checks


def waitForConstruction(session, city_id, final_lvl):
    """
    Parameters
    ----------
    session : ikabot.web.session.Session
    city_id : int
    final_lvl : int

    Returns
    -------
    city : dict
    """
    # FIX #5: was infinite with no timeout; FIX #11: city['name'] -> city['cityName']
    total_waited = 0
    while True:
        html = session.get(city_url + city_id)
        city = getCity(html)

        construction_buildings = [
            building for building in city["position"] if "completed" in building
        ]
        if len(construction_buildings) == 0:
            break

        construction_building = construction_buildings[0]
        construction_time = construction_building["completed"]

        current_time = int(time.time())
        final_time = int(construction_time)
        seconds_to_wait = final_time - current_time

        # FIX #5: guard against negative or runaway wait times
        if seconds_to_wait < 0:
            seconds_to_wait = 0
        if total_waited + seconds_to_wait > _MAX_CONSTRUCTION_WAIT_SECS:
            sendToBotDebug(
                session,
                "waitForConstruction: exceeded 7-day safety cap, aborting wait.",
                debugON_constructionList,
            )
            break

        msg = "{}: I wait {:d} seconds so that {} gets to the level {:d}".format(
            city["cityName"],
            seconds_to_wait,
            construction_building["name"],
            construction_building["level"] + 1,
        )
        sendToBotDebug(session, msg, debugON_constructionList)
        # FIX #11: was city['name'], correct key is city['cityName']
        session.setStatus(
            f"Waiting until {getDateTime(time.time()+seconds_to_wait+10)[8:]}, "
            f"{construction_building['name']} {construction_building['level']} -> "
            f"{construction_building['level']+1} in {city['cityName']}, final lvl: {final_lvl}"
        )
        wait(seconds_to_wait + 10)
        total_waited += seconds_to_wait + 10

    html = session.get(city_url + city_id)
    city = getCity(html)
    return city


def _printQueueSummary(buildings):
    """Print a formatted summary of the construction queue."""
    total_levels = sum(b["upgradeTo"] - b.get("startLevel", b["level"]) for b in buildings)
    print("\nConstruction queue ({:d} building(s), {:d} total level(s)):".format(
        len(buildings), total_levels
    ))
    for i, b in enumerate(buildings, 1):
        start = b.get("startLevel", b["level"])
        levels = b["upgradeTo"] - start
        print("  {:d}. {} lv {:d} -> {:d}  ({:d} level(s))".format(
            i, b["name"], start, b["upgradeTo"], levels
        ))
    print("  Queue will retry every {:d} min if resources are insufficient.\n".format(
        _RESOURCE_RETRY_INTERVAL // 60
    ))


def expandBuilding(session, cityId, building, waitForResources):
    """
    Parameters
    ----------
    session : ikabot.web.session.Session
    cityId : int
    building : dict
    waitForResources : bool
    """
    current_level = building["level"]
    if building["isBusy"]:
        current_level += 1
    levels_to_upgrade = building["upgradeTo"] - current_level
    position = building["position"]
    upgradeTo = building["upgradeTo"]
    time.sleep(
        random.randint(5, 15)
    )  # to avoid race conditions with sendResourcesNeeded

    for lv in range(levels_to_upgrade):
        city = waitForConstruction(session, cityId, upgradeTo)
        building = city["position"][position]

        # Queue retry loop: if resources are insufficient, poll every 30 minutes.
        # This runs unconditionally — the queue will keep trying until resources
        # arrive, the 7-day safety cap is hit, or the user interrupts.
        retry_total = 0
        while building["canUpgrade"] is False:
            if retry_total >= _MAX_RESOURCE_WAIT_SECS:
                sendToBotDebug(
                    session,
                    "expandBuilding: 7-day resource retry cap reached, skipping remaining levels.",
                    debugON_constructionList,
                )
                break
            levels_remaining = levels_to_upgrade - lv
            msg = (
                "{}: Cannot upgrade {} to level {:d} — insufficient resources. "
                "{:d} level(s) remaining in queue. Retrying in {:d} min."
            ).format(
                city["cityName"],
                building["name"],
                building["level"] + 1,
                levels_remaining,
                _RESOURCE_RETRY_INTERVAL // 60,
            )
            sendToBotDebug(session, msg, debugON_constructionList)
            session.setStatus(
                "Queue: {} lv {:d}->{:d} in {} | {:d} level(s) pending | retry in {:d}min".format(
                    building["name"],
                    building["level"],
                    building["level"] + 1,
                    city["cityName"],
                    levels_remaining,
                    _RESOURCE_RETRY_INTERVAL // 60,
                )
            )
            wait(_RESOURCE_RETRY_INTERVAL)
            retry_total += _RESOURCE_RETRY_INTERVAL
            html = session.get(city_url + cityId)
            city = getCity(html)
            building = city["position"][position]

        if building["canUpgrade"] is False:
            msg = "City: {}\nBuilding: {}\nCould not upgrade due to insufficient resources.\nMissed {:d} level(s).".format(
                city["cityName"], building["name"], levels_to_upgrade - lv
            )
            sendToBot(session, msg)
            return

        url = "action=CityScreen&function=upgradeBuilding&actionRequest={}&cityId={}&position={:d}&level={}&activeTab=tabSendTransporter&backgroundView=city&currentCityId={}&templateView={}&ajax=1".format(
            actionRequest,
            cityId,
            position,
            building["level"],
            cityId,
            building["building"],
        )
        resp = session.post(url)
        html = session.get(city_url + cityId)
        city = getCity(html)
        building = city["position"][position]
        if building["isBusy"] is False:
            msg = "{}: The building {} was not extended".format(
                city["cityName"], building["name"]
            )
            sendToBot(session, msg)
            sendToBot(session, resp)
            return

        msg = "{}: The building {} is being extended to level {:d}.".format(
            city["cityName"], building["name"], building["level"] + 1
        )
        sendToBotDebug(session, msg, debugON_constructionList)

    msg = "{}: The building {} finished extending to level: {:d}.".format(
        city["cityName"], building["name"], building["level"] + 1
    )
    sendToBotDebug(session, msg, debugON_constructionList)


def getCostsReducers(city):
    """
    Parameters
    ----------
    city : dict

    Returns
    -------
    reducers_per_material_level : list[int]
    """
    reducers_per_material = [0] * len(materials_names)
    assert len(reducers_per_material) == 5

    for building in city["position"]:
        if building["name"] == "empty":
            continue
        lv = building["level"]
        if building["building"] == "carpentering":
            reducers_per_material[0] = lv
        elif building["building"] == "vineyard":
            reducers_per_material[1] = lv
        elif building["building"] == "architect":
            reducers_per_material[2] = lv
        elif building["building"] == "optician":
            reducers_per_material[3] = lv
        elif building["building"] == "fireworker":
            reducers_per_material[4] = lv
    return reducers_per_material


def getResourcesNeeded(session, city, building, current_level, final_level):
    """
    Parameters
    ----------
    session : ikabot.web.session.Session
    city : dict
    building : dict
    current_level : int
    final_level : int

    Returns
    -------
    costs_per_material : list[int]
    """
    # FIX #10 (partial): wrap all JSON parsing in try/except
    try:
        building_detail_url = "view=buildingDetail&buildingId=0&helpId=1&backgroundView=city&currentCityId={}&templateView=ikipedia&actionRequest={}&ajax=1".format(
            city["id"], actionRequest
        )
        building_detail_response = session.post(building_detail_url)
        building_detail = json.loads(building_detail_response, strict=False)
        building_html = building_detail[1][1][1]
    except (json.JSONDecodeError, IndexError, KeyError, TypeError) as e:
        print("Failed to fetch building details: {}".format(e))
        return [-1, -1, -1, -1, -1]

    regex_building_detail = (
        r'<div class="(?:selected)? button_building '
        + re.escape(building["building"])
        + r'"\s*onmouseover="\$\(this\)\.addClass\(\'hover\'\);" onmouseout="\$\(this\)\.removeClass\(\'hover\'\);"\s*onclick="ajaxHandlerCall\(\'\?(.*?)\'\);'
    )
    match = re.search(regex_building_detail, building_html)
    # FIX #8: was match.group(1) with no None guard — would crash on unmatched regex
    if match is None:
        print("Could not find upgrade details for building: {}".format(building["name"]))
        return [-1, -1, -1, -1, -1]

    try:
        building_costs_url = match.group(1)
        building_costs_url += "backgroundView=city&currentCityId={}&templateView=buildingDetail&actionRequest={}&ajax=1".format(
            city["id"], actionRequest
        )
        building_costs_response = session.post(building_costs_url)
        building_costs = json.loads(building_costs_response, strict=False)
        html_costs = building_costs[1][1][1]
    except (json.JSONDecodeError, IndexError, KeyError, TypeError) as e:
        print("Failed to fetch building costs: {}".format(e))
        return [-1, -1, -1, -1, -1]

    sessionData = session.getSessionData()
    if "reduccion_inv_max" in sessionData:
        costs_reduction = 14
    else:
        try:
            url = "view=noViewChange&researchType=economy&backgroundView=city&currentCityId={}&templateView=researchAdvisor&actionRequest={}&ajax=1".format(
                city["id"], actionRequest
            )
            rta = session.post(url)
            rta = json.loads(rta, strict=False)
            studies = rta[2][1]["new_js_params"]
            studies = json.loads(studies, strict=False)
            studies = studies["currResearchType"]
        except (json.JSONDecodeError, IndexError, KeyError, TypeError) as e:
            print("Failed to fetch research data, assuming no cost reduction: {}".format(e))
            studies = {}

        costs_reduction = 0
        for study in studies:
            if studies[study]["liClass"] != "explored":
                continue
            link = studies[study]["aHref"]
            if "2020" in link:
                costs_reduction += 2
            elif "2060" in link:
                costs_reduction += 4
            elif "2100" in link:
                costs_reduction += 8

        if costs_reduction == 14:
            sessionData["reduccion_inv_max"] = True
            session.setSessionData(sessionData)

    costs_reduction /= 100
    costs_reduction = 1 - costs_reduction

    costs_reductions = getCostsReducers(city)

    resources_types = re.findall(
        r'<th class="costs"><img src="(.*?)\.png"/></th>', html_costs
    )[:-1]

    matches = re.findall(
        r'<td class="level">\d+</td>(?:\s+<td class="costs">.*?</td>)+', html_costs
    )

    final_costs = [0] * len(materials_names)
    levels_to_upgrade = 0
    for match in matches:
        lv = re.search(r'"level">(\d+)</td>', match).group(1)
        lv = int(lv)

        if lv <= current_level:
            continue
        if lv > final_level:
            break

        levels_to_upgrade += 1
        costs = re.findall(r'<td class="costs"><div.*>([\d,\.\s\xa0]*)</div></div></td>', match)
        costs = [value.replace('\xa0', '').replace(' ', '') for value in costs]

        for i in range(len(costs)):
            resource_type = checkhash("https:" + resources_types[i] + ".png")

            # FIX #8 (partial): checkhash can now return None; skip unknown resource types
            if resource_type is None:
                continue

            resource_index = None
            for j in range(len(materials_names_tec)):
                name = materials_names_tec[j]
                if resource_type == name:
                    resource_index = j
                    break

            # FIX #8 (partial): resource_index was used unguarded; skip if not found
            if resource_index is None:
                continue

            cost = costs[i]
            cost = cost.replace(",", "").replace(".", "")
            cost = 0 if cost == "" else int(cost)

            real_cost = Decimal(cost)
            original_cost = Decimal(real_cost) / Decimal(costs_reduction)
            real_cost -= Decimal(original_cost) * (
                Decimal(costs_reductions[resource_index]) / Decimal(100)
            )

            final_costs[resource_index] += math.ceil(real_cost)

    if levels_to_upgrade < final_level - current_level:
        print(
            "This building only allows you to expand {:d} more levels".format(
                levels_to_upgrade
            )
        )
        msg = "Expand {:d} levels? [Y/n]:".format(levels_to_upgrade)
        rta = read(msg=msg, values=["Y", "y", "N", "n", ""])
        if rta.lower() == "n":
            return [-1, -1, -1, -1, -1]

    return final_costs


def sendResourcesNeeded(session, destination_city_id, city_origins, missing_resources, useFreighters=False):
    """
    Parameters
    ----------
    session : ikabot.web.session.Session
    destination_city_id : int
    city_origins : dict
    missing_resources : list[int]
    """
    info = "\nTransport resources to upload building\n"

    try:
        routes = []
        html = session.get(city_url + destination_city_id)
        cityD = getCity(html)
        for i in range(len(materials_names)):
            missing = missing_resources[i]
            if missing <= 0:
                continue

            for cityOrigin in city_origins[i]:
                if missing == 0:
                    break

                available = cityOrigin["availableResources"][i]
                send = min(available, missing)
                missing -= send
                toSend = [0] * len(materials_names)
                toSend[i] = send
                route = (cityOrigin, cityD, cityD["islandId"], *toSend)
                routes.append(route)
        executeRoutes(session, routes, useFreighters)
    except Exception as e:
        msg = "Error in:\n{}\nCause:\n{}".format(info, traceback.format_exc())
        sendToBot(session, msg)
        # no s.logout() because this is a thread, not a process


def chooseResourceProviders(session, cities_ids, cities, city_id, resource, missing):
    """
    Parameters
    ----------
    session : ikabot.web.session.Session
    cities_ids : list[int]
    cities : dict[int, dict]
    city_id : int
    resource : int
    missing : int

    Returns
    -------
    tuple: (origin_cities, send_resources, do_expand)
    """
    # FIX #16: removed global sendResources / expand; now returned as local values
    send_resources = True
    do_expand = True

    banner()
    print("From what cities obtain {}?".format(materials_names[resource].lower()))

    tradegood_initials = [material_name[0] for material_name in materials_names]
    maxName = max(
        [len(cities[city]["name"]) for city in cities if cities[city]["id"] != city_id]
    )

    origin_cities = []
    total_available = 0
    for cityId in cities_ids:
        if cityId == city_id:
            continue

        html = session.get(city_url + cityId)
        city = getCity(html)

        available = city["availableResources"][resource]
        if available == 0:
            continue

        tradegood_initial = tradegood_initials[int(cities[cityId]["tradegood"])]
        pad = " " * (maxName - len(cities[cityId]["name"]))
        is_producer = (int(cities[cityId]["tradegood"]) == int(resource))
        msg = "{}{} ({}): {} [{}]:".format(
            pad,
            cities[cityId]["name"],
            tradegood_initial,
            addThousandSeparator(available),
            ("y/N", "Y/n")[is_producer is True]
        )
        choice = read(msg=msg, values=["Y", "y", "N", "n", ""], default=("N", "Y")[is_producer is True])
        if choice.lower() == "n":
            continue

        total_available += available
        origin_cities.append(city)
        if total_available >= missing:
            return origin_cities, send_resources, do_expand

    print("\nThere are not enough resources.")

    if len(origin_cities) > 0:
        print("\nSend the resources anyway? [Y/n]")
        choice = read(values=["y", "Y", "n", "N", ""])
        if choice.lower() == "n":
            send_resources = False

    print("\nTry to expand the building anyway? [y/N]")
    choice = read(values=["y", "Y", "n", "N", ""])
    if choice.lower() == "n" or choice == "":
        do_expand = False

    return origin_cities, send_resources, do_expand


def sendResourcesMenu(session, city_id, missing, useFreighters=False):
    """
    Parameters
    ----------
    session : ikabot.web.session.Session
    city_id : int
    missing : list[int]
    useFreighters : bool

    Returns
    -------
    tuple: (resource_thread or None, do_expand)
    """
    # FIX #16: removed global thread; thread is now returned to the caller
    cities_ids, cities = getIdsOfCities(session)
    origins = {}
    do_expand = True

    for resource in range(len(missing)):
        if missing[resource] <= 0:
            continue

        origin_cities, send_resources, do_expand = chooseResourceProviders(
            session, cities_ids, cities, city_id, resource, missing[resource]
        )
        if send_resources is False and do_expand:
            print("\nThe building will be expanded if possible.")
            enter()
            return None, do_expand
        elif send_resources is False:
            return None, False
        origins[resource] = origin_cities

    if do_expand:
        print("\nThe resources will be sent and the building will be expanded if possible.")
    else:
        print("\nThe resources will be sent.")

    enter()

    resource_thread = threading.Thread(
        target=sendResourcesNeeded,
        args=(
            session,
            city_id,
            origins,
            missing,
            useFreighters,
        ),
    )
    resource_thread.start()
    return resource_thread, do_expand


def getBuildingsToExpand(session, cityId):
    """
    Parameters
    ----------
    session : ikabot.web.session.Session
    cityId : int

    Returns
    -------
    buildings : list of dict
    """
    html = session.get(city_url + cityId)
    city = getCity(html)

    banner()
    print("Which buildings do you want to expand? Separate numbers with commas (7, 1, 3, 5, ...)\n")
    print("(0)\t\texit")
    buildings = [
        building for building in city["position"] if building["name"] != "empty"
    ]
    for i in range(len(buildings)):
        building = buildings[i]

        level = building["level"]

        if building["isMaxLevel"] is True:
            color = bcolors.BLACK
        elif building["canUpgrade"] is True:
            color = bcolors.GREEN
        else:
            color = bcolors.RED
        if level < 10:
            level = " " + str(level)
        else:
            level = str(level)
        if building["isBusy"]:
            level = level + "+"
        print("({:d})\tlv:{}\t{}{}{}".format(i + 1, level, color, building["name"], bcolors.ENDC))

    selected_building_ids = read().split(",")
    selected_building_ids = [int(id.strip()) for id in selected_building_ids if id.strip().isdigit()]

    if len(selected_building_ids) == 0 or 0 in selected_building_ids:
        return None

    max_building_id = len(buildings)
    selected_buildings = []
    for building_id in selected_building_ids:
        # FIX #17: out-of-range input would cause IndexError; now validated
        if building_id < 1 or building_id > max_building_id:
            print("Invalid selection {:d} (valid range: 1-{:d}), skipping.".format(
                building_id, max_building_id
            ))
            continue

        building = buildings[building_id - 1]

        current_level = int(building["level"])
        if building["isBusy"]:
            current_level += 1

        banner()
        print("building:{}".format(building["name"]))
        print("current level:{}".format(current_level))

        final_level = read(min=current_level, msg="increase to level:")
        building["upgradeTo"] = final_level
        # FIX #19: store computed start level so the info string uses it correctly
        building["startLevel"] = current_level
        selected_buildings.append(building)

    return selected_buildings


@cache
def checkhash(url):
    # FIX #6/#7/#8: was checking intermediate MD5 hashes per chunk (never correct),
    # had no HTTP error handling, and could return unbound 'material' on no match.
    # Now: full MD5 computed across all chunks, errors handled, returns None on failure.
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
    except requests.RequestException:
        return None
    m = hashlib.md5()
    for data in r.iter_content(8192):
        m.update(data)
    digest = m.hexdigest()
    if digest == config.material_img_hash[0]:
        return "wood"
    elif digest == config.material_img_hash[1]:
        return "wine"
    elif digest == config.material_img_hash[2]:
        return "marble"
    elif digest == config.material_img_hash[3]:
        return "glass"
    elif digest == config.material_img_hash[4]:
        return "sulfur"
    return None


def constructionList(session, event, stdin_fd, predetermined_input):
    """
    Parameters
    ----------
    session : ikabot.web.session.Session
    event : multiprocessing.Event
    stdin_fd: int
    predetermined_input : multiprocessing.managers.SyncManager.list
    """
    sys.stdin = os.fdopen(stdin_fd)
    config.predetermined_input = predetermined_input
    try:
        # FIX #16: removed global expand / sendResources resets; state flows via return values
        banner()
        wait_resources = False
        do_expand = True
        resource_thread = None

        print("In which city do you want to expand buildings?")
        city = chooseCity(session)
        cityId = city["id"]
        buildings = getBuildingsToExpand(session, cityId)
        if buildings is None or len(buildings) == 0:
            event.set()
            return

        banner()
        _printQueueSummary(buildings)

        for building in buildings:
            # FIX #9: city state was stale for every building after the first;
            # re-fetch on each iteration so resource counts are current
            html = session.get(city_url + cityId)
            city = getCity(html)

            current_level = building["level"]
            if building["isBusy"]:
                current_level += 1
            final_level = building["upgradeTo"]

            resourcesNeeded = getResourcesNeeded(
                session, city, building, current_level, final_level
            )
            if -1 in resourcesNeeded:
                event.set()
                return

            print("\nMaterials needed for {}:".format(building["name"]))
            for i, name in enumerate(materials_names):
                amount = resourcesNeeded[i]
                if amount == 0:
                    continue
                print("- {}: {}".format(name, addThousandSeparator(amount)))
            print("")

            missing = [0] * len(materials_names)
            for i in range(len(materials_names)):
                if city["availableResources"][i] < resourcesNeeded[i]:
                    missing[i] = resourcesNeeded[i] - city["availableResources"][i]

            if sum(missing) > 0:
                print("\nMissing:")
                for i in range(len(materials_names)):
                    if missing[i] == 0:
                        continue
                    name = materials_names[i].lower()
                    print("{} of {}".format(addThousandSeparator(missing[i]), name))
                print("")

                print("Automatically transport resources? [Y/n]")
                rta = read(values=["y", "Y", "n", "N", ""])
                if rta.lower() == "n":
                    print("Proceed anyway? [Y/n]")
                    rta = read(values=["y", "Y", "n", "N", ""])
                    if rta.lower() == "n":
                        event.set()
                        return
                else:
                    print("What type of ships do you want to use? (Default: Trade ships)")
                    print("(1) Trade ships")
                    print("(2) Freighters")
                    shiptype_raw = read(min=1, max=2, digit=True, empty=True)
                    # FIX #15: read() returns a string; comparing str to int was always False,
                    # leaving useFreighters undefined and causing UnboundLocalError
                    shiptype = 1 if shiptype_raw == "" else int(shiptype_raw)
                    useFreighters = (shiptype == 2)
                    wait_resources = True
                    resource_thread, do_expand = sendResourcesMenu(
                        session, cityId, missing, useFreighters
                    )
            else:
                print("\nYou have enough materials")
                print("Proceed? [Y/n]")
                rta = read(values=["y", "Y", "n", "N", ""])
                if rta.lower() == "n":
                    event.set()
                    return
    except KeyboardInterrupt:
        event.set()
        return

    set_child_mode(session)

    # FIX #19: info string previously used only the last loop iteration's building/level vars.
    # Now covers all buildings using the startLevel stored in getBuildingsToExpand.
    info = "\nUpgrade buildings\n"
    info += "City: {}\n".format(city["cityName"])
    for b in buildings:
        b_start = b.get("startLevel", b["level"])
        info += "Building: {}. From {:d}, to {:d}\n".format(b["name"], b_start, b["upgradeTo"])

    # FIX #18: setInfoSignal was called after event.set(), risking a teardown race.
    # Signal is now registered before we release the event.
    setInfoSignal(session, info)
    event.set()

    try:
        if do_expand:
            for building in buildings:
                expandBuilding(session, cityId, building, wait_resources)
        elif resource_thread:
            resource_thread.join()
    except Exception as e:
        msg = "Error in:\n{}\nCause:\n{}".format(info, traceback.format_exc())
        sendToBot(session, msg)
    finally:
        session.logout()
