"""Scheduler Monitor decides when a stopped scheduler must be restarted.

The point of the module is that one wrong decision either leaves a scheduler
dead (the bug it exists to fix) or starts a second worker alongside a live one
(duplicate builds and shipments). These tests pin every branch of that call.
"""

import importlib.util
import os
import types

import pytest

_MODULE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "modules", "schedulerMonitor_v1.0.0.py",
)


@pytest.fixture(scope="module")
def sm():
    spec = importlib.util.spec_from_file_location("schedulerMonitor", _MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeSession:
    username = "TestPlayer"
    servidor = "s70-en"
    mundo = "Alpha"

    def setStatus(self, message):
        pass


@pytest.fixture
def session():
    return FakeSession()


@pytest.fixture
def fake_construction(sm, monkeypatch, tmp_path):
    """Stand in for constructionManager with controllable state."""
    mod = types.SimpleNamespace(
        running=False,
        pending=0,
        stop_flag=str(tmp_path / "stop"),
    )
    mod._is_worker_running = lambda session: mod.running
    mod.csv_count_pending = lambda session: mod.pending
    mod.stop_flag_path = lambda session: mod.stop_flag
    monkeypatch.setattr(sm, "_module_for",
                        lambda key: mod if key == "construction" else None)
    return mod


@pytest.fixture
def activations(sm, monkeypatch):
    calls = []

    def _activate(session, key):
        calls.append(key)
        return True

    monkeypatch.setattr(sm, "_activate", _activate)
    return calls


def test_running_scheduler_is_left_alone(sm, session, fake_construction, activations):
    fake_construction.running = True
    fake_construction.pending = 5

    line = sm._check_target(session, "construction", {}, notify=False)

    assert activations == []
    assert "running" in line


def test_stopped_scheduler_with_work_is_restarted(sm, session, fake_construction,
                                                  activations):
    fake_construction.pending = 3

    line = sm._check_target(session, "construction", {}, notify=False)

    assert activations == ["construction"]
    assert "restarted" in line


def test_stopped_scheduler_with_empty_queue_is_not_started(sm, session,
                                                           fake_construction,
                                                           activations):
    fake_construction.pending = 0

    line = sm._check_target(session, "construction", {}, notify=False)

    assert activations == []
    assert "idle" in line


def test_stop_flag_means_the_user_stopped_it(sm, session, fake_construction,
                                             activations):
    fake_construction.pending = 3
    open(fake_construction.stop_flag, "w").close()

    line = sm._check_target(session, "construction", {}, notify=False)

    assert activations == []
    assert "stopping" in line


def test_a_failing_worker_is_not_relaunched_every_pass(sm, session,
                                                       fake_construction,
                                                       activations):
    fake_construction.pending = 3
    last_start = {}

    sm._check_target(session, "construction", last_start, notify=False)
    line = sm._check_target(session, "construction", last_start, notify=False)

    assert activations == ["construction"]
    assert "before retrying" in line


def test_uninstalled_module_is_reported_not_started(sm, session, monkeypatch,
                                                    activations):
    monkeypatch.setattr(sm, "_module_for", lambda key: None)

    line = sm._check_target(session, "construction", {}, notify=False)

    assert activations == []
    assert "not installed" in line


def test_a_broken_module_does_not_stop_the_pass(sm, session, monkeypatch,
                                                activations):
    def _boom(key):
        raise RuntimeError("bad module")

    monkeypatch.setattr(sm, "_module_for", _boom)

    lines = sm._run_pass(session,
                         {"enabled": ["construction"], "notify": False},
                         {})

    assert activations == []
    assert len(lines) == 1


def test_handoff_marker_round_trips(sm):
    marker = sm._HANDOFF_PREFIX + '{"target": "recruit_ships"}'

    assert sm._take_handoff([marker]) == {"target": "recruit_ships"}
    assert sm._take_handoff(["1", "2"]) is None
    assert sm._take_handoff([]) is None


def test_saved_settings_are_validated_before_use(sm, session, monkeypatch):
    monkeypatch.setattr(sm, "_HAS_MODULE_PREFS", True)
    monkeypatch.setattr(sm, "_mp_load_prefs", lambda session, name: {
        "enabled": ["construction", "noSuchModule"],
        "interval_minutes": 99999,
        "notify": False,
    }, raising=False)

    settings = sm._load_settings(session)

    assert settings["enabled"] == ["construction"]
    assert settings["interval_minutes"] == sm._DEFAULT_INTERVAL_MINUTES
    assert settings["notify"] is False


def test_monitor_lock_reports_a_live_monitor(sm, session, monkeypatch, tmp_path):
    lock = str(tmp_path / "monitor.lock")
    monkeypatch.setattr(sm, "_monitor_lock_path", lambda session: lock)

    assert sm._monitor_lock_is_fresh(session) is False
    sm._monitor_heartbeat(session)
    assert sm._monitor_lock_is_fresh(session) is True
    sm._monitor_lock_release(session)
    assert sm._monitor_lock_is_fresh(session) is False


@pytest.fixture(autouse=True)
def no_console_detach(monkeypatch):
    """_run_worker_child detaches the process from the terminal — not in tests."""
    import ikabot.helpers.process as process
    monkeypatch.setattr(process, "detach_console", lambda: None)


class CountingEvent:
    def __init__(self):
        self.sets = 0

    def set(self):
        self.sets += 1


def test_worker_child_hands_the_menu_back_exactly_once(sm, session, monkeypatch,
                                                       fake_construction):
    """A child that cannot start must still release the parent's menu."""
    fake_construction.pending = 0
    event = CountingEvent()

    sm._run_worker_child(session, event, 0, {"target": "construction"})

    assert event.sets == 1


def test_worker_child_reports_an_unknown_target(sm, session, monkeypatch):
    monkeypatch.setattr(sm, "_module_for", lambda key: None)
    event = CountingEvent()

    sm._run_worker_child(session, event, 0, {"target": "nonsense"})

    assert event.sets == 1
