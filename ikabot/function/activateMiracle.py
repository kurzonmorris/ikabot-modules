#! /usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import re
import traceback

from ikabot.config import *
from ikabot.helpers.botComm import *
from ikabot.helpers.getJson import getCity
from ikabot.helpers.gui import *
from ikabot.helpers.modulePrefs import load_prefs, prompt_use_saved, save_prefs
from ikabot.helpers.pedirInfo import *
from ikabot.helpers.process import set_child_mode
from ikabot.helpers.signals import setInfoSignal
from ikabot.helpers.varios import *


MIRACLE_CACHE_KEY = "miracleCache"


def _query_temple(session, city_id, position):
    """Sends the temple view request for a single city and returns the live
    wonder data (level, whether it can be activated now, and the remaining
    cooldown in seconds).

    Parameters
    ----------
    session : ikabot.web.session.Session
    city_id : int | str
    position : str
        the building position of the temple within the city

    Returns
    -------
    (level, available, available_in) : tuple[int, bool, int | None]
        ``available_in`` is ``None`` when the miracle can be activated now.
    """
    params = {
        "view": "temple",
        "cityId": city_id,
        "position": position,
        "backgroundView": "city",
        "currentCityId": city_id,
        "actionRequest": actionRequest,
        "ajax": "1",
    }
    data = session.post(params=params)
    data = json.loads(data, strict=False)
    html = data[1][1][1]
    match = re.search(r'<div id="wonderLevelDisplay"[^>]*>\s*(\d+)\s*</div>', html)
    level = int(match.group(1)) if match else 0

    data = data[2][1]
    available = data["js_WonderViewButton"]["buttonState"] == "enabled"
    available_in = None
    if available is False:
        for elem in data:
            if "countdown" in data[elem]:
                enddate = data[elem]["countdown"]["enddate"]
                currentdate = data[elem]["countdown"]["currentdate"]
                available_in = int(float(enddate)) - int(float(currentdate))
                break
    return level, available, available_in


def _load_miracle_cache(session):
    """Return the saved list of activable wonders for this account, or None."""
    try:
        data = session.getSessionData()
        cache = data.get(MIRACLE_CACHE_KEY)
        if cache:
            return cache
    except Exception:
        pass
    return None


def _save_miracle_cache(session, islands):
    """Persist the static structure (which city/temple holds each wonder) so a
    future run can skip the city/island discovery entirely.  Only the parts
    that don't change run-to-run are stored — live data (level, availability,
    cooldown) is always re-queried from the temple.
    """
    cache = [
        {
            "id": island["id"],
            "wonder": island["wonder"],
            "wonderName": island["wonderName"],
            "ciudad": {
                "id": island["ciudad"]["id"],
                "pos": island["ciudad"]["pos"],
            },
        }
        for island in islands
    ]
    try:
        data = session.getSessionData()
        data[MIRACLE_CACHE_KEY] = cache
        session.setSessionData(data)
    except Exception:
        pass  # caching is best-effort; never block the activation flow


def obtainMiraclesFromCache(session, cache):
    """Rebuild the activable-islands list from the saved structure, refreshing
    only the live wonder data with one temple request per cached wonder.

    Returns
    -------
    islands : list[dict] | None
        ``None`` if any cached entry is no longer valid (city/temple changed),
        signalling the caller to fall back to a full re-scan.
    """
    islands = []
    for entry in cache:
        try:
            city_id = entry["ciudad"]["id"]
            position = entry["ciudad"]["pos"]
            level, available, available_in = _query_temple(session, city_id, position)
        except Exception:
            return None  # stale cache — force a full re-scan

        island = {
            "id": entry["id"],
            "wonder": entry["wonder"],
            "wonderName": entry["wonderName"],
            "ciudad": {"id": city_id, "pos": position},
            "activable": True,
            "wonderActivationLevel": level,
            "available": available,
        }
        if available is False:
            island["available_in"] = available_in
        islands.append(island)
    return islands


def obtainMiraclesAvailable(session):
    """
    Parameters
    ----------
    session : ikabot.web.session.Session

    Returns
    -------
    islands: list[dict]
    """
    ids, cities = getIdsOfCities(session)

    # Fetch each city once (the previous implementation fetched every city
    # twice — once to find island ids, once to find temples).  The full city
    # object already carries its islandId, so a single pass is enough.
    city_objs = {}
    island_ids = set()
    for city_id in ids:
        html = session.get(city_url + str(city_id))
        city = getCity(html)
        city_objs[city_id] = city
        island_ids.add(city["islandId"])

    # Fetch each island once.
    islands = []
    for island_id in island_ids:
        html = session.get(island_url + island_id)
        island = getIsland(html)
        island["activable"] = False
        islands.append(island)

    islands_by_id = {island["id"]: island for island in islands}
    activable_wonders = set()
    for city_id in ids:
        city = city_objs[city_id]
        island = islands_by_id.get(city["islandId"])
        if island is None:
            continue
        # Each wonder type is listed once (miracle cooldowns are per-wonder).
        if island["wonder"] in activable_wonders:
            continue

        # make sure that the city has a temple
        for i in range(len(city["position"])):
            if city["position"][i]["building"] == "temple":
                city["pos"] = str(i)
                break
        else:
            continue

        level, available, available_in = _query_temple(session, city["id"], city["pos"])

        island["activable"] = True
        island["ciudad"] = city
        island["wonderActivationLevel"] = level
        island["available"] = available
        if available is False:
            island["available_in"] = available_in
        activable_wonders.add(island["wonder"])

    # only return island which wonder we can activate
    return [island for island in islands if island["activable"]]


def activateMiracleHttpCall(session, island):
    """
    Parameters
    ----------
    session : ikabot.web.session.Session
    island : dict

    Returns
    -------
    json : dict
    """
    params = {
        "action": "CityScreen",
        "cityId": island["ciudad"]["id"],
        "function": "activateWonder",
        "position": island["ciudad"]["pos"],
        "backgroundView": "city",
        "currentCityId": island["ciudad"]["id"],
        "templateView": "temple",
        "actionRequest": actionRequest,
        "ajax": "1",
    }
    response = session.post(params=params)
    return json.loads(response, strict=False)


def chooseIsland(islands):
    """
    Parameters
    ----------
    islands : list[dict]

    Returns
    -------
    island : dict
    """
    print("Which miracle do you want to activate?")
    # Sort islands by level descending, then by name
    sorted_islands = sorted(islands, key=lambda x: (-x["wonderActivationLevel"], x["wonderName"]))
    i = 0
    print("(0) Exit")
    for island in sorted_islands:
        i += 1
        if island["available"]:
            print("({:d}) {} (level {})".format(i, island["wonderName"], island["wonderActivationLevel"]))
        else:
            print(
                "({:d}) {} (level {}) (available in: {})".format(
                    i, island["wonderName"], island["wonderActivationLevel"], daysHoursMinutes(island["available_in"])
                )
            )

    index = read(min=0, max=i)
    if index == 0:
        return None
    island = sorted_islands[index - 1]
    return island


def activateMiracle(session, event, stdin_fd, predetermined_input):
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

        # --- module memory: reuse the previous run's answers if the user wants ---
        _MODULE = "activateMiracle"
        saved = load_prefs(session, _MODULE)
        use_saved = False
        if saved:
            try:
                _saved_iterations = int(saved["iterations"])
                assert saved.get("wonder") is not None
                assert _saved_iterations >= 1
                use_saved = prompt_use_saved(
                    session,
                    _MODULE,
                    [
                        "Miracle:     " + str(saved.get("wonderName", saved["wonder"])),
                        f"Activations: {_saved_iterations}",
                    ],
                )
            except Exception:
                use_saved = False

        cache = _load_miracle_cache(session)
        use_cache = bool(cache)
        # Only offer the re-scan choice when a human is at the keyboard and is
        # not replaying saved settings.  Under sequenceRunner
        # (predetermined_input populated) we silently use the saved list so the
        # recorded keystrokes stay aligned.
        if cache and not config.predetermined_input and not use_saved:
            print("A saved temple list exists for this account.")
            print("(1) Use saved list (fast)")
            print("(2) Full re-scan (slower; picks up new or changed temples)")
            use_cache = read(min=1, max=2) == 1

        islands = None
        if use_cache and cache:
            islands = obtainMiraclesFromCache(session, cache)

        if islands is None:
            islands = obtainMiraclesAvailable(session)
            _save_miracle_cache(session, islands)

        if islands == []:
            print("There are no miracles available.")
            enter()
            event.set()
            return

        island = None
        if use_saved:
            # Match on the wonder id, not a list position: chooseIsland sorts by
            # level then name, so a saved index would point at a different
            # miracle as soon as a wonder level changes.
            island = next(
                (i for i in islands if str(i.get("wonder")) == str(saved["wonder"])),
                None,
            )
            if island is None:
                print("The saved miracle is no longer available; please choose again.")
                use_saved = False

        if island is None:
            island = chooseIsland(islands)
        if island is None:
            event.set()
            return

        if island["available"]:
            print("\nThe miracle {} (level {}) will be activated".format(island["wonderName"], island["wonderActivationLevel"]))
            if not use_saved:
                print("Proceed? [Y/n]")
                activate_miracle_input = read(values=["y", "Y", "n", "N", ""])
                if activate_miracle_input.lower() == "n":
                    event.set()
                    return

            miracle_activation_result = activateMiracleHttpCall(session, island)

            if miracle_activation_result[1][1][0] == "error":
                print(
                    "The miracle {} could not be activated.".format(
                        island["wonderName"]
                    )
                )
                enter()
                event.set()
                return

            data = miracle_activation_result[2][1]
            for elem in data:
                if "countdown" in data[elem]:
                    enddate = data[elem]["countdown"]["enddate"]
                    currentdate = data[elem]["countdown"]["currentdate"]
                    break
            wait_time = int(float(enddate)) - int(float(currentdate))

            print("The miracle {} was activated.".format(island["wonderName"]))
            if use_saved:
                iterations = int(saved["iterations"])
            else:
                enter()
                banner()

                while True:
                    print("Do you wish to activate it again when it is finished? [y/N]")

                    reactivate_again_input = read(values=["y", "Y", "n", "N", ""])
                    if reactivate_again_input.lower() != "y":
                        event.set()
                        return

                    iterations = read(msg="How many times?: ", digit=True, min=0)

                    if iterations == 0:
                        event.set()
                        return

                    duration = wait_time * iterations

                    print("It will finish in:{}".format(daysHoursMinutes(duration)))

                    print("Proceed? [Y/n]")
                    reactivate_again_input = read(values=["y", "Y", "n", "N", ""])
                    if reactivate_again_input.lower() == "n":
                        banner()
                        continue
                    break
        else:
            print(
                "\nThe miracle {} will be activated in {}".format(
                    island["wonderName"], daysHoursMinutes(island["available_in"])
                )
            )
            if not use_saved:
                print("Proceed? [Y/n]")
                user_confirm = read(values=["y", "Y", "n", "N", ""])
                if user_confirm.lower() == "n":
                    event.set()
                    return
            wait_time = island["available_in"]
            iterations = 1

            print("\nThe mirable will be activated.")
            if use_saved:
                iterations = int(saved["iterations"])
            else:
                enter()
                banner()

                while True:
                    print("Do you wish to activate it again when it is finished? [y/N]")

                    reactivate_again_input = read(values=["y", "Y", "n", "N", ""])
                    again = reactivate_again_input.lower() == "y"
                    if again is True:
                        try:
                            iterations = read(msg="How many times?: ", digit=True, min=0)
                        except KeyboardInterrupt:
                            iterations = 1
                            break

                        if iterations == 0:
                            iterations = 1
                            break

                        iterations += 1
                        duration = wait_time * iterations
                        print("It is not possible to calculate the time of finalization. (at least: {})".format(daysHoursMinutes(duration)))
                        print("Proceed? [Y/n]")

                        try:
                            activate_input = read(values=["y", "Y", "n", "N", ""])
                        except KeyboardInterrupt:
                            iterations = 1
                            break

                        if activate_input.lower() == "n":
                            iterations = 1
                            banner()
                            continue
                    break

        if not use_saved:
            # Store the wonder id (stable) rather than the menu position.
            save_prefs(
                session,
                _MODULE,
                {
                    "wonder": island.get("wonder"),
                    "wonderName": island.get("wonderName"),
                    "iterations": int(iterations),
                },
            )
    except KeyboardInterrupt:
        event.set()
        return

    set_child_mode(session)
    event.set()

    info = "\nI activate the miracle {} {:d} times\n".format(
        island["wonderName"], iterations
    )
    setInfoSignal(session, info)
    try:
        do_it(session, island, iterations)
    except Exception as e:
        msg = "Error in:\n{}\nCause:\n{}".format(info, traceback.format_exc())
        sendToBot(session, msg)
    finally:
        session.logout()


def wait_for_miracle(session, island):
    """
    Parameters
    ----------
    session : ikabot.web.session.Session
    island : dict
    """
    while True:
        params = {
            "view": "temple",
            "cityId": island["ciudad"]["id"],
            "position": island["ciudad"]["pos"],
            "backgroundView": "city",
            "currentCityId": island["ciudad"]["id"],
            "actionRequest": actionRequest,
            "ajax": "1",
        }
        temple_response = session.post(params=params)
        temple_response = json.loads(temple_response, strict=False)
        temple_response = temple_response[2][1]

        for elem in temple_response:
            if "countdown" in temple_response[elem]:
                enddate = temple_response[elem]["countdown"]["enddate"]
                currentdate = temple_response[elem]["countdown"]["currentdate"]
                wait_time = int(float(enddate)) - int(float(currentdate))
                next_activation_time = time.time() + wait_time
                session.setStatus(
                    f"Miracle {island['wonderName']} is activated. Available at: {getDateTime(next_activation_time)}"
                )
                break
        else:
            available = (
                temple_response["js_WonderViewButton"]["buttonState"] == "enabled"
            )
            if available:
                return
            else:
                wait_time = 60

        msg = "I wait {:d} seconds to activate the miracle {}".format(
            wait_time, island["wonderName"]
        )
        sendToBotDebug(session, msg, debugON_activateMiracle)
        wait(wait_time + 5)


def do_it(session, island, iterations):
    """
    Parameters
    ----------
    session : ikabot.web.session.Session
    island : dict
    iterations : int
    """
    iterations_left = iterations
    session.setStatus(f"Waiting to activate {island['wonderName']}...")
    for i in range(iterations):

        wait_for_miracle(session, island)

        response = activateMiracleHttpCall(session, island)

        if response[1][1][0] == "error":
            msg = "The miracle {} could not be activated.".format(
                island["wonderName"]
            )
            sendToBot(session, msg)
            return
        iterations_left -= 1
        session.setStatus(
            f"Activated {island['wonderName']} @{getDateTime()}, iterations left: {iterations_left}"
        )
        msg = "Miracle {} successfully activated".format(island["wonderName"])
        sendToBotDebug(session, msg, debugON_activateMiracle)
