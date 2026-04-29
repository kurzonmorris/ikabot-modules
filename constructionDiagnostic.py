#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""Construction diagnostic — walk the build/upgrade HTTP flow step by step,
dumping every request URL, params, and raw response body to a timestamped
file under ~/.

Goal: figure out why builds/upgrades are being rejected. We compare what the
game returns at each step against what the legacy parser expects, so a site
change shows up as a parse-shape divergence in the dump file.
"""

import json
import os
import re
import sys
import time
import traceback

from ikabot import config
from ikabot.config import actionRequest, city_url
from ikabot.helpers.getJson import getCity
from ikabot.helpers.gui import banner, bcolors, enter
from ikabot.helpers.pedirInfo import chooseCity, read


def _log(fh, label, value):
    fh.write("\n" + "=" * 78 + "\n")
    fh.write(f"  {label}\n")
    fh.write("=" * 78 + "\n")
    if isinstance(value, (dict, list)):
        fh.write(json.dumps(value, indent=2, default=str))
    else:
        fh.write(str(value))
    fh.write("\n")
    fh.flush()


def _dump_path():
    ts = time.strftime("%Y%m%d_%H%M%S")
    return os.path.join(os.path.expanduser("~"),
                        f"ikabot_construction_diag_{ts}.txt")


def _log_request_history(fh, session, label):
    """Dump the most recent request the session sent — including the
    post-substitution URL (token replaced), the actual params/headers, and
    the response status. Reveals whether 'REQUESTID' made it to the wire."""
    try:
        last = session.requestHistory[-1]
    except (AttributeError, IndexError):
        _log(fh, f"{label}: requestHistory unavailable", "")
        return
    # Trim cookies / sensitive header bits
    headers = {k: v for k, v in (last.get("headers") or {}).items()
               if k.lower() not in ("cookie",)}
    safe = {
        "method": last.get("method"),
        "url": last.get("url"),
        "params": last.get("params"),
        "payload": last.get("payload"),
        "headers": headers,
        "response_status": (last.get("response") or {}).get("status"),
        "response_elapsed_s": (last.get("response") or {}).get("elapsed"),
    }
    _log(fh, label, safe)
    # Sanity check: did REQUESTID get substituted?
    wire = json.dumps({"url": safe["url"], "params": safe["params"],
                       "payload": safe["payload"]})
    if "REQUESTID" in wire:
        _log(fh, f"{label} — TOKEN SUBSTITUTION FAILED",
             "literal 'REQUESTID' reached the wire — session.__token() did "
             "not substitute it. This is the rejection cause.")


def _summarise(fh, step, ok, note=""):
    mark = f"{bcolors.GREEN}OK{bcolors.ENDC}" if ok else f"{bcolors.RED}FAIL{bcolors.ENDC}"
    line = f"  [{mark}] {step}"
    if note:
        line += f" — {note}"
    print(line)
    fh.write(f"\n>>> {step}: {'OK' if ok else 'FAIL'}")
    if note:
        fh.write(f" — {note}")
    fh.write("\n")
    fh.flush()


def _check_reload_rejection(parsed):
    """Return (is_rejection, reason) for the 'custom/reload' rejection pattern."""
    try:
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], list):
            if parsed[0][0] == "custom" and parsed[0][1][0] == "reload":
                return True, f"server returned custom/reload → {parsed[0][1][1].get('link')}"
    except Exception:
        pass
    return False, ""


def _check_actionrequest_token(url_or_params):
    """Detect whether 'REQUESTID' literal escaped to the wire (i.e. the
    session.post() token substitution didn't fire)."""
    s = url_or_params if isinstance(url_or_params, str) else json.dumps(url_or_params)
    return "REQUESTID" in s


def _change_current_city(session, fh, target_city_id):
    """POST action=header&function=changeCurrentCity, mirroring planRoutes.sendGoods.

    The game's server tracks 'current city' separately from the cityId field on
    individual requests. State-changing actions (build, upgrade, transport) are
    silently ignored if the server-side current-city doesn't match. Working
    modules (resource transport) always do this switch explicitly first.
    """
    # Discover the city the bot is currently 'at' on the server.
    try:
        landing_html = session.get()
        from ikabot.helpers.getJson import getCity as _getCity
        current_city = _getCity(landing_html)
        currId = current_city["id"]
    except Exception:
        currId = target_city_id  # best-effort fallback
    _log(fh, "PRE-STEP: server-side current city before switch", currId)
    if str(currId) == str(target_city_id):
        _log(fh, "PRE-STEP: changeCurrentCity",
             f"already at city {target_city_id}, skipping switch POST")
        return True
    data = {
        "action": "header",
        "function": "changeCurrentCity",
        "actionRequest": actionRequest,
        "oldView": "city",
        "cityId": target_city_id,
        "backgroundView": "city",
        "currentCityId": currId,
        "ajax": "1",
    }
    _log(fh, "PRE-STEP REQUEST PARAMS (changeCurrentCity)", data)
    try:
        resp = session.post(params=data)
    except Exception:
        _log(fh, "PRE-STEP EXCEPTION", traceback.format_exc())
        _log_request_history(fh, session, "PRE-STEP WIRE REQUEST (after exception)")
        _summarise(fh, "Pre-step changeCurrentCity", False, "exception, see log")
        return False
    _log(fh, "PRE-STEP RAW RESPONSE", resp)
    _log_request_history(fh, session, "PRE-STEP WIRE REQUEST (post-substitution)")
    _summarise(fh, "Pre-step changeCurrentCity", True,
               f"switched server-side current city to {target_city_id}")
    return True


def _diagnose_build(session, fh, city, slot_pos):
    """Walk the new-build flow for an empty slot."""
    cid = city["id"]
    _change_current_city(session, fh, cid)

    # ---- Step 1: GET buildingGround listing ----
    print(f"\nStep 1: fetch buildingGround listing for slot {slot_pos}…")
    params1 = {
        "view": "buildingGround",
        "cityId": cid,
        "position": slot_pos,
        "backgroundView": "city",
        "currentCityId": cid,
        "actionRequest": actionRequest,
        "ajax": "1",
    }
    _log(fh, "STEP 1 REQUEST PARAMS (buildingGround listing)", params1)
    if _check_actionrequest_token(params1):
        _log(fh, "STEP 1 NOTE", "params still contain literal 'REQUESTID' — "
             "session.post() will substitute at send time. Confirm the wire "
             "request below does NOT contain REQUESTID.")
    try:
        resp1 = session.post(params=params1, noIndex=True)
    except Exception:
        _log(fh, "STEP 1 EXCEPTION", traceback.format_exc())
        _log_request_history(fh, session, "STEP 1 WIRE REQUEST (after exception)")
        _summarise(fh, "Step 1 buildingGround POST", False, "exception, see log")
        return
    _log(fh, "STEP 1 RAW RESPONSE", resp1)
    _log_request_history(fh, session, "STEP 1 WIRE REQUEST (post-substitution)")

    try:
        parsed1 = json.loads(resp1, strict=False)
    except Exception:
        _log(fh, "STEP 1 PARSE ERROR", traceback.format_exc())
        _summarise(fh, "Step 1 buildingGround POST", False, "response was not JSON")
        return
    _log(fh, "STEP 1 PARSED JSON (top-level shape)", parsed1)

    is_rej, reason = _check_reload_rejection(parsed1)
    if is_rej:
        _summarise(fh, "Step 1 buildingGround POST", False, reason)
        return

    # Legacy parser expects: parsed[1][1][1] is the HTML
    try:
        html = parsed1[1][1][1]
    except (IndexError, TypeError, KeyError):
        _log(fh, "STEP 1 SHAPE MISMATCH",
             "Expected parsed[1][1][1] to be HTML — got: "
             + repr(parsed1)[:300])
        _summarise(fh, "Step 1 buildingGround POST", False,
                   "shape changed — parsed[1][1][1] is not the HTML payload")
        return
    _log(fh, "STEP 1 EXTRACTED HTML (first 4000 chars)", html[:4000])

    # Legacy regex from research/constructBuilding.py:65-68
    pat = (
        r'<li class="building (.+?)">\s*<div class="buildinginfo">\s*'
        r'<div title="(.+?)"\s*class="buildingimg .+?"\s*'
        r'onclick="ajaxHandlerCall\(\'.*?buildingId=(\d+)&'
    )
    matches = re.findall(pat, html)
    _log(fh, "STEP 1 REGEX MATCHES (legacy pattern)", matches)
    if not matches:
        _summarise(fh, "Step 1 buildingGround listing parse", False,
                   "legacy regex matched 0 buildings — HTML structure may "
                   "have changed; inspect EXTRACTED HTML in the dump")
        return
    _summarise(fh, "Step 1 buildingGround listing parse", True,
               f"{len(matches)} buildings parseable")

    # ---- Step 2: pick the first building and POST CityScreen/build ----
    chosen = matches[0]
    print(f"\nStep 2: POST CityScreen/build for building '{chosen[1]}' (id {chosen[2]})…")
    params2 = {
        "action": "CityScreen",
        "function": "build",
        "cityId": cid,
        "position": slot_pos,
        "building": chosen[2],
        "backgroundView": "city",
        "currentCityId": cid,
        "templateView": "buildingGround",
        "actionRequest": actionRequest,
        "ajax": "1",
    }
    _log(fh, "STEP 2 REQUEST PARAMS (CityScreen/build)", params2)
    print(f"  About to POST a real build request — slot {slot_pos} "
          f"will become a {chosen[1]} if it succeeds.")
    print("  Continue? [y/N]")
    if read(values=["y", "Y", "n", "N", ""], default="N").lower() != "y":
        _summarise(fh, "Step 2 build POST", False, "skipped by user")
        return
    try:
        resp2 = session.post(params=params2, noIndex=True)
    except Exception:
        _log(fh, "STEP 2 EXCEPTION", traceback.format_exc())
        _log_request_history(fh, session, "STEP 2 WIRE REQUEST (after exception)")
        _summarise(fh, "Step 2 build POST", False, "exception, see log")
        return
    _log(fh, "STEP 2 RAW RESPONSE", resp2)
    _log_request_history(fh, session, "STEP 2 WIRE REQUEST (post-substitution)")

    try:
        parsed2 = json.loads(resp2, strict=False)
    except Exception:
        _log(fh, "STEP 2 PARSE ERROR", traceback.format_exc())
        _summarise(fh, "Step 2 build POST", False, "response was not JSON")
        return
    _log(fh, "STEP 2 PARSED JSON", parsed2)

    is_rej, reason = _check_reload_rejection(parsed2)
    if is_rej:
        _summarise(fh, "Step 2 build POST", False, reason)
        return

    # Legacy expects: parsed[3][1][0]["text"] is the result message
    try:
        msg = parsed2[3][1][0]["text"]
        _summarise(fh, "Step 2 build POST", True, f"server message: {msg!r}")
    except (IndexError, TypeError, KeyError):
        _log(fh, "STEP 2 SHAPE MISMATCH",
             "Expected parsed[3][1][0]['text'] — see PARSED JSON above")
        _summarise(fh, "Step 2 build POST", False,
                   "request did not crash but result-message shape changed")


def _diagnose_upgrade(session, fh, city, slot_pos):
    """Walk the upgrade flow for an occupied slot.

    Tries multiple URL variants in a single run. Once any variant flips
    isBusy=True, subsequent variants are skipped (the slot is now busy and
    can't be re-tested without waiting for completion).
    """
    cid = city["id"]
    _change_current_city(session, fh, cid)
    slot = city["position"][slot_pos]
    building = slot.get("building", "")
    current_lv = slot.get("level", 0)
    can_upgrade = slot.get("canUpgrade")

    _log(fh, "SLOT INFO", {
        "position": slot_pos, "building": building, "name": slot.get("name"),
        "level": current_lv, "isBusy": slot.get("isBusy"),
        "isMaxLevel": slot.get("isMaxLevel"), "canUpgrade": can_upgrade,
        "completed": slot.get("completed"),
    })
    if slot.get("isBusy"):
        _summarise(fh, "Pre-flight slot check", False,
                   "slot is currently isBusy — game will reject any new POST")
        return
    if can_upgrade is False:
        _summarise(fh, "Pre-flight slot check", False,
                   "canUpgrade=False — game-side prerequisite not met")
        return

    base_url = (
        f"action=CityScreen&function=upgradeBuilding"
        f"&actionRequest={actionRequest}"
        f"&cityId={cid}&position={slot_pos}"
        f"&level={current_lv}"
        f"&backgroundView=city&currentCityId={cid}"
        f"&templateView={building}"
        f"&ajax=1"
    )
    legacy_url = (
        f"action=CityScreen&function=upgradeBuilding"
        f"&actionRequest={actionRequest}"
        f"&cityId={cid}&position={slot_pos}"
        f"&level={current_lv}"
        f"&activeTab=tabSendTransporter"
        f"&backgroundView=city&currentCityId={cid}"
        f"&templateView={building}"
        f"&ajax=1"
    )
    params_form = {
        "action": "CityScreen",
        "function": "upgradeBuilding",
        "actionRequest": actionRequest,
        "cityId": cid,
        "position": slot_pos,
        "level": current_lv,
        "activeTab": "tabSendTransporter",
        "backgroundView": "city",
        "currentCityId": cid,
        "templateView": building,
        "ajax": "1",
    }

    variants = [
        ("A", "URL string, NO activeTab (current code)",
         lambda: session.post(base_url)),
        ("B", "URL string, WITH activeTab=tabSendTransporter (legacy match)",
         lambda: session.post(legacy_url)),
        ("C", "params=dict, WITH activeTab (matches working sendGoods style)",
         lambda: session.post(params=params_form)),
    ]

    print(f"\nAbout to POST upgrade for {building} lv {current_lv} → {current_lv + 1}.")
    print(f"Will try up to {len(variants)} URL variants until one succeeds.")
    print("Continue? [y/N]")
    if read(values=["y", "Y", "n", "N", ""], default="N").lower() != "y":
        _summarise(fh, "Upgrade variants", False, "skipped by user")
        return

    succeeded = False
    for code, desc, do_post in variants:
        if succeeded:
            _log(fh, f"VARIANT {code} SKIPPED", "previous variant succeeded")
            continue
        print(f"\n  Variant {code}: {desc}…")
        _log(fh, f"VARIANT {code} DESCRIPTION", desc)
        try:
            resp = do_post()
        except Exception:
            _log(fh, f"VARIANT {code} EXCEPTION", traceback.format_exc())
            _log_request_history(fh, session, f"VARIANT {code} WIRE (after exception)")
            _summarise(fh, f"Variant {code} POST", False, "exception, see log")
            continue
        _log(fh, f"VARIANT {code} RAW RESPONSE", resp)
        _log_request_history(fh, session, f"VARIANT {code} WIRE REQUEST")

        # Verify by re-fetching the city
        time.sleep(2)
        try:
            html = session.get(city_url + str(cid))
            city2 = getCity(html)
            slot2 = city2["position"][slot_pos]
        except Exception:
            _log(fh, f"VARIANT {code} VERIFY EXCEPTION", traceback.format_exc())
            _summarise(fh, f"Variant {code} verify", False, "city re-fetch failed")
            continue
        _log(fh, f"VARIANT {code} SLOT AFTER POST", {
            "isBusy": slot2.get("isBusy"),
            "completed": slot2.get("completed"),
            "level": slot2.get("level"),
            "canUpgrade": slot2.get("canUpgrade"),
        })
        if slot2.get("isBusy"):
            _summarise(fh, f"Variant {code} verify", True,
                       f"isBusy=True, completes at {slot2.get('completed')} "
                       f"— THIS IS THE WORKING VARIANT")
            succeeded = True
        else:
            _summarise(fh, f"Variant {code} verify", False,
                       "slot is NOT isBusy — variant rejected")


def constructionDiagnostic(session, event, stdin_fd, predetermined_input):
    """Entry point — pick city + slot, run the relevant flow, dump everything."""
    sys.stdin = os.fdopen(stdin_fd)
    config.predetermined_input = predetermined_input
    path = _dump_path()
    fh = None
    try:
        banner()
        print("Construction Diagnostic\n")
        print("Walks the build/upgrade HTTP flow step by step and saves every")
        print("request and response to a file you can inspect or share.\n")
        print(f"Dump file: {path}\n")

        fh = open(path, "w", encoding="utf-8")
        fh.write(f"Construction diagnostic dump\n")
        fh.write(f"Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        fh.write(f"Player: {getattr(session, 'username', '?')} "
                 f"@ {getattr(session, 'servidor', '?')}\n")
        fh.flush()

        print("Pick a city to test against:")
        city = chooseCity(session)
        cid = city["id"]
        _log(fh, "CITY", {
            "id": cid, "name": city.get("cityName"),
            "islandId": city.get("islandId"),
            "availableResources": city.get("availableResources"),
        })

        positions = city.get("position", [])
        print(f"\nCity has {len(positions)} slots:\n")
        for i, s in enumerate(positions):
            if s.get("building") == "empty":
                print(f"  ({i:>2}) [empty, {s.get('type', '?')}]")
            else:
                extras = []
                if s.get("isBusy"):
                    extras.append("BUSY")
                if s.get("isMaxLevel"):
                    extras.append("MAX")
                if s.get("canUpgrade") is False:
                    extras.append("canUpgrade=False")
                suffix = f"  [{', '.join(extras)}]" if extras else ""
                print(f"  ({i:>2}) {s.get('name')} lv{s.get('level', 0)}{suffix}")

        print(f"\nPick a slot (0–{len(positions) - 1}):")
        slot_pos = int(read(min=0, max=len(positions) - 1))
        slot = positions[slot_pos]
        _log(fh, "CHOSEN SLOT INDEX", slot_pos)

        if slot.get("building") == "empty":
            print(f"\n→ Slot {slot_pos} is empty. Running NEW-BUILD flow.\n")
            _diagnose_build(session, fh, city, slot_pos)
        else:
            print(f"\n→ Slot {slot_pos} is occupied. Running UPGRADE flow.\n")
            _diagnose_upgrade(session, fh, city, slot_pos)

        print(f"\n{bcolors.GREEN}Dump written to:{bcolors.ENDC} {path}")
        print("Share that file (or paste the relevant STEP sections) so the")
        print("exact site response can be inspected.\n")
    except KeyboardInterrupt:
        print("\nCancelled.")
    except Exception:
        tb = traceback.format_exc()
        # Show prominently — don't let it scroll off
        print(f"\n{bcolors.RED}{'=' * 70}")
        print("Diagnostic crashed:")
        print(f"{'=' * 70}{bcolors.ENDC}")
        print(tb)
        print(f"\n{bcolors.YELLOW}Dump file (partial): {path}{bcolors.ENDC}")
        if fh is not None:
            try:
                _log(fh, "DIAGNOSTIC CRASHED", tb)
            except Exception:
                pass
    finally:
        if fh is not None:
            try:
                fh.close()
            except Exception:
                pass
        try:
            print("\nPress Enter to return to the main menu…")
            enter()
        except Exception:
            pass
        event.set()
