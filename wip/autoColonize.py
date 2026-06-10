#! /usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import re
import time
import random
import traceback
from decimal import Decimal, InvalidOperation

from ikabot import config
from ikabot.config import city_url
from ikabot.helpers.getJson import getCity
from ikabot.helpers.gui import banner
from ikabot.helpers.pedirInfo import read, chooseCity
from ikabot.helpers.process import set_child_mode
from ikabot.helpers.signals import setInfoSignal
from ikabot.helpers.varios import decodeUnicodeEscape
from ikabot.helpers.botComm import sendToBot


# =========================================================
# CONFIG
# =========================================================

CARGO_PEOPLE   = 40
CARGO_GOLD     = 9000
CARGO_WOOD     = 0
TRANSPORTERS   = 3
CAPACITY       = 5
JET_PROPULSION = 0

POLL_SECONDS = 2.0

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_PATH   = os.path.join(SCRIPT_DIR, "autoColonize.log")


# =========================================================
# LOGGING
# =========================================================

def _reset_log():
    try:
        open(LOG_PATH, "w", encoding="utf-8").close()
    except Exception:
        pass

def _log(msg):
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        line = str(msg)[:4000]
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[{}] {}\n".format(ts, line))
    except Exception:
        pass

def _jitter():
    time.sleep(random.uniform(0.2, 0.5))

def _trunc(x, n=2000):
    s = str(x)
    return s[:n] + "...<trunc>" if len(s) > n else s


# =========================================================
# HELPERS
# =========================================================

def _safe_int(v, default=0):
    try:
        if v is None: return default
        return int(Decimal(str(v).strip()))
    except Exception:
        return default

def _parse_resp(resp_text):
    try:
        return json.loads(resp_text, strict=False)
    except Exception:
        return None

def _extract_action_request(resp_text):
    try:
        data = _parse_resp(resp_text)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, list) and len(item) >= 2 and item[0] == "updateGlobalData":
                    return (item[1].get("actionRequest") or "").strip()
    except Exception:
        pass
    m = re.search(r'"actionRequest"\s*:\s*"([^"]+)"', resp_text)
    return m.group(1) if m else ""

def _extract_background_data(resp_text):
    try:
        data = _parse_resp(resp_text)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, list) and len(item) >= 2 and item[0] == "updateGlobalData":
                    bg = item[1].get("backgroundData")
                    if isinstance(bg, dict):
                        return bg
    except Exception:
        pass
    return {}

def _extract_header_data(resp_text):
    try:
        data = _parse_resp(resp_text)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, list) and len(item) >= 2 and item[0] == "updateGlobalData":
                    hd = item[1].get("headerData")
                    if isinstance(hd, dict):
                        return hd
    except Exception:
        pass
    return {}

def _is_success(resp_text):
    """
    True فقط لو السيرفر رجّع provideFeedback من type=10
    (ده هو نفس check اللي بتعمله planRoutes.py)
    مش substring match عشان يتجنب false positives
    """
    try:
        data = _parse_resp(resp_text)
        if not isinstance(data, list):
            return False
        for item in data:
            if isinstance(item, list) and len(item) >= 2 and item[0] == "provideFeedback":
                feedbacks = item[1] if isinstance(item[1], list) else []
                for fb in feedbacks:
                    if isinstance(fb, dict) and fb.get("type") == 10:
                        _log("SUCCESS_TEXT: {}".format(fb.get("text", "")))
                        return True
    except Exception:
        pass
    return False


# =========================================================
# ISLAND HELPERS
# =========================================================

def _get_island_id(session, x, y):
    html = session.get("view=worldmap_iso&islandX={}&islandY={}".format(x, y), noIndex=True)
    _jitter()
    m = re.search(r"jsonData\s*=\s*'(.*?)';", html)
    if not m:
        return None
    try:
        j = json.loads(m.group(1), strict=False)
        return str(j["data"][str(x)][str(y)][0])
    except Exception as e:
        _log("_get_island_id error: {}".format(e))
        return None

def _find_empty_slots(bg):
    empties = []
    for i, c in enumerate((bg or {}).get("cities", []) or []):
        if not isinstance(c, dict):
            continue
        if c.get("type") == "buildplace":
            bp_type = (c.get("buildplace_type") or "").strip()
            if bp_type in ("", "normal"):
                pos = c.get("position") or c.get("pos") or (i + 1)
                try:
                    pos = int(pos)
                except Exception:
                    pos = i + 1
                if 1 <= pos <= 17:
                    empties.append(pos)
    return sorted(set(empties))

def _get_ships(session, city_id):
    """عدد السفن المتاحة من updateGlobalData"""
    try:
        data = session.get(
            "view=updateGlobalData&backgroundView=city&currentCityId={}&ajax=1".format(city_id),
            noIndex=True
        )
        _jitter()
        hd = _extract_header_data(data)
        ships = _safe_int(hd.get("freeTransporters"), 0)
        _log("_get_ships({}): {}".format(city_id, ships))
        return ships
    except Exception as e:
        _log("_get_ships error: {}".format(e))
        return 0


# =========================================================
# CORE COLONIZE — نفس نمط ikabot الموحد (session.post + params)
# =========================================================

def _load_colonize_view(session, island_id, position):
    """
    خطوة 1: GET للـ colonize view — بالظبط زي ما المتصفح يفتح الفورم
    session.get() بتستخدم session.s.get() مباشرة بدون __token()
    """
    url = (
        "view=colonize&islandId={}&position={}"
        "&backgroundView=island&currentIslandId={}"
        "&templateView=version&currentTab=tab_version&ajax=1"
    ).format(island_id, position, island_id)
    _log("LOAD_VIEW url={}".format(url))
    resp = session.get(url, noIndex=True)
    _log("LOAD_VIEW RESP={}".format(_trunc(resp)))
    _jitter()
    ar  = _extract_action_request(resp) or AR_PLACEHOLDER
    bg  = _extract_background_data(resp)
    return ar, bg

def _game_base_url(session):
    """https://ae.ikariam.gameforge.com/index.php — بدون trailing ?"""
    return session.urlBase.replace("index.php?", "index.php")

def _do_colonize(session, island_id, position, origin_city_id, action_request):
    """
    يبعت الـ colonize POST بالظبط زي المتصفح:
      - params في الـ BODY (data=) مش URL
      - actionRequest جاي من LOAD_VIEW مباشرة (مش من __token())
      - بيستخدم session.s.post() عشان يتجنب __token() اللي بيعمل GET ويغير الـ context
    """
    ships_before = _get_ships(session, origin_city_id)
    _log("DO_COLONIZE islandId={} pos={} ar={} ships_before={}".format(
        island_id, position, action_request, ships_before))

    payload = {
        "action":              "transportOperations",
        "function":            "startColonization",
        "islandId":            str(island_id),
        "cargo_people":        str(CARGO_PEOPLE),
        "cargo_gold":          str(CARGO_GOLD),
        "desiredPosition":     str(position),
        "actionRequest":       str(action_request),
        "cargo_resource":      str(CARGO_WOOD),
        "cargo_tradegood1":    "0",
        "cargo_tradegood2":    "0",
        "cargo_tradegood3":    "0",
        "cargo_tradegood4":    "0",
        "capacity":            str(CAPACITY),
        "max_capacity":        str(CAPACITY),
        "jetPropulsion":       str(JET_PROPULSION),
        "transporters":        str(TRANSPORTERS),
        "position":            str(position),
        "destinationIslandId": str(island_id),
        "backgroundView":      "island",
        "currentIslandId":     str(island_id),
        "templateView":        "colonize",
        "ajax":                "1",
    }

    url = _game_base_url(session)
    _log("COLONIZE_POST url={} body={}".format(url, payload))

    # params في الـ body (data=)، مش في URL (params=)
    # session.s.post مباشرة → بدون __token() GET → الـ context بيفضل colonize
    response = session.s.post(
        url,
        data=payload,
        params={},
        verify=config.do_ssl_verify,
        timeout=300,
    )
    resp = response.text
    _log("COLONIZE_POST STATUS={} RESP={}".format(response.status_code, _trunc(resp, 10000)))
    _jitter()

    if _is_success(resp):
        return True, "success_text"

    ships_after = _get_ships(session, origin_city_id)
    if ships_after < ships_before:
        return True, "ships_decreased"

    _log("COLONIZE FAILED ships_before={} ships_after={}".format(ships_before, ships_after))
    return False, "failed"


# =========================================================
# WATCH LOOP
# =========================================================

def _watch_and_colonize(session, island_id, x, y, origin_city_id, origin_name):
    cycle = 0
    print("\nمراقبة [{}:{}] — كل {:.0f}ث (Ctrl+C للإيقاف)\n".format(x, y, POLL_SECONDS))

    while True:
        cycle += 1

        # جيب بيانات الجزيرة
        ar, bg = _load_colonize_view(session, island_id, position=1)
        empties = _find_empty_slots(bg)
        ships   = _get_ships(session, origin_city_id)

        _log("CYCLE={} empties={} ships={}".format(cycle, empties, ships))
        ts = "[{}]".format(time.strftime("%H:%M:%S"))

        if not empties:
            print("\r  {} الجزيرة ممتلئة — انتظار مكان...          ".format(ts), end="", flush=True)
            time.sleep(POLL_SECONDS)
            continue

        if ships < TRANSPORTERS:
            print("\r  {} مكان={} | سفن={}/{} — انتظار سفن...  ".format(
                ts, empties, ships, TRANSPORTERS), end="", flush=True)
            time.sleep(POLL_SECONDS)
            continue

        # ── مكان + سفن متاحين ──
        pos = empties[0]
        print("\n\n  ✓ مكان={} سفن={} — جاري الاستعمار...".format(pos, ships))
        _log("SLOT_FOUND pos={} ships={}".format(pos, ships))

        # تحديث view بالـ position المطلوب
        ar2, bg2 = _load_colonize_view(session, island_id, position=pos)
        if pos not in _find_empty_slots(bg2):
            _log("SLOT {} gone before POST".format(pos))
            print("  المكان اختفى — إعادة...")
            time.sleep(POLL_SECONDS)
            continue

        ok, reason = _do_colonize(session, island_id, pos, origin_city_id, ar2)

        if ok:
            msg = ("✅ الاستعمار تم!\n"
                   "الأصل: {} ({})\nالهدف: [{}:{}] id={}\nالموضع: {} | السبب: {}").format(
                origin_name, origin_city_id, x, y, island_id, pos, reason)
            print("\n" + msg)
            _log("SUCCESS pos={} reason={}".format(pos, reason))
            try:
                sendToBot(session, "AutoColonize SUCCESS\n" + msg)
            except Exception:
                pass
            return

        print("  ⚠ فشل — سيُعاد...")
        time.sleep(POLL_SECONDS)


# =========================================================
# ENTRY POINT
# =========================================================

def autoColonize(session, event, stdin_fd, predetermined_input):
    _reset_log()
    _log("=== autoColonize START ===")

    try:
        try:
            sys.stdin = os.fdopen(stdin_fd)
        except Exception:
            pass

        config.predetermined_input = predetermined_input
        banner()

        origin_city = chooseCity(session)
        if not origin_city:
            print("لم يتم اختيار مدينة.")
            return

        origin_city_id = int(origin_city["id"])
        origin_name    = decodeUnicodeEscape(origin_city.get("name", ""))
        _log("origin city='{}' id={}".format(origin_name, origin_city_id))

        x = int(read(min=0, max=100, digit=True, msg="X coord: "))
        y = int(read(min=0, max=100, digit=True, msg="Y coord: "))

        island_id = _get_island_id(session, x, y)
        if not island_id:
            print("تعذّر تحديد معرّف الجزيرة [{}:{}]".format(x, y))
            return

        _log("targetIslandId={} coords=[{}:{}]".format(island_id, x, y))
        print("الجزيرة: [{}:{}] id={}".format(x, y, island_id))

        set_child_mode(session)
        setInfoSignal(session, "AutoColonize: {} -> [{}:{}]".format(origin_name, x, y))
        try:
            event.set()
        except Exception:
            pass

        _watch_and_colonize(session, island_id, x, y, origin_city_id, origin_name)

    except KeyboardInterrupt:
        print("\n[توقف يدوي]")
        _log("KeyboardInterrupt")

    except Exception:
        tb = traceback.format_exc()
        _log("FATAL:\n{}".format(tb))
        print(tb)
        try:
            sendToBot(session, "AutoColonize ERROR:\n{}".format(tb))
        except Exception:
            pass

    finally:
        try:
            event.set()
        except Exception:
            pass


def GoodLisT(session, event, stdin_fd, predetermined_input):
    return autoColonize(session, event, stdin_fd, predetermined_input)
