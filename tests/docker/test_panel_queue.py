"""A queue of presses, run one at a time, because two at once collide.

Pressing a button across twenty-four instances takes minutes, and two key
sequences typed into one instance at the same moment interleave into nonsense.
So the rules worth pinning are about order and exclusion: steps run in the
order shown, a manual press cannot start while a step is running, stopping
never cuts a step off part way through, and nothing reaches the queue that was
not meant to be queueable.
"""

import base64
import importlib.machinery
import importlib.util
import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

_PANEL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "docker", "ika-panel_v1.4.0",
)
AUTH = "Basic " + base64.b64encode(b"t:t").decode()


@pytest.fixture(scope="module")
def panel():
    loader = importlib.machinery.SourceFileLoader("ika_panel", _PANEL)
    spec = importlib.util.spec_from_loader("ika_panel", loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def server(panel, monkeypatch):
    # Every step is a stub: these tests are about the queue, not about tmux.
    ran = []

    def fake(act, arg):
        ran.append(act if not arg else "%s:%s" % (act, arg))
        time.sleep(float(os.environ.get("FAKE_STEP", "0") or 0))
        if arg == "boom":
            return "", "it went wrong", act
        return "did %s" % act, "", act

    monkeypatch.setattr(panel, "QUEUEABLE",
                        {k: fake for k in panel.QUEUEABLE}, raising=True)
    monkeypatch.setattr(panel, "USER", "t")
    monkeypatch.setattr(panel, "PASS", "t")
    with panel._queue_lock:
        panel._queue["items"] = []
        panel._queue["running"] = False
        panel._queue["stop"] = False

    srv = ThreadingHTTPServer(("127.0.0.1", 0), panel.Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:%d" % srv.server_address[1]
    yield base, ran, panel
    with panel._queue_lock:
        panel._queue["stop"] = True
    srv.shutdown()
    srv.server_close()


def call(base, path, body=None, auth=True, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data,
                                 method="POST" if data else "GET")
    if data:
        req.add_header("Content-Type", "application/json")
    if auth:
        req.add_header("Authorization", AUTH)
    def body_of(raw):
        # A refused request answers with no body at all, which is not an error
        # here — the status is the whole answer.
        return json.loads(raw) if raw else {}

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, body_of(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, body_of(exc.read())


def put(base, *steps):
    items = [{"action": a, "arg": g, "label": l} for a, g, l in steps]
    return call(base, "/api/queue", {"op": "set", "items": items})


def rows(base):
    return [(i["label"], i["state"]) for i in call(base, "/api/queue")[1]["items"]]


def settle(base, seconds=20):
    for _ in range(int(seconds * 10)):
        time.sleep(0.1)
        if not call(base, "/api/queue")[1]["running"]:
            return True
    return False


# ------------------------------------------------------------------ auth ---

def test_the_queue_cannot_be_read_without_signing_in(server):
    base, _, _ = server
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(base + "/api/queue", timeout=10)
    assert caught.value.code == 401


def test_the_queue_cannot_be_filled_without_signing_in(server):
    base, _, _ = server
    status, _ = call(base, "/api/queue",
                     {"op": "set", "items": [{"action": "status", "arg": ""}]},
                     auth=False)
    assert status == 401
    assert rows(base) == []


# ------------------------------------------------------------- the list ----

def test_steps_keep_the_order_they_were_given(server):
    base, _, _ = server
    put(base, ("status", "", "One"), ("restart_all", "", "Two"),
        ("web_stop", "", "Three"))
    assert [r[0] for r in rows(base)] == ["One", "Two", "Three"]


def test_reordering_keeps_each_step_its_own_identity(server):
    base, _, _ = server
    put(base, ("status", "", "One"), ("restart_all", "", "Two"))
    before = call(base, "/api/queue")[1]["items"]
    flipped = [{"id": before[1]["id"], "action": "restart_all", "arg": "",
                "label": "Two"},
               {"id": before[0]["id"], "action": "status", "arg": "",
                "label": "One"}]
    call(base, "/api/queue", {"op": "set", "items": flipped})
    after = call(base, "/api/queue")[1]["items"]
    assert [i["label"] for i in after] == ["Two", "One"]
    assert [i["id"] for i in after] == [before[1]["id"], before[0]["id"]]


def test_an_action_that_is_not_queueable_is_refused(server):
    base, _, _ = server
    put(base, ("status", "", "Kept"))
    for action in ("panel_upgrade", "term_key", "kill_instance", "restart_one",
                   "self_upgrade", "locks_one"):
        status, out = call(base, "/api/queue", {
            "op": "set", "items": [{"action": action, "arg": "1"}]})
        assert status == 400, action
        assert "queued" in out["error"]
    # The refusal changed nothing.
    assert [r[0] for r in rows(base)] == ["Kept"]


def test_more_steps_than_the_limit_are_refused(server, panel):
    base, _, _ = server
    items = [{"action": "status", "arg": "", "label": "s"}
             for _ in range(panel.QUEUE_MAX + 1)]
    status, out = call(base, "/api/queue", {"op": "set", "items": items})
    assert status == 400
    assert str(panel.QUEUE_MAX) in out["error"]


def test_an_unknown_queue_action_is_refused(server):
    base, _, _ = server
    assert call(base, "/api/queue", {"op": "drop tables"})[0] == 400


# ------------------------------------------------------------- running -----

def test_the_steps_run_in_the_order_shown(server):
    base, ran, _ = server
    put(base, ("status", "", "One"), ("restart_all", "", "Two"),
        ("web_stop", "", "Three"))
    call(base, "/api/queue", {"op": "start"})
    assert settle(base)
    assert ran == ["status", "restart_all", "web_stop"]
    assert [r[1] for r in rows(base)] == ["done", "done", "done"]


def test_a_failed_step_is_marked_and_the_rest_still_run(server):
    base, ran, _ = server
    put(base, ("status", "boom", "Breaks"), ("restart_all", "", "After"))
    call(base, "/api/queue", {"op": "start"})
    assert settle(base)
    assert [r[1] for r in rows(base)] == ["failed", "done"]
    assert "restart_all" in ran


def test_the_reason_a_step_failed_is_kept(server):
    base, _, _ = server
    put(base, ("status", "boom", "Breaks"))
    call(base, "/api/queue", {"op": "start"})
    assert settle(base)
    full = call(base, "/api/queue")[1]["items"][0]
    assert full["output"] == "it went wrong"
    assert full["has_output"] is True


def test_starting_twice_does_not_run_anything_twice(server, monkeypatch):
    base, ran, _ = server
    monkeypatch.setenv("FAKE_STEP", "0.4")
    put(base, ("status", "", "One"), ("restart_all", "", "Two"))
    call(base, "/api/queue", {"op": "start"})
    status, out = call(base, "/api/queue", {"op": "start"})
    assert "already running" in out["output"]
    assert settle(base)
    assert ran == ["status", "restart_all"]


def test_starting_an_empty_queue_says_so(server):
    base, _, _ = server
    assert "Nothing is waiting" in call(base, "/api/queue",
                                        {"op": "start"})[1]["output"]


def test_a_step_added_while_it_runs_is_picked_up(server, monkeypatch):
    base, ran, _ = server
    monkeypatch.setenv("FAKE_STEP", "0.4")
    put(base, ("status", "", "One"))
    call(base, "/api/queue", {"op": "start"})
    time.sleep(0.1)
    call(base, "/api/queue", {"op": "set", "items": [
        {"action": "web_stop", "arg": "", "label": "Late"}]})
    assert settle(base)
    assert ran == ["status", "web_stop"]


# -------------------------------------------------------------- stopping ---

def test_stopping_lets_the_running_step_finish(server, monkeypatch):
    base, ran, _ = server
    monkeypatch.setenv("FAKE_STEP", "0.5")
    put(base, ("status", "", "One"), ("restart_all", "", "Two"),
        ("web_stop", "", "Three"))
    call(base, "/api/queue", {"op": "start"})
    time.sleep(0.15)
    call(base, "/api/queue", {"op": "stop"})
    assert settle(base)
    assert [r[1] for r in rows(base)] == ["done", "skipped", "skipped"]
    # The one that was running was never cut off part way through.
    assert ran == ["status"]


def test_stopping_a_queue_that_is_not_running_says_so(server):
    base, _, _ = server
    assert "not running" in call(base, "/api/queue", {"op": "stop"})[1]["output"]


# -------------------------------------------------------------- clearing ---

def test_clearing_finished_keeps_what_is_still_waiting(server):
    base, _, _ = server
    put(base, ("status", "", "One"))
    call(base, "/api/queue", {"op": "start"})
    assert settle(base)
    call(base, "/api/queue", {"op": "set", "items": [
        {"action": "web_stop", "arg": "", "label": "Later"}]})
    call(base, "/api/queue", {"op": "clear", "what": "finished"})
    assert [r[0] for r in rows(base)] == ["Later"]


def test_clearing_everything_empties_the_list(server):
    base, _, _ = server
    put(base, ("status", "", "One"), ("web_stop", "", "Two"))
    call(base, "/api/queue", {"op": "clear", "what": "all"})
    assert rows(base) == []


# ----------------------------------------------------------- exclusion -----

def test_a_manual_press_waits_for_the_step_that_is_running(server, monkeypatch):
    # The whole point of the queue: nothing else types into an instance while
    # a step is typing into it. The real steps take the same lock a press does.
    base, _, panel = server
    order = []

    def slow(act, arg):
        with panel._run_lock:
            order.append("queue start")
            time.sleep(0.5)
            order.append("queue end")
        return "slow", "", act

    # Only the step is faked. The manual press goes through the real
    # run_command, and therefore the real lock — which is the thing under test.
    monkeypatch.setattr(panel, "QUEUEABLE", {k: slow for k in panel.QUEUEABLE})
    monkeypatch.setattr(panel, "command_for", lambda act, arg: ["true"])
    put(base, ("status", "", "Slow"))
    call(base, "/api/queue", {"op": "start"})
    time.sleep(0.1)
    started = time.time()
    status, out = call(base, "/api/run", {"action": "status", "arg": ""})
    waited = time.time() - started
    assert status == 200, out
    assert order == ["queue start", "queue end"]
    assert waited >= 0.3, "the press did not wait for the step: %.2fs" % waited
    assert settle(base)


# ------------------------------------------------------------ the state ----

def test_the_queue_is_in_the_state_the_page_polls(server):
    base, _, _ = server
    put(base, ("status", "", "One"))
    _, s = call(base, "/api/state")
    assert [i["label"] for i in s["queue"]["items"]] == ["One"]
    assert s["queue"]["running"] is False
    # The output itself is not sent on every poll, only whether there is any.
    assert "output" not in s["queue"]["items"][0]
    assert s["queue"]["items"][0]["has_output"] is False
