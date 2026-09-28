"""The panel edits destinations that hold bot tokens, over a LAN and Tailscale.

So the rules worth pinning are about what leaves the server and what reaches
the disk: a token is never sent to the browser, an edit that sends nothing back
keeps the stored token rather than blanking it, and the file is not readable by
anyone but its owner. The panel serves this to whoever can reach the port.
"""

import importlib.machinery
import importlib.util
import json
import os
import stat
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

import ikabot.helpers.notifyTargets as nt

_PANEL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "docker", "ika-panel_v1.3.0",
)

TOKEN = "123456:abcdefghijklmnopqrstuvwx"
HOOK = "https://discord.com/api/webhooks/1/abcdefghijklmnop"


@pytest.fixture(scope="module")
def panel():
    loader = importlib.machinery.SourceFileLoader("ika_panel", _PANEL)
    spec = importlib.util.spec_from_loader("ika_panel", loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def server(panel, tmp_path, monkeypatch):
    store = tmp_path / "notify_targets.json"
    monkeypatch.setattr(nt, "TARGETS_FILE", str(store))
    monkeypatch.setattr(panel, "USER", "t")
    monkeypatch.setattr(panel, "PASS", "t")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), panel.Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:%d" % srv.server_address[1], store
    srv.shutdown()
    srv.server_close()


def call(base, path, body=None):
    url = base + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    if data:
        req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Basic dDp0")          # t:t
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def telegram(name="My phone", **extra):
    body = {"name": name, "kind": "telegram",
            "telegram": {"botToken": TOKEN, "chatId": "42"}}
    body.update(extra)
    return body


# ------------------------------------------------------------------- auth ---

def test_the_destinations_cannot_be_read_without_signing_in(server):
    base, _ = server
    req = urllib.request.Request(base + "/api/targets")
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(req, timeout=10)
    assert caught.value.code == 401


def test_the_destinations_cannot_be_written_without_signing_in(server):
    base, store = server
    req = urllib.request.Request(base + "/api/targets", method="POST",
                                 data=json.dumps({"action": "save",
                                                  "target": telegram()}).encode())
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(req, timeout=10)
    assert caught.value.code == 401
    assert not store.exists()


# ------------------------------------------------------------------ round ---

def test_a_destination_is_added_and_listed_back(server):
    base, _ = server
    status, out = call(base, "/api/targets", {"action": "save",
                                              "target": telegram()})
    assert (status, out.get("ok")) == (200, True)
    status, out = call(base, "/api/targets")
    assert [t["name"] for t in out["targets"]] == ["My phone"]


def test_the_token_never_reaches_the_browser(server):
    base, _ = server
    call(base, "/api/targets", {"action": "save", "target": telegram()})
    _, out = call(base, "/api/targets")
    assert TOKEN not in json.dumps(out)
    assert out["targets"][0]["telegram"]["botToken"].endswith("uvwx")
    # The chat id is not a credential and the form needs it back.
    assert out["targets"][0]["telegram"]["chatId"] == "42"


def test_an_edit_that_sends_no_token_keeps_the_stored_one(server):
    base, store = server
    _, out = call(base, "/api/targets", {"action": "save", "target": telegram()})
    tid = out["id"]
    status, out = call(base, "/api/targets", {
        "action": "save",
        "target": {"id": tid, "name": "Renamed", "kind": "telegram",
                   "telegram": {"botToken": "", "chatId": "42"}}})
    assert status == 200
    stored = json.loads(store.read_text())["targets"]
    assert len(stored) == 1
    assert stored[0]["name"] == "Renamed"
    assert stored[0]["telegram"]["botToken"] == TOKEN


def test_two_telegram_accounts_are_two_rows_not_a_replacement(server):
    base, _ = server
    call(base, "/api/targets", {"action": "save", "target": telegram("One")})
    call(base, "/api/targets", {"action": "save", "target": telegram("Two")})
    _, out = call(base, "/api/targets")
    assert sorted(t["name"] for t in out["targets"]) == ["One", "Two"]


def test_a_destination_is_deleted(server):
    base, _ = server
    _, out = call(base, "/api/targets", {"action": "save", "target": telegram()})
    status, _ = call(base, "/api/targets", {"action": "delete", "id": out["id"]})
    assert status == 200
    _, out = call(base, "/api/targets")
    assert out["targets"] == []


# ------------------------------------------------------------- refusals -----

def test_a_webhook_that_is_not_discord_is_refused(server):
    base, store = server
    status, out = call(base, "/api/targets", {
        "action": "save",
        "target": {"name": "Bad", "kind": "discord",
                   "discord": {"webhookUrl": "https://evil.example/hook"}}})
    assert status == 400
    assert "discord.com" in out["error"]
    assert not store.exists()


def test_an_unknown_id_is_not_found_rather_than_a_crash(server):
    base, _ = server
    assert call(base, "/api/targets", {"action": "delete", "id": "nope"})[0] == 404
    assert call(base, "/api/targets", {"action": "test", "id": "nope"})[0] == 404


def test_an_unknown_action_is_refused(server):
    base, _ = server
    status, out = call(base, "/api/targets", {"action": "drop everything"})
    assert status == 400
    assert out["error"]


def test_a_body_that_is_not_json_is_refused(server):
    base, _ = server
    req = urllib.request.Request(base + "/api/targets", method="POST",
                                 data=b"not json at all")
    req.add_header("Authorization", "Basic dDp0")
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(req, timeout=10)
    assert caught.value.code == 400


# ------------------------------------------------------------------ disk ----

def test_the_file_of_tokens_is_owner_only(server):
    base, store = server
    call(base, "/api/targets", {"action": "save", "target": telegram()})
    mode = stat.S_IMODE(os.stat(store).st_mode)
    assert mode & (stat.S_IRGRP | stat.S_IROTH) == 0
