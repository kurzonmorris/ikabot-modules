"""The panel reads the message logs itself, so it must survive what it finds.

The logs are appended to by up to twenty-four processes at once and are capped
and rotated underneath the reader. So a half-written last line, a file that
grew between two reads, and a log far larger than the page shows are all
normal, not faults. The reader also runs on every poll, which is why it reads
the tail of each file rather than the whole thing.
"""

import importlib.machinery
import importlib.util
import json
import os
import time

import pytest

_PANEL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "docker", "ika-panel_v1.2.0",
)


@pytest.fixture(scope="module")
def panel():
    # The panel ships without a .py suffix, so the loader is named outright.
    loader = importlib.machinery.SourceFileLoader("ika_panel", _PANEL)
    spec = importlib.util.spec_from_loader("ika_panel", loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def logs(panel, tmp_path, monkeypatch):
    monkeypatch.setattr(panel, "MESSAGE_DIR", str(tmp_path))
    return tmp_path


def write(logs, account, rows):
    with open(logs / ("%s.jsonl" % account), "a", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def record(t, title, body="", account="acct01_en70", sent=None):
    return {"schema": 1, "t": t, "account": account, "module": "alertAttacks",
            "title": title, "body": body, "sent": sent or []}


# ------------------------------------------------------------------ meta ----

def test_every_log_is_listed_with_its_size_and_time(panel, logs):
    write(logs, "acct01_en70", [record(10, "one")])
    write(logs, "acct02_en70", [record(20, "two")])
    meta = panel.message_meta()
    assert sorted(meta) == ["acct01_en70", "acct02_en70"]
    assert meta["acct01_en70"]["size"] > 0
    assert meta["acct01_en70"]["last"] <= int(time.time())


def test_a_rotated_log_is_not_listed_as_an_account(panel, logs):
    write(logs, "acct01_en70", [record(10, "one")])
    (logs / "acct01_en70.jsonl.1").write_text("{}\n")
    assert list(panel.message_meta()) == ["acct01_en70"]


def test_no_messages_folder_is_not_an_error(panel, tmp_path, monkeypatch):
    monkeypatch.setattr(panel, "MESSAGE_DIR", str(tmp_path / "missing"))
    assert panel.message_meta() == {}
    assert panel.read_message_log() == []


# --------------------------------------------------------------- reading ----

def test_messages_come_back_newest_first_across_every_account(panel, logs):
    write(logs, "acct01_en70", [record(10, "old"), record(30, "newest")])
    write(logs, "acct02_en70", [record(20, "middle")])
    assert [r["title"] for r in panel.read_message_log()] == [
        "newest", "middle", "old"]


def test_one_account_can_be_asked_for_on_its_own(panel, logs):
    write(logs, "acct01_en70", [record(10, "mine")])
    write(logs, "acct02_en70", [record(20, "theirs")])
    rows = panel.read_message_log(["acct01_en70"])
    assert [r["title"] for r in rows] == ["mine"]


def test_the_account_comes_from_the_filename_not_the_record(panel, logs):
    # A log copied between containers carries the old name inside it. The file
    # it is in is what decides which instance the page files it under.
    write(logs, "acct01_en70", [record(10, "one", account="somebody_else")])
    assert panel.read_message_log()[0]["account"] == "acct01_en70"


def test_the_limit_is_honoured_and_cannot_be_raised_without_bound(panel, logs):
    write(logs, "acct01_en70", [record(n, "m%d" % n) for n in range(50)])
    assert len(panel.read_message_log(limit=5)) == 5
    assert len(panel.read_message_log(limit=10 ** 9)) == 50


def test_a_corrupt_line_is_skipped_rather_than_fatal(panel, logs):
    write(logs, "acct01_en70", [record(10, "good")])
    with open(logs / "acct01_en70.jsonl", "a") as f:
        f.write('{"schema": 1, "t": 20, "tit\n')
    assert [r["title"] for r in panel.read_message_log()] == ["good"]


def test_a_line_that_is_not_an_object_is_skipped(panel, logs):
    write(logs, "acct01_en70", [record(10, "good")])
    with open(logs / "acct01_en70.jsonl", "a") as f:
        f.write('"just a string"\n[1,2,3]\n\n')
    assert [r["title"] for r in panel.read_message_log()] == ["good"]


# ---------------------------------------------------------------- search ----

def test_the_search_matches_the_title_and_the_body(panel, logs):
    write(logs, "acct01_en70", [
        record(10, "Attack on Athens", "two ships"),
        record(20, "Trade done", "marble delivered"),
    ])
    assert [r["title"] for r in panel.read_message_log(query="athens")] == [
        "Attack on Athens"]
    assert [r["title"] for r in panel.read_message_log(query="MARBLE")] == [
        "Trade done"]
    assert panel.read_message_log(query="nothing here") == []


def test_the_search_is_plain_text_and_not_a_regular_expression(panel, logs):
    # A pattern typed into a box on a page must not be compiled and run here.
    write(logs, "acct01_en70", [record(10, "Attack on Athens")])
    assert panel.read_message_log(query=".*") == []
    assert panel.read_message_log(query="(") == []


# ------------------------------------------------------------------ tail ----

def test_only_the_end_of_a_large_log_is_read(panel, logs, monkeypatch):
    monkeypatch.setattr(panel, "MSG_TAIL_BYTES", 400)
    write(logs, "acct01_en70", [record(n, "m%d" % n, "x" * 60) for n in range(60)])
    rows = panel.read_message_log(limit=1000)
    assert rows, "the tail must still parse"
    assert len(rows) < 60
    # The seek lands mid-record, and that fragment must not reach the page.
    assert all(r.get("title") for r in rows)


def test_a_log_smaller_than_the_tail_is_read_whole(panel, logs):
    write(logs, "acct01_en70", [record(n, "m%d" % n) for n in range(7)])
    assert len(panel.read_message_log(limit=1000)) == 7


# -------------------------------------------------------------- the page ----

def test_the_messages_folder_is_never_walked_for_lock_files(panel):
    # It holds one file per account with a name shaped like everything else in
    # the data folder, and it is the one folder that grows without bound.
    assert "messages" in panel.LOCK_SKIP_DIRS
