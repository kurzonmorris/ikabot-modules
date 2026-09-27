"""Adding the first destination must not change an install that has none.

sendToBot() is reached from everywhere in ikabot, so the switch between the
original three shared settings and the named destinations is the riskiest part
of this change. These pin both sides of it, and pin that a destination list
that cannot be read falls back rather than swallowing the notification.
"""

import pytest

import ikabot.helpers.botComm as bc
import ikabot.helpers.messageLog as ml
import ikabot.helpers.notifyTargets as nt


class FakeRequests:
    def __init__(self):
        self.headers = {}


class FakeSession:
    username = "alpha"
    servidor = "en"
    word = 70
    mundo = 70

    def __init__(self, shared=None):
        self._shared = shared or {}
        self.s = FakeRequests()

    def getSessionData(self):
        return {"shared": self._shared}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(nt, "TARGETS_FILE", str(tmp_path / "notify.json"))
    return tmp_path


@pytest.fixture
def written(monkeypatch):
    calls = []
    monkeypatch.setattr(ml, "log_message",
                        lambda session, msg, sent=(), module="": calls.append(
                            list(sent)) or True)
    return calls


def add(raw):
    target, error = nt.clean_target(raw)
    assert target is not None, error
    assert nt.save({"schema": 1, "targets": [target]})
    return target


TG = {"name": "My phone", "kind": "telegram",
      "telegram": {"botToken": "123456:abcdefghijklmnop", "chatId": "42"}}


def test_with_no_destinations_the_original_settings_are_used(store, written,
                                                             monkeypatch):
    used = []
    monkeypatch.setattr(bc, "_send_ntfy", lambda *a, **k: used.append("ntfy") or True)
    session = FakeSession({"ntfy": {"topic": "abc"}})
    bc.sendToBot(session, "hello")
    assert used == ["ntfy"]
    assert written[0] == [("ntfy", True)]


def test_once_a_destination_exists_it_is_the_whole_list(store, written,
                                                        monkeypatch):
    add(TG)
    # The original settings are still there, and must now be ignored: a second
    # copy of every message is worse than none.
    monkeypatch.setattr(bc, "_send_ntfy", lambda *a, **k: pytest.fail(
        "the original ntfy setting was used as well"))
    monkeypatch.setitem(nt._SENDERS, "telegram", lambda *a, **k: True)
    session = FakeSession({"ntfy": {"topic": "abc"}})
    bc.sendToBot(session, "hello")
    assert written[0] == [("telegram", True)]


def test_a_destination_for_another_account_does_not_take_over(store, written,
                                                              monkeypatch):
    add(dict(TG, accounts=["somebody_en99"]))
    monkeypatch.setitem(nt._SENDERS, "telegram", lambda *a, **k: pytest.fail(
        "sent to another account's destination"))
    used = []
    monkeypatch.setattr(bc, "_send_ntfy", lambda *a, **k: used.append("ntfy") or True)
    session = FakeSession({"ntfy": {"topic": "abc"}})
    bc.sendToBot(session, "hello")
    # There is a destination list, but nothing in it is for this account, so
    # nothing is sent and the message is still kept locally.
    assert used == []
    assert written[0] == []


def test_a_destination_list_that_cannot_be_read_falls_back(store, written,
                                                           monkeypatch):
    def boom():
        raise RuntimeError("disk gone")

    monkeypatch.setattr(nt, "have_targets", boom)
    used = []
    monkeypatch.setattr(bc, "_send_ntfy", lambda *a, **k: used.append("ntfy") or True)
    bc.sendToBot(FakeSession({"ntfy": {"topic": "abc"}}), "hello")
    assert used == ["ntfy"]


def test_the_account_a_destination_is_matched_against_includes_the_world(store):
    # alpha on world 70 and alpha on world 71 are two accounts, and a route
    # naming one must not catch the other.
    add(dict(TG, accounts=["alpha_en70"]))
    assert len(nt.targets_for("alpha_en70")) == 1
    assert nt.targets_for("alpha_en71") == []
