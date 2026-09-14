"""Proxy health has to reach the status file before anyone is asked about it.

A failing proxy makes the menu stop and ask whether to enter a new one, and
write_status() is only reached once that has been answered — which on an
unattended instance is hours. So the proxy state is written on its own, the
moment it is known, or the panel's alert would only ever appear to somebody
already looking at the screen.
"""

import json
import os

import pytest

import ikabot.helpers.taskWatchdog as tw


class FakeSession:
    username = "acct01"
    servidor = "en"
    mundo = 70


@pytest.fixture
def status_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(tw, "STATUS_DIR", str(tmp_path))
    return tmp_path


def read(status_dir):
    files = list(status_dir.glob("*.json"))
    assert len(files) == 1, files
    return json.loads(files[0].read_text())


# ------------------------------------------------------------- writing ------

def test_a_broken_proxy_is_written_immediately(status_dir):
    session = FakeSession()

    tw.set_proxy_state(session, ok=False, url="socks5://1.2.3.4:9050")

    proxy = read(status_dir)["proxy"]
    assert proxy["set"] is True
    assert proxy["ok"] is False
    assert proxy["url"] == "socks5://1.2.3.4:9050"


def test_no_proxy_configured_is_not_an_alert(status_dir):
    session = FakeSession()

    tw.set_proxy_state(session, ok=True, url=None)

    proxy = read(status_dir)["proxy"]
    assert proxy["set"] is False
    assert proxy["ok"] is True


def test_it_writes_even_with_no_status_file_yet(status_dir):
    """An account that has not reached the menu still has a proxy to report."""
    session = FakeSession()

    tw.set_proxy_state(session, ok=False, url="socks5://1.2.3.4:9050")

    payload = read(status_dir)
    assert payload["schema"] == 1
    assert payload["account"] == "acct01_en70"
    # No task state invented for tasks nobody has looked at yet.
    assert "tasks" not in payload


# ------------------------------------------------------------- keeping ------

def test_a_full_write_keeps_the_proxy_state(status_dir):
    """write_status must not drop what set_proxy_state recorded."""
    session = FakeSession()
    tw.set_proxy_state(session, ok=False, url="socks5://1.2.3.4:9050")

    tw.write_status(session, [])

    payload = read(status_dir)
    assert payload["proxy"]["ok"] is False
    assert payload["task_count"] == 0        # and still a real status file


def test_the_clock_starts_when_it_broke_not_at_every_pass(status_dir):
    """So the panel can say how long, rather than 'just now' for ever."""
    session = FakeSession()
    tw.set_proxy_state(session, ok=False, url="socks5://1.2.3.4:9050")
    first = read(status_dir)["proxy"]["since"]

    session._proxy_state["since"] = first - 600      # as if ten minutes passed
    tw.set_proxy_state(session, ok=False, url="socks5://1.2.3.4:9050")

    assert read(status_dir)["proxy"]["since"] == first - 600


def test_the_clock_restarts_when_the_state_changes(status_dir):
    session = FakeSession()
    tw.set_proxy_state(session, ok=False, url="socks5://1.2.3.4:9050")
    session._proxy_state["since"] = 1

    tw.set_proxy_state(session, ok=True, url="socks5://1.2.3.4:9050")

    assert read(status_dir)["proxy"]["since"] > 1


# ------------------------------------------------------------- safety -------

def test_it_never_raises(monkeypatch, tmp_path):
    """Status export is a convenience; it must not be able to stop ikabot."""
    monkeypatch.setattr(tw, "STATUS_DIR", str(tmp_path / "nope" / "\0bad"))
    tw.set_proxy_state(FakeSession(), ok=False, url="socks5://1.2.3.4:9050")
