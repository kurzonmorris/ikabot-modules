#! /usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import re
import time
from decimal import *

from ikabot.config import *
from ikabot.function.autoPirate import getPirateFortressPoints
from ikabot.helpers.getJson import getCity
from ikabot.helpers.gui import *
from ikabot.helpers.market import getGold
from ikabot.helpers.naval import *
from ikabot.helpers.pedirInfo import *
from ikabot.helpers.resources import *
from ikabot.helpers.varios import *

getcontext().prec = 30

# The whole account is scanned once (two requests per city) and kept in a
# per-account file, so re-entering the menu costs nothing. Anything older than
# the TTL is re-scanned automatically instead of being shown as if it were
# current.
STATUS_CACHE_DIR = os.path.join(IKABOT_DATA_DIR, "status_cache")
STATUS_CACHE_TTL = 10 * 60

resources_abbr = {"1": "(W)", "2": "(M)", "3": "(C)", "4": "(S)"}


def _safe(string):
    return "".join(c for c in str(string) if c.isalnum() or c in "-_")


def getCacheFile(session):
    """Cache file for this account — username, server and world, never shared."""
    name = "{}_{}{}.json".format(
        _safe(getattr(session, "username", "") or "unknown"),
        _safe(getattr(session, "servidor", "") or ""),
        _safe(getattr(session, "mundo", "") or ""),
    )
    return os.path.join(STATUS_CACHE_DIR, name)


def saveCache(session, data):
    """Persist the collected account data, stamped with the time it was read."""
    cache_file = getCacheFile(session)
    try:
        os.makedirs(STATUS_CACHE_DIR, exist_ok=True)
        data["saved_at"] = time.time()
        with open(cache_file, "w") as f:
            json.dump(data, f, ensure_ascii=False)
    except OSError:
        print("Warning: could not write cache to {}".format(cache_file))


def loadCache(session):
    """Return the cached data, or None when it is missing, unusable or stale."""
    try:
        with open(getCacheFile(session)) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not data.get("own_ids"):
        return None
    saved_at = data.get("saved_at")
    if saved_at is None or time.time() - saved_at > STATUS_CACHE_TTL:
        return None
    return data


def parseCityProduction(html, typeGood):
    """Read the hourly wood and luxury production out of an already fetched city page."""
    resource_search_pool = {
        1: "js_GlobalMenu_production_wine",
        2: "js_GlobalMenu_production_marble",
        3: "js_GlobalMenu_production_crystal",
        4: "js_GlobalMenu_production_sulfur",
    }
    production_pattern = r'<td id="{}"[^>]*>\s*([\d.,\s\xa0]+)\s*</td>'

    wood_match = re.search(
        production_pattern.format("js_GlobalMenu_resourceProduction"), html
    )
    luxury_match = re.search(
        production_pattern.format(resource_search_pool[typeGood]), html
    )
    if not wood_match or not luxury_match:
        return None, None
    wood = re.sub(r"[^\d]", "", wood_match.group(1))
    good = re.sub(r"[^\d]", "", luxury_match.group(1))
    if wood == "" or good == "":
        return None, None
    return int(wood), int(good)


def formatBuildingLevel(level, isBusy):
    """Render a building level, appending a '+' while it is being upgraded."""
    lvl = str(level)
    if level < 10:
        lvl = " " + lvl
    if isBusy:
        lvl += "+"
    return lvl


def displayBuildingTable(city_names, ids, city_buildings):
    """Print the building levels as a table, one column per city."""
    by_name = {}
    for cid in ids:
        for (name, level, isBusy, pos) in city_buildings[cid]:
            by_name.setdefault(name, {}).setdefault(cid, []).append((level, isBusy, pos))
    building_names = sorted(by_name)
    if not building_names:
        print("No buildings found.")
        return

    cells = {}
    for name in building_names:
        name_cells = {}
        for cid in ids:
            entries = by_name[name].get(cid)
            if entries is None:
                name_cells[cid] = "-"
            else:
                name_cells[cid] = ",".join(
                    formatBuildingLevel(level, isBusy).strip()
                    for (level, isBusy, pos) in entries
                )
        cells[name] = name_cells

    def city_width(cid):
        width = max(15, len(city_names[cid]))
        for name in building_names:
            width = max(width, len(cells[name][cid]))
        return width

    widths = {cid: city_width(cid) for cid in ids}

    try:
        term_width = os.get_terminal_size().columns
    except OSError:
        term_width = 120

    available = term_width - 26
    pages = []
    page = []
    used = 0
    for cid in ids:
        width = widths[cid] + 1
        if page and used + width > available:
            pages.append(page)
            page = []
            used = 0
        page.append(cid)
        used += width
    if page:
        pages.append(page)

    for idx, page in enumerate(pages, 1):
        col_width = max(widths[cid] for cid in page)
        header = "{:<25}".format("Building")
        for cid in page:
            header += "|{:>{}}".format(city_names[cid], col_width)
        print(header)
        print("-" * len(header))

        for name in building_names:
            row = "{:<25}".format(decodeUnicodeEscape(name)[:25])
            for cid in page:
                row += "|{:>{}}".format(cells[name][cid], col_width)
            print(row)

        print("-" * len(header))

        if idx < len(pages):
            print(
                "\n({}/{}) Building levels - press Enter to continue".format(
                    idx, len(pages)
                )
            )
            enter()


def displayBuildingList(city_names, ids, city_buildings, city_tradegoods):
    """List every city's buildings in board order, tagged with their slot position."""
    any_data = False
    for cid in ids:
        buildings = city_buildings[cid]
        if not buildings:
            continue
        any_data = True
        abbr = resources_abbr.get(str(city_tradegoods[cid]), "")
        print("{} {}:".format(city_names[cid], abbr))
        for (name, level, isBusy, pos) in buildings:
            print(
                "  {}: Lv{} (pos {})".format(
                    decodeUnicodeEscape(name),
                    formatBuildingLevel(level, isBusy).strip(),
                    pos + 1,
                )
            )
        print()

    if not any_data:
        print("No buildings found.")


def showCityDetail(city, city_header, wood_prod, good_prod, color_arr):
    banner()
    typeGood = int(city_header["producedTradegood"])
    print("\033[1m{}{}{}".format(color_arr[typeGood], city["cityName"], color_arr[0]))

    resources = city["availableResources"]
    storageCapacity = city["storageCapacity"]
    citizens = city["freeCitizens"]
    housing_space = int(city_header["currentResources"]["population"])

    color_resources = []
    for i in range(len(materials_names)):
        if resources[i] == storageCapacity:
            color_resources.append(bcolors.RED)
        else:
            color_resources.append(bcolors.ENDC)

    print("Population:")
    print(
        "{}: {} {}: {}".format(
            "Housing space",
            addThousandSeparator(housing_space),
            "Citizens",
            addThousandSeparator(citizens),
        )
    )
    print("Storage: {}".format(addThousandSeparator(storageCapacity)))
    print("Resources:")
    for i in range(len(materials_names)):
        print(
            "{} {}{}{} ".format(
                materials_names[i],
                color_resources[i],
                addThousandSeparator(resources[i]),
                bcolors.ENDC,
            ),
            end="",
        )
    print("")

    print("Production:")
    print(
        "{}: {} {}: {}".format(
            materials_names[0],
            addThousandSeparator(wood_prod),
            materials_names[typeGood],
            addThousandSeparator(good_prod),
        )
    )

    hasTavern = "tavern" in [building["building"] for building in city["position"]]
    if hasTavern:
        consumption_per_hour = city["wineConsumptionPerHour"]
        if consumption_per_hour == 0:
            print(
                "{}{}Does not consume wine!{}".format(
                    bcolors.RED, bcolors.BOLD, bcolors.ENDC
                )
            )
        else:
            if typeGood == 1 and good_prod > consumption_per_hour:
                elapsed_time_run_out = "∞"
            else:
                consumption_per_second = Decimal(consumption_per_hour) / Decimal(3600)
                remaining_resources_to_consume = Decimal(resources[1]) / Decimal(
                    consumption_per_second
                )
                elapsed_time_run_out = daysHoursMinutes(remaining_resources_to_consume)
            print("There is wine for: {}".format(elapsed_time_run_out))

    for building in city["position"]:
        if building["name"] in ("", "empty"):
            continue
        if building["isMaxLevel"] is True:
            color = bcolors.BLACK
        elif building["canUpgrade"] is True:
            color = bcolors.GREEN
        else:
            color = bcolors.RED

        print(
            "lv:{}\t{}{}{}".format(
                formatBuildingLevel(building["level"], building["isBusy"]),
                color,
                decodeUnicodeEscape(building["name"]),
                bcolors.ENDC,
            )
        )


def displayAccountSummary(data):
    """Print the account overview, telling the user how old the numbers are."""
    saved_at = data.get("saved_at")
    if saved_at is not None:
        age = int(time.time() - saved_at)
        print(
            "Read at {} ({} ago)".format(
                time.strftime("%H:%M", time.localtime(saved_at)),
                daysHoursMinutes(age) if age >= 60 else "{}s".format(age),
            )
        )
    print(
        "Ships {:d}/{:d}".format(int(data["available_ships"]), int(data["total_ships"]))
    )
    if data.get("pirate_points") is not None:
        print(data["pirate_points"])
    print("\nTotal:")
    print("{:>10}".format(" "), end="|")
    for i in range(len(materials_names)):
        print("{:>12}".format(materials_names_english[i]), end="|")
    print()
    print("{:>10}".format("Available"), end="|")
    for i in range(len(materials_names)):
        print("{:>12}".format(addThousandSeparator(data["total_resources"][i])), end="|")
    print()
    print("{:>10}".format("Production"), end="|")
    for i in range(len(materials_names)):
        print("{:>12}".format(addThousandSeparator(data["total_production"][i])), end="|")
    print()
    print()
    print(
        "Housing Space: {}, Citizens: {}".format(
            addThousandSeparator(data["total_housing_space"]),
            addThousandSeparator(data["total_citizens"]),
        )
    )
    print(
        "Gold: {}, Gold production: {}".format(
            addThousandSeparator(data["total_gold"]),
            addThousandSeparator(data["total_gold_production"]),
        )
    )
    print(
        "Wine consumption: {}".format(
            addThousandSeparator(data["total_wine_consumption"])
        )
    )


def collectData(session):
    """Scan the account: two requests per city, everything else parsed from them."""
    (ids, __) = getIdsOfCities(session)
    total_resources = [0] * len(materials_names)
    total_production = [0] * len(materials_names)
    total_wine_consumption = 0
    total_housing_space = 0
    total_citizens = 0
    available_ships = 0
    total_ships = 0
    total_gold = 0
    total_gold_production = 0
    pirate_city_id = None

    city_info = {}
    city_headers = {}
    city_names = {}
    city_tradegoods = {}
    city_productions = {}
    city_buildings = {}

    for id in ids:
        html = session.get(city_url + str(id))
        data = session.get("view=updateGlobalData&ajax=1", noIndex=True)
        wait(0.5)
        json_data = json.loads(data, strict=False)
        json_data = json_data[0][1]["headerData"]
        if json_data["relatedCity"]["owncity"] != 1:
            continue

        cid = str(id)
        city = getCity(html)
        typeGood = int(json_data["producedTradegood"])
        city_info[cid] = city
        city_headers[cid] = json_data
        city_names[cid] = city["cityName"]
        city_tradegoods[cid] = json_data["producedTradegood"]
        city_productions[cid] = parseCityProduction(html, typeGood)
        city_buildings[cid] = [
            (
                building["name"],
                building["level"],
                building["isBusy"],
                building["position"],
            )
            for building in city["position"]
            if building["name"] not in ("", "empty")
        ]

        if pirate_city_id is None:
            for building in city["position"]:
                if building["building"] == "pirateFortress":
                    pirate_city_id = cid
                    break

        total_production[0] += int(Decimal(json_data["resourceProduction"]) * 3600)
        total_production[typeGood] += int(
            Decimal(json_data["tradegoodProduction"]) * 3600
        )
        total_wine_consumption += json_data["wineSpendings"]
        total_housing_space += int(json_data["currentResources"]["population"])
        total_citizens += int(json_data["currentResources"]["citizens"])
        total_resources[0] += json_data["currentResources"]["resource"]
        total_resources[1] += json_data["currentResources"]["1"]
        total_resources[2] += json_data["currentResources"]["2"]
        total_resources[3] += json_data["currentResources"]["3"]
        total_resources[4] += json_data["currentResources"]["4"]
        available_ships = json_data["freeTransporters"]
        total_ships = json_data["maxTransporters"]
        total_gold = int(Decimal(json_data["gold"]))
        # godGoldResult and badTaxAccountant are not sent by every server
        total_gold_production = int(
            Decimal(
                json_data["income"]
                + json_data.get("godGoldResult", 0)
                + json_data.get("badTaxAccountant", 0)
                + json_data["upkeep"]
                + json_data["scientistsUpkeep"]
            )
        )

    pirate_points = None
    if pirate_city_id is not None:
        try:
            points = getPirateFortressPoints(session, int(pirate_city_id))
        except Exception:
            points = None
        if points is not None:
            (capture_points, crew_points) = points
            pirate_points = "Pirate fortress: {} capture points, {} crew strength".format(
                addThousandSeparator(capture_points),
                addThousandSeparator(crew_points),
            )

    return {
        "own_ids": [cid for cid in city_info],
        "city_info": city_info,
        "city_headers": city_headers,
        "city_names": city_names,
        "city_tradegoods": city_tradegoods,
        "city_productions": city_productions,
        "city_buildings": city_buildings,
        "total_resources": total_resources,
        "total_production": total_production,
        "total_wine_consumption": total_wine_consumption,
        "total_housing_space": total_housing_space,
        "total_citizens": total_citizens,
        "available_ships": available_ships,
        "total_ships": total_ships,
        "total_gold": total_gold,
        "total_gold_production": total_gold_production,
        "pirate_points": pirate_points,
    }


def cityProduction(data, cid):
    """Hourly wood and luxury production, falling back to the header values."""
    wood_prod, good_prod = data["city_productions"][cid]
    if wood_prod is None or good_prod is None:
        header = data["city_headers"][cid]
        wood_prod = int(Decimal(header["resourceProduction"]) * 3600)
        good_prod = int(Decimal(header["tradegoodProduction"]) * 3600)
    return wood_prod, good_prod


def buildingLevelsMenu(data, own_ids):
    table_format = True
    while True:
        banner()
        print("Building levels\n")
        if table_format:
            displayBuildingTable(data["city_names"], own_ids, data["city_buildings"])
        else:
            displayBuildingList(
                data["city_names"],
                own_ids,
                data["city_buildings"],
                data["city_tradegoods"],
            )
        print()
        print("(0) Back")
        print("(1) View as table")
        print("(2) View as list")
        print()
        selected = read(min=0, max=2, digit=True)
        if selected is None or selected == 0:
            return
        table_format = selected == 1


def cityDetailsMenu(data, own_ids, color_arr):
    while True:
        banner()
        print("City details\n")
        print("(0) Back")
        for i, cid in enumerate(own_ids):
            print(
                "({}) {} {}".format(
                    i + 1,
                    data["city_names"][cid],
                    resources_abbr.get(str(data["city_tradegoods"][cid]), ""),
                )
            )
        print()
        selected = read(min=0, max=len(own_ids), digit=True)
        if selected is None or selected == 0:
            return
        cid = own_ids[selected - 1]
        wood_prod, good_prod = cityProduction(data, cid)
        showCityDetail(
            data["city_info"][cid],
            data["city_headers"][cid],
            wood_prod,
            good_prod,
            color_arr,
        )
        enter()
        print("")


def getStatus(session, event, stdin_fd, predetermined_input):
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
        banner()
        color_arr = [
            bcolors.ENDC,
            bcolors.HEADER,
            bcolors.STONE,
            bcolors.BLUE,
            bcolors.WARNING,
        ]

        data = loadCache(session)
        if data is None:
            print("Reading the account...")
            data = collectData(session)
            saveCache(session, data)

        own_ids = data["own_ids"]
        if not own_ids:
            return

        while True:
            banner()
            displayAccountSummary(data)
            print()
            print("(0) Exit")
            print("(1) Building levels")
            print("(2) City details")
            print("(3) Refresh data")
            print()
            selected = read(min=0, max=3, digit=True)

            if selected is None or selected == 0:
                return
            if selected == 1:
                buildingLevelsMenu(data, own_ids)
            elif selected == 2:
                cityDetailsMenu(data, own_ids, color_arr)
            elif selected == 3:
                banner()
                print("Refreshing data...")
                data = collectData(session)
                saveCache(session, data)
                own_ids = data["own_ids"]
                if not own_ids:
                    return
    except KeyboardInterrupt:
        return
    finally:
        event.set()
