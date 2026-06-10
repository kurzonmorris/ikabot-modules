#! /usr/bin/env python3
# -*- coding: utf-8 -*-


import json
import re

from ikabot.config import *
from ikabot.helpers.gui import *
from ikabot.helpers.pedirInfo import *
from ikabot.helpers.varios import addThousandSeparator


def parseBuildingCosts(html_block):
    costs = [0] * len(materials_names)
    resource_map = {
        "wood": 0,
        "wine": 1,
        "marble": 2,
        "crystal": 3,
        "glass": 3,
        "sulfur": 4,
    }
    matches = re.findall(
        r'<li\s+class="(\w+)[^"]*"[^>]*\s+title="[^"]*?:\s*([\d\s\xa0\.]+)"', html_block
    )
    for res_type, amount_str in matches:
        if res_type not in resource_map:
            continue
        idx = resource_map[res_type]
        amount_str = amount_str.replace("\xa0", "").replace(" ", "").replace(".", "")
        try:
            costs[idx] = int(amount_str)
        except ValueError:
            pass
    return costs


def splitBuildingBlocks(html):
    blocks = []
    pattern = r'<li class="building (\w+)[^"]*">((?:(?!<li class="building ).)+)</li>'
    for match in re.finditer(pattern, html, re.DOTALL):
        blocks.append({"type": match.group(1), "html": match.group(2)})
    return blocks


def constructBuilding(session, event, stdin_fd, predetermined_input):
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

        print("City where to build:")
        city = chooseCity(session)
        banner()

        # list of free spaces in the selected city
        free_spaces = [
            buildings
            for buildings in city["position"]
            if buildings["building"] == "empty"
        ]

        # get a list of all the posible buildings that can be built
        buildings = []
        seen_ids = set()
        # different buildings can be built in different areas (dockyard added in 7.4.0)
        type_spaces = ["sea", "land", "shore", "wall", "dockyard"]
        for type_space in type_spaces:
            free_spaces_of_type = [
                free_space
                for free_space in free_spaces
                if free_space["type"] == type_space
            ]
            if len(free_spaces_of_type) > 0:
                # we take any space in the desired area
                free_space_of_type = free_spaces_of_type[0]
                params = {
                    "view": "buildingGround",
                    "cityId": city["id"],
                    "position": free_space_of_type["position"],
                    "backgroundView": "city",
                    "currentCityId": city["id"],
                    "actionRequest": actionRequest,
                    "ajax": "1",
                }
                buildings_response = session.post(params=params, noIndex=True)
                try:
                    parsed_response = json.loads(buildings_response, strict=False)
                    buildings_response = parsed_response[1][1]
                except Exception:
                    continue
                if buildings_response == "":
                    continue
                html = buildings_response[1]

                blocks = splitBuildingBlocks(html)
                for block in blocks:
                    btype = block["type"]
                    block_html = block["html"]
                    info_match = re.search(
                        r'<div title="(.+?)"\s*class="buildingimg .+?"\s*onclick="ajaxHandlerCall\(\'.*?buildingId=(\d+)&',
                        block_html,
                    )
                    if not info_match:
                        continue
                    building_id = info_match.group(2)
                    if building_id in seen_ids:
                        continue
                    seen_ids.add(building_id)
                    costs = parseBuildingCosts(block_html)
                    can_afford = all(
                        costs[i] <= city["availableResources"][i]
                        for i in range(len(materials_names))
                    )
                    buildings.append(
                        {
                            "building": btype,
                            "name": info_match.group(1),
                            "buildingId": building_id,
                            "type": type_space,
                            "costs": costs,
                            "canAfford": can_afford,
                        }
                    )

        if len(buildings) == 0:
            print("No building can be built.")
            enter()
            event.set()
            return

        # show list of buildings with color coding (green = can afford, red = cannot)
        while True:
            banner()
            print("What building do you want to build?\n")
            i = 0
            for building in buildings:
                i += 1
                color = bcolors.GREEN if building["canAfford"] else bcolors.RED
                print("({:d}) {}{}{}".format(i, color, building["name"], bcolors.ENDC))
            print(
                "\n{}Green{} = can afford | {}Red{} = insufficient resources | (0) Exit".format(
                    bcolors.GREEN, bcolors.ENDC, bcolors.RED, bcolors.ENDC
                )
            )
            selected_building_index = read(min=0, max=i)
            if selected_building_index == 0:
                event.set()
                return
            selected_building = buildings[selected_building_index - 1]

            if not selected_building["canAfford"]:
                banner()
                print("{}Insufficient resources to build: {}{}".format(
                    bcolors.RED, selected_building["name"], bcolors.ENDC))
                print("\nMissing resources:")
                for idx, cost in enumerate(selected_building["costs"]):
                    if cost == 0:
                        continue
                    have = city["availableResources"][idx]
                    if have >= cost:
                        continue
                    missing = cost - have
                    print("- {}{}: {} {}(have: {}){}".format(
                        materials_names[idx],
                        bcolors.RED,
                        addThousandSeparator(missing),
                        bcolors.STONE,
                        addThousandSeparator(have),
                        bcolors.ENDC,
                    ))
                print("\nPress Enter to return to the menu...")
                enter()
                continue
            else:
                break
        building = selected_building

        # show posible positions for the selected building
        print("{}\n".format(building["name"]))
        options = [
            position_id
            for position_id in city["position"]
            if position_id["building"] == "empty"
            and position_id["type"] == building["type"]
        ]
        if len(options) == 1:
            option = options[0]
        else:
            print("In which position do you want to build?\n")
            i = 0
            for option in options:
                i += 1
                print("({:d}) {}".format(i, option["position"]))
            selected_building_index = read(min=1, max=i)
            option = options[selected_building_index - 1]
            banner()

        # build it
        params = {
            "action": "CityScreen",
            "function": "build",
            "cityId": city["id"],
            "position": option["position"],
            "building": building["buildingId"],
            "backgroundView": "city",
            "currentCityId": city["id"],
            "templateView": "buildingGround",
            "actionRequest": actionRequest,
            "ajax": "1",
        }
        buildings_response = session.post(params=params, noIndex=True)
        try:
            response_data = json.loads(buildings_response, strict=False)
            msg = None
            for item in response_data:
                if item[0] == 'provideFeedback':
                    for feedback in item[1]:
                        if feedback.get('text'):
                            msg = feedback['text']
                            break
                    if msg:
                        break
            if not msg:
                msg = response_data[3][1][0]["text"]
        except Exception:
            msg = "Building process completed."
        print(msg)
        enter()
        event.set()
    except KeyboardInterrupt:
        event.set()
        return
