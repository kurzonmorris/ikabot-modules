"""A notification must leave a copy behind, because the services cannot be read.

Telegram will not list a bot's own messages and a Discord webhook cannot be
read at all. So the only complete list of what ikabot sent is the one written
here as it is sent. That makes two things matter more than usual: the write
must never break the notification it belongs to, and it must never carry a
credential into a file the control panel serves to a browser.
"""

import json
import os

import pytest

import ikabot.helpers.messageLog as ml


class FakeSession:
    username = "acct01"
    servidor = "en"
    mundo = 70


@pytest.fixture
def message_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ml, "MESSAGE_DIR", str(tmp_path))
    return tmp_path


def records(message_dir, name="acct01_en70.jsonl"):
    path = message_dir / name
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


# ------------------------------------------------------------- writing ------

def test_a_message_is_written_under_the_account_it_came_from(message_dir):
    ml.log_message(FakeSession(), "Attack on Athens\ntwo ships", [("telegram", True)])
    rows = records(message_dir)
    assert len(rows) == 1
    assert rows[0]["account"] == "acct01_en70"
    assert rows[0]["title"] == "Attack on Athens"
    assert rows[0]["body"] == "two ships"
    assert rows[0]["sent"] == [{"kind": "telegram", "ok": True}]


def test_the_world_is_part_of_the_name_so_two_worlds_do_not_share_a_file(message_dir):
    class Other(FakeSession):
        mundo = 71

    ml.log_message(FakeSession(), "one", [])
    ml.log_message(Other(), "two", [])
    assert sorted(p.name for p in message_dir.glob("*.jsonl")) == [
        "acct01_en70.jsonl", "acct01_en71.jsonl"]


def test_a_message_is_kept_even_when_nothing_is_configured(message_dir):
    ml.log_message(FakeSession(), "nowhere to send this", [])
    rows = records(message_dir)
    assert len(rows) == 1
    assert rows[0]["sent"] == []


def test_a_failed_backend_is_recorded_as_failed(message_dir):
    ml.log_message(FakeSession(), "hello", [("telegram", True), ("ntfy", False)])
    assert records(message_dir)[0]["sent"] == [
        {"kind": "telegram", "ok": True},
        {"kind": "ntfy", "ok": False},
    ]


def test_several_messages_append_rather_than_replace(message_dir):
    for n in range(5):
        ml.log_message(FakeSession(), "message %d" % n, [])
    assert [r["title"] for r in records(message_dir)] == [
        "message %d" % n for n in range(5)]


# ------------------------------------------------------------ secrets -------

@pytest.mark.parametrize("secret", [
    "123456789:AAFakeTokenThatIsLongEnoughToMatch123456",
    "https://discord.com/api/webhooks/12345/abcdefghijklmnop",
    "Bearer tk_abcdefghijklmnopqrst",
])
def test_a_credential_never_reaches_the_file(message_dir, secret):
    ml.log_message(FakeSession(), "login failed\n%s" % secret, [])
    text = (message_dir / "acct01_en70.jsonl").read_text()
    assert secret not in text
    assert "[removed]" in text


# ----------------------------------------------------------- never raises ---

def test_an_unwritable_directory_does_not_break_the_notification(tmp_path,
                                                                 monkeypatch):
    # A file where the directory should be: every write below it fails.
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory")
    monkeypatch.setattr(ml, "MESSAGE_DIR", str(blocker / "messages"))
    assert ml.log_message(FakeSession(), "hello", []) is False


def test_a_session_missing_its_fields_still_writes_something(message_dir):
    class Bare:
        pass

    assert ml.log_message(Bare(), "hello", []) is True
    assert len(list(message_dir.glob("*.jsonl"))) == 1


# ------------------------------------------------------------- rotation -----

def test_the_log_is_rotated_rather_than_grown_without_limit(message_dir,
                                                            monkeypatch):
    monkeypatch.setattr(ml, "MAX_BYTES", 400)
    for n in range(40):
        ml.log_message(FakeSession(), "message %d" % n, [])
    assert (message_dir / "acct01_en70.jsonl.1").exists()
    assert (message_dir / "acct01_en70.jsonl").stat().st_size < 400 * 3


# -------------------------------------------------------------- reading -----

def test_reading_gives_the_newest_first(message_dir):
    for n in range(5):
        ml.log_message(FakeSession(), "message %d" % n, [])
    rows = ml.read_messages("acct01_en70", limit=3)
    assert [r["title"] for r in rows] == ["message 4", "message 3", "message 2"]


def test_reading_an_account_with_no_log_gives_nothing(message_dir):
    assert ml.read_messages("nobody_en70") == []


def test_a_half_written_line_is_skipped_rather_than_fatal(message_dir):
    ml.log_message(FakeSession(), "good", [])
    with open(message_dir / "acct01_en70.jsonl", "a") as f:
        f.write('{"schema": 1, "t": 1, "tit\n')
    rows = ml.read_messages("acct01_en70")
    assert [r["title"] for r in rows] == ["good"]
