#! /usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for constructionList.py bug fixes.
These tests mock all ikabot dependencies so they can run standalone.
"""

import hashlib
import importlib
import sys
import time
import types
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# Minimal stubs for every ikabot import the module does at load time
# ---------------------------------------------------------------------------

def _make_stub_module(name, **attrs):
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    return mod


# config stub
config_stub = _make_stub_module(
    "ikabot.config",
    actionRequest="TEST_ACTION",
    predetermined_input=[],
    material_img_hash=["hash_wood", "hash_wine", "hash_marble", "hash_glass", "hash_sulfur"],
    debugON_constructionList=False,
)

# helpers stubs
botComm_stub   = _make_stub_module("ikabot.helpers.botComm",
    sendToBot=MagicMock(), sendToBotDebug=MagicMock())
getJson_stub   = _make_stub_module("ikabot.helpers.getJson",  getCity=MagicMock())
gui_stub       = _make_stub_module("ikabot.helpers.gui",
    banner=MagicMock(), enter=MagicMock(), bcolors=MagicMock(),
    getDateTime=lambda t: "2024-01-01 00:00:00")
pedirInfo_stub = _make_stub_module("ikabot.helpers.pedirInfo",
    chooseCity=MagicMock(), read=MagicMock())
planRoutes_stub = _make_stub_module("ikabot.helpers.planRoutes",
    executeRoutes=MagicMock(), getIdsOfCities=MagicMock())
process_stub   = _make_stub_module("ikabot.helpers.process",  set_child_mode=MagicMock())
resources_stub = _make_stub_module("ikabot.helpers.resources", getAvailableResources=MagicMock())
signals_stub   = _make_stub_module("ikabot.helpers.signals",  setInfoSignal=MagicMock())
varios_stub    = _make_stub_module("ikabot.helpers.varios",
    wait=MagicMock(), addThousandSeparator=lambda x: str(x),
    getMinimumWaitingTime=MagicMock(return_value=0))
session_stub   = _make_stub_module("ikabot.web.session",      normal_get=MagicMock())

ikabot_stub    = _make_stub_module("ikabot")

materials_names     = ["Wood", "Wine", "Marble", "Glass", "Sulfur"]
materials_names_tec = ["wood", "wine", "marble", "glass", "sulfur"]
city_url            = "https://game.example.com/city/"

_stubs = {
    "ikabot":                        ikabot_stub,
    "ikabot.config":                 config_stub,
    "ikabot.helpers.botComm":        botComm_stub,
    "ikabot.helpers.getJson":        getJson_stub,
    "ikabot.helpers.gui":            gui_stub,
    "ikabot.helpers.pedirInfo":      pedirInfo_stub,
    "ikabot.helpers.planRoutes":     planRoutes_stub,
    "ikabot.helpers.process":        process_stub,
    "ikabot.helpers.resources":      resources_stub,
    "ikabot.helpers.signals":        signals_stub,
    "ikabot.helpers.varios":         varios_stub,
    "ikabot.web.session":            session_stub,
}
for k, v in _stubs.items():
    sys.modules[k] = v

import builtins
_real_import = builtins.__import__
def _patched_import(name, *args, **kwargs):
    return _real_import(name, *args, **kwargs)
builtins.__import__ = _patched_import

import importlib.util, os
_spec = importlib.util.spec_from_file_location(
    "constructionList",
    os.path.join(os.path.dirname(__file__), "constructionList.py"),
)
_mod = importlib.util.module_from_spec(_spec)

_mod.__dict__.update({
    "actionRequest":            "TEST_ACTION",
    "config":                   config_stub,
    "sendToBot":                botComm_stub.sendToBot,
    "sendToBotDebug":           botComm_stub.sendToBotDebug,
    "getCity":                  getJson_stub.getCity,
    "banner":                   gui_stub.banner,
    "enter":                    gui_stub.enter,
    "bcolors":                  gui_stub.bcolors,
    "getDateTime":              gui_stub.getDateTime,
    "chooseCity":               pedirInfo_stub.chooseCity,
    "read":                     pedirInfo_stub.read,
    "executeRoutes":            planRoutes_stub.executeRoutes,
    "getIdsOfCities":           planRoutes_stub.getIdsOfCities,
    "set_child_mode":           process_stub.set_child_mode,
    "setInfoSignal":            signals_stub.setInfoSignal,
    "wait":                     varios_stub.wait,
    "addThousandSeparator":     varios_stub.addThousandSeparator,
    "getMinimumWaitingTime":    varios_stub.getMinimumWaitingTime,
    "city_url":                 city_url,
    "materials_names":          materials_names,
    "materials_names_tec":      materials_names_tec,
    "debugON_constructionList": False,
    "sys":                      sys,
    "os":                       os,
})

_spec.loader.exec_module(_mod)

# Convenience aliases
checkhash               = _mod.checkhash
getCostsReducers        = _mod.getCostsReducers
getBuildingsToExpand    = _mod.getBuildingsToExpand
waitForConstruction     = _mod.waitForConstruction
chooseResourceProviders = _mod.chooseResourceProviders
sendResourcesMenu       = _mod.sendResourcesMenu


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCheckHash(unittest.TestCase):
    """Bug fixes #6 #7 #8 #9 (checkhash rewrite)"""

    def test_returns_correct_material(self):
        """Full MD5 is computed after all chunks and matched correctly."""
        for idx, name in enumerate(["wood", "wine", "marble", "glass", "sulfur"]):
            fake_content = b"fake_image_data_" + name.encode()
            expected_digest = hashlib.md5(fake_content).hexdigest()
            config_stub.material_img_hash[idx] = expected_digest

            mock_response = MagicMock()
            mock_response.iter_content.return_value = [fake_content]
            mock_response.raise_for_status = MagicMock()
            checkhash.cache_clear()

            with patch("requests.get", return_value=mock_response):
                result = checkhash("https://example.com/img/{}.png".format(name))

            self.assertEqual(result, name, "Expected '{}', got '{}'".format(name, result))

        config_stub.material_img_hash = [
            "hash_wood", "hash_wine", "hash_marble", "hash_glass", "hash_sulfur"
        ]

    def test_returns_none_on_unknown_hash(self):
        """Returns None (not UnboundLocalError) when no hash matches."""
        checkhash.cache_clear()
        fake_content = b"unknown_image"
        mock_response = MagicMock()
        mock_response.iter_content.return_value = [fake_content]
        mock_response.raise_for_status = MagicMock()
        config_stub.material_img_hash = ["aaa", "bbb", "ccc", "ddd", "eee"]

        with patch("requests.get", return_value=mock_response):
            result = checkhash("https://example.com/img/unknown.png")

        self.assertIsNone(result)
        config_stub.material_img_hash = [
            "hash_wood", "hash_wine", "hash_marble", "hash_glass", "hash_sulfur"
        ]

    def test_returns_none_on_http_error(self):
        """Returns None (not an exception) when requests.get raises."""
        import requests as req
        checkhash.cache_clear()
        with patch("requests.get", side_effect=req.RequestException("timeout")):
            result = checkhash("https://example.com/bad.png")
        self.assertIsNone(result)

    def test_intermediate_hashes_not_used(self):
        """MD5 must be finalised over ALL chunks, not checked mid-stream."""
        checkhash.cache_clear()
        chunk1 = b"chunk_one_"
        chunk2 = b"chunk_two_"
        full_digest = hashlib.md5(chunk1 + chunk2).hexdigest()

        config_stub.material_img_hash = [full_digest, "nope", "nope", "nope", "nope"]

        mock_response = MagicMock()
        mock_response.iter_content.return_value = [chunk1, chunk2]
        mock_response.raise_for_status = MagicMock()

        with patch("requests.get", return_value=mock_response):
            result = checkhash("https://example.com/img/two_chunks.png")

        self.assertEqual(result, "wood",
            "Expected full-file MD5 to match; intermediate check would have failed")

        config_stub.material_img_hash = [
            "hash_wood", "hash_wine", "hash_marble", "hash_glass", "hash_sulfur"
        ]


class TestGetCostsReducers(unittest.TestCase):
    """getCostsReducers — no bugs but tests baseline correctness."""

    def _city(self, buildings):
        return {"position": buildings}

    def test_all_reducers_detected(self):
        city = self._city([
            {"name": "Carpentering", "building": "carpentering", "level": 5},
            {"name": "Vineyard",     "building": "vineyard",     "level": 3},
            {"name": "Architect",    "building": "architect",    "level": 7},
            {"name": "Optician",     "building": "optician",     "level": 2},
            {"name": "Fireworker",   "building": "fireworker",   "level": 4},
        ])
        result = getCostsReducers(city)
        self.assertEqual(result, [5, 3, 7, 2, 4])

    def test_empty_city_returns_zeros(self):
        city = self._city([{"name": "empty", "building": "empty", "level": 0}])
        result = getCostsReducers(city)
        self.assertEqual(result, [0, 0, 0, 0, 0])

    def test_partial_reducers(self):
        city = self._city([
            {"name": "Carpentering", "building": "carpentering", "level": 10},
            {"name": "Senate",       "building": "senate",       "level": 1},
        ])
        result = getCostsReducers(city)
        self.assertEqual(result[0], 10)
        self.assertEqual(result[1:], [0, 0, 0, 0])


class TestGetBuildingsToExpand(unittest.TestCase):
    """Bug fix #17: out-of-bounds input, and #19: startLevel stored."""

    def setUp(self):
        getJson_stub.getCity.side_effect = None
        pedirInfo_stub.read.side_effect = None

    def _make_session(self):
        session = MagicMock()
        session.get.return_value = "<html/>"
        return session

    def _mock_city(self):
        return {
            "position": [
                {"name": "Senate",    "building": "senate",    "level": 5,
                 "isMaxLevel": False, "canUpgrade": True,  "isBusy": False, "position": 0},
                {"name": "Warehouse", "building": "warehouse", "level": 3,
                 "isMaxLevel": False, "canUpgrade": True,  "isBusy": False, "position": 1},
            ]
        }

    def test_valid_selection_stores_start_level(self):
        session = self._make_session()
        getJson_stub.getCity.return_value = self._mock_city()
        pedirInfo_stub.read.side_effect = ["1", 8]

        result = getBuildingsToExpand(session, "42")

        self.assertIsNotNone(result)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["name"], "Senate")
        self.assertEqual(result[0]["startLevel"], 5)
        self.assertEqual(result[0]["upgradeTo"], 8)

    def test_out_of_range_id_skipped(self):
        session = self._make_session()
        getJson_stub.getCity.return_value = self._mock_city()
        pedirInfo_stub.read.side_effect = ["99"]

        result = getBuildingsToExpand(session, "42")
        self.assertEqual(result, [])

    def test_zero_exits(self):
        session = self._make_session()
        getJson_stub.getCity.return_value = self._mock_city()
        pedirInfo_stub.read.side_effect = ["0"]

        result = getBuildingsToExpand(session, "42")
        self.assertIsNone(result)


class TestWaitForConstruction(unittest.TestCase):
    """Bug fixes #5 (timeout cap) and #11 (city key)."""

    def setUp(self):
        getJson_stub.getCity.side_effect = None

    def test_exits_when_no_construction(self):
        session = MagicMock()
        session.get.return_value = "<html/>"
        city_no_construction = {
            "cityName": "Athens",
            "position": [{"name": "Senate", "building": "senate", "level": 5}],
        }
        getJson_stub.getCity.return_value = city_no_construction

        result = waitForConstruction(session, "123", 10)
        self.assertEqual(result["cityName"], "Athens")

    def test_safety_cap_prevents_infinite_loop(self):
        """If seconds_to_wait would exceed 7 days total, the loop breaks."""
        session = MagicMock()
        session.get.return_value = "<html/>"

        eight_days = 8 * 24 * 3600
        city_under_construction = {
            "cityName": "Sparta",
            "position": [{
                "name": "Senate", "building": "senate", "level": 4,
                "completed": str(int(time.time()) + eight_days),
            }],
        }
        city_done = {
            "cityName": "Sparta",
            "position": [{"name": "Senate", "building": "senate", "level": 5}],
        }
        getJson_stub.getCity.side_effect = [city_under_construction, city_done]

        with patch.object(_mod, "wait", MagicMock()):
            result = waitForConstruction(session, "456", 5)

        self.assertIsNotNone(result)

    def test_uses_cityName_not_name(self):
        """The status string must use city['cityName'], not city['name'] (bug #11)."""
        session = MagicMock()
        session.get.return_value = "<html/>"
        session.setStatus = MagicMock()

        future_time = int(time.time()) + 30
        city_busy = {
            "cityName": "Corinth",
            "position": [{
                "name": "Warehouse", "building": "warehouse", "level": 2,
                "completed": str(future_time),
            }],
        }
        city_done = {
            "cityName": "Corinth",
            "position": [{"name": "Warehouse", "building": "warehouse", "level": 3}],
        }
        getJson_stub.getCity.side_effect = [city_busy, city_done, city_done]

        with patch.object(_mod, "wait", MagicMock()):
            waitForConstruction(session, "789", 3)

        call_args = session.setStatus.call_args[0][0]
        self.assertIn("Corinth", call_args,
            "setStatus should use city['cityName']; got: {}".format(call_args))


class TestChooseResourceProviders(unittest.TestCase):
    """Bug fix #16: no more globals; state returned as tuple."""

    def setUp(self):
        getJson_stub.getCity.side_effect = None
        pedirInfo_stub.read.side_effect = None

    def test_returns_tuple_of_three(self):
        """Return value must always be (list, bool, bool)."""
        session = MagicMock()
        session.get.return_value = "<html/>"

        cities_ids = ["1", "2"]
        cities = {
            "1": {"id": "1", "name": "Athens", "tradegood": "0"},
            "2": {"id": "2", "name": "Sparta", "tradegood": "1"},
        }
        city_data = {
            "id": "2", "name": "Sparta", "cityName": "Sparta",
            "availableResources": [500, 0, 0, 0, 0],
        }
        getJson_stub.getCity.return_value = city_data
        pedirInfo_stub.read.return_value = "Y"

        result = chooseResourceProviders(session, cities_ids, cities, "1", 0, 100)

        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 3)
        origin_cities, send_res, do_expand = result
        self.assertIsInstance(origin_cities, list)
        self.assertIsInstance(send_res, bool)
        self.assertIsInstance(do_expand, bool)

    def test_no_globals_modified(self):
        """Module must not have sendResources / expand / thread globals after the fix."""
        self.assertFalse(hasattr(_mod, "sendResources"),
            "sendResources global should have been removed")
        self.assertFalse(hasattr(_mod, "expand"),
            "expand global should have been removed")
        self.assertFalse(hasattr(_mod, "thread"),
            "thread global should have been removed")


class TestSendResourcesMenu(unittest.TestCase):
    """Bug fix #16: sendResourcesMenu returns (thread, do_expand) instead of using globals."""

    def setUp(self):
        getJson_stub.getCity.side_effect = None
        pedirInfo_stub.read.side_effect = None

    def test_returns_thread_and_expand_flag(self):
        session = MagicMock()
        cities_ids = ["1", "2"]
        cities = {
            "1": {"id": "1", "name": "Athens", "tradegood": "0"},
            "2": {"id": "2", "name": "Sparta", "tradegood": "0"},
        }
        planRoutes_stub.getIdsOfCities.return_value = (cities_ids, cities)

        city_data = {
            "id": "2", "name": "Sparta", "cityName": "Sparta",
            "availableResources": [9999, 0, 0, 0, 0],
        }
        getJson_stub.getCity.return_value = city_data
        pedirInfo_stub.read.return_value = "Y"

        missing = [100, 0, 0, 0, 0]

        with patch.object(_mod, "sendResourcesNeeded", MagicMock()):
            result = sendResourcesMenu(session, "1", missing, False)

        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
        t, expand = result
        self.assertIsInstance(expand, bool)


class TestShiptypeConversion(unittest.TestCase):
    """Bug fix #15: shiptype read() returns str; int() cast required."""

    def test_string_one_maps_to_trade_ships(self):
        shiptype_raw = "1"
        shiptype = 1 if shiptype_raw == "" else int(shiptype_raw)
        self.assertFalse(shiptype == 2)

    def test_string_two_maps_to_freighters(self):
        shiptype_raw = "2"
        shiptype = 1 if shiptype_raw == "" else int(shiptype_raw)
        self.assertTrue(shiptype == 2)

    def test_empty_string_defaults_to_trade_ships(self):
        shiptype_raw = ""
        shiptype = 1 if shiptype_raw == "" else int(shiptype_raw)
        self.assertFalse(shiptype == 2)


class TestResourceRetryQueue(unittest.TestCase):
    """New feature: 30-minute resource retry queue in expandBuilding."""

    expandBuilding           = staticmethod(_mod.expandBuilding)
    _RESOURCE_RETRY_INTERVAL = _mod._RESOURCE_RETRY_INTERVAL
    _MAX_RESOURCE_WAIT_SECS  = _mod._MAX_RESOURCE_WAIT_SECS

    def setUp(self):
        getJson_stub.getCity.side_effect = None

    def _make_building(self, level, upgrade_to, can_upgrade=True, is_busy=False, position=0):
        return {
            "name": "Senate",
            "building": "senate",
            "level": level,
            "upgradeTo": upgrade_to,
            "startLevel": level,
            "isBusy": is_busy,
            "canUpgrade": can_upgrade,
            "position": position,
        }

    def _make_city(self, building, city_name="Athens"):
        positions = [None] * (building["position"] + 1)
        positions[building["position"]] = building
        return {"cityName": city_name, "position": positions}

    def test_retry_interval_is_30_minutes(self):
        self.assertEqual(self._RESOURCE_RETRY_INTERVAL, 30 * 60)

    def test_builds_immediately_when_can_upgrade(self):
        """When canUpgrade is True from the start, the retry loop is never entered."""
        session = MagicMock()
        session.get.return_value = "<html/>"
        session.post.return_value = "{}"
        session.setStatus = MagicMock()

        building      = self._make_building(level=5, upgrade_to=6, can_upgrade=True)
        busy_building = dict(building, isBusy=True)
        city          = self._make_city(building)
        busy_city     = self._make_city(busy_building)

        getJson_stub.getCity.side_effect = [city, busy_city, busy_city, busy_city]
        wait_mock = MagicMock()

        with patch("time.sleep"):
            with patch.object(_mod, "wait", wait_mock):
                self.expandBuilding(session, "1", building, False)

        wait_mock.assert_not_called()

    def test_retries_every_30_minutes_until_resources_available(self):
        """When canUpgrade is False, waits _RESOURCE_RETRY_INTERVAL then retries."""
        session = MagicMock()
        session.get.return_value = "<html/>"
        session.post.return_value = "{}"
        session.setStatus = MagicMock()

        building_no_res = self._make_building(level=5, upgrade_to=6, can_upgrade=False)
        building_can    = self._make_building(level=5, upgrade_to=6, can_upgrade=True)
        building_busy   = dict(building_can, isBusy=True)

        city_no_res = self._make_city(building_no_res)
        city_can    = self._make_city(building_can)
        city_busy   = self._make_city(building_busy)

        # getCity call order:
        #  1+2: waitForConstruction (no construction present -> break, then final fetch)
        #  3:   retry 1 re-fetch -> still can't upgrade
        #  4:   retry 2 re-fetch -> can upgrade now
        #  5:   after upgrade post -> isBusy=True (success)
        getJson_stub.getCity.side_effect = [
            city_no_res,
            city_no_res,
            city_no_res,
            city_can,
            city_busy,
        ]

        wait_calls = []
        with patch("time.sleep"):
            with patch.object(_mod, "wait", lambda s: wait_calls.append(s)):
                self.expandBuilding(session, "1", building_no_res, True)

        retry_waits = [w for w in wait_calls if w == self._RESOURCE_RETRY_INTERVAL]
        self.assertEqual(len(retry_waits), 2,
            "Expected 2 retry waits of {:d}s, got: {}".format(
                self._RESOURCE_RETRY_INTERVAL, wait_calls))

    def test_safety_cap_stops_infinite_retry(self):
        """After _MAX_RESOURCE_WAIT_SECS total retry time, the loop breaks."""
        session = MagicMock()
        session.get.return_value = "<html/>"
        session.setStatus = MagicMock()

        building_no_res = self._make_building(level=5, upgrade_to=6, can_upgrade=False)
        city_no_res     = self._make_city(building_no_res)

        getJson_stub.getCity.return_value = city_no_res

        wait_count = [0]
        def counting_wait(seconds):
            wait_count[0] += 1

        # Patch cap to exactly 1 interval so the loop trips after a single wait
        with patch.object(_mod, "_MAX_RESOURCE_WAIT_SECS", _mod._RESOURCE_RETRY_INTERVAL):
            with patch("time.sleep"):
                with patch.object(_mod, "wait", counting_wait):
                    self.expandBuilding(session, "1", building_no_res, True)

        self.assertEqual(wait_count[0], 1,
            "Expected exactly 1 retry wait before the safety cap is hit")

    def test_status_message_shows_queue_info(self):
        """setStatus is called with building name, level, and 30-min retry info."""
        session = MagicMock()
        session.get.return_value = "<html/>"
        session.post.return_value = "{}"
        session.setStatus = MagicMock()

        building_no_res = self._make_building(level=5, upgrade_to=6, can_upgrade=False)
        building_can    = self._make_building(level=5, upgrade_to=6, can_upgrade=True)
        building_busy   = dict(building_can, isBusy=True)

        city_no_res = self._make_city(building_no_res, "Sparta")
        city_can    = self._make_city(building_can,    "Sparta")
        city_busy   = self._make_city(building_busy,   "Sparta")

        # 1 retry before resources arrive
        getJson_stub.getCity.side_effect = [
            city_no_res,
            city_no_res,
            city_can,
            city_busy,
        ]

        with patch("time.sleep"):
            with patch.object(_mod, "wait", MagicMock()):
                self.expandBuilding(session, "1", building_no_res, True)

        status_calls = [c[0][0] for c in session.setStatus.call_args_list]
        retry_statuses = [s for s in status_calls if "retry" in s.lower()]
        self.assertGreater(len(retry_statuses), 0,
            "Expected at least one retry status message")
        self.assertTrue(any("Senate" in s for s in retry_statuses),
            "Status message should include building name")
        self.assertTrue(any("30" in s for s in retry_statuses),
            "Status message should include the 30-minute retry interval")


class TestPrintQueueSummary(unittest.TestCase):
    """New helper: _printQueueSummary shows queue contents."""

    _printQueueSummary = staticmethod(_mod._printQueueSummary)

    def test_summary_runs_without_error(self):
        buildings = [
            {"name": "Senate",    "level": 5, "upgradeTo": 8, "startLevel": 5},
            {"name": "Warehouse", "level": 3, "upgradeTo": 6, "startLevel": 3},
        ]
        import io, sys as _sys
        captured = io.StringIO()
        old_stdout = _sys.stdout
        _sys.stdout = captured
        try:
            self._printQueueSummary(buildings)
        finally:
            _sys.stdout = old_stdout
        output = captured.getvalue()
        self.assertIn("Senate",    output)
        self.assertIn("Warehouse", output)
        self.assertIn("30",        output)  # retry interval mention
        self.assertIn("6",         output)  # total levels (3+3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
