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


def chooseIslands(islands):
    """Pick one or more miracles. Returns list[dict] (empty if cancelled).

    Same shape as option 5's god selection: pick one at a time, 0 to finish.

    Parameters
    ----------
    islands : list[dict]

    Returns
    -------
    list[dict]
    """
    sorted_islands = sorted(
        islands, key=lambda x: (-x["wonderActivationLevel"], x["wonderName"])
    )
    chosen = []

    while True:
        banner()
        print("Which miracle(s) do you want to activate?")
        if chosen:
            print("Selected: " + ", ".join(i["wonderName"] for i in chosen))
        print("")
        print("(0) Continue" if chosen else "(0) Exit")
        for i, island in enumerate(sorted_islands, start=1):
            mark = "*" if island in chosen else " "
            if island["available"]:
                print("({:d}){} {} (level {})".format(
                    i, mark, island["wonderName"], island["wonderActivationLevel"]))
            else:
                print("({:d}){} {} (level {}) (available in: {})".format(
                    i, mark, island["wonderName"], island["wonderActivationLevel"],
                    daysHoursMinutes(island["available_in"])))

        index = read(min=0, max=len(sorted_islands))
        if index == 0:
            return chosen
        island = sorted_islands[index - 1]
        # Selecting an already-selected miracle removes it, so a misclick is
        # recoverable without restarting the module.
        if island in chosen:
            chosen.remove(island)
        else:
            chosen.append(island)


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
        saved_wonders = []
        if saved:
            try:
                _saved_iterations = int(saved["iterations"])
                assert _saved_iterations >= 1
                # "wonders" is the current schema; "wonder" is the pre-1.7.6
                # single-miracle key, still honoured so old prefs keep working.
                if saved.get("wonders"):
                    saved_wonders = [str(w) for w in saved["wonders"]]
                elif saved.get("wonder") is not None:
                    saved_wonders = [str(saved["wonder"])]
                assert saved_wonders
                # Old prefs stored the singular "wonderName"; without this the
                # summary would show a bare wonder id instead of its name.
                _names = saved.get("wonderNames")
                if not _names and saved.get("wonderName"):
                    _names = [saved["wonderName"]]
                _names = _names or saved_wonders
                use_saved = prompt_use_saved(
                    session,
                    _MODULE,
                    [
                        "Miracles:    " + ", ".join(str(n) for n in _names),
                        f"Activations: {_saved_iterations} each",
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

        selected = []
        if use_saved:
            # Match on wonder id, not list position: the list is sorted by level
            # then name, so a saved index would drift as levels change.
            selected = [i for i in islands if str(i.get("wonder")) in saved_wonders]
            missing = len(saved_wonders) - len(selected)
            if not selected:
                print("The saved miracles are no longer available; please choose again.")
                use_saved = False
            elif missing > 0:
                print(f"{missing} saved miracle(s) are no longer available; continuing with the rest.")

        if not selected:
            selected = chooseIslands(islands)
        if not selected:
            event.set()
            return

        if use_saved:
            iterations = int(saved["iterations"])
        else:
            banner()
            print("Selected miracles:\n")
            for isl in selected:
                if isl["available"]:
                    print("  - {} (level {}) - available now".format(
                        isl["wonderName"], isl["wonderActivationLevel"]))
                else:
                    print("  - {} (level {}) - available in {}".format(
                        isl["wonderName"], isl["wonderActivationLevel"],
                        daysHoursMinutes(isl["available_in"])))

            print("\nHow many times should each miracle be activated?")
            iterations = read(msg="Activations each: ", digit=True, min=1)

            print("\nProceed? [Y/n]")
            if read(values=["y", "Y", "n", "N", ""]).lower() == "n":
                event.set()
                return

            # Store wonder ids (stable) rather than menu positions.
            save_prefs(
                session,
                _MODULE,
                {
                    "wonders": [isl.get("wonder") for isl in selected],
                    "wonderNames": [isl.get("wonderName") for isl in selected],
                    "iterations": int(iterations),
                },
            )
    except KeyboardInterrupt:
        event.set()
        return

    set_child_mode(session)
    event.set()

    info = "\nI activate the miracle(s) {} {:d} times each\n".format(
        ", ".join(isl["wonderName"] for isl in selected), iterations
    )
    setInfoSignal(session, info)
    try:
        do_it(session, selected, iterations)
    except Exception as e:
        msg = "Error in:\n{}\nCause:\n{}".format(info, traceback.format_exc())
        sendToBot(session, msg)
    finally:
        session.logout()


def miracle_status(session, island):
    """Return (available, wait_seconds) for this island's miracle.

    Non-blocking, unlike wait_for_miracle: the scheduler in do_it() needs to
    compare several miracles before deciding which to sleep for.
    """
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
            return False, int(float(enddate)) - int(float(currentdate))

    available = temple_response["js_WonderViewButton"]["buttonState"] == "enabled"
    # No countdown and not enabled: poll again shortly rather than guessing.
    return (True, 0) if available else (False, 60)


def wait_for_miracle(session, island):
    """Block until this island's miracle can be activated.

    Parameters
    ----------
    session : ikabot.web.session.Session
    island : dict
    """
    while True:
        available, wait_time = miracle_status(session, island)
        if available:
            return
        next_activation_time = time.time() + wait_time
        session.setStatus(
            f"Miracle {island['wonderName']} is activated. Available at: {getDateTime(next_activation_time)}"
        )
        msg = "I wait {:d} seconds to activate the miracle {}".format(
            wait_time, island["wonderName"]
        )
        sendToBotDebug(session, msg, debugON_activateMiracle)
        wait(wait_time + 5)


def do_it(session, islands, iterations):
    """Activate each of `islands` `iterations` times.

    Miracles run concurrently: each has its own cooldown, so the scheduler
    sleeps only until the *next* one becomes ready rather than blocking on one
    island while another is already available.

    Parameters
    ----------
    session : ikabot.web.session.Session
    islands : list[dict]   (a single dict is accepted for backwards compatibility)
    iterations : int
    """
    if isinstance(islands, dict):
        islands = [islands]

    pending = [{"island": isl, "left": int(iterations)} for isl in islands]
    names = ", ".join(p["island"]["wonderName"] for p in pending)
    session.setStatus(f"Waiting to activate {names}...")

    while any(p["left"] > 0 for p in pending):
        soonest = None
        for entry in list(pending):
            if entry["left"] <= 0:
                continue
            island = entry["island"]
            try:
                available, wait_time = miracle_status(session, island)
            except Exception:
                # One unreadable temple must not kill the whole schedule.
                sendToBotDebug(
                    session,
                    "Could not read temple for {}".format(island["wonderName"]),
                    debugON_activateMiracle,
                )
                soonest = 60 if soonest is None else min(soonest, 60)
                continue

            if not available:
                soonest = wait_time if soonest is None else min(soonest, wait_time)
                continue

            response = activateMiracleHttpCall(session, island)
            if response[1][1][0] == "error":
                # Drop just this miracle; the others carry on.
                msg = "The miracle {} could not be activated. Skipping it.".format(
                    island["wonderName"]
                )
                sendToBot(session, msg)
                entry["left"] = 0
                continue

            entry["left"] -= 1
            sendToBotDebug(
                session,
                "Miracle {} successfully activated".format(island["wonderName"]),
                debugON_activateMiracle,
            )

        remaining = [p for p in pending if p["left"] > 0]
        if not remaining:
            break

        session.setStatus(
            "Activating "
            + ", ".join(f"{p['island']['wonderName']} x{p['left']}" for p in remaining)
        )
        # soonest is None when everything was just activated; re-poll shortly to
        # pick up the fresh countdowns.
        wait((soonest if soonest is not None else 60) + 5)
