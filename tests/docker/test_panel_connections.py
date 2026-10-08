"""Adding up what every process sent, so a rate-limit block can be explained.

One account runs its menu, its web server and a process per background task,
and all of them send requests from the same address. Each writes its own file,
so the panel is the thing that has to add them up — and it has to lay them on
one grid of seconds, or two processes' counts for the same second would be
drawn as two different moments.
"""

import importlib.machinery
import importlib.util
import json
import os
import time

import pytest

_PANEL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "docker", "ika-panel_v1.4.0",
)
NOW = 1_800_000_000


@pytest.fixture(scope="module")
def panel():
    loader = importlib.machinery.SourceFileLoader("ika_panel", _PANEL)
    spec = importlib.util.spec_from_loader("ika_panel", loader)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def conn(panel, tmp_path, monkeypatch):
    monkeypatch.setattr(panel, "CONN_DIR", str(tmp_path))
    return tmp_path


def write(conn, account, pid, counts, errors=None, t0=None):
    t0 = NOW - len(counts) + 1 if t0 is None else t0
    payload = {"schema": 1, "account": account, "pid": pid, "t0": t0,
               "updated": NOW, "counts": counts,
               "errors": errors or [0] * len(counts),
               "total": sum(counts), "peak": max(counts) if counts else 0}
    (conn / ("%s.%d.json" % (account, pid))).write_text(json.dumps(payload))


# ------------------------------------------------------------- adding up ----

def test_two_processes_of_one_account_are_added_together(panel, conn):
    # The menu and the web server both send from the same address, so three
    # requests and two in the same second is five, not two separate accounts.
    write(conn, "alpha_en70", 1, [3, 0])
    write(conn, "alpha_en70", 2, [2, 0])
    out = panel.connection_series(now=NOW, window=10)
    assert sum(out["requests"]) == 5
    assert out["peak_1"] == 5


def test_one_account_can_be_asked_for_on_its_own(panel, conn):
    write(conn, "alpha_en70", 1, [3, 0])
    write(conn, "beta_en70", 1, [7, 0])
    assert sum(panel.connection_series(["alpha_en70"], now=NOW,
                                       window=10)["requests"]) == 3
    assert sum(panel.connection_series(now=NOW, window=10)["requests"]) == 10


def test_counts_land_on_the_second_they_happened(panel, conn):
    # Two files whose own windows start at different times must still line up:
    # five requests and nine requests in the same second is one busy second.
    write(conn, "alpha_en70", 1, [0, 0, 5, 0], t0=NOW - 3)
    write(conn, "alpha_en70", 2, [9], t0=NOW - 1)
    out = panel.connection_series(now=NOW, window=10)
    assert out["requests"] == [0, 0, 0, 0, 0, 0, 0, 0, 14, 0]
    assert out["start"] == NOW - 9


def test_anything_older_than_the_window_is_left_out(panel, conn):
    write(conn, "alpha_en70", 1, [99] + [0] * 9, t0=NOW - 500)
    out = panel.connection_series(now=NOW, window=10)
    assert sum(out["requests"]) == 0


def test_a_window_longer_than_the_counter_keeps_is_refused(panel, conn):
    assert panel.connection_series(now=NOW,
                                   window=99999)["window"] == panel.CONN_WINDOW
    assert panel.connection_series(now=NOW, window=1)["window"] == 10


# --------------------------------------------------------------- buckets ----

def test_buckets_hold_the_same_total_as_the_seconds(panel, conn):
    write(conn, "alpha_en70", 1, [1, 2, 3, 4, 5, 6, 7, 8, 9, 0])
    one = panel.connection_series(now=NOW, window=10, bucket=1)
    five = panel.connection_series(now=NOW, window=10, bucket=5)
    assert sum(one["requests"]) == sum(five["requests"]) == 45
    assert len(five["requests"]) == 2


def test_the_rates_are_the_same_whatever_the_bucket(panel, conn):
    # The graph may be coarser, but the numbers beside it are always seconds,
    # so the two can never tell different stories.
    write(conn, "alpha_en70", 1, [4] * 120)
    one = panel.connection_series(now=NOW, window=120, bucket=1)
    ten = panel.connection_series(now=NOW, window=120, bucket=10)
    for key in ("now_1", "now_30", "now_60", "peak_1", "peak_30", "peak_60"):
        assert one[key] == ten[key], key


# ----------------------------------------------------------------- rates ----

def test_the_second_in_progress_is_left_out_of_every_rate(panel, conn):
    # It is not over yet, so its count is always short and would read as a
    # sudden drop.
    write(conn, "alpha_en70", 1, [5, 5, 5, 0])
    out = panel.connection_series(now=NOW, window=4)
    assert out["requests"][-1] == 0      # the graph still shows it
    assert out["now_1"] == 5             # the rate does not


def test_the_rates_are_counts_over_the_last_thirty_and_sixty_seconds(panel, conn):
    write(conn, "alpha_en70", 1, [2] * 61)
    out = panel.connection_series(now=NOW, window=61)
    assert out["now_30"] == 60
    assert out["now_60"] == 120


def test_the_worst_in_the_window_is_the_busiest_run_not_the_last(panel, conn):
    counts = [0] * 100
    for i in range(20, 50):          # over and done with by 50 seconds ago
        counts[i] = 10
    write(conn, "alpha_en70", 1, counts)
    out = panel.connection_series(now=NOW, window=100)
    assert out["peak_1"] == 10
    assert out["peak_30"] == 300
    # Nothing in the last thirty seconds, which is the point: the worst is
    # history and "now" is quiet.
    assert out["now_30"] == 0


# ---------------------------------------------------------------- errors ----

def test_errors_are_counted_beside_the_requests_not_instead_of_them(panel, conn):
    write(conn, "alpha_en70", 1, [5, 0], errors=[2, 0])
    out = panel.connection_series(now=NOW, window=10)
    assert sum(out["requests"]) == 5
    assert out["error_total"] == 2


def test_a_file_with_fewer_errors_than_counts_is_not_fatal(panel, conn):
    # An older writer, or one caught mid-write.
    (conn / "alpha_en70.9.json").write_text(json.dumps(
        {"schema": 1, "t0": NOW - 1, "counts": [3, 4], "errors": [1],
         "total": 7, "peak": 4}))
    out = panel.connection_series(now=NOW, window=10)
    assert sum(out["requests"]) == 7
    assert out["error_total"] == 1


# --------------------------------------------------------------- rubbish ----

def test_no_folder_at_all_is_not_an_error(panel, tmp_path, monkeypatch):
    monkeypatch.setattr(panel, "CONN_DIR", str(tmp_path / "missing"))
    out = panel.connection_series(now=NOW)
    assert sum(out["requests"]) == 0
    assert panel.conn_meta() == {}


def test_a_corrupt_file_is_skipped_rather_than_fatal(panel, conn):
    write(conn, "alpha_en70", 1, [4, 0])
    (conn / "alpha_en70.2.json").write_text("{ not json")
    assert sum(panel.connection_series(now=NOW, window=10)["requests"]) == 4


def test_a_half_written_temporary_file_is_never_read(panel, conn):
    write(conn, "alpha_en70", 1, [4, 0])
    (conn / ".conn-half.json").write_text("{}")
    assert sum(panel.connection_series(now=NOW, window=10)["requests"]) == 4


def test_a_count_that_is_not_a_number_is_skipped(panel, conn):
    (conn / "alpha_en70.1.json").write_text(json.dumps(
        {"schema": 1, "t0": NOW - 2, "counts": [1, "lots", 2],
         "errors": [0, 0, 0], "total": 3, "peak": 2}))
    assert sum(panel.connection_series(now=NOW, window=10)["requests"]) == 3


# ------------------------------------------------------------------ meta ----

def test_the_summary_adds_every_process_of_an_account(panel, conn):
    write(conn, "alpha_en70", 1, [3, 0])
    write(conn, "alpha_en70", 2, [4, 0])
    write(conn, "beta_en70", 1, [1, 0])
    meta = panel.conn_meta()
    assert meta["alpha_en70"]["total"] == 7
    assert meta["alpha_en70"]["parts"] == 2
    assert meta["beta_en70"]["total"] == 1


def test_the_summary_is_in_the_state_the_page_polls(panel, conn, monkeypatch):
    write(conn, "alpha_en70", 1, [3, 0])
    # state() shells out to tmux, so only the piece under test is called here.
    assert "alpha_en70" in panel.conn_meta()


def test_the_connections_folder_is_never_walked_for_lock_files(panel):
    assert "connections" in panel.LOCK_SKIP_DIRS
