#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""Server-side injection of the IkaEasy "lite" in-page features.

This lets the ikabot web server serve the IkaEasy feature panels (Resource
Production Manager, Tavern Manager, RTM transport scheduling, Construction
Manager report) directly into the game page, so they work in ANY browser with
no Chrome extension installed.

Design goals (in priority order):
  1. Never break the proxy. Every function here is pure and defensive; the
     web server calls them inside try/except so a failure degrades to the
     unmodified page rather than a broken one.
  2. Only ever touch a real, full HTML game page — never AJAX/JSON responses
     (which the game parses as arrays) and never images.
  3. Be idempotent — injecting twice is a no-op.

This module deliberately imports nothing from flask or the heavier ikabot
helpers so it can be unit-tested on its own.
"""

import os

# Bump when the served bundle changes so browsers re-fetch it (cache-buster).
LITE_VERSION = "1.0.0"

# URL path prefix the web server routes to serve_lite_asset().
LITE_ROUTE_PREFIX = "ikaeasy-lite/"

# Marker attribute so we never inject twice into the same document.
_INJECT_MARKER = "data-ikaeasy-lite"

# Directory holding the static bundle (loader.js, css, feature widgets).
_LITE_DIR = os.path.join(os.path.dirname(__file__), "ikaeasy_lite")

_CONTENT_TYPES = {
    ".js":   "application/javascript; charset=utf-8",
    ".mjs":  "application/javascript; charset=utf-8",
    ".css":  "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".svg":  "image/svg+xml",
    ".png":  "image/png",
    ".gif":  "image/gif",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}


def is_full_html_page(text):
    """True only for a complete HTML document we may safely inject into.

    The game's page loads are full HTML documents; nearly everything after
    that is AJAX returning JSON arrays (text starting with ``[``). We must
    never modify those. We also skip anything that isn't clearly HTML.
    """
    if not text or not isinstance(text, str):
        return False
    head = text.lstrip()[:512].lower()
    if not head.startswith(("<!doctype html", "<html")):
        return False
    # A real page has a </head> or </body> we can anchor to.
    lowered = text.lower()
    return "</head>" in lowered or "</body>" in lowered


def _script_tag():
    return (
        f'<script {_INJECT_MARKER}="1" '
        f'src="/{LITE_ROUTE_PREFIX}loader.js?v={LITE_VERSION}" defer></script>'
    )


def inject(html):
    """Return *html* with the IkaEasy-lite loader script inserted.

    Idempotent: if the marker is already present the input is returned
    unchanged. If no anchor is found the input is returned unchanged. Never
    raises — on any unexpected input it returns the original string.
    """
    try:
        if not isinstance(html, str) or _INJECT_MARKER in html:
            return html

        tag = _script_tag()

        # Prefer to inject right before </head>; fall back to </body>. Match
        # the tag case-insensitively but preserve the document otherwise.
        for anchor in ("</head>", "</body>"):
            idx = html.lower().find(anchor)
            if idx != -1:
                return html[:idx] + tag + html[idx:]
        return html
    except Exception:
        return html


def _safe_asset_path(request_path):
    """Resolve a ``ikaeasy-lite/...`` request path to a real file inside the
    bundle directory, or None if it escapes the directory / doesn't exist.

    Blocks path traversal by resolving real paths and confirming the result
    stays within _LITE_DIR.
    """
    if not request_path:
        return None
    # Normalise: strip a leading slash and the route prefix.
    rel = request_path.lstrip("/")
    if rel.startswith(LITE_ROUTE_PREFIX):
        rel = rel[len(LITE_ROUTE_PREFIX):]
    # Drop any query string if one slipped through.
    rel = rel.split("?", 1)[0]
    if not rel or rel.endswith("/"):
        return None

    base = os.path.realpath(_LITE_DIR)
    candidate = os.path.realpath(os.path.join(base, rel))

    # Must stay within the bundle directory and be an existing file.
    if candidate != base and not candidate.startswith(base + os.sep):
        return None
    if not os.path.isfile(candidate):
        return None
    return candidate


def serve_lite_asset(request_path):
    """Return (bytes, content_type) for a bundle file, or None if not found.

    *request_path* is the request path, e.g. "ikaeasy-lite/loader.js" or
    "/ikaeasy-lite/css/lite.css". Safe against path traversal.
    """
    path = _safe_asset_path(request_path)
    if path is None:
        return None
    try:
        with open(path, "rb") as f:
            content = f.read()
    except OSError:
        return None
    ext = os.path.splitext(path)[1].lower()
    ctype = _CONTENT_TYPES.get(ext, "application/octet-stream")
    return content, ctype
