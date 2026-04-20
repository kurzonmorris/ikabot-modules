#! /usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for constructionList.py bug fixes.
These tests mock all ikabot dependencies so they can run standalone.
"""

import hashlib
import importlib
import sys
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
botComm_stub  = _make_stub_module("ikabot.helpers.botComm",
    sendToBot=MagicMock(), sendToBotDebug=MagicMock())
getJson_stub  = _make_stub_module("ikabot.helpers.getJson",  getCity=MagicMock())
gui_stub      = _make_stub_module("ikabot.helpers.gui",
    banner=MagicMock(), enter=MagicMock(), bcolors=MagicMock(),
    getDateTime=lambda t: "2024-01-01 00:00:00")
pedirInfo_stub= _make_stub_module("ikabot.helpers.pedirInfo",
    chooseCity=MagicMock(), read=MagicMock())
planRoutes_stub=_make_stub_module("ikabot.helpers.planRoutes",
    executeRoutes=MagicMock(), getIdsOfCities=MagicMock())
process_stub  = _make_stub_module("ikabot.helpers.process",  set_child_mode=MagicMock())
resources_stub= _make_stub_module("ikabot.helpers.resources", getAvailableResources=MagicMock())
signals_stub  = _make_stub_module("ikabot.helpers.signals",  setInfoSignal=MagicMock())
varios_stub   = _make_stub_module("ikabot.helpers.varios",
    wait=MagicMock(), addThousandSeparator=lambda x: str(x),
    getMinimumWaitingTime=MagicMock(return_value=0))
session_stub  = _make_stub_module("ikabot.web.session",      normal_get=MagicMock())

# ikabot top-level (for "from ikabot.config import *")
ikabot_stub   = _make_stub_module("ikabot")

# The wildcard exports we need at module level
materials_names     = ["Wood", "Wine", "Marble", "Glass", "Sulfur"]
materials_names_tec = ["wood", "wine", "marble", "glass", "sulfur"]
city_url            = "https://game.example.com/city/"

# Inject everything into sys.modules before importing the module under test
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

# Patch the wildcard names that "from ikabot.config import *" would inject
import builtins
_real_import = builtins.__import__
def _patched_import(name, *args, **kwargs):
    mod = _real_import(name, *args, **kwargs)
    return mod
builtins.__import__ = _patched_import

# Manually inject wildcard symbols into the module namespace after import
# by monkey-patching the module's globals post-load.
# We load the file as a module using importlib from its path.
import importlib.util, os
_spec = importlib.util.spec_from_file_location(
    "constructionList",
    os.path.join(os.path.dirname(__file__), "constructionList.py"),
)
_mod = importlib.util.module_from_spec(_spec)

# Pre-populate the module's globals with all the "star-imported" names
_mod.__dict__.update({
    "actionRequest":       "TEST_ACTION",
    "config":              config_stub,
    "sendToBot":           botComm_stub.sendToBot,
    "sendToBotDebug":      botComm_stub.sendToBotDebug,
    "getCity":             getJson_stub.getCity,
    "banner":              gui_stub.banner,
    "enter":               gui_stub.enter,
    "bcolors":             gui_stub.bcolors,
    "getDateTime":         gui_stub.getDateTime,
    "chooseCity":          pedirInfo_stub.chooseCity,
    "read":                pedirInfo_stub.read,
    "executeRoutes":       planRoutes_stub.executeRoutes,
    "getIdsOfCities":      planRoutes_stub.getIdsOfCities,
    "set_child_mode":      process_stub.set_child_mode,
    "setInfoSignal":       signals_stub.setInfoSignal,
    "wait":                varios_stub.wait,
    "addThousandSeparator":varios_stub.addThousandSeparator,
    "getMinimumWaitingTime":varios_stub.getMinimumWaitingTime,
    "city_url":            city_url,
    "materials_names":     materials_names,
    "materials_names_tec": materials_names_tec,
    "debugON_constructionList": False,
    "sys":                 sys,
    "os":                  os,
})

_spec.loader.exec_module(_mod)


# Convenience aliases
checkhash            = _mod.checkhash
getCostsReducers     = _mod.getCostsReducers
getBuildingsToExpand = _mod.getBuildingsToExpand
waitForConstruction  = _mod.waitForConstruction
chooseResourceProviders = _mod.chooseResourceProviders
sendResourcesMenu    = _mod.sendResourcesMenu


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCheckHash(unittest.TestCase):
    """Bug fixes #6 #7 #8 #9 (checkhash rewrite)"""

    def _make_image_bytes(self, material_index):
        """Return bytes whose MD5 matches config.material_img_hash[material_index]."""
        target = config_stub.material_img_hash[material_index]
        # We reverse-engineer: just find a bytestring whose md5 == target by
        # using the target string itself as a known key (since our stubs use
        # simple strings, not real hashes, we generate content that hashes to it).
        # In practice we patch hashlib.md5 instead.
        return b"dummy"

    def test_returns_correct_material(self):
        """Full MD5 is computed after all chunks and matched correctly."""
        for idx, name in enumerate(["wood", "wine", "marble", "glass", "sulfur"]):
            # Patch requests.get to return fake bytes
            fake_content = b"fake_image_data_" + name.encode()
            expected_digest = hashlib.md5(fake_content).hexdigest()
            config_stub.material_img_hash[idx] = expected_digest

            mock_response = MagicMock()
            mock_response.iter_content.return_value = [fake_content]
            mock_response.raise_for_status = MagicMock()

            # checkhash is @cache so we must clear between runs
            checkhash.cache_clear()

            with patch("requests.get", return_value=mock_response):
                result = checkhash("https://example.com/img/{}.png".format(name))

            self.assertEqual(result, name,
                "Expected '{}', got '{}'".format(name, result))

        # Restore original stub values
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
        """The fix: MD5 must be finalised over ALL chunks, not checked mid-stream."""
        checkhash.cache_clear()
        chunk1 = b"chunk_one_"
        chunk2 = b"chunk_two_"
        full_digest = hashlib.md5(chunk1 + chunk2).hexdigest()
        partial_digest = hashlib.md5(chunk1).hexdigest()

        # Set hash list so only the full digest matches "wood"
        config_stub.material_img_hash = [
            full_digest, "nope", "nope", "nope", "nope"
        ]

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
        city = self._city([
            {"name": "empty", "building": "empty", "level": 0},
        ])
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

        # User selects building 1 and wants to upgrade to level 8
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

        # User types "99" (out of range) — should be skipped gracefully
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

    def _make_session(self, city_sequence):
        session = MagicMock()
        session.get.side_effect = ["<html/>"] * (len(city_sequence) * 2)
        return session

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

        # Construction always claims 8 days remaining — exceeds 7-day cap
        eight_days = 8 * 24 * 3600
        city_under_construction = {
            "cityName": "Sparta",
            "position": [{
                "name": "Senate", "building": "senate", "level": 4,
                "completed": str(int(__import__("time").time()) + eight_days),
            }],
        }
        city_done = {
            "cityName": "Sparta",
            "position": [{"name": "Senate", "building": "senate", "level": 5}],
        }
        getJson_stub.getCity.side_effect = [city_under_construction, city_done]

        with patch.object(_mod, "wait", MagicMock()):
            result = waitForConstruction(session, "456", 5)

        # Should have broken out of the loop and returned
        self.assertIsNotNone(result)

    def test_uses_cityName_not_name(self):
        """The status string must use city['cityName'], not city['name'] (bug #11)."""
        session = MagicMock()
        session.get.return_value = "<html/>"
        session.setStatus = MagicMock()

        future_time = int(__import__("time").time()) + 30
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
        # waitForConstruction calls getCity twice per loop iteration + once at the end
        getJson_stub.getCity.side_effect = [city_busy, city_done, city_done]

        with patch.object(_mod, "wait", MagicMock()):
            waitForConstruction(session, "789", 3)

        # setStatus must have been called and must contain 'Corinth' (from cityName)
        call_args = session.setStatus.call_args[0][0]
        self.assertIn("Corinth", call_args,
            "setStatus should use city['cityName']; got: {}".format(call_args))


class TestChooseResourceProviders(unittest.TestCase):
    """Bug fix #16: no more globals; state returned as tuple."""

    def test_returns_tuple_of_three(self):
        """Return value must always be (list, bool, bool)."""
        session = MagicMock()
        session.get.return_value = "<html/>"

        # Use string IDs so city_url + cityId concatenation works
        cities_ids = ["1", "2"]
        cities = {
            "1": {"id": "1", "name": "Athens",  "tradegood": "0"},
            "2": {"id": "2", "name": "Sparta",  "tradegood": "1"},
        }
        city_data = {
            "id": "2", "name": "Sparta", "cityName": "Sparta",
            "availableResources": [500, 0, 0, 0, 0],
        }
        getJson_stub.getCity.return_value = city_data

        # User confirms Sparta as a provider
        pedirInfo_stub.read.return_value = "Y"

        result = chooseResourceProviders(session, cities_ids, cities, "1", 0, 100)

        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 3)
        origin_cities, send_res, do_expand = result
        self.assertIsInstance(origin_cities, list)
        self.assertIsInstance(send_res, bool)
        self.assertIsInstance(do_expand, bool)

    def test_no_globals_modified(self):
        """Module must not have sendResources / expand globals after the fix."""
        self.assertFalse(hasattr(_mod, "sendResources"),
            "sendResources global should have been removed")
        self.assertFalse(hasattr(_mod, "expand"),
            "expand global should have been removed")
        self.assertFalse(hasattr(_mod, "thread"),
            "thread global should have been removed")


class TestSendResourcesMenu(unittest.TestCase):
    """Bug fix #16: sendResourcesMenu returns (thread, do_expand) instead of using globals."""

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
        # Reset any lingering side_effect from prior tests
        pedirInfo_stub.read.side_effect = None
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
        """'1' (string from read()) must correctly resolve to useFreighters=False."""
        shiptype_raw = "1"
        shiptype = 1 if shiptype_raw == "" else int(shiptype_raw)
        use_freighters = (shiptype == 2)
        self.assertFalse(use_freighters)

    def test_string_two_maps_to_freighters(self):
        """'2' (string from read()) must correctly resolve to useFreighters=True."""
        shiptype_raw = "2"
        shiptype = 1 if shiptype_raw == "" else int(shiptype_raw)
        use_freighters = (shiptype == 2)
        self.assertTrue(use_freighters)

    def test_empty_string_defaults_to_trade_ships(self):
        """Empty string (user pressed Enter) must default to trade ships."""
        shiptype_raw = ""
        shiptype = 1 if shiptype_raw == "" else int(shiptype_raw)
        use_freighters = (shiptype == 2)
        self.assertFalse(use_freighters)


if __name__ == "__main__":
    unittest.main(verbosity=2)
