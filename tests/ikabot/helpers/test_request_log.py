"""Counting requests, because an IP block has to be traced to what caused it.

Ikariam rate-limits by address and twenty-four instances share one. The
per-instance web server makes it worse: it is a reverse proxy, so every image
a browser loads through it is another request. So the count has to include
everything, be cheap enough to sit on every request, and never be able to
break the request it is counting.
"""

import json
import os
import time

import pytest

import ikabot.helpers.requestLog as rl


class FakeSession:
    username = "alpha"
    servidor = "en"
    mundo = 70


@pytest.fixture
def monitor(tmp_path, monkeypatch):
    monkeypatch.setattr(rl, "MONITOR_DIR", str(tmp_path))
    monkeypatch.setattr(rl, "FLUSH_SECONDS", 0.0)
    rl._state.update({"account": "", "path": "", "t0": 0, "counts": [],
                      "errors": [], "total": 0, "peak": 0, "flushed": 0.0,
                      "pruned": 0.0})
    return tmp_path


def only(monitor):
    files = [p for p in monitor.glob("*.json") if not p.name.startswith(".")]
    assert len(files) == 1, [p.name for p in files]
    return json.loads(files[0].read_text())


# ------------------------------------------------------------- counting -----

def test_a_request_is_counted_under_its_account(monitor):
    assert rl.record_request(FakeSession(), 200) is True
    data = only(monitor)
    assert data["account"] == "alpha_en70"
    assert sum(data["counts"]) == 1
    assert data["total"] == 1


def test_the_world_is_part_of_the_name_so_two_worlds_do_not_share_a_file(monitor):
    class Other(FakeSession):
        mundo = 71

    rl.record_request(FakeSession(), 200)
    rl.record_request(Other(), 200)
    names = sorted(p.name.split(".")[0] for p in monitor.glob("*.json")
                   if not p.name.startswith("."))
    assert names == ["alpha_en70", "alpha_en71"]


def test_one_file_per_process_so_no_lock_is_needed_on_the_hot_path(monitor):
    rl.record_request(FakeSession(), 200)
    name = [p.name for p in monitor.glob("*.json")][0]
    assert name == "alpha_en70.%d.json" % os.getpid()


def test_many_requests_in_one_second_land_in_one_bucket(monitor):
    for _ in range(25):
        rl.record_request(FakeSession(), 200)
    data = only(monitor)
    assert sum(data["counts"]) == 25
    assert data["peak"] >= 25 or max(data["counts"]) == 25


def test_the_peak_is_the_busiest_second_not_the_last(monitor, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(rl.time, "time", lambda: clock[0])
    for _ in range(9):
        rl.record_request(FakeSession(), 200)
    clock[0] = 1002.0
    rl.record_request(FakeSession(), 200)
    data = only(monitor)
    assert data["peak"] == 9
    assert data["counts"] == [9, 0, 1]


# -------------------------------------------------------------- silence -----

def test_quiet_seconds_are_kept_as_zeros(monitor, monkeypatch):
    # Without them a graph would draw a busy second next to one a minute later
    # as though they were neighbours.
    clock = [1000.0]
    monkeypatch.setattr(rl.time, "time", lambda: clock[0])
    rl.record_request(FakeSession(), 200)
    clock[0] = 1005.0
    rl.record_request(FakeSession(), 200)
    assert only(monitor)["counts"] == [1, 0, 0, 0, 0, 1]


def test_a_long_silence_starts_the_window_again(monitor, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(rl.time, "time", lambda: clock[0])
    rl.record_request(FakeSession(), 200)
    clock[0] = 1000.0 + rl.WINDOW * 3
    rl.record_request(FakeSession(), 200)
    data = only(monitor)
    assert data["counts"] == [1]
    assert data["t0"] == int(clock[0])


def test_the_window_never_grows_past_its_limit(monitor, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(rl.time, "time", lambda: clock[0])
    monkeypatch.setattr(rl, "WINDOW", 10)
    for n in range(40):
        clock[0] = 1000.0 + n
        rl.record_request(FakeSession(), 200)
    data = only(monitor)
    assert len(data["counts"]) == 10
    assert len(data["errors"]) == 10
    # The newest second is still the last one in the list.
    assert data["t0"] + len(data["counts"]) - 1 == int(clock[0])


# --------------------------------------------------------------- errors -----

@pytest.mark.parametrize("status,is_error", [
    (200, False), (204, False), (302, False), (399, False),
    (403, True), (429, True), (500, True), (503, True), (None, True),
])
def test_anything_that_is_not_a_good_answer_counts_as_an_error(monitor, status,
                                                               is_error):
    rl.record_request(FakeSession(), status)
    data = only(monitor)
    assert sum(data["counts"]) == 1
    assert sum(data["errors"]) == (1 if is_error else 0)


def test_a_request_that_never_answered_is_still_a_request(monitor):
    # It left this address, which is what the rate limit counts.
    rl.record_request(FakeSession(), None)
    assert sum(only(monitor)["counts"]) == 1


# ---------------------------------------------------------- never raises ----

def test_an_unwritable_folder_does_not_break_the_request(tmp_path, monkeypatch):
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory")
    monkeypatch.setattr(rl, "MONITOR_DIR", str(blocker / "connections"))
    monkeypatch.setattr(rl, "FLUSH_SECONDS", 0.0)
    rl._state.update({"account": "", "counts": [], "errors": [], "flushed": 0.0})
    assert rl.record_request(FakeSession(), 200) is False


def test_a_session_missing_its_fields_does_not_break_the_request(monitor):
    class Bare:
        pass

    assert rl.record_request(Bare(), 200) is True


def test_a_status_that_is_not_a_number_does_not_break_the_request(monitor):
    assert rl.record_request(FakeSession(), "weird") is False


# -------------------------------------------------------------- reading -----

def test_reading_gives_one_payload_per_process(monitor):
    rl.record_request(FakeSession(), 200)
    (monitor / "alpha_en70.999999.json").write_text(json.dumps(
        {"schema": 1, "account": "alpha_en70", "pid": 999999, "t0": 1000,
         "updated": 1000, "counts": [4], "errors": [0], "total": 4, "peak": 4}))
    assert len(rl.read_counts("alpha_en70")) == 2
    assert sum(sum(p["counts"]) for p in rl.read_counts("alpha_en70")) == 5


def test_reading_ignores_another_account(monitor):
    rl.record_request(FakeSession(), 200)
    assert rl.read_counts("beta_en70") == []


def test_a_corrupt_file_is_skipped_rather_than_fatal(monitor):
    rl.record_request(FakeSession(), 200)
    (monitor / "alpha_en70.888888.json").write_text("{ not json")
    assert len(rl.read_counts("alpha_en70")) == 1


def test_a_half_written_temporary_file_is_never_read(monitor):
    rl.record_request(FakeSession(), 200)
    (monitor / ".conn-half.json").write_text("{}")
    assert len(rl.read_counts()) == 1


# -------------------------------------------------------------- pruning -----

def test_a_dead_process_file_is_cleaned_up(monitor):
    stale = monitor / "alpha_en70.777777.json"
    stale.write_text(json.dumps({"schema": 1, "counts": [1], "errors": [0]}))
    old = time.time() - rl.STALE_SECONDS * 2
    os.utime(stale, (old, old))
    rl.record_request(FakeSession(), 200)
    assert not stale.exists()


def test_this_process_own_file_is_never_pruned(monitor):
    rl._state["pruned"] = 0.0
    rl.record_request(FakeSession(), 200)
    rl._state["pruned"] = 0.0
    rl.record_request(FakeSession(), 200)
    assert len(rl.read_counts("alpha_en70")) == 1
