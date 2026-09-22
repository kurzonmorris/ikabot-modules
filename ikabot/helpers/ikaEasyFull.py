#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""Serve the FULL IkaEasy extension in-page (Phase 2, experimental / opt-in).

Phase 1 (ikaEasyInject.py) serves a small hand-written "lite" feature bundle.
Phase 2 goes further: it serves the *entire* unmodified IkaEasy extension so
the whole thing runs in any browser with no Chrome extension installed. The
extension's Chrome-only APIs are provided by a page-world shim (served from the
lite bundle as full/chrome-shim.js); this module only locates and serves the
extension's own files under the /ikaeasy-full/ route.

This is OPT-IN and default-off — nothing here runs unless the user enables full
mode — so it cannot affect the working lite experience or the proxied game.

Pure and flask-free so it can be unit-tested on its own.
"""

import os

FULL_ROUTE_PREFIX = "ikaeasy-full/"

# Where the unmodified extension lives. Checked in order; first with a
# manifest.json wins. Override with the IKAEASY_EXT_DIR environment variable.
_HELPERS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HELPERS_DIR, "..", ".."))


def _data_dir():
    if os.name == "nt":
        return os.path.join(os.getenv("APPDATA", os.path.expanduser("~")), ".ikabot")
    return os.path.expanduser("~/.ikabot")


_CANDIDATE_DIRS = [
    os.environ.get("IKAEASY_EXT_DIR", ""),
    # User-writable override that survives rebuilds — edit here for quick fixes.
    os.path.join(_data_dir(), "ikaeasy_full"),
    # A copy bundled inside ikabot (how a real deployment would ship it).
    os.path.join(_HELPERS_DIR, "ikaeasy_full"),
    # A folder placed next to the ikabot package.
    os.path.join(_REPO_ROOT, "ikaeasy_ext"),
    # This repo's own layout (dev / testing).
    os.path.join(_REPO_ROOT, "ika-easy v3 local ip fixed", "IkaEasy_V3_LocalIP_v2"),
]

_CONTENT_TYPES = {
    ".js":   "application/javascript; charset=utf-8",
    ".mjs":  "application/javascript; charset=utf-8",
    ".css":  "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".ejs":  "text/plain; charset=utf-8",
    ".svg":  "image/svg+xml",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif":  "image/gif",
    ".cur":  "image/x-icon",
    ".ico":  "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf":  "font/ttf",
}

# Cache the resolved directory so we don't stat the filesystem on every request.
_resolved = {"dir": None, "done": False}


def extension_dir():
    """Return the resolved extension directory, or None if not found."""
    if _resolved["done"]:
        return _resolved["dir"]
    found = None
    for cand in _CANDIDATE_DIRS:
        if cand and os.path.isfile(os.path.join(cand, "manifest.json")):
            found = os.path.realpath(cand)
            break
    _resolved["dir"] = found
    _resolved["done"] = True
    return found


def _reset_cache_for_tests():
    _resolved["dir"] = None
    _resolved["done"] = False


def is_available():
    return extension_dir() is not None


def _safe_asset_path(request_path):
    """Resolve a ``ikaeasy-full/...`` request path to a real file inside the
    extension directory, or None if it escapes / doesn't exist."""
    base = extension_dir()
    if not base:
        return None
    if not request_path:
        return None
    rel = request_path.lstrip("/")
    if rel.startswith(FULL_ROUTE_PREFIX):
        rel = rel[len(FULL_ROUTE_PREFIX):]
    rel = rel.split("?", 1)[0]
    if not rel or rel.endswith("/"):
        return None

    candidate = os.path.realpath(os.path.join(base, rel))
    if candidate != base and not candidate.startswith(base + os.sep):
        return None
    if not os.path.isfile(candidate):
        return None
    return candidate


# A few extension files use root-absolute asset paths (/js/, /tpl/) that assume
# the extension is served from the server root. In full mode it is served from
# /ikaeasy-full/, so those paths would hit the game proxy and 404. Rewrite just
# these known files at serve time. Keyed by path relative to the extension dir.
_FULL_REWRITES = {
    "sandbox.html": [
        ('src="/js/', 'src="/ikaeasy-full/js/'),
    ],
    "js/sandbox/templater.js": [
        ("this._prefix = '/tpl/'", "this._prefix = '/ikaeasy-full/tpl/'"),
    ],
}


def serve_full_asset(request_path):
    """Return (bytes, content_type) for an extension file, or None if not found.
    Safe against path traversal."""
    path = _safe_asset_path(request_path)
    if path is None:
        return None
    try:
        with open(path, "rb") as f:
            content = f.read()
    except OSError:
        return None

    base = extension_dir()
    rel = os.path.relpath(path, base).replace(os.sep, "/") if base else ""
    rewrites = _FULL_REWRITES.get(rel)
    if rewrites:
        try:
            text = content.decode("utf-8")
            for old, new in rewrites:
                text = text.replace(old, new)
            content = text.encode("utf-8")
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass

    ext = os.path.splitext(path)[1].lower()
    return content, _CONTENT_TYPES.get(ext, "application/octet-stream")
