"""Clearing a session lock from the page, without ever stealing a live one.

A session lock is not a module lock. It lives in the sessions folder, it stops
an account logging in at all, and Clear all lock files deliberately never
touches it — an account was once locked out until the file was deleted by
hand. The panel can now remove one, which makes the safety rule the whole
point: it must refuse any lock that something still holds, because two
processes writing one session file is how that file is lost.
"""

import importlib.machinery
import importlib.util
import json
import os
import time

import pytest

_PANEL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "docker", "ika-panel_v1.4.1",
)


@pytest.fixture(scope="module")
def panel():
    loader = importlib.machinery.SourceFileLoader("ika_panel", _PANEL)
    spec = importlib.util.spec_from_loader("ika_panel", loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sessions(panel, tmp_path, monkeypatch):
    monkeypatch.setattr(panel, "SESSION_DIR", str(tmp_path))
    return tmp_path


NAME = "5375e00a9cfd71ac_8teen8_en70.session.lock"


def put(sessions, name, text, age=0):
    path = sessions / name
    path.write_text(text)
    if age:
        when = time.time() - age
        os.utime(path, (when, when))
    return path


def dead_pid(panel):
    for candidate in range(4_000_000, 4_000_050):
        if not panel._pid_alive(candidate):
            return candidate
    raise AssertionError("could not find an unused pid")


# ------------------------------------------------------------- judging ------

def test_an_empty_lock_that_has_aged_is_stuck(panel, sessions):
    # The case that locked the account out: created, never written.
    put(sessions, NAME, "", age=panel.SESSION_STALE + 5)
    info = panel.read_session_lock(NAME)
    assert info["stuck"] is True
    assert info["empty"] is True
    assert info["pid"] == 0


def test_a_fresh_empty_lock_is_not_offered(panel, sessions):
    put(sessions, NAME, "")
    assert panel.read_session_lock(NAME)["stuck"] is False


def test_a_lock_naming_a_dead_process_is_stuck(panel, sessions):
    put(sessions, NAME, "%d:%.3f" % (dead_pid(panel), time.time()))
    info = panel.read_session_lock(NAME)
    assert info["stuck"] is True
    assert info["alive"] is False


def test_a_lock_held_by_a_live_process_is_not_stuck(panel, sessions):
    put(sessions, NAME, "%d:%.3f" % (os.getpid(), time.time()))
    info = panel.read_session_lock(NAME)
    assert info["stuck"] is False
    assert info["alive"] is True


def test_a_live_process_holding_it_too_long_is_stuck(panel, sessions):
    put(sessions, NAME, "%d:%.3f" % (os.getpid(),
                                     time.time() - panel.SESSION_STALE - 5))
    assert panel.read_session_lock(NAME)["stuck"] is True


def test_the_age_comes_from_the_file_when_the_lock_says_nothing(panel, sessions):
    put(sessions, NAME, "", age=90)
    assert panel.read_session_lock(NAME)["age"] >= 89


def test_a_lock_that_is_not_there_reads_as_nothing(panel, sessions):
    assert panel.read_session_lock(NAME) is None


def test_only_session_locks_are_listed(panel, sessions):
    put(sessions, NAME, "")
    (sessions / "something.session").write_text("not a lock")
    (sessions / "other.lock").write_text("not a session lock")
    assert [i["name"] for i in panel.session_locks()] == [NAME]


# ------------------------------------------------------------- matching -----

def test_a_lock_is_matched_to_the_account_in_its_name(panel, sessions):
    put(sessions, NAME, "")
    entry = {"username": "8teen8", "server": "en", "world": "70"}
    assert panel.session_lock_for(entry, panel.session_locks())["name"] == NAME


def test_another_account_does_not_claim_it(panel, sessions):
    put(sessions, NAME, "")
    locks = panel.session_locks()
    for entry in ({"username": "8teen8", "server": "en", "world": "71"},
                  {"username": "8teen", "server": "en", "world": "70"},
                  {"username": "other", "server": "en", "world": "70"}):
        assert panel.session_lock_for(entry, locks) is None


def test_a_lock_from_before_a_first_login_belongs_to_nobody(panel, sessions):
    # Named by hash alone, so no card can claim it.
    put(sessions, "5375e00a9cfd71ac.session.lock", "")
    entry = {"username": "8teen8", "server": "en", "world": "70"}
    assert panel.session_lock_for(entry, panel.session_locks()) is None


def test_an_account_with_no_name_yet_claims_nothing(panel, sessions):
    put(sessions, NAME, "")
    locks = panel.session_locks()
    assert panel.session_lock_for({}, locks) is None
    assert panel.session_lock_for({"username": "8teen8"}, locks) is None


# ------------------------------------------------------------- clearing -----

def test_an_abandoned_lock_is_removed_and_said_why(panel, sessions):
    put(sessions, "x_acct_en70.session.lock", "", age=panel.SESSION_STALE + 5)
    out, error, _ = panel.run_session_lock_named(
        "session_lock_named", "x_acct_en70.session.lock")
    assert error == ""
    assert "Cleared" in out
    assert not (sessions / "x_acct_en70.session.lock").exists()


def test_a_lock_still_held_is_refused_and_left_alone(panel, sessions):
    held = "%d:%.3f" % (os.getpid(), time.time())
    path = put(sessions, "x_acct_en70.session.lock", held)
    out, error, _ = panel.run_session_lock_named(
        "session_lock_named", "x_acct_en70.session.lock")
    assert error == ""
    assert "still running" in out
    assert path.read_text() == held, "a held lock must survive the attempt"


def test_a_name_the_panel_never_listed_is_refused(panel, sessions):
    # Nothing outside the sessions folder is reachable, whatever is sent.
    put(sessions, NAME, "", age=99)
    for attempt in ("../../../etc/passwd", "/etc/passwd",
                    "..%2f..%2fvault", "nothing.session.lock", ""):
        out, error, _ = panel.run_session_lock_named("session_lock_named",
                                                     attempt)
        assert error == "no such session lock", attempt
    assert (sessions / NAME).exists()


def test_clearing_one_that_vanished_first_is_not_an_error(panel, sessions,
                                                          monkeypatch):
    put(sessions, NAME, "", age=99)
    real = os.unlink

    def vanish(path):
        real(path)
        return real(path)        # second call raises FileNotFoundError

    monkeypatch.setattr(panel.os, "unlink", vanish)
    out, error, _ = panel.run_session_lock_named("session_lock_named", NAME)
    assert error == ""
    assert "already gone" in out


# ------------------------------------------------------------ the rules -----

def test_the_module_lock_sweep_still_never_touches_a_session_lock(panel):
    # The two are different things and the big red button must stay away.
    assert "sessions" in panel.LOCK_SKIP_DIRS
    src = open(_PANEL, encoding="utf-8").read()
    body = src[src.index("def lock_files("):src.index("def delete_locks(")]
    assert '.session.lock' in body and 'continue' in body


def test_clearing_by_instance_number_refuses_anything_but_a_number(panel):
    for junk in ("", "all", "../1", "1;rm", "-1"):
        out, error, _ = panel.run_session_lock("session_lock_one", junk)
        assert error == "bad instance", junk
