#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""Tests for the Phase 2 full-extension serving layer (ikaEasyFull).

Proves asset serving is confined to the extension directory (no traversal),
resolves the dev-layout extension folder, and degrades to None when absent.

Pure stdlib unittest. Run with:
    python3 -m unittest tests.test_ikaeasy_full -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ikabot.helpers import ikaEasyFull as full


class TestExtensionDir(unittest.TestCase):
    def setUp(self):
        full._reset_cache_for_tests()

    def test_resolves_dev_layout(self):
        # This repo ships the extension under "ika-easy .../IkaEasy_V3_LocalIP_v2".
        d = full.extension_dir()
        self.assertIsNotNone(d, "extension dir should resolve in this repo")
        self.assertTrue(os.path.isfile(os.path.join(d, "manifest.json")))

    def test_is_available(self):
        self.assertTrue(full.is_available())

    def test_env_override(self):
        full._reset_cache_for_tests()
        old = os.environ.get("IKAEASY_EXT_DIR")
        os.environ["IKAEASY_EXT_DIR"] = "/definitely/not/here"
        try:
            # Env points nowhere, but later candidates still resolve the repo copy.
            full._reset_cache_for_tests()
            self.assertTrue(full.is_available())
        finally:
            if old is None:
                del os.environ["IKAEASY_EXT_DIR"]
            else:
                os.environ["IKAEASY_EXT_DIR"] = old
            full._reset_cache_for_tests()


class TestServeFullAsset(unittest.TestCase):
    def setUp(self):
        full._reset_cache_for_tests()

    def test_serves_manifest(self):
        res = full.serve_full_asset("ikaeasy-full/manifest.json")
        self.assertIsNotNone(res)
        content, ctype = res
        self.assertIn(b"manifest_version", content)
        self.assertIn("json", ctype)

    def test_serves_nested_js(self):
        res = full.serve_full_asset("ikaeasy-full/js/initModule.js")
        self.assertIsNotNone(res)
        _, ctype = res
        self.assertIn("javascript", ctype)

    def test_leading_slash_and_query(self):
        self.assertIsNotNone(full.serve_full_asset("/ikaeasy-full/manifest.json?v=1.0.1"))

    def test_missing_returns_none(self):
        self.assertIsNone(full.serve_full_asset("ikaeasy-full/nope/nothing.js"))

    def test_path_traversal_blocked(self):
        for evil in (
            "ikaeasy-full/../../ikabot/config.py",
            "ikaeasy-full/../../../etc/passwd",
            "ikaeasy-full/../ikaEasyFull.py",
        ):
            self.assertIsNone(full.serve_full_asset(evil), evil)

    def test_directory_returns_none(self):
        self.assertIsNone(full.serve_full_asset("ikaeasy-full/js/"))


if __name__ == "__main__":
    unittest.main()
