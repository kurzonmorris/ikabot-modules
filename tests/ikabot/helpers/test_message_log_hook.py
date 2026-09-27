"""sendToBot is the one place every notification passes through.

So it is the one place the local copy can be written from, and the copy has to
survive a backend that throws. A record that only appears when Telegram
succeeds would be missing exactly the messages worth looking for.
"""

import pytest

import ikabot.helpers.botComm as bc
import ikabot.helpers.messageLog as ml
import ikabot.helpers.notifyTargets as nt


@pytest.fixture(autouse=True)
def no_named_destinations(tmp_path, monkeypatch):
    # These tests are about the original path, so the destinations file must be
    # this test's own and empty, never the one on the machine running them.
    monkeypatch.setattr(nt, "TARGETS_FILE", str(tmp_path / "notify.json"))


class FakeRequests:
    username = "acct01"


class FakeSession:
    username = "acct01"
    servidor = "en"
    word = 70
    mundo = 70

    def __init__(self, shared=None):
        self._shared = shared or {}
        self.s = FakeRequests()

    def getSessionData(self):
        return {"shared": self._shared}


@pytest.fixture
def written(monkeypatch):
    calls = []
    monkeypatch.setattr(ml, "log_message",
                        lambda session, msg, sent=(), module="": calls.append(
                            (msg, list(sent), module)) or True)
    return calls


def test_a_message_is_logged_with_no_backend_configured(written):
    bc.sendToBot(FakeSession(), "nothing is set up")
    assert len(written) == 1
    msg, sent, _ = written[0]
    assert "nothing is set up" in msg
    assert sent == []


def test_a_working_backend_is_recorded_as_sent(written, monkeypatch):
    monkeypatch.setattr(bc, "_send_ntfy", lambda *a, **k: True)
    session = FakeSession({"ntfy": {"server": "https://ntfy.sh",
                                    "topic": "abc", "token": ""}})
    bc.sendToBot(session, "hello")
    assert written[0][1] == [("ntfy", True)]


def test_a_backend_that_returns_false_is_recorded_as_failed(written, monkeypatch):
    monkeypatch.setattr(bc, "_send_discord", lambda *a, **k: False)
    session = FakeSession({"discord": {"webhookUrl": "https://example/hook"}})
    bc.sendToBot(session, "hello")
    assert written[0][1] == [("discord", False)]


def test_a_backend_that_throws_is_recorded_and_does_not_stop_the_rest(
        written, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no network")

    monkeypatch.setattr(bc, "_send_discord", boom)
    monkeypatch.setattr(bc, "_send_ntfy", lambda *a, **k: True)
    session = FakeSession({"discord": {"webhookUrl": "https://example/hook"},
                           "ntfy": {"topic": "abc"}})
    bc.sendToBot(session, "hello")
    assert written[0][1] == [("discord", False), ("ntfy", True)]


def test_the_header_ikabot_adds_is_part_of_the_record(written):
    bc.sendToBot(FakeSession(), "the body")
    assert "Server:en, World:70, Player:acct01" in written[0][0]


def test_a_failing_log_does_not_reach_the_caller(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(ml, "log_message", boom)
    bc.sendToBot(FakeSession(), "hello")
