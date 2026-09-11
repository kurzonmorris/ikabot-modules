#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""Tests for the IkaEasy-lite injection helper.

These prove the web-server injection is SAFE:
  * only full HTML game pages are modified,
  * AJAX/JSON responses and images are never touched,
  * injection is idempotent,
  * asset serving is confined to the bundle dir (no path traversal).

Pure stdlib unittest — no flask, no pytest, no network. Run with:
    python3 -m unittest tests.test_ikaeasy_inject -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ikabot.helpers import ikaEasyInject as inj


FULL_PAGE = (
    "<!DOCTYPE html>\n<html><head><title>Ikariam</title>"
    "<script>var x=1;</script></head><body><div id='container'>hi</div></body></html>"
)


class TestIsFullHtmlPage(unittest.TestCase):
    def test_accepts_full_doctype_page(self):
        self.assertTrue(inj.is_full_html_page(FULL_PAGE))

    def test_accepts_html_without_doctype(self):
        self.assertTrue(inj.is_full_html_page("<html><head></head><body></body></html>"))

    def test_accepts_leading_whitespace(self):
        self.assertTrue(inj.is_full_html_page("   \n  <!doctype html><head></head>x"))

    def test_rejects_ajax_json_array(self):
        # This is what nearly every in-game navigation returns.
        self.assertFalse(inj.is_full_html_page('[["provideFeedback",[{"x":1}]]]'))

    def test_rejects_json_object(self):
        self.assertFalse(inj.is_full_html_page('{"ok": true, "cities": {}}'))

    def test_rejects_fragment_without_head_or_body(self):
        self.assertFalse(inj.is_full_html_page("<div>just a fragment</div>"))

    def test_rejects_png_binary_ish(self):
        self.assertFalse(inj.is_full_html_page("\x89PNG\r\n\x1a\n...."))

    def test_rejects_empty_and_none(self):
        self.assertFalse(inj.is_full_html_page(""))
        self.assertFalse(inj.is_full_html_page(None))
        self.assertFalse(inj.is_full_html_page(b"<html><head></head>"))  # bytes, not str


class TestInject(unittest.TestCase):
    def test_injects_before_head_close(self):
        out = inj.inject(FULL_PAGE)
        self.assertIn(inj.LITE_ROUTE_PREFIX + "loader.js", out)
        self.assertIn(inj._INJECT_MARKER, out)
        # Script must land before </head>.
        self.assertLess(out.index(inj._INJECT_MARKER), out.lower().index("</head>"))

    def test_preserves_original_content(self):
        out = inj.inject(FULL_PAGE)
        self.assertIn("<div id='container'>hi</div>", out)
        self.assertIn("<title>Ikariam</title>", out)

    def test_idempotent(self):
        once = inj.inject(FULL_PAGE)
        twice = inj.inject(once)
        self.assertEqual(once, twice)
        self.assertEqual(once.count(inj._INJECT_MARKER), 1)

    def test_body_fallback_when_no_head(self):
        page = "<html><body><p>no head here</p></body></html>"
        out = inj.inject(page)
        self.assertIn(inj._INJECT_MARKER, out)
        self.assertLess(out.index(inj._INJECT_MARKER), out.lower().index("</body>"))

    def test_no_anchor_returns_unchanged(self):
        page = "<html><p>no closing head or body</p>"
        self.assertEqual(inj.inject(page), page)

    def test_case_insensitive_head(self):
        page = "<HTML><HEAD></HEAD><BODY></BODY></HTML>"
        out = inj.inject(page)
        self.assertIn(inj._INJECT_MARKER, out)

    def test_non_string_returns_unchanged(self):
        self.assertEqual(inj.inject(None), None)
        self.assertEqual(inj.inject(123), 123)

    def test_ajax_response_shape_never_injected_end_to_end(self):
        # Belt and braces: even if someone passed an AJAX body to inject(),
        # the caller gates on is_full_html_page first — verify that gate.
        ajax = '[["changeView","tavern",...]]'
        self.assertFalse(inj.is_full_html_page(ajax))


class TestServeLiteAsset(unittest.TestCase):
    def test_serves_loader_js(self):
        res = inj.serve_lite_asset("ikaeasy-lite/loader.js")
        self.assertIsNotNone(res)
        content, ctype = res
        self.assertIsInstance(content, bytes)
        self.assertIn("javascript", ctype)
        self.assertIn(b"IKEL", content)

    def test_serves_css(self):
        res = inj.serve_lite_asset("ikaeasy-lite/css/lite.css")
        self.assertIsNotNone(res)
        _, ctype = res
        self.assertIn("text/css", ctype)

    def test_serves_feature(self):
        for name in ("production", "tavern", "construction", "transport"):
            res = inj.serve_lite_asset("ikaeasy-lite/features/%s.js" % name)
            self.assertIsNotNone(res, name)

    def test_leading_slash_ok(self):
        self.assertIsNotNone(inj.serve_lite_asset("/ikaeasy-lite/loader.js"))

    def test_strips_query_string(self):
        self.assertIsNotNone(inj.serve_lite_asset("ikaeasy-lite/loader.js?v=1.0.0"))

    def test_missing_file_returns_none(self):
        self.assertIsNone(inj.serve_lite_asset("ikaeasy-lite/does-not-exist.js"))

    def test_path_traversal_blocked(self):
        for evil in (
            "ikaeasy-lite/../ikaEasyInject.py",
            "ikaeasy-lite/../../config.py",
            "ikaeasy-lite/../../../etc/passwd",
            "ikaeasy-lite/..%2f..%2fconfig.py",
        ):
            self.assertIsNone(inj.serve_lite_asset(evil), evil)

    def test_directory_request_returns_none(self):
        self.assertIsNone(inj.serve_lite_asset("ikaeasy-lite/features/"))
        self.assertIsNone(inj.serve_lite_asset("ikaeasy-lite/"))


if __name__ == "__main__":
    unittest.main()
