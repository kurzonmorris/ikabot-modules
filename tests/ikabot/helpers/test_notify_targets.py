"""Named destinations: several Telegram accounts, and one route per account.

The original settings hold one Telegram, one Discord and one ntfy for the whole
machine, so there is nowhere to put a second Telegram account. These are the
rules that make a list of them safe to add: an install with no destinations
must behave exactly as it did, a credential must never have to travel to a
browser and back to survive an edit, and a file full of bot tokens must not be
world-readable.
"""

import json
import os
import stat

import pytest

import ikabot.helpers.notifyTargets as nt


TG = {"name": "My phone", "kind": "telegram",
      "telegram": {"botToken": "123456:abcdefghijklmnop", "chatId": "42"}}
TG2 = {"name": "Second phone", "kind": "telegram",
       "telegram": {"botToken": "999999:zyxwvutsrqponmlk", "chatId": "43"}}
DC = {"name": "War room", "kind": "discord",
      "discord": {"webhookUrl": "https://discord.com/api/webhooks/1/abc"}}
NT = {"name": "Tablet", "kind": "ntfy",
      "ntfy": {"server": "https://ntfy.sh", "topic": "secret-topic"}}


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(nt, "TARGETS_FILE", str(tmp_path / "notify_targets.json"))
    return tmp_path / "notify_targets.json"


def put(*raw):
    data = {"schema": 1, "targets": []}
    for one in raw:
        target, err = nt.clean_target(one)
        assert target is not None, err
        data["targets"].append(target)
    assert nt.save(data)
    return data


# ---------------------------------------------------------- nothing set -----

def test_no_file_reads_as_no_destinations(store):
    assert nt.load()["targets"] == []
    assert nt.have_targets() is False


def test_a_corrupt_file_reads_as_no_destinations_rather_than_crashing(store):
    store.write_text("{ this is not json")
    assert nt.load()["targets"] == []
    assert nt.have_targets() is False


# ------------------------------------------------------------- the list -----

def test_three_telegram_accounts_can_exist_side_by_side(store):
    put(TG, TG2, dict(TG, name="Third phone"))
    assert len(nt.load()["targets"]) == 3
    assert len({t["id"] for t in nt.load()["targets"]}) == 3


def test_every_kind_is_accepted(store):
    put(TG, DC, NT)
    assert sorted(t["kind"] for t in nt.load()["targets"]) == [
        "discord", "ntfy", "telegram"]


# ------------------------------------------------------------- routing ------

def test_an_empty_account_list_means_every_account(store):
    put(TG)
    assert len(nt.targets_for("alpha_en70")) == 1
    assert len(nt.targets_for("beta_en71")) == 1


def test_a_destination_can_be_limited_to_named_accounts(store):
    put(dict(TG, accounts=["alpha_en70"]), dict(DC, accounts=["beta_en70"]))
    assert [t["kind"] for t in nt.targets_for("alpha_en70")] == ["telegram"]
    assert [t["kind"] for t in nt.targets_for("beta_en70")] == ["discord"]
    assert nt.targets_for("gamma_en70") == []


def test_a_disabled_destination_is_skipped(store):
    put(dict(TG, enabled=False), DC)
    assert [t["kind"] for t in nt.targets_for("alpha_en70")] == ["discord"]


# ---------------------------------------------------------- validation ------

@pytest.mark.parametrize("bad,why", [
    ({"name": "", "kind": "telegram"}, "empty name"),
    ({"name": "x" * 40, "kind": "telegram"}, "name too long"),
    ({"name": "ok", "kind": "carrier pigeon"}, "unknown kind"),
    ({"name": "ok", "kind": "telegram", "telegram": {"chatId": "1"}}, "no token"),
    ({"name": "ok", "kind": "telegram",
      "telegram": {"botToken": "nocolon", "chatId": "1"}}, "not a token"),
    ({"name": "ok", "kind": "discord",
      "discord": {"webhookUrl": "https://evil.example/hook"}}, "not discord"),
    ({"name": "ok", "kind": "ntfy", "ntfy": {"topic": ""}}, "no topic"),
    ({"name": "ok", "kind": "ntfy",
      "ntfy": {"topic": "t", "server": "ntfy.sh"}}, "no scheme"),
])
def test_a_bad_destination_is_refused_with_a_reason(bad, why):
    target, error = nt.clean_target(bad)
    assert target is None, why
    assert error


def test_an_account_name_cannot_carry_a_path(store):
    target, error = nt.clean_target(dict(TG, accounts=["../../etc/passwd"]))
    assert target is None
    assert "account" in error.lower()


# ------------------------------------------------------------- secrets ------

def test_a_credential_is_replaced_by_a_hint_before_it_leaves(store):
    data = put(TG, DC, NT)
    shown = [nt.public(t) for t in data["targets"]]
    blob = json.dumps(shown)
    assert "123456:abcdefghijklmnop" not in blob
    assert "https://discord.com/api/webhooks/1/abc" not in blob
    # On the public server the topic is the only thing guarding the feed.
    assert "secret-topic" not in blob
    assert shown[0]["telegram"]["botToken"].endswith("mnop")
    # What is not a credential still shows, or the form cannot be filled in.
    assert shown[0]["telegram"]["chatId"] == "42"
    assert shown[2]["ntfy"]["server"] == "https://ntfy.sh"


def test_a_blank_secret_on_an_edit_keeps_the_one_already_stored(store):
    data = put(TG)
    stored = data["targets"][0]
    edited, error = nt.clean_target(
        {"id": stored["id"], "name": "Renamed", "kind": "telegram",
         "telegram": {"botToken": "", "chatId": "42"}}, previous=stored)
    assert edited is not None, error
    assert edited["telegram"]["botToken"] == "123456:abcdefghijklmnop"
    assert edited["name"] == "Renamed"


def test_the_file_is_not_readable_by_anyone_else(store):
    put(TG)
    mode = stat.S_IMODE(os.stat(store).st_mode)
    assert mode & (stat.S_IRGRP | stat.S_IROTH) == 0


# -------------------------------------------------------------- sending -----

def test_sending_reports_one_result_per_destination(store, monkeypatch):
    put(TG, DC, NT)
    monkeypatch.setattr(nt, "_send_telegram", lambda *a, **k: True)
    monkeypatch.setattr(nt, "_send_discord", lambda *a, **k: False)
    monkeypatch.setattr(nt, "_send_ntfy", lambda *a, **k: True)
    monkeypatch.setitem(nt._SENDERS, "telegram", nt._send_telegram)
    monkeypatch.setitem(nt._SENDERS, "discord", nt._send_discord)
    monkeypatch.setitem(nt._SENDERS, "ntfy", nt._send_ntfy)
    assert nt.send_all("alpha_en70", "hello") == [
        ("telegram", True), ("discord", False), ("ntfy", True)]


def test_one_destination_throwing_does_not_stop_the_others(store, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no network")

    put(TG, NT)
    monkeypatch.setitem(nt._SENDERS, "telegram", boom)
    monkeypatch.setitem(nt._SENDERS, "ntfy", lambda *a, **k: True)
    assert nt.send_all("alpha_en70", "hello") == [
        ("telegram", False), ("ntfy", True)]
