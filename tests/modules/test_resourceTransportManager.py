"""Resource Transport Manager: the behaviour that cost a release to learn.

Every group below exists because something went wrong on a live account. Where
a bug was diagnosed rather than guessed, the old implementation is kept inline
and run, so the test proves the diagnosis and not only the fix.

The module is loaded by path. It needs no game session, no network and no
vault, so every test here is offline.
"""

import csv
import datetime
import glob
import importlib.util
import json
import os
import re
import time

import pytest

_MODULES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "modules",
)


def _module_path():
    """Find the module whatever its version suffix is.

    Matching on the name rather than the version keeps this file out of the
    list of places a version bump has to touch.
    """
    found = sorted(glob.glob(os.path.join(
        _MODULES_DIR, "resourceTransportManager_v*.py")))
    assert found, "resourceTransportManager_v*.py not found in modules/"
    return found[-1]


@pytest.fixture(scope="module")
def rtm():
    path = _module_path()
    spec = importlib.util.spec_from_file_location("resourceTransportManager",
                                                  path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeSession:
    """Only the attributes the tested functions actually read."""

    username = "Stave"
    servidor = "en"
    mundo = "70"

    def __init__(self, bodies=None):
        self.bodies = list(bodies or [])
        self.calls = 0
        self.status = []

    def _next(self):
        self.calls += 1
        body = self.bodies.pop(0) if self.bodies else "<html>nothing</html>"
        if isinstance(body, Exception):
            raise body
        return body

    def get(self, *a, **k):
        return self._next()

    def post(self, *a, **k):
        return self._next()

    def setStatus(self, text):
        self.status.append(text)


@pytest.fixture
def session():
    return FakeSession()


@pytest.fixture
def quiet(rtm, monkeypatch):
    """Collect printed output instead of writing it to the terminal."""
    lines = []
    monkeypatch.setattr(rtm, "print",
                        lambda *a, **k: lines.append(
                            " ".join(str(x) for x in a)),
                        raising=False)
    monkeypatch.setattr(rtm, "enter", lambda: None)
    monkeypatch.setattr(rtm, "print_module_banner", lambda *a, **k: None)
    monkeypatch.setattr(rtm, "_set_redraw", lambda fn: None)
    return lines


# ===========================================================================
#  Trading port queue
#
#  A port loads one shipment at a time. Four orders out of one city failed
#  because the three behind the first were treated as failures rather than as
#  a queue. Detection that cannot be trusted must read as "free": a wrong
#  hold stops shipping entirely.
# ===========================================================================

NOW = 1_000_000


@pytest.mark.parametrize("html,expected,why", [
    ("'queueTime': 999999,", 0, "a time already past means the port is free"),
    ("'queueTime': 1000000,", 0, "exactly now is free"),
    ("'queueTime': 1000300,", 300, "the seconds remaining are returned"),
    ('"queueTime":1000300,', 300, "the JSON quoting is read too"),
    ("nothing here", None, "absent is unknown, never busy"),
    ("", None, "an empty response is unknown"),
    ("'queueTime': 1099999,", None, "an absurd value is unknown, not a hold"),
    ("'queueTime': 120,", 0, "a duration-shaped value degrades to free"),
])
def test_port_queue_time_is_read_or_treated_as_free(rtm, html, expected, why):
    assert rtm._parse_port_queue_time(html, NOW) == expected, why


def test_a_long_queue_is_capped_rather_than_trusted(rtm):
    assert (rtm._parse_port_queue_time("'queueTime': 1010000,", NOW)
            == rtm.PORT_HOLD_MAX_SECONDS)


def test_a_hold_expires_and_is_not_left_behind(rtm):
    rtm._port_holds.clear()
    assert rtm._port_hold_remaining("7") == 0
    rtm._port_hold_set("7", 300)
    assert 299 <= rtm._port_hold_remaining("7") <= 300
    assert rtm._port_hold_remaining(7) > 0, "the id type must not matter"
    rtm._port_holds["7"] = time.time() - 1
    assert rtm._port_hold_remaining("7") == 0
    assert "7" not in rtm._port_holds, "an expired hold must not leak"


def test_a_zero_hold_clears_and_an_over_long_one_is_capped(rtm):
    rtm._port_holds.clear()
    assert rtm._port_hold_set("8", 0) == 0
    assert "8" not in rtm._port_holds
    assert rtm._port_hold_set("9", 99999) == rtm.PORT_HOLD_MAX_SECONDS


def test_clearing_a_port_forgets_the_cached_probe_too(rtm):
    rtm._port_holds.clear()
    rtm._port_probe_cache.clear()
    rtm._port_hold_set("9", 300)
    rtm._port_probe_cache["9"] = (time.time(), 500)
    rtm._port_hold_clear("9")
    assert "9" not in rtm._port_holds
    assert "9" not in rtm._port_probe_cache


def test_a_known_busy_port_costs_no_further_requests(rtm):
    rtm._port_holds.clear()
    rtm._port_probe_cache.clear()
    s = FakeSession(["'queueTime': %d," % (int(time.time()) + 240)])
    assert 235 <= rtm._port_busy_seconds(s, "11", "22", "33") <= 240
    assert rtm._port_hold_remaining("11") > 0
    before = s.calls
    rtm._port_busy_seconds(s, "11", "22", "33")
    assert s.calls == before, "the recorded hold must answer on its own"


@pytest.mark.parametrize("body", [
    "no queue data at all",
    RuntimeError("server exploded"),
])
def test_a_port_that_cannot_be_read_is_treated_as_free(rtm, body):
    rtm._port_holds.clear()
    rtm._port_probe_cache.clear()
    assert rtm._port_busy_seconds(FakeSession([body]), "12") == 0
    assert rtm._port_hold_remaining("12") == 0


def test_held_orders_come_back_priority_first_then_oldest(rtm):
    # run_bulk_cycle sorts the held queue on priority alone. Python's sort is
    # stable, so the queue's existing age order survives inside each band.
    queue = [("a", 3), ("b", 1), ("c", 3), ("d", 2), ("e", 1)]
    order = [n for n, _ in sorted(queue,
                                  key=lambda it: rtm._clamp_priority(it[1]))]
    assert order == ["b", "e", "d", "a", "c"]


def test_a_junk_priority_sorts_as_standard_not_first(rtm):
    assert rtm._clamp_priority("nonsense") == rtm.PRIORITY_DEFAULT


# ===========================================================================
#  Page parsing that used to crash
#
#  A shipment failed with "'NoneType' object has no attribute 'group'".
#  getCity guards its own regex and then calls getWarehouseCapacity, which
#  did not. Hardening a parser means following it into what it calls.
# ===========================================================================

def _old_warehouse_capacity(html):
    """getWarehouseCapacity before the guard. Kept to prove the diagnosis."""
    return int(re.search(
        r'maxResources:\s*JSON\.parse\(\'{\\"resource\\":(\d+),', html
    ).group(1))


OWN_CITY = 'maxResources: JSON.parse(\'{\\"resource\\":32000,\\"1\\":32000}\')'
# Parseable as a city, but with no warehouse block: a city that is not ours,
# an ajax fragment, or a maintenance page.
NOT_OUR_CITY = '"updateBackgroundData", {"id":123} ],["updateTemplateData"'


def test_the_old_warehouse_reader_raised_the_reported_error():
    assert _old_warehouse_capacity(OWN_CITY) == 32000
    with pytest.raises(AttributeError) as caught:
        _old_warehouse_capacity(NOT_OUR_CITY)
    assert str(caught.value) == "'NoneType' object has no attribute 'group'"


@pytest.mark.parametrize("html,expected", [
    (OWN_CITY, 32000),
    (NOT_OUR_CITY, 0),
    ("", 0),
])
def test_the_warehouse_reader_now_reports_unknown_instead(html, expected):
    from ikabot.helpers.resources import getWarehouseCapacity
    assert getWarehouseCapacity(html) == expected


def _free_space_decision(storage, free_space, wanted, stock, ship_room,
                         guard_unknown):
    """What the sender decides for one resource.

    guard_unknown is the fix: unknown capacity is treated like a foreign
    city, so the destination clamp is skipped.
    """
    foreign = guard_unknown and not storage
    limits = [stock, wanted, ship_room]
    if not foreign:
        limits.append(free_space)
    return max(0, min(limits))


def test_unknown_capacity_must_not_read_as_a_full_warehouse():
    # Zero capacity makes freeSpaceForResources all zeros. Read naively that
    # says "full", and the sender stalls in an hourly retry forever.
    assert _free_space_decision(0, 0, 1000, 5000, 5000,
                                guard_unknown=False) == 0
    assert _free_space_decision(0, 0, 1000, 5000, 5000,
                                guard_unknown=True) == 1000


def test_a_warehouse_that_really_is_full_is_still_respected():
    assert _free_space_decision(32000, 0, 1000, 5000, 5000,
                                guard_unknown=True) == 0
    assert _free_space_decision(32000, 400, 1000, 5000, 5000,
                                guard_unknown=True) == 400


# ===========================================================================
#  Ship capacity
#
#  Every one-off shipment failed on this. The regex in pedirInfo was missing
#  a bracket, so it could never match any response. Recurring schedules used
#  the module's own reader and kept working, which hid it.
# ===========================================================================

AJAX = ('x(ajax.Responder, [["changeView",["a"]],["b",{}],["c",{}],'
        '["d",{"singleTransporterCapacity":500,'
        '"singleFreighterCapacity":2500,"draftEffect":0}]]);')
PLAIN_PAGE = ('<html><script>var o = {"singleTransporterCapacity":500,'
              '"singleFreighterCapacity":2500};</script></html>')
QUOTED = '{"singleTransporterCapacity":"500","singleFreighterCapacity":"2500"}'
JUNK = "<html>maintenance</html>"


def _ajax_pattern_from(path):
    """The ajax.Responder pattern a file actually uses."""
    found = re.findall(r"r'(ajax\.Responder.*?)'", open(path).read())
    assert found, f"no ajax.Responder pattern in {path}"
    return found[0]


def test_the_old_capacity_regex_could_never_match_anything():
    # '\[\[\S\s]' is '[[' then ONE non-space then ONE space then ']'*, not
    # '[[' then any characters, so it matched nothing at all.
    broken = r'ajax.Responder, (\[\[\S\s]*?\]\])\)\;'
    assert re.search(broken, AJAX) is None, "this is why every send failed"


@pytest.mark.parametrize("path", [
    "ikabot/helpers/pedirInfo.py",
    "ikabot/helpers/getJson.py",
])
def test_the_shipped_ajax_patterns_match_a_real_response(path):
    # Read the pattern out of the file rather than restating it here, so a
    # regression in the file fails this test.
    assert re.search(_ajax_pattern_from(path), AJAX) is not None, \
        f"{path} cannot read an ajax response"


def test_the_modules_own_ajax_pattern_matches_a_real_response():
    assert re.search(_ajax_pattern_from(_module_path()), AJAX) is not None


@pytest.mark.parametrize("html,expected", [
    (AJAX, (500, 2500)),
    (PLAIN_PAGE, (500, 2500)),
    (QUOTED, (500, 2500)),
    (JUNK, None),
    ("", None),
    ('{"singleTransporterCapacity":500}', None),
])
def test_capacity_is_read_from_either_shape_or_reported_unknown(
        rtm, html, expected):
    assert rtm._parse_ship_capacity(html) == expected


def test_core_and_the_module_agree_on_every_shape(rtm):
    from ikabot.helpers.pedirInfo import parseShipCapacity
    for html in (AJAX, PLAIN_PAGE, QUOTED, JUNK, ""):
        assert parseShipCapacity(html) == rtm._parse_ship_capacity(html)


def test_a_bad_first_response_falls_through_and_then_retries(rtm):
    rtm._ship_capacity_cache.clear()
    s = FakeSession([AJAX])
    assert rtm.getShipCapacity(s) == (500, 2500)
    assert s.calls == 1, "a good response should cost one request"

    rtm._ship_capacity_cache.clear()
    s = FakeSession([JUNK, PLAIN_PAGE])
    assert rtm.getShipCapacity(s) == (500, 2500)
    assert s.calls == 2, "a bad POST should fall through to the GET"

    rtm._ship_capacity_cache.clear()
    s = FakeSession([RuntimeError("boom"), PLAIN_PAGE])
    assert rtm.getShipCapacity(s) == (500, 2500)


def test_one_unreadable_response_cannot_cost_a_shipment(rtm):
    # Capacity only rises with research and port level, so a remembered value
    # sends more ships than needed, never too few.
    rtm._ship_capacity_cache.clear()
    rtm.getShipCapacity(FakeSession([AJAX]))
    assert rtm.getShipCapacity(FakeSession([JUNK] * 6)) == (500, 2500)


def test_with_nothing_remembered_it_fails_rather_than_inventing(rtm):
    rtm._ship_capacity_cache.clear()
    with pytest.raises(RuntimeError, match="Could not read ship capacity"):
        rtm.getShipCapacity(FakeSession([JUNK] * 6))


# ===========================================================================
#  The send path must not depend on ikabot core
#
#  The capacity fix was first made in ikabot/helpers/pedirInfo.py. That file
#  ships with the ikabot install, so dropping a new module file in does not
#  update it and the fix never arrived. planRoutes imports the helper into
#  its own namespace, where the module's copy cannot shadow it.
# ===========================================================================

def _module_source():
    return open(_module_path()).read()


def test_no_part_of_core_route_logic_is_imported():
    # executeRoutes went in v10.12.2 because it read ship capacity through a
    # helper a dropped-in module cannot fix. sendGoods went when it turned
    # out to retry a refused load forever, five seconds apart, with no way
    # to give up — the whole send path is the module's own now.
    import ast
    imported = set()
    for node in ast.parse(_module_source()).body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                imported.add(alias.asname or alias.name)
    assert "executeRoutes" not in imported
    assert "sendGoods" not in imported, \
        "importing it invites the unbounded retry back"


def test_every_shipment_goes_through_the_modules_own_sender(rtm):
    import ast
    src = _module_source()
    send = next(n for n in ast.parse(src).body
                if isinstance(n, ast.FunctionDef) and n.name == "send_shipment")
    body = ast.get_source_segment(src, send)
    called = {n.func.id for n in ast.walk(send)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "executeRoutes" not in called
    assert "_execute_routes_bounded" in called
    assert src.count("sent = _execute_routes_bounded(") == 1, \
        "one sender, used unconditionally"
    assert "No ships became free in time" in body, \
        "a one-off must not blame a cycle limit it never had"
    assert "Cycle time limit reached" in body


def test_a_successful_shipment_reports_the_amounts_it_sent(rtm):
    import ast
    src = _module_source()
    send = next(n for n in ast.parse(src).body
                if isinstance(n, ast.FunctionDef) and n.name == "send_shipment")
    body = ast.get_source_segment(src, send)
    assert '"sent": []' in body, "the result must declare it"
    assert 'result["sent"] = list(resources)' in body, \
        "a shared budget needs what went, not what was planned"
    cycle = next(n for n in ast.parse(src).body
                 if isinstance(n, ast.FunctionDef)
                 and n.name == "run_consolidate_cycle")
    assert '_reduce_outstanding' in ast.get_source_segment(src, cycle)


def test_the_module_owns_its_capacity_reader(rtm):
    assert callable(rtm.getShipCapacity)
    assert callable(rtm._parse_ship_capacity)
    assert rtm.ONE_OFF_SEND_LIMIT_SECONDS >= 3 * 3600, \
        "the one-off bound must not cut a real delivery short"


# ===========================================================================
#  Amount parsing
# ===========================================================================

@pytest.mark.parametrize("text,expected", [
    ("4m", 4_000_000),
    ("4404m", 4_404_000_000),
    ("4M", 4_000_000),
    ("1.5m", 1_500_000),
    ("4,404m", 4_404_000_000),
    ("  4m  ", 4_000_000),
    ("500", 500),
    ("10k", 10_000),
    ("1.5k", 1_500),
    ("10,000", 10_000),
    ("10K", 10_000),
    # float(0.7) * 1000 is 699.9999999999999, so this used to be 699.
    ("0.7k", 700),
    ("", 0),
    ("   ", 0),
    ("m", 0),
    ("k", 0),
    ("lots", 0),
    ("4m5", 0),
    ("0", 0),
])
def test_amounts_accept_k_and_m_and_reject_junk(rtm, text, expected):
    assert rtm._parse_amount(text) == expected


@pytest.mark.parametrize("cell,expected", [
    ("4m", ("exact", 4_000_000)),
    ("10k", ("exact", 10_000)),
    ("all-4m", ("except", 4_000_000)),
    ("a-1.5m", ("except", 1_500_000)),
    ("e4m", ("except", 4_000_000)),
    ("all", ("except", 0)),
    # 'm' is the transport column's code for merchant ships. In a resource
    # cell it is not an amount.
    ("m", ("exact", 0)),
])
def test_csv_resource_cells_parse_the_same_way(rtm, cell, expected):
    assert rtm.parse_resource_value(cell) == expected


# ===========================================================================
#  Error wording
#
#  Python's own text tells the reader nothing. Translate raw wording only:
#  the module's own messages are already readable, and rewording them says
#  something untrue.
# ===========================================================================

def test_the_nonetype_crash_gets_a_plain_reason(rtm):
    try:
        re.search("x", "y").group(1)
    except AttributeError as exc:
        reason = rtm._explain_exception(exc)
    assert "could not read" in reason.lower()
    assert "NoneType" not in reason


@pytest.mark.parametrize("exc,expected_in", [
    (RuntimeError("Could not parse city data from page"), "could not read"),
    (RuntimeError("Could not find actionRequest token in page"), "session"),
])
def test_known_failures_are_explained(rtm, exc, expected_in):
    assert expected_in in rtm._explain_exception(exc).lower()


def test_an_unrecognised_error_invents_no_explanation(rtm):
    assert rtm._explain_exception(ValueError("something new")) == ""


@pytest.mark.parametrize("reason,translated", [
    ("'NoneType' object has no attribute 'group'", True),
    ("KeyError: 'freeSpaceForResources'", True),
    # These are the module's own messages. A ship-wait timeout is not a
    # server timeout, and saying so would be false.
    ("No merchant ships available (timed out)", False),
    ("Could not acquire shipping lock after 3 attempts", False),
    ("10 Lluhios trading port is loading another shipment", False),
    ("3 Polis port is blockaded", False),
    ("", False),
])
def test_only_raw_python_wording_is_reworded(rtm, reason, translated):
    assert bool(rtm._explain_log_reason(reason)) is translated


# ===========================================================================
#  The schedule row
# ===========================================================================

@pytest.mark.parametrize("column,default", [
    ("last_error", ""),
    ("armed_at", 0),
    ("amount_scope", "per_city"),
])
def test_added_columns_are_stored_and_backfilled(rtm, column, default):
    assert column in rtm.SCHEDULE_COLUMNS
    assert rtm.SCHEDULE_COLUMN_DEFAULTS.get(column) == default


def test_the_schema_version_is_past_every_migration(rtm):
    assert rtm.SCHEDULE_SCHEMA_VERSION >= 6


def test_a_new_schedule_is_armed_now_and_defaults_to_per_city(rtm):
    row = rtm.build_schedule_row(schedule_id=1, mode="consolidate")
    assert abs(row["armed_at"] - int(time.time())) <= 1
    assert row["amount_scope"] == rtm.AMOUNT_SCOPE_PER_CITY
    assert row["last_error"] == ""


def test_every_built_field_is_a_real_column(rtm):
    row = rtm.build_schedule_row(schedule_id=1, mode="consolidate")
    assert set(row) == set(rtm.SCHEDULE_COLUMNS)


# ===========================================================================
#  Wrapping and the shipment log
# ===========================================================================

LONG_REASON = ("Gave up: retried for over 24 hours and never shipped "
               "anything. The usual causes are no free ships of the type "
               "this schedule uses, no action points, or a blockade.")


def test_a_long_reason_wraps_without_losing_a_word(rtm):
    lines = rtm._wrap_note(LONG_REASON, "    ", width=40)
    assert len(lines) > 3
    assert all(len(x) <= 44 for x in lines)
    assert all(x.startswith("    ") for x in lines)
    assert " ".join(x.strip() for x in lines) == LONG_REASON


def test_an_empty_reason_wraps_to_nothing(rtm):
    assert rtm._wrap_note("", "  ") == []


def _write_log(rtm, path, rows, columns=None):
    columns = columns or rtm.LOG_COLUMNS
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: "" for c in columns} | r)


def test_an_older_log_gains_the_new_columns_without_losing_rows(rtm, tmp_path):
    # Appending a row with a column the header lacks puts every later value
    # in the wrong place, so the header is migrated once instead.
    old_columns = [c for c in rtm.LOG_COLUMNS
                   if c not in ("Schedule", "Source_Id", "Dest_Id")]
    path = str(tmp_path / "legacy.csv")
    _write_log(rtm, path, [{
        "Date": "2026-09-01", "Time": "10:00:00", "Account": "Stave",
        "Mode": "Consolidate", "Source_City": "Lluhios",
        "Dest_City": "St1-W-4", "Status": "FAILED", "Error": "boom",
    }], columns=old_columns)

    rtm._upgrade_log_header(path)

    with open(path, newline="", encoding="utf-8") as f:
        header = next(csv.reader(f))
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert header == rtm.LOG_COLUMNS
    assert rows[0]["Error"] == "boom"
    assert rows[0]["Dest_City"] == "St1-W-4"
    assert rows[0]["Schedule"] == ""

    before = open(path, encoding="utf-8").read()
    rtm._upgrade_log_header(path)
    assert open(path, encoding="utf-8").read() == before, "must be idempotent"


def test_an_already_current_log_is_not_rewritten_at_all(rtm, tmp_path,
                                                        monkeypatch):
    # Comparing content is not enough: rewriting the file with identical
    # data looks the same but rewrites the whole log on every shipment,
    # under the log lock, for every one of ~24 instances.
    path = str(tmp_path / "current.csv")
    _write_log(rtm, path, [{"Date": "2026-09-01", "Status": "SENT"}])
    replacements = []
    real_replace = os.replace
    monkeypatch.setattr(rtm.os, "replace",
                        lambda a, b: (replacements.append(b),
                                      real_replace(a, b))[1])
    rtm._upgrade_log_header(path)
    assert replacements == [], "a current header must cost no write"


def test_the_upgrade_is_safe_on_a_missing_or_empty_log(rtm, tmp_path):
    missing = str(tmp_path / "nope.csv")
    rtm._upgrade_log_header(missing)
    assert not os.path.exists(missing), "it must not create a log"
    empty = str(tmp_path / "empty.csv")
    open(empty, "w").close()
    rtm._upgrade_log_header(empty)
    assert os.path.exists(empty)


def test_a_logged_shipment_records_its_schedule_and_cities(rtm, tmp_path,
                                                           monkeypatch):
    monkeypatch.setattr(rtm, "_lock_acquire", lambda *a, **k: False)
    monkeypatch.setattr(rtm, "_lock_release", lambda *a, **k: None)
    path = str(tmp_path / "fresh.csv")
    rtm.log_shipment(path, FakeSession(), "Consolidate", "A", "", "B",
                     "[1:2]", "", [1, 0, 0, 0, 0], 1, "merchant ships",
                     "FAILED", "something broke", "in 1h",
                     schedule_id=7, source_id="111", dest_id="222")
    with open(path, newline="", encoding="utf-8") as f:
        row = next(csv.DictReader(f))
    assert row["Schedule"] == "7"
    assert (row["Source_Id"], row["Dest_Id"]) == ("111", "222")
    assert row["Error"] == "something broke"


def test_a_shipment_with_no_schedule_logs_a_blank_not_a_crash(rtm, tmp_path,
                                                             monkeypatch):
    monkeypatch.setattr(rtm, "_lock_acquire", lambda *a, **k: False)
    monkeypatch.setattr(rtm, "_lock_release", lambda *a, **k: None)
    path = str(tmp_path / "anon.csv")
    rtm.log_shipment(path, FakeSession(), "Consolidate", "A", "", "B", "", "",
                     [0] * 5, 0, "merchant ships", "SENT")
    with open(path, newline="", encoding="utf-8") as f:
        assert next(csv.DictReader(f))["Schedule"] == ""


# ===========================================================================
#  Telling one schedule's shipments from another's
#
#  Several bulk schedules all logged as "Bulk Distribution" and could not be
#  told apart, which is why the log now carries the schedule id.
# ===========================================================================

@pytest.fixture
def log_for(rtm, tmp_path, monkeypatch):
    base = str(tmp_path / "shipment_log.csv")
    monkeypatch.setattr(rtm, "load_prefs", lambda: {"log_path": base})
    path = rtm._account_log_path(base, FakeSession())

    def write(rows):
        _write_log(rtm, path, rows)
    return write


def test_only_this_schedules_rows_come_back(rtm, log_for):
    log_for([
        {"Schedule": "1", "Mode": "Consolidate", "Status": "SENT"},
        {"Schedule": "2", "Mode": "Bulk Distribution", "Status": "FAILED",
         "Error": "port busy"},
        {"Schedule": "1", "Mode": "Consolidate", "Status": "SKIPPED",
         "Error": "no ships"},
        {"Schedule": "3", "Mode": "Bulk Distribution", "Status": "SENT"},
    ])
    rows, note = rtm._schedule_log_rows(FakeSession(),
                                        {"schedule_id": 1,
                                         "mode": "consolidate"})
    assert len(rows) == 2
    assert all(r["Schedule"] == "1" for r in rows)
    assert rows[-1]["Status"] == "SKIPPED", "oldest first"
    assert note == "", "an exact match needs no caveat"


def test_two_bulk_schedules_are_not_confused(rtm, log_for):
    log_for([
        {"Schedule": "2", "Mode": "Bulk Distribution", "Status": "FAILED",
         "Error": "port busy"},
        {"Schedule": "3", "Mode": "Bulk Distribution", "Status": "SENT"},
    ])
    rows, _ = rtm._schedule_log_rows(FakeSession(),
                                     {"schedule_id": 2, "mode": "bulk"})
    assert [r["Error"] for r in rows] == ["port busy"]


def test_rows_written_before_the_column_are_matched_loosely_and_labelled(
        rtm, log_for):
    log_for([{"Schedule": "", "Mode": "Consolidate", "Status": "FAILED",
              "Error": "old row"}])
    rows, note = rtm._schedule_log_rows(FakeSession(),
                                        {"schedule_id": 9,
                                         "mode": "consolidate"})
    assert len(rows) == 1
    assert "predate" in note, "the screen must not overstate what it knows"


def test_no_log_at_all_is_unknown_rather_than_empty(rtm, tmp_path,
                                                    monkeypatch):
    monkeypatch.setattr(rtm, "load_prefs",
                        lambda: {"log_path": str(tmp_path / "absent.csv")})
    rows, _ = rtm._schedule_log_rows(FakeSession(),
                                     {"schedule_id": 1,
                                      "mode": "consolidate"})
    assert rows is None


# ===========================================================================
#  Recording why a cycle failed
# ===========================================================================

def test_the_error_note_leads_with_the_plain_reason(rtm):
    rtm._record_cycle_error("The game sent back a page ikabot could not read.",
                            "'NoneType' object has no attribute 'group'")
    note = rtm._cycle_error_note()
    assert note.startswith("The game sent back")
    assert "NoneType" in note, "the technical detail is kept for reporting"


def test_a_cleared_error_shows_nothing(rtm):
    rtm._record_cycle_error("")
    assert rtm._cycle_error_note() == ""


def test_a_detail_with_no_plain_reason_is_still_shown(rtm):
    rtm._record_cycle_error("", "raw only")
    assert rtm._cycle_error_note() == "raw only"


def test_a_cycle_hold_is_per_cycle_and_keeps_the_longest(rtm):
    rtm._record_cycle_error("")
    assert rtm._cycle_hold_until() == 0
    rtm._record_port_hold(240)
    assert 235 <= rtm._cycle_hold_until() - time.time() <= 240
    rtm._record_port_hold(60)
    assert rtm._cycle_hold_until() - time.time() > 200, "longest wins"
    rtm._record_port_hold(900)
    assert rtm._cycle_hold_until() - time.time() > 800
    rtm._record_cycle_error("")
    assert rtm._cycle_hold_until() == 0, "cleared for the next schedule"
    rtm._record_port_hold(300)
    rtm._record_cycle_error("something broke", "detail")
    assert rtm._cycle_hold_until() > 0, "a real error must not wipe the hold"


# ===========================================================================
#  Giving up, and not giving up
#
#  Four orders out of one city stopped after one attempt each. The 24 hour
#  give-up clock ran from created_at, which retrying never reset, so an order
#  older than a day was already past the limit on its first retried attempt.
# ===========================================================================

def _gives_up(rtm, armed_at, created_at, now, port_held):
    """The scheduler's rule for a one-off that shipped nothing."""
    armed = armed_at or created_at
    age = now - armed if armed else 0
    return age > rtm.ONE_SHOT_GIVEUP_SECONDS and not port_held


def _next_run(rtm, finished, hold_until, port_held):
    if port_held:
        return max(finished + 60, int(hold_until) + 30)
    return finished + rtm.ONE_SHOT_RETRY_SECONDS


@pytest.fixture
def clock():
    now = int(time.time())
    return now, now - 9 * 86400       # now, and a week-and-a-bit ago


def test_the_old_clock_gave_up_on_the_first_retried_attempt(rtm, clock):
    now, week_old = clock
    assert _gives_up(rtm, 0, week_old, now, port_held=False)


def test_arming_the_clock_gives_a_full_fresh_window(rtm, clock):
    now, week_old = clock
    assert not _gives_up(rtm, now, week_old, now, port_held=False)
    assert _gives_up(rtm, now - 25 * 3600, week_old, now, port_held=False), \
        "it must still give up 24h after being retried"


def test_a_schedule_with_no_clock_does_not_give_up(rtm, clock):
    now, _ = clock
    assert not _gives_up(rtm, 0, 0, now, port_held=False)


def test_a_port_queue_is_not_a_failed_attempt(rtm, clock):
    now, week_old = clock
    assert not _gives_up(rtm, now - 40 * 3600, week_old, now, port_held=True)
    assert _gives_up(rtm, now - 40 * 3600, week_old, now, port_held=False)


def test_a_held_order_comes_back_when_the_port_frees(rtm, clock):
    now, _ = clock
    assert _next_run(rtm, now, now + 240, True) == now + 270
    assert _next_run(rtm, now, now + 240, True) < now + rtm.ONE_SHOT_RETRY_SECONDS
    assert _next_run(rtm, now, now + 1, True) == now + 60, "never immediate"
    assert _next_run(rtm, now, now + 3000, True) == now + 3030
    assert _next_run(rtm, now, 0, False) == now + rtm.ONE_SHOT_RETRY_SECONDS


def test_orders_behind_a_loading_port_chain_instead_of_stopping(rtm, clock):
    now, week_old = clock
    armed, rounds, clock_now = now, 0, now
    for _ in range(10):
        if _gives_up(rtm, armed, week_old, clock_now, port_held=True):
            break
        rounds += 1
        clock_now = _next_run(rtm, clock_now, clock_now + 240, True)
    assert rounds == 10, "ten loading windows and still trying"
    assert clock_now - now >= 10 * 240, "it waited real time rather than spinning"


# ===========================================================================
#  Retrying a failed schedule
#
#  A schedule that errored or finished had no way back: pause/resume refused
#  it and its next_run was blank, so the only route was to delete it and
#  build the whole thing again.
# ===========================================================================

RUN_COLUMNS = ["City", "Wood", "Run_0901-1200", "Issues_0901-1200",
               "Run_0902-1200", "Issues_0902-1200"]


def _bulk_csv(tmp_path, name, rows):
    path = str(tmp_path / name)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=RUN_COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: "" for c in RUN_COLUMNS} | r)
    return path


def _read_rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_resetting_a_run_touches_only_that_slot(rtm, tmp_path):
    path = _bulk_csv(tmp_path, "bulk.csv", [
        {"City": "A", "Run_0901-1200": "X", "Issues_0901-1200": "stale",
         "Run_0902-1200": "X"},
        {"City": "B", "Run_0901-1200": "1,3", "Issues_0901-1200": "no ships",
         "Run_0902-1200": ""},
        {"City": "C", "Run_0901-1200": "", "Run_0902-1200": "X"},
    ])
    reset, err = rtm._reset_bulk_run_column(
        {"bulk_csv_path": path, "bulk_run_column": "Run_0901-1200"})
    rows = _read_rows(path)
    assert err == ""
    assert reset == 2, "only rows that had progress are counted"
    assert all(r["Run_0901-1200"] == "" for r in rows)
    assert all(r["Issues_0901-1200"] == "" for r in rows)
    assert [r["Run_0902-1200"] for r in rows] == ["X", "", "X"], \
        "another run of the same file keeps its history"
    assert [r["City"] for r in rows] == ["A", "B", "C"]


@pytest.mark.parametrize("sched,expected_in", [
    ({"bulk_csv_path": "/nowhere/gone.csv",
      "bulk_run_column": "Run_0901-1200"}, "no longer"),
    ({"bulk_csv_path": "", "bulk_run_column": ""}, "no CSV"),
])
def test_an_unusable_csv_is_reported_not_crashed_on(rtm, sched, expected_in):
    reset, err = rtm._reset_bulk_run_column(sched)
    assert reset == 0
    assert expected_in in err


def test_a_missing_run_column_is_reported(rtm, tmp_path):
    path = _bulk_csv(tmp_path, "other.csv", [{"City": "A"}])
    reset, err = rtm._reset_bulk_run_column(
        {"bulk_csv_path": path, "bulk_run_column": "Run_9999-9999"})
    assert (reset, "column" in err) == (0, True)


@pytest.fixture
def retry_probe(rtm, monkeypatch, quiet):
    """Capture the CSV writes a retry performs."""
    updates = []
    monkeypatch.setattr(rtm, "transport_csv_update",
                        lambda s, sid, **f: updates.append((sid, f)))
    monkeypatch.setattr(rtm, "_is_transport_worker_running", lambda s: True)
    return updates


def test_a_retry_re_arms_the_schedule_and_clears_the_error(rtm, retry_probe):
    rtm._retry_schedule(FakeSession(), {
        "schedule_id": 7, "mode": "consolidate", "status": "error",
        "interval_hours": 6, "last_error": "gave up",
        "created_at": int(time.time()) - 9 * 86400,
    })
    sid, fields = retry_probe[0]
    assert sid == 7
    assert fields["status"] == "active"
    assert fields["last_error"] == ""
    assert abs(fields["next_run"] - int(time.time())) <= 1
    assert abs(fields["armed_at"] - int(time.time())) <= 1, \
        "without this an old order gets a single attempt"
    assert set(fields) == {"status", "next_run", "last_error", "armed_at"}
    assert set(fields) <= set(rtm.SCHEDULE_COLUMNS)


def test_a_bulk_retry_leaves_finished_rows_alone_by_default(rtm, retry_probe,
                                                            quiet):
    rtm._retry_schedule(FakeSession(), {
        "schedule_id": 9, "mode": "bulk", "status": "error",
        "interval_hours": 0,
    })
    out = " ".join(quiet)
    assert "already marked done stay done" in out
    assert "Cleared progress" not in out


def test_retrying_the_whole_file_clears_its_progress(rtm, tmp_path,
                                                     retry_probe, quiet):
    path = _bulk_csv(tmp_path, "bulk2.csv", [
        {"City": "A", "Run_0901-1200": "X"},
        {"City": "B", "Run_0901-1200": "X"},
    ])
    rtm._retry_schedule(FakeSession(), {
        "schedule_id": 10, "mode": "bulk", "status": "completed",
        "interval_hours": 0, "bulk_csv_path": path,
        "bulk_run_column": "Run_0901-1200",
    }, reset_bulk_rows=True)
    assert all(r["Run_0901-1200"] == "" for r in _read_rows(path))
    assert "Cleared progress on 2 row(s)" in " ".join(quiet)
    assert retry_probe[0][1]["status"] == "active"


def test_a_csv_that_cannot_be_reset_does_not_cancel_the_retry(rtm, retry_probe,
                                                              quiet):
    rtm._retry_schedule(FakeSession(), {
        "schedule_id": 11, "mode": "bulk", "status": "error",
        "interval_hours": 0, "bulk_csv_path": "/nowhere/gone.csv",
        "bulk_run_column": "Run_0901-1200",
    }, reset_bulk_rows=True)
    out = " ".join(quiet)
    assert retry_probe[0][1]["status"] == "active", "unsent rows must still go"
    assert "Could not reset" in out and "still being retried" in out


def test_a_stopped_scheduler_is_called_out(rtm, monkeypatch, retry_probe,
                                            quiet):
    monkeypatch.setattr(rtm, "_is_transport_worker_running", lambda s: False)
    rtm._retry_schedule(FakeSession(), {
        "schedule_id": 12, "mode": "topup", "status": "error",
        "interval_hours": 4,
    })
    assert "scheduler is not running" in " ".join(quiet)


def test_retry_all_offers_nothing_when_everything_is_healthy(rtm, retry_probe,
                                                              quiet,
                                                              monkeypatch):
    monkeypatch.setattr(rtm, "_safe_read", lambda **k: "'")
    rtm._retry_all_failed(FakeSession(), [
        {"schedule_id": 1, "status": "active"},
        {"schedule_id": 2, "status": "paused"},
    ])
    assert "No schedules are reporting a problem" in " ".join(quiet)
    assert retry_probe == []


def test_retry_all_picks_errors_and_schedules_that_reported_a_problem(
        rtm, retry_probe, quiet, monkeypatch):
    monkeypatch.setattr(rtm, "_safe_read", lambda **k: 1)
    rtm._retry_all_failed(FakeSession(), [
        {"schedule_id": 1, "status": "active", "last_error": ""},
        {"schedule_id": 2, "status": "error", "mode": "topup",
         "interval_hours": 2},
        {"schedule_id": 3, "status": "active", "mode": "bulk",
         "interval_hours": 6, "last_error": "sent nothing"},
    ])
    assert "#2, #3" in " ".join(quiet)
    assert {u[0] for u in retry_probe} == {2, 3}
    assert all(u[1]["status"] == "active" for u in retry_probe)


def test_cancelling_retry_all_changes_nothing(rtm, retry_probe, quiet,
                                              monkeypatch):
    monkeypatch.setattr(rtm, "_safe_read", lambda **k: "'")
    rtm._retry_all_failed(FakeSession(), [{"schedule_id": 2,
                                           "status": "error"}])
    assert retry_probe == []


# ===========================================================================
#  Shipment history
# ===========================================================================

def _hist_row(rtm, days_ago, **extra):
    when = datetime.datetime.now() - datetime.timedelta(days=days_ago)
    when = when.replace(hour=12, minute=0, second=0)
    row = {c: "" for c in rtm.LOG_COLUMNS}
    row.update({
        "Date": when.strftime("%Y-%m-%d"), "Time": when.strftime("%H:%M:%S"),
        "Account": "Stave", "Mode": "Consolidate",
        "Source_City": "10 Lluhios", "Source_Id": "111",
        "Dest_City": "St1-W-4", "Dest_Id": "222", "Dest_Island": "[97:40]",
        "Ship_Type": "merchant ships", "Ships_Used": "3", "Status": "SENT",
        "Wood": "0", "Wine": "0", "Marble": "0", "Crystal": "0",
        "Sulphur": "0", "Total_Resources": "0",
    })
    row.update(extra)
    return row


def test_history_filters_by_age_and_by_count(rtm, log_for):
    log_for([_hist_row(rtm, d) for d in (10, 8, 6, 4, 2, 0)])
    recent = rtm._load_history(FakeSession(), days=5)
    assert len(recent) == 3
    assert rtm._row_timestamp(recent[0]) < rtm._row_timestamp(recent[-1])

    log_for([_hist_row(rtm, 0) for _ in range(30)])
    assert len(rtm._load_history(FakeSession(),
                                 count=rtm.HISTORY_RECENT_COUNT)) == 20
    assert len(rtm._load_history(FakeSession(), count=100)) == 30


def test_a_quiet_period_is_an_empty_list_not_unknown(rtm, log_for):
    log_for([_hist_row(rtm, 10), _hist_row(rtm, 9)])
    assert rtm._load_history(FakeSession(), days=5) == []


def test_a_missing_log_is_unknown_rather_than_empty(rtm, tmp_path,
                                                    monkeypatch):
    monkeypatch.setattr(rtm, "load_prefs",
                        lambda: {"log_path": str(tmp_path / "absent.csv")})
    assert rtm._load_history(FakeSession()) is None


def test_a_row_with_an_unreadable_date_is_kept_not_hidden(rtm, log_for):
    odd = _hist_row(rtm, 0)
    odd["Date"] = "not a date"
    log_for([_hist_row(rtm, 10), odd])
    assert rtm._row_timestamp(odd) == 0
    kept = rtm._load_history(FakeSession(), days=5)
    assert [r["Date"] for r in kept] == ["not a date"]


def test_the_brief_line_lists_only_what_was_carried(rtm):
    row = _hist_row(rtm, 0, Wood="5000", Marble="1200")
    sep = rtm.addThousandSeparator
    assert rtm._history_brief(row) == f"{sep(5000)}W {sep(1200)}M"
    assert rtm._history_brief(_hist_row(rtm, 0)) == "-"
    assert rtm._history_resources(row) == [5000, 0, 1200, 0, 0]
    assert rtm._history_resources({"Wood": "", "Wine": None}) == [0] * 5


@pytest.mark.parametrize("status,attr", [
    ("SENT", "OK"), ("FAILED", "RED"), ("PARTIAL", "YELLOW"),
    ("HELD", "YELLOW"), ("SKIPPED", "YELLOW"),
])
def test_a_status_is_coloured_by_what_it_means(rtm, status, attr):
    assert rtm._history_colour(status) == getattr(rtm.C, attr)


def test_an_unknown_status_gets_no_colour(rtm):
    assert rtm._history_colour("WAT") == ""


@pytest.mark.parametrize("status", ["HELD", "SKIPPED", "FAILED", "PARTIAL",
                                    "DELAYED", "EXHAUSTED"])
def test_every_failure_status_has_plain_english(rtm, status):
    assert rtm._LOG_STATUS_HELP.get(status)


@pytest.fixture
def resend_probe(rtm, monkeypatch, quiet):
    queued = []
    monkeypatch.setattr(rtm, "transport_csv_append_with_id",
                        lambda s, row: (queued.append(row), 42)[1])
    monkeypatch.setattr(rtm, "_ask_priority", lambda *a, **k: 2)
    monkeypatch.setattr(rtm, "_is_transport_worker_running", lambda s: True)
    return queued


def test_a_resend_repeats_the_shipment_exactly(rtm, resend_probe, quiet):
    rtm._resend_shipment(FakeSession(), _hist_row(
        rtm, 0, Status="FAILED", Wood="5000", Marble="1200",
        Error="something broke"))
    sched = resend_probe[0]
    assert sched["resource_config"] == [5000, 0, 1200, 0, 0]
    assert sched["source_city_ids"] == ["111"]
    assert sched["dest_city_ids"] == ["222"]
    assert sched["interval_hours"] == 0, "a one-off, not a repeat"
    assert sched["send_mode"] == "send"
    assert sched["status"] == "active"
    assert sched["next_run"] <= int(time.time()) + 1
    # consolidate is the mode that resolves another player's city from
    # island data, which is where much of this account's shipping goes.
    assert sched["mode"] == "consolidate"
    assert sched["priority"] == 2
    assert "Resend of" in sched["notes"]
    assert "schedule #42" in " ".join(quiet)


@pytest.mark.parametrize("ship_type,expected", [
    ("freighters", "f"),
    ("merchant ships", "m"),
])
def test_a_resend_keeps_the_ship_type(rtm, resend_probe, ship_type, expected):
    rtm._resend_shipment(FakeSession(),
                         _hist_row(rtm, 0, Wood="900", Ship_Type=ship_type))
    assert resend_probe[0]["ship_type"] == expected


def test_a_resend_that_cannot_be_queued_is_reported(rtm, monkeypatch, quiet):
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(rtm, "transport_csv_append_with_id", boom)
    monkeypatch.setattr(rtm, "_ask_priority", lambda *a, **k: 2)
    rtm._resend_shipment(FakeSession(), _hist_row(rtm, 0, Wood="900"))
    out = " ".join(quiet)
    assert "Could not queue" in out
    assert "Queued as" not in out, "nothing may be claimed to have been queued"


# ===========================================================================
#  Consolidate: per city, or in total
#
#  Requesting 20,895,749 across several sources previewed 71,372,554, because
#  the figure was asked of every city. The screen never said so.
# ===========================================================================

WANTED = [8_000_000, 4_000_000, 3_895_749, 3_000_000, 2_000_000]
RICH = [[50_000_000] * 5 for _ in range(4)]


def _consolidate(rtm, requested, stocks, scope, dest_space=None):
    """Walk the source cities the way run_consolidate_cycle does.

    The loop is here, but every amount decision and every deduction is the
    module's own, so a change to either fails these tests.
    """
    n = len(requested)
    outstanding = [requested[i] if isinstance(requested[i], int) else None
                   for i in range(n)]
    share = scope == rtm.AMOUNT_SCOPE_TOTAL
    shipments = []
    for stock in stocks:
        if share and not any(o for o in outstanding if o):
            break
        to_send = [0] * n
        for i in range(n):
            if requested[i] is None:
                continue
            s = rtm._consolidate_amount(
                requested[i], stock[i], 2,
                outstanding[i] if share else None)
            if dest_space is not None:
                s = min(s, dest_space[i])
                dest_space[i] -= s
            to_send[i] = s
        if sum(to_send) == 0:
            continue
        if share:
            rtm._reduce_outstanding(outstanding, to_send)
        shipments.append(to_send)
    return shipments, sum(sum(sh) for sh in shipments)


def test_one_city_contributes_its_share_and_no_more(rtm):
    # The decision the cycle and the preview both make.
    assert rtm._consolidate_amount(5000, 9000, 2, None) == 5000
    assert rtm._consolidate_amount(5000, 9000, 2, 2000) == 2000, \
        "a shared budget caps the contribution at what is outstanding"
    assert rtm._consolidate_amount(5000, 1200, 2, 9000) == 1200, \
        "stock still wins"
    assert rtm._consolidate_amount(5000, 9000, 2, 0) == 0, \
        "nothing outstanding means nothing to send"
    assert rtm._consolidate_amount(5000, 9000, 2, -10) == 0


def test_the_budget_is_reduced_by_what_shipped(rtm):
    outstanding = [1000, None, 500]
    rtm._reduce_outstanding(outstanding, [400, 999, 500])
    assert outstanding == [600, None, 0]
    rtm._reduce_outstanding(outstanding, [9999, 0, 0])
    assert outstanding == [0, None, 0], "it never goes negative"


def test_the_total_entered_adds_up_to_20_895_749():
    assert sum(WANTED) == 20_895_749


def test_per_city_asks_every_city_and_multiplies_the_total(rtm):
    shipments, total = _consolidate(rtm, WANTED, RICH,
                                    rtm.AMOUNT_SCOPE_PER_CITY)
    assert len(shipments) == 4
    assert total == 4 * 20_895_749, "this is the over-shipping reported"


def test_a_total_request_delivers_the_figure_entered(rtm):
    shipments, total = _consolidate(rtm, WANTED, RICH, rtm.AMOUNT_SCOPE_TOTAL)
    assert total == 20_895_749
    assert len(shipments) == 1, "later cities stand down once it is met"
    for i in range(5):
        assert sum(sh[i] for sh in shipments) == WANTED[i]


def test_a_total_is_shared_when_no_single_city_can_cover_it(rtm):
    poor = [[2_000_000, 1_000_000, 1_000_000, 800_000, 500_000]
            for _ in range(6)]
    shipments, total = _consolidate(rtm, WANTED, poor, rtm.AMOUNT_SCOPE_TOTAL)
    pooled = [sum(c[i] for c in poor) for i in range(5)]
    assert len(shipments) > 1
    assert total == sum(min(WANTED[i], pooled[i]) for i in range(5))
    assert total <= sum(WANTED)


def test_a_partly_stocked_first_city_does_not_cause_an_overshoot(rtm):
    staggered = [[3_000_000] * 5, [50_000_000] * 5, [50_000_000] * 5]
    shipments, total = _consolidate(rtm, WANTED, staggered,
                                    rtm.AMOUNT_SCOPE_TOTAL)
    assert total == 20_895_749
    for i in range(5):
        assert sum(sh[i] for sh in shipments) == WANTED[i]


def test_skipped_and_send_all_resources_are_untouched_by_the_budget(rtm):
    mixed = [8_000_000, None, 0, ("except", 0), 1_000_000]
    shipments, _ = _consolidate(rtm, mixed, RICH, rtm.AMOUNT_SCOPE_TOTAL)
    assert all(sh[1] == 0 for sh in shipments), "a blank is never sent"
    assert all(sh[2] == 0 for sh in shipments), "a zero is never sent"
    assert sum(sh[3] for sh in shipments) > 0, "send-all still draws per city"
    assert sum(sh[0] for sh in shipments) == 8_000_000


def test_destination_space_still_beats_the_request(rtm):
    tight = [100_000] * 5
    _, total = _consolidate(rtm, WANTED, RICH, rtm.AMOUNT_SCOPE_TOTAL,
                            dest_space=list(tight))
    assert total <= sum(tight)


def test_one_source_city_cannot_tell_the_two_readings_apart(rtm):
    one = [[50_000_000] * 5]
    per_city = _consolidate(rtm, WANTED, one, rtm.AMOUNT_SCOPE_PER_CITY)[1]
    in_total = _consolidate(rtm, WANTED, one, rtm.AMOUNT_SCOPE_TOTAL)[1]
    assert per_city == in_total == 20_895_749


def test_the_setup_asks_which_reading_applies_and_stores_it():
    import ast
    src = _module_source()
    setup = next(n for n in ast.parse(src).body
                 if isinstance(n, ast.FunctionDef)
                 and n.name == "consolidateMode")
    body = ast.get_source_segment(src, setup)
    assert "Amount Meaning" in body, "the question must be asked"
    assert "amount_scope=amount_scope" in body, "and the answer stored"
    assert "FROM EACH of the" in body and "TOTAL of each resource" in body
    assert "from EACH" in body and "in total, shared" in body, \
        "the summary must name the reading it used"
    assert "outstanding_preview" in body, \
        "the preview must share the budget or it still shows N times"


# ===========================================================================
#  Waiting for ships
#
#  The module used to ask every twenty seconds whether ships were home. The
#  game already knows: the military advisor lists every one of our own
#  movements with the moment it lands. No local ledger of ships is kept,
#  because one would drift the first time the player sends a fleet by hand.
# ===========================================================================

def _advisor(movements, server_time=1_000_000):
    """A military advisor reply in the shape the game sends."""
    return json.dumps([
        ["x", {"time": server_time}],
        ["y", ["a", "b", {"viewScriptParams":
                          {"militaryAndFleetMovements": movements}}]],
    ])


def _mv(event_time, own=True, returning=True):
    return {"isOwnArmyOrFleet": own, "eventTime": event_time,
            "event": {"isFleetReturning": returning}}


def test_our_own_arrivals_are_read_soonest_first(rtm):
    payload = _advisor([_mv(1_000_600), _mv(1_000_120), _mv(1_003_600)])
    assert rtm._parse_fleet_events(payload) == [120, 600, 3600]


def test_other_players_fleets_are_ignored(rtm):
    payload = _advisor([_mv(1_000_060, own=False), _mv(1_000_600)])
    assert rtm._parse_fleet_events(payload) == [600]


def test_nothing_of_ours_moving_is_an_answer_not_a_failure(rtm):
    assert rtm._parse_fleet_events(_advisor([])) == []


@pytest.mark.parametrize("payload", [
    "not json at all",
    "{}",
    json.dumps([["x", {}], ["y", []]]),
    json.dumps([["x", {"time": 1}], ["y", ["z", {"viewScriptParams": {}}]]]),
])
def test_a_reply_that_cannot_be_read_is_unknown_not_empty(rtm, payload):
    # None and [] must stay distinguishable: [] means nothing is out, which
    # would wrongly say ships are free right now.
    assert rtm._parse_fleet_events(payload) is None


def test_the_servers_clock_is_used_not_this_machines(rtm):
    # A local clock an hour out must not change the answer.
    payload = _advisor([_mv(5_000_300)], server_time=5_000_000)
    assert rtm._parse_fleet_events(payload) == [300]


def test_a_landing_already_past_reads_as_zero_not_negative(rtm):
    assert rtm._parse_fleet_events(_advisor([_mv(999_000)])) == [0]


def test_an_absurd_arrival_is_discarded(rtm):
    far = 1_000_000 + rtm.FLEET_EVENT_SANITY_SECONDS + 600
    assert rtm._parse_fleet_events(_advisor([_mv(far), _mv(1_000_300)])) == [300]


def test_a_movement_missing_its_time_is_skipped_not_fatal(rtm):
    payload = _advisor([{"isOwnArmyOrFleet": True}, _mv(1_000_300)])
    assert rtm._parse_fleet_events(payload) == [300]


@pytest.fixture
def fleet(rtm, monkeypatch):
    """Answer the advisor with a scripted reply and count the requests."""
    rtm._fleet_event_cache.clear()
    state = {"calls": 0, "payload": _advisor([])}
    monkeypatch.setattr(rtm, "_current_city_id", lambda s: "123")

    class Advisor(FakeSession):
        def post(self, *a, **k):
            state["calls"] += 1
            if isinstance(state["payload"], Exception):
                raise state["payload"]
            return state["payload"]

    state["session"] = Advisor()
    return state


def test_the_earliest_event_is_what_the_module_waits_for(rtm, fleet):
    now = int(time.time())
    fleet["payload"] = _advisor([_mv(now + 7200), _mv(now + 900)],
                                server_time=now)
    assert rtm._next_fleet_event_seconds(fleet["session"]) == 900


def test_the_answer_is_cached_so_one_cycle_asks_once(rtm, fleet):
    now = int(time.time())
    fleet["payload"] = _advisor([_mv(now + 900)], server_time=now)
    first = rtm._next_fleet_event_seconds(fleet["session"])
    again = rtm._next_fleet_event_seconds(fleet["session"])
    assert first == again == 900
    assert fleet["calls"] == 1


def test_an_advisor_that_fails_reports_unknown(rtm, fleet):
    fleet["payload"] = RuntimeError("server down")
    assert rtm._next_fleet_event_seconds(fleet["session"]) is None


def test_an_unreadable_advisor_falls_back_to_the_short_poll(rtm, fleet):
    fleet["payload"] = "nonsense"
    nap, arrival = rtm._ship_wait_sleep(fleet["session"], remaining=3600)
    assert arrival is None
    assert nap <= rtm.SHIP_WAIT_POLL_SECONDS + 5, \
        "with no information it must keep checking often"


def test_a_known_arrival_is_waited_out_instead_of_polled(rtm, fleet,
                                                         monkeypatch):
    monkeypatch.setattr(rtm, "load_prefs",
                        lambda: {"ship_wait_max_sleep_minutes": 60})
    now = int(time.time())
    fleet["payload"] = _advisor([_mv(now + 1800)], server_time=now)
    nap, arrival = rtm._ship_wait_sleep(fleet["session"], remaining=7200)
    assert arrival == 1800
    assert nap == 1810, "it waits for the landing, plus a small margin"
    assert nap > rtm.SHIP_WAIT_POLL_SECONDS * 10


def test_the_ceiling_keeps_manual_play_noticeable(rtm, fleet, monkeypatch):
    # Ships can come free sooner than a known landing if the player finishes
    # building more, so the module never sleeps past the ceiling.
    monkeypatch.setattr(rtm, "load_prefs",
                        lambda: {"ship_wait_max_sleep_minutes": 15})
    now = int(time.time())
    fleet["payload"] = _advisor([_mv(now + 7200)], server_time=now)
    nap, arrival = rtm._ship_wait_sleep(fleet["session"], remaining=86400)
    assert arrival == 7200
    assert nap == 15 * 60


def test_the_ceiling_is_configurable(rtm, monkeypatch):
    monkeypatch.setattr(rtm, "load_prefs",
                        lambda: {"ship_wait_max_sleep_minutes": 40})
    assert rtm._ship_wait_ceiling() == 40 * 60
    monkeypatch.setattr(rtm, "load_prefs", lambda: {})
    assert rtm._ship_wait_ceiling() == rtm.SHIP_WAIT_MAX_SLEEP_MINUTES * 60
    monkeypatch.setattr(rtm, "load_prefs",
                        lambda: {"ship_wait_max_sleep_minutes": "rubbish"})
    assert rtm._ship_wait_ceiling() == rtm.SHIP_WAIT_MAX_SLEEP_MINUTES * 60
    monkeypatch.setattr(rtm, "load_prefs",
                        lambda: {"ship_wait_max_sleep_minutes": 0})
    assert rtm._ship_wait_ceiling() >= rtm.SHIP_WAIT_MIN_SLEEP_SECONDS, \
        "zero must not mean a busy loop"


def test_the_wait_never_runs_past_the_budget_it_was_given(rtm, fleet):
    now = int(time.time())
    fleet["payload"] = _advisor([_mv(now + 3600)], server_time=now)
    nap, _ = rtm._ship_wait_sleep(fleet["session"], remaining=120)
    assert nap == 120
    assert rtm._ship_wait_sleep(fleet["session"], remaining=0) == (0, None)


def test_nothing_of_ours_out_means_look_again_at_once(rtm, fleet):
    # Zero ships free and nothing moving is contradictory, so re-check
    # promptly rather than sleeping on it.
    fleet["payload"] = _advisor([])
    nap, arrival = rtm._ship_wait_sleep(fleet["session"], remaining=3600)
    assert arrival == 0
    assert nap == rtm.SHIP_WAIT_MIN_SLEEP_SECONDS


def test_a_ships_wait_and_a_port_wait_are_told_apart(rtm):
    rtm._record_cycle_error("")
    assert rtm._cycle_hold_reason() == ""
    rtm._record_fleet_hold(600)
    assert rtm._cycle_hold_reason() == "fleet"
    rtm._record_port_hold(300)
    assert rtm._cycle_hold_reason() == "fleet", "the longer wait wins"
    rtm._record_port_hold(1200)
    assert rtm._cycle_hold_reason() == "port"
    rtm._record_cycle_error("")
    assert rtm._cycle_hold_reason() == ""


def test_the_scheduler_names_what_it_is_waiting_for(rtm):
    import ast
    src = _module_source()
    loop = next(n for n in ast.parse(src).body
                if isinstance(n, ast.FunctionDef)
                and n.name == "transport_scheduler_loop")
    body = ast.get_source_segment(src, loop)
    assert "_cycle_hold_reason()" in body
    assert "Waiting for ships" in body
    assert "Waiting for the trading port" in body


def test_a_shipment_short_of_ships_records_when_they_land(rtm):
    import ast
    src = _module_source()
    send = next(n for n in ast.parse(src).body
                if isinstance(n, ast.FunctionDef) and n.name == "send_shipment")
    body = ast.get_source_segment(src, send)
    assert "_record_fleet_hold(back_in)" in body, \
        "the retry must land when the fleet does, not on a flat interval"


def test_no_local_ledger_of_ships_is_kept():
    # The game is the only authority: a local count drifts the first time the
    # player sends a fleet by hand, builds more, or loses some.
    src = _module_source()
    for banned in ("_ship_inventory", "_fleet_ledger", "ships_owned"):
        assert banned not in src


# ===========================================================================
#  Request pacing
#
#  An IP was temporarily blocked while a 40,000,000 shipment sat refusing to
#  send. Nothing below this module limits the rate: session.get and
#  session.post go out as fast as they are called. The ceiling that matters
#  is per address, and around two dozen instances share one.
# ===========================================================================

def _reset_throttle(rtm, allowance=None, burst=None):
    rtm._throttle["allowance"] = (float(burst if allowance is None
                                        else allowance)
                                  if allowance is not None
                                  else float(rtm.REQUEST_BURST))
    rtm._throttle["checked"] = None
    rtm._throttle["installed"].clear()
    rtm._throttle["paced"] = 0
    rtm._throttle["waited"] = 0.0


def test_a_burst_goes_straight_through_then_pacing_bites(rtm, monkeypatch):
    monkeypatch.setattr(rtm, "load_prefs",
                        lambda: {"request_min_interval_seconds": 1.0,
                                 "request_burst": 3})
    napped = []
    monkeypatch.setattr(rtm.time, "sleep", lambda s: napped.append(s))
    _reset_throttle(rtm, allowance=3.0)
    clock = 1000.0
    monkeypatch.setattr(rtm.time, "monotonic", lambda: clock)
    # Three in a row at the same instant: the burst allowance covers them.
    assert [rtm._throttle_wait() for _ in range(3)] == [0.0, 0.0, 0.0]
    assert napped == []
    # The fourth has to wait, because no time has passed to refill it.
    assert rtm._throttle_wait() > 0
    assert napped and napped[0] > 0


def test_a_tight_loop_settles_to_the_sustained_rate(rtm, monkeypatch):
    monkeypatch.setattr(rtm, "load_prefs",
                        lambda: {"request_min_interval_seconds": 2.0,
                                 "request_burst": 1})
    napped = []
    monkeypatch.setattr(rtm.time, "sleep", lambda s: napped.append(s))
    _reset_throttle(rtm, allowance=1.0)
    clock = [500.0]
    monkeypatch.setattr(rtm.time, "monotonic", lambda: clock[0])
    rtm._throttle_wait()                      # spends the one token
    for _ in range(5):
        rtm._throttle_wait()
    assert len(napped) == 5
    assert all(abs(n - 2.0) < 0.01 for n in napped), \
        "with no time passing, each request waits a full interval"


def test_time_passing_refills_the_allowance(rtm, monkeypatch):
    monkeypatch.setattr(rtm, "load_prefs",
                        lambda: {"request_min_interval_seconds": 1.0,
                                 "request_burst": 5})
    monkeypatch.setattr(rtm.time, "sleep", lambda s: None)
    _reset_throttle(rtm, allowance=0.0)
    clock = [0.0]
    monkeypatch.setattr(rtm.time, "monotonic", lambda: clock[0])
    rtm._throttle_wait()
    clock[0] = 10.0                            # ten seconds of quiet
    assert rtm._throttle_wait() == 0.0, "a quiet period must not be punished"


def test_pacing_can_be_turned_off_but_is_on_by_default(rtm, monkeypatch):
    monkeypatch.setattr(rtm, "load_prefs",
                        lambda: {"request_min_interval_seconds": 0})
    _reset_throttle(rtm)
    assert rtm._throttle_wait() == 0.0
    monkeypatch.setattr(rtm, "load_prefs", lambda: {})
    interval, burst = rtm._request_rate_settings()
    assert interval == rtm.REQUEST_MIN_INTERVAL_SECONDS
    assert interval >= 1.0, \
        "two dozen instances at one a second is already too many per address"
    assert burst == rtm.REQUEST_BURST


@pytest.mark.parametrize("prefs", [
    {"request_min_interval_seconds": "nonsense"},
    {"request_burst": None},
    {"request_min_interval_seconds": -5, "request_burst": 0},
])
def test_junk_pacing_settings_fall_back_to_safe_ones(rtm, monkeypatch, prefs):
    monkeypatch.setattr(rtm, "load_prefs", lambda: prefs)
    interval, burst = rtm._request_rate_settings()
    assert interval >= 0
    assert burst >= 1, "a zero burst must not divide by nothing"


def test_the_throttle_wraps_the_session_once(rtm, monkeypatch):
    monkeypatch.setattr(rtm, "load_prefs",
                        lambda: {"request_min_interval_seconds": 0.0})
    _reset_throttle(rtm)
    s = FakeSession(["a", "b"])
    original_get = s.get
    assert rtm.install_request_throttle(s) is True
    assert s.get is not original_get, "requests must go through the pacer"
    assert rtm.install_request_throttle(s) is False, "wrapping twice stacks"
    assert s.get() == "a", "the wrapper must still return the response"


def test_wrapping_the_session_catches_cores_requests_too(rtm, monkeypatch):
    # The loops that flooded the server are in core, and core is handed this
    # same session object, so pacing it is what bounds them.
    monkeypatch.setattr(rtm, "load_prefs",
                        lambda: {"request_min_interval_seconds": 1.0,
                                 "request_burst": 1})
    napped = []
    monkeypatch.setattr(rtm.time, "sleep", lambda s: napped.append(s))
    _reset_throttle(rtm, allowance=1.0)
    clock = [0.0]
    monkeypatch.setattr(rtm.time, "monotonic", lambda: clock[0])
    s = FakeSession(["x"] * 6)
    rtm.install_request_throttle(s)
    for _ in range(4):
        s.post()
    assert len(napped) == 3, "only the first went free"


def test_pacing_a_request_does_not_hide_it_from_the_connection_count(rtm,
                                                                    monkeypatch,
                                                                    tmp_path):
    """The throttle wraps the session, and the counter is inside it.

    If the pacer ever answered a request itself rather than calling through,
    the Connections graph would quietly under-report the one module most
    likely to flood — so the two are pinned together here rather than left to
    be true by construction.
    """
    import ikabot.helpers.requestLog as rl

    monkeypatch.setattr(rl, "MONITOR_DIR", str(tmp_path))
    monkeypatch.setattr(rl, "FLUSH_SECONDS", 0.0)
    rl._state.update({"account": "", "path": "", "t0": 0, "counts": [],
                      "errors": [], "total": 0, "peak": 0, "flushed": 0.0,
                      "pruned": 0.0})
    monkeypatch.setattr(rtm, "load_prefs",
                        lambda: {"request_min_interval_seconds": 0.0,
                                 "request_burst": 10})
    _reset_throttle(rtm, allowance=10.0)

    class Counted(FakeSession):
        def get(self, *a, **k):
            body = self._next()
            rl.record_request(self, 200)
            return body

        def post(self, *a, **k):
            return self.get(*a, **k)

    session = Counted(["page"] * 6)
    assert rtm.install_request_throttle(session) is True
    for _ in range(6):
        session.get()

    assert session.calls == 6, "the pacer must call through, not answer itself"
    counted = rl.read_counts("Stave_en70")
    assert counted, "the paced requests never reached the counter"
    assert sum(sum(part["counts"]) for part in counted) == 6


def test_both_entry_points_install_the_throttle():
    src = _module_source()
    import ast
    for name in ("transport_scheduler_loop", "resourceTransportManager"):
        fn = next(n for n in ast.parse(src).body
                  if isinstance(n, ast.FunctionDef) and n.name == name)
        assert "install_request_throttle(session)" in \
            ast.get_source_segment(src, fn), f"{name} must pace its requests"


# ===========================================================================
#  A refused load must give up
# ===========================================================================

def _reply(code=None, text=None, nested=False):
    inner = {"type": code} if code is not None else {}
    if text and not nested:
        inner["text"] = text
    body = [0, 0, 0, [None, [inner]]]
    if text and nested:
        body[1] = {"deep": [{"other": {"text": text}}]}
    return json.dumps(body)


def test_the_games_reason_is_found_wherever_it_sits(rtm):
    assert rtm._refusal_text(json.loads(_reply(11, "Not enough ships"))) \
        == "Not enough ships"
    assert "Buried" in rtm._refusal_text(
        json.loads(_reply(11, "Buried reason", nested=True)))
    assert rtm._refusal_text(json.loads(_reply(10))) == ""
    assert rtm._refusal_text({}) == ""


def test_a_repeated_reason_is_not_repeated_back(rtm):
    payload = {"a": {"text": "same"}, "b": {"text": "same"}}
    assert rtm._refusal_text(payload) == "same"


@pytest.fixture
def sender(rtm, monkeypatch):
    monkeypatch.setattr(rtm.time, "sleep", lambda s: None)
    monkeypatch.setattr(rtm, "_next_fleet_event_seconds",
                        lambda s, **k: 300)
    monkeypatch.setattr(rtm, "_ship_wait_ceiling", lambda: 900)
    city = ('"updateBackgroundData", {"id":"111","name":"A",'
            '"availableResources":[1,1,1,1,1],"ownerId":1,"ownerName":"x",'
            '"islandXCoord":"1","islandYCoord":"2"} ],'
            '["updateTemplateData"')

    class Sender(FakeSession):
        def __init__(self, replies):
            super().__init__()
            self.replies = list(replies)
            self.posts = 0

        def get(self, *a, **k):
            return city

        def post(self, *a, **k):
            self.posts += 1
            # the changeCurrentCity post has no reply we read
            if a or k.get("params", {}).get("function") == "changeCurrentCity":
                if k.get("params", {}).get("function") == "changeCurrentCity":
                    return ""
            if self.replies:
                return self.replies.pop(0)
            return _reply(None)
    return Sender


def test_a_refused_load_stops_instead_of_retrying_forever(rtm, sender,
                                                          monkeypatch):
    monkeypatch.setattr(rtm, "getCity", lambda html: {
        "id": "111", "availableResources": [1, 1, 1, 1, 1]})
    s = sender([_reply(11, "Not enough ships")] * 20)
    ok, why = rtm._send_one_load(s, "111", "222", "9", 3, [500, 0, 0, 0, 0],
                                 False)
    assert ok is False
    assert "Not enough ships" in why, "the reason must reach the user"
    # The old code retried for as long as the account existed.
    assert s.posts <= rtm.SEND_ATTEMPT_LIMIT * 2 + 2


def test_the_sender_has_no_unbounded_loop_in_it():
    # The flood came from `while True` with a five second sleep. The bound
    # must be structural, not a break somebody can delete.
    import ast
    src = _module_source()
    fn = next(n for n in ast.parse(src).body
              if isinstance(n, ast.FunctionDef) and n.name == "_send_one_load")
    for loop in [n for n in ast.walk(fn) if isinstance(n, ast.While)]:
        assert not (isinstance(loop.test, ast.Constant)
                    and loop.test.value is True), \
            "a refused load must not be retried in an unbounded loop"
    ranges = [n for n in ast.walk(fn) if isinstance(n, ast.For)]
    assert ranges, "the attempts must be a bounded loop"
    assert 1 < rtm_attempt_limit() <= 10


def rtm_attempt_limit():
    import ast
    src = _module_source()
    node = next(n for n in ast.parse(src).body
                if isinstance(n, ast.Assign)
                and any(getattr(t, "id", "") == "SEND_ATTEMPT_LIMIT"
                        for t in n.targets))
    return ast.literal_eval(node.value)


def test_an_accepted_load_returns_at_once(rtm, sender, monkeypatch):
    monkeypatch.setattr(rtm, "getCity", lambda html: {
        "id": "111", "availableResources": [1, 1, 1, 1, 1]})
    s = sender([_reply(rtm.SEND_ACCEPTED)])
    ok, why = rtm._send_one_load(s, "111", "222", "9", 3, [500, 0, 0, 0, 0],
                                 False)
    assert (ok, why) == (True, "")


def test_a_load_with_nothing_in_it_is_not_sent(rtm, sender, monkeypatch):
    # getCity is patched so the send path would genuinely proceed. Without
    # that, this passed because the real parser choked on the fake page
    # rather than because the guard held.
    monkeypatch.setattr(rtm, "getCity", lambda html: {
        "id": "111", "availableResources": [1, 1, 1, 1, 1]})
    s = sender([_reply(rtm.SEND_ACCEPTED)] * 4)
    assert rtm._send_one_load(s, "1", "2", "9", 0, [500, 0, 0, 0, 0],
                              False)[0] is False, "no ships means no load"
    assert rtm._send_one_load(s, "1", "2", "9", 3, [0] * 5,
                              False)[0] is False, "no cargo means no load"
    assert s.posts == 0, "it must not even ask the game"


def test_a_silent_refusal_still_says_something(rtm, sender, monkeypatch):
    monkeypatch.setattr(rtm, "getCity", lambda html: {
        "id": "111", "availableResources": [1, 1, 1, 1, 1]})
    s = sender([_reply(None)] * 20)
    ok, why = rtm._send_one_load(s, "111", "222", "9", 3, [500, 0, 0, 0, 0],
                                 False)
    assert ok is False
    assert why, "a refusal with no reason must not read as success"


# ===========================================================================
#  A shipment too big to send
# ===========================================================================

def test_the_loads_a_shipment_needs_are_counted_before_sending():
    import ast
    src = _module_source()
    send = next(n for n in ast.parse(src).body
                if isinstance(n, ast.FunctionDef) and n.name == "send_shipment")
    body = ast.get_source_segment(src, send)
    assert "MAX_LOADS_PER_SHIPMENT" in body, \
        "40,000,000 by merchant ship is 80,000 loads and must be refused"
    assert "SHIPMENT TOO BIG" in body
    assert "Freighters carry" in body, "it must point at the way that works"


def test_the_reported_shipment_would_now_be_refused(rtm):
    # 40,000,000 at 500 per merchant ship.
    import math
    loads = math.ceil(40_000_000 / 500)
    assert loads == 80_000
    assert loads > rtm.MAX_LOADS_PER_SHIPMENT
    # The same by freighter is still far too many, so that is refused too.
    assert math.ceil(40_000_000 / 2500) > rtm.MAX_LOADS_PER_SHIPMENT


def test_an_ordinary_shipment_is_not_refused(rtm):
    import math
    assert math.ceil(100_000 / 500) <= rtm.MAX_LOADS_PER_SHIPMENT
    assert math.ceil(500_000 / 2500) <= rtm.MAX_LOADS_PER_SHIPMENT


def test_a_cycle_stops_after_a_sane_number_of_loads(rtm):
    import ast
    src = _module_source()
    fn = next(n for n in ast.parse(src).body
              if isinstance(n, ast.FunctionDef)
              and n.name == "_execute_routes_bounded")
    body = ast.get_source_segment(src, fn)
    assert "MAX_LOADS_PER_CYCLE" in body
    assert "TRIP_GAP_SECONDS" in body, \
        "back-to-back loads with no pause is what floods the server"
    assert "_send_one_load(" in body
    assert "if not accepted:" in body, "a refusal must end the cycle"
    assert 0 < rtm.MAX_LOADS_PER_CYCLE <= 200
    assert rtm.TRIP_GAP_SECONDS >= 1
