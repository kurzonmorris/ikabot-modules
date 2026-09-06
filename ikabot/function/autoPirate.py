#! /usr/bin/env python3
# -*- coding: utf-8 -*-

import base64
import json
from contextlib import contextmanager
import re
import sys
import time
import traceback

import requests

from ikabot.config import *
from ikabot.helpers.botComm import *
from ikabot.helpers.getJson import *
from ikabot.helpers.gui import *
from ikabot.helpers.pedirInfo import *
from ikabot.helpers.process import run, set_child_mode
from ikabot.helpers.varios import timeStringToSec, wait
from ikabot.helpers.apiComm import getPiratesCaptchaSolution
from ikabot.helpers.logging import getLogger

logger = getLogger(__name__)

_LOCAL_IMPORT_ERROR = None
try:
    from ikabot.helpers.piratesDecaptcha import get_captcha_string
    LOCAL_DECAPTCHA = True
except Exception as _exc:          # keep the reason: "not installed" and
    LOCAL_DECAPTCHA = False        # "weights missing" need different fixes
    _LOCAL_IMPORT_ERROR = _exc


class PirateStageError(Exception):
    """An error tagged with the pirate stage it came from.

    Auto-Pirate used to report every failure as "Error in:" with an empty
    subject and a bare traceback, so a notification told you something broke
    but never what. Wrapping each step names the stage in the message.
    """

    def __init__(self, stage, cause):
        self.stage = stage
        self.cause = cause
        super().__init__("{}: {}: {}".format(stage, type(cause).__name__, cause))


@contextmanager
def _stage(name):
    """Tag anything raised inside with the stage name."""
    try:
        yield
    except PirateStageError:
        raise                      # keep the innermost stage, do not re-tag
    except Exception as exc:
        raise PirateStageError(name, exc) from exc


def _report_failure(session, exc, fatal=True):
    """Notify about a pirate failure, naming the stage that broke."""
    if isinstance(exc, PirateStageError):
        stage = exc.stage
        cause = "{}: {}".format(type(exc.cause).__name__, exc.cause)
    else:
        stage = "an unrecorded step"
        cause = "{}: {}".format(type(exc).__name__, exc)

    headline = "Auto-Pirate stopped." if fatal else "Auto-Pirate hit a problem."
    msg = "{}\n\nFailed while: {}\nCause: {}\n\n{}".format(
        headline, stage, cause, traceback.format_exc()
    )
    logger.error("autoPirate failed during %s: %s", stage, cause)
    sendToBot(session, msg)
    return msg


def extract_captcha_image(html):
    """Extract the pirates captcha PNG bytes from the capture response.

    The captcha image is now embedded by the game as a base64 data URI
    (js_captchaImage.src) inside the JSON response of the capture request,
    instead of being served on a separate createCaptcha endpoint.
    """
    if not isinstance(html, str):
        return None

    # Prefer parsing the JSON response, which unescapes everything for us.
    try:
        data = json.loads(html)
    except Exception:
        data = None
    if isinstance(data, list):
        for command, payload in data:
            if command == "updateTemplateData" and isinstance(payload, dict):
                image = payload.get("js_captchaImage")
                if isinstance(image, dict) and isinstance(image.get("src"), str):
                    match = re.search(
                        r"data:image/png;base64,([A-Za-z0-9+/=]+)", image["src"]
                    )
                    if match:
                        return base64.b64decode(match.group(1))

    # Fallback: parse the base64 straight out of the raw (JSON-escaped) html.
    match = re.search(r"base64,([A-Za-z0-9+/=\\\\]+)", html)
    if match:
        b64 = match.group(1).replace("\\/", "/").replace("\n", "").replace("\r", "")
        try:
            return base64.b64decode(b64)
        except Exception:
            return None
    return None


def autoPirate(session, event, stdin_fd, predetermined_input):
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
    banner()
    try:        

        if not LOCAL_DECAPTCHA:
            print("💡 TIP: You can process captchas locally! Run `pip install onnxruntime` to move inference to your client.")
            print("This feature is not available if you are using the precombile binary for Windows!\n\n")
            print("{}⚠️ USING THIS FEATURE WILL EXPOSE YOUR IP ADDRESS TO A THIRD PARTY FOR CAPTCHA SOLVING ⚠️{}\n\n".format(bcolors.WARNING, bcolors.ENDC))
        else:
            print("{}[SUCCESS]{} You are using local decaptcha!\n\n".format(bcolors.GREEN, bcolors.ENDC))
    
        print("How many pirate missions should I do? (min = 1)")
        pirateCount = read(min=1, digit=True)
        print("Should I schedule pirate missions by the time of day? (y/N)")
        scheduleInput = read(values=["y", "Y", "n", "N", ""])
        if scheduleInput.lower() == "y":
            pirateSchedule = True
            print(
                """Which pirate mission should I do at daytime? (Default mission)
        (1) 2m 30s
        (2) 7m 30s
        (3) 15m
        (4) 30m
        (5) 1h
        (6) 2h
        (7) 4h
        (8) 8h
        (9) 16h
        """
            )
            pirateMissionDayChoice = read(min=1, max=9, digit=True)
            print(
                """At which hours should I operate at daytime? (Default: 9 hours from 10 till 18)
            """
            )
            print("From: ")
            dayStart = read()
            if dayStart == "":
                dayStart = 10
            else:
                dayStart = int(dayStart)

            print("Till: ")
            dayEnd = read()
            if dayEnd == "":
                dayEnd = 18
            else:
                dayEnd = int(dayEnd)

            print(
                """Which pirate mission should I do at night time?
            (1) 2m 30s
            (2) 7m 30s
            (3) 15m
            (4) 30m
            (5) 1h
            (6) 2h
            (7) 4h
            (8) 8h
            (9) 16h
            """
            )
            pirateMissionNightChoice = read(min=1, max=9, digit=True)

            print(
                """At which hours should I operate at night time? (Default: 15 hours from 19 till 9): 
            """
            )
            print("From: ")
            nightStart = read()
            if nightStart == "":
                nightStart = 19
            else:
                nightStart = int(nightStart)

            print("Till: ")
            nightEnd = read()
            if nightEnd == "":
                nightEnd = 9
            else:
                nightEnd = int(nightEnd)
        else:
            pirateSchedule = False
            print(
                """Which pirate mission should I do?
        (1) 2m 30s
        (2) 7m 30s
        (3) 15m
        (4) 30m
        (5) 1h
        (6) 2h
        (7) 4h
        (8) 8h
        (9) 16h
        """
            )
            pirateMissionChoice = read(min=1, max=9, digit=True)
        if pirateSchedule == True:
            current_hour = int(time.strftime("%H"))
            if current_hour >= dayStart and current_hour <= dayEnd:
                pirateMissionChoice = pirateMissionDayChoice
            elif current_hour >= nightStart or current_hour <= nightEnd:
                pirateMissionChoice = pirateMissionNightChoice
            else:
                pirateMissionChoice = pirateMissionDayChoice
        print(
            "Do you want me to automatically convert capture points to crew strength? (Y|N)"
        )
        autoConvert = read(values=["y", "Y", "n", "N"])
        if autoConvert.lower() == "y":
            print(
                'How many points should I convert every time I do a mission? (Type "all" to convert all points at once)'
            )
            convertPerMission = read(min=0, additionalValues=["all"], digit=True)
        print(
            "Enter a maximum additional random waiting time between missions in seconds. (min = 0)"
        )
        maxRandomWaitingTime = read(min=0, digit=True)
        piracyCities = getPiracyCities(session, pirateMissionChoice)
        if piracyCities == []:
            print(
                "You do not have any city with a pirate fortress capable of executing this mission!"
            )
            enter()
            event.set()
            return

        print(
            "YAAAAAR!"
        )  # get data for options such as auto-convert to crew strength, time intervals, number of piracy attempts... ^^
        enter()
    except KeyboardInterrupt:
        event.set()
        return

    set_child_mode(session)
    event.set()

    # Take the ~2s solver load off the critical path of the first captcha.
    if LOCAL_DECAPTCHA:
        try:
            from ikabot.helpers.piratesDecaptcha import warm_up
            warm_up()
        except Exception:
            pass

    try:
        while pirateCount > 0:
            session.setStatus("Pirating for " + str(pirateCount) + " more runs")
            if pirateSchedule == True:
                current_hour = int(time.strftime("%H"))
                if current_hour >= dayStart and current_hour <= dayEnd:
                    pirateMissionChoice = pirateMissionDayChoice
                elif current_hour >= nightStart or current_hour <= nightEnd:
                    pirateMissionChoice = pirateMissionNightChoice
                else:
                    pirateMissionChoice = pirateMissionDayChoice
            pirateCount -= 1
            with _stage("looking for a city with a pirate fortress"):
                piracyCities = getPiracyCities(
                    session, pirateMissionChoice
                )  # this is done again inside the loop in case the user destroys / creates another pirate fortress while this module is running
            if piracyCities == []:
                raise Exception(
                    "No city with pirate fortress capable of executing selected mission"
                )
            with _stage("opening the city that holds the pirate fortress"):
                html = session.post(
                    city_url + str(piracyCities[0]["id"])
                )  # this is needed because for some reason you need to look at the town where you are sending a request from in the line below, before you send that request
            if (
                '"showPirateFortressShip":0' in html
            ):  # this is in case the user has manually run a capture run, in that case, there is no need to wait 150secs instead we can check every 5
                url = "view=pirateFortress&cityId={}&position=17&backgroundView=city&currentCityId={}&actionRequest={}&ajax=1".format(
                    piracyCities[0]["id"], piracyCities[0]["id"], actionRequest
                )
                with _stage("reading the timer of the mission already running"):
                    html = session.post(url)
                    wait_for = getCurrentMissionWaitingTime(html)
                wait(wait_for, maxRandomWaitingTime)
                pirateCount += 1  # don't count this as an iteration of the loop
                continue

            url = "action=PiracyScreen&function=capture&buildingLevel={0}&view=pirateFortress&cityId={1}&position=17&activeTab=tabBootyQuest&backgroundView=city&currentCityId={1}&templateView=pirateFortress&actionRequest={2}&ajax=1".format(
                piracyMissionToBuildingLevel[pirateMissionChoice],
                piracyCities[0]["id"],
                actionRequest,
            )
            with _stage("starting the capture mission"):
                html = session.post(url)

            if (
                "function=createCaptcha" in html or "js_captchaImage" in html
            ):
                try:
                    for i in range(20):
                        session.setStatus("Resolving captcha " + str(i) + "/20")
                        if i == 19:
                            msg = "Failed to resolve captcha too many times, autoPirate has been terminated."
                            sendToBot(session, msg)
                            raise Exception("Failed to resolve captcha too many times")
                        with _stage("fetching the captcha image"):
                            picture = extract_captcha_image(html)
                            if picture is None:
                                logger.warning(
                                    "Captcha image not embedded in the capture "
                                    "response; falling back to createCaptcha"
                                )
                                picture = session.get(
                                    "action=Options&function=createCaptcha",
                                    fullResponse=True,
                                ).content
                            if not picture:
                                raise ValueError(
                                    "the game returned no captcha image, neither "
                                    "embedded in the capture response nor from "
                                    "the createCaptcha endpoint"
                                )
                        with _stage("solving the captcha"):
                            captcha = resolveCaptcha(session, picture)
                        session.setStatus("Got captcha: " + captcha)
                        if captcha == "Error":
                            time.sleep(5)
                            continue
                        session.post(city_url + str(piracyCities[0]["id"]))
                        _submit_params = {
                            "action": "PiracyScreen",
                            "function": "capture",
                            "cityId": piracyCities[0]["id"],
                            "position": "17",
                            "captchaNeeded": "1",
                            "buildingLevel": str(
                                piracyMissionToBuildingLevel[pirateMissionChoice]
                            ),
                            "captcha": captcha,
                            "activeTab": "tabBootyQuest",
                            "backgroundView": "city",
                            "currentCityId": piracyCities[0]["id"],
                            "templateView": "pirateFortress",
                            "actionRequest": actionRequest,
                            "ajax": "1",
                        }
                        with _stage("submitting the solved captcha"):
                            html = session.post(params=_submit_params, noIndex=True)
                        if (
                            '"showPirateFortressShip":1' in html
                        ):  # if this is true, then the crew is still in the town, that means that the request didn't succeed
                            time.sleep(5)
                            continue
                        break
                except Exception as exc:
                    _report_failure(session, exc)
                    break
            if autoConvert.lower() == "y":
                with _stage("converting capture points"):
                    convertCapturePoints(session, piracyCities, convertPerMission)
            wait(piracyMissionWaitingTime[pirateMissionChoice], maxRandomWaitingTime)

    except Exception as exc:
        _report_failure(session, exc)
        event.set()
        return


_LOCAL_FALLBACK_WARNED = False


def _warn_local_fallback(session, exc):
    """Report once per run that captchas are going to the remote API.

    Once, not per captcha: this fires inside the solve loop, and a message per
    attempt would bury everything else. The reason still reaches the log every
    time.
    """
    global _LOCAL_FALLBACK_WARNED
    reason = "{}: {}".format(type(exc).__name__, exc) if exc else "unknown reason"
    logger.warning("Local captcha solver unavailable (%s); using the remote API", reason)
    if _LOCAL_FALLBACK_WARNED:
        return
    _LOCAL_FALLBACK_WARNED = True
    sendToBot(
        session,
        "Auto-Pirate: the local captcha solver is unavailable, so captchas are "
        "being sent to the remote API (slower, and it uses your quota).\n\n"
        "Reason: {}\n\nThis is reported once per run; see the log for each "
        "occurrence.".format(reason),
    )


def resolveCaptcha(session, picture):
    session_data = session.getSessionData()
    if (
        "decaptcha" not in session_data
        or session_data["decaptcha"]["name"] == "default"
    ):
        if LOCAL_DECAPTCHA:
            try:
                return get_captcha_string(picture)
            except Exception as exc:
                # Falling back is correct, but say why. A silent fallback hides
                # a broken local solver behind slower, quota-consuming API
                # calls, and that was invisible until you looked at timings.
                _warn_local_fallback(session, exc)
        else:
            _warn_local_fallback(session, _LOCAL_IMPORT_ERROR)
        try:
            return getPiratesCaptchaSolution(session, picture)
        except Exception as exc:
            raise PirateStageError(
                "solving the captcha with the remote API "
                "(the local solver was unavailable too)", exc
            ) from exc
    elif session_data["decaptcha"]["name"] == "custom":
        files = {"upload_file": picture}
        captcha = requests.post(
            "{0}".format(session_data["decaptcha"]["endpoint"]), files=files
        ).text
        return captcha
    elif session_data["decaptcha"]["name"] == "9kw.eu":
        credits = requests.get(
            "https://www.9kw.eu/index.cgi?action=usercaptchaguthaben&apikey={}".format(
                session_data["decaptcha"]["relevant_data"]["apiKey"]
            )
        ).text
        if int(credits) < 10:
            raise Exception("You do not have enough 9kw.eu credits!")
        captcha_id = requests.post(
            "https://www.9kw.eu/index.cgi?action=usercaptchaupload&apikey={}".format(
                session_data["decaptcha"]["relevant_data"]["apiKey"]
            ),
            headers={"Content-Type": "multipart/form-data"},
            files={"file-upload-01": picture},
        ).text
        while True:
            captcha_result = requests.get(
                "https://www.9kw.eu/index.cgi?action=usercaptchacorrectdata&id={}&apikey={}".format(
                    captcha_id, session_data["decaptcha"]["relevant_data"]["apiKey"]
                )
            ).text
            if captcha_result != "":
                return captcha_result.upper()
            wait(5)
    elif session_data["decaptcha"]["name"] == "telegram":
        sendToBot(session, "Please solve the captcha", Photo=picture)
        captcha_time = time.time()
        while True:
            response = getUserResponse(session, fullResponse=True)
            if len(response) == 0:
                time.sleep(5)
                continue
            response = response[-1]
            if response["date"] > captcha_time:
                return response["text"]
            time.sleep(5)


def getPiracyCities(session, pirateMissionChoice):
    """Gets all user's cities which have a pirate fortress in them
    Parameters
    ----------
    session : ikabot.web.session.Session

    Returns
    -------
    piracyCities : list[dict]
    """
    cities_ids = getIdsOfCities(session)[0]
    piracyCities = []
    for city_id in cities_ids:
        html = session.get(city_url + city_id)
        city = getCity(html)
        for pos, building in enumerate(city["position"]):
            if (
                building["building"] == "pirateFortress"
                and building["level"]
                >= piracyMissionToBuildingLevel[pirateMissionChoice]
            ):
                piracyCities.append(city)
                break
    return piracyCities


def convertCapturePoints(session, piracyCities, convertPerMission):
    """Converts all the users capture points into crew strength
    Parameters
    ----------
    session : ikabot.web.session.Session
    piracyCities: a list containing all cities which have a pirate fortress
    """
    params = {
        "view": "pirateFortress",
        "activeTab": "tabCrew",
        "cityId": piracyCities[0]["id"],
        "position": 17,
        "backgroundView": "city",
        "currentCityId": piracyCities[0]["id"],
        "templateView": "pirateFortress",
        "actionRequest": actionRequest,
        "ajax": 1,
    }
    html = session.post(params=params)
    rta = re.search(r'\\"capturePoints\\":\\"(\d+)\\"', html)
    capturePoints = int(rta.group(1))
    if convertPerMission == "all":
        convertPerMission = capturePoints
    if "conversionProgressBar" in html:  # if a conversion is still in progress
        return
    data = {
        "action": "PiracyScreen",
        "function": "convert",
        "view": "pirateFortress",
        "cityId": piracyCities[0]["id"],
        "islandId": piracyCities[0]["islandId"],
        "activeTab": "tabCrew",
        "crewPoints": str(int(convertPerMission / 10)),
        "position": "17",
        "backgroundView": "city",
        "currentCityId": piracyCities[0]["id"],
        "templateView": "pirateFortress",
        "actionRequest": actionRequest,
        "ajax": "1",
    }
    html = session.post(params=data, noIndex=True)


def getCurrentMissionWaitingTime(html):
    try:
        match = re.search(r'ongoingMissionTimeRemaining\\":(\d+),', html)
        assert (
            match
        ), "Couldn't find remaining ongoing mission time, did you run a pirate mission manually?"
        return int(match.group(1))
    except Exception as exc:
        # Falling back to a fixed wait is fine, but say so — silently sleeping
        # 10 minutes looks identical to the module having hung.
        logger.warning(
            "Could not read the ongoing mission timer (%s: %s); waiting 10 minutes instead",
            type(exc).__name__, exc,
        )
        return 10 * 60
