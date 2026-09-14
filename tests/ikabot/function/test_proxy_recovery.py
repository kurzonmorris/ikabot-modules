"""A dead proxy is a setting to correct, not a reason to end the instance.

What used to happen, on an instance whose proxy had stopped working:

    Do you want to disable the proxy? [Y/n]
    n  -> sys.exit()                      the instance dies
    y  -> proxy disabled, then sys.exit() the instance dies anyway

So it took two goes — start, answer, watch it die, start again — to get back
to a menu, and answering "no" never got there at all. These tests pin the
replacement: ask whether a new proxy should be entered, and carry on either
way.
"""

import pytest

import ikabot.config as config
import ikabot.function.proxyConf as pc
from ikabot.web.session import Session


BROKEN = "socks5://1.2.3.4:9050"
WORKING = "socks5://127.0.0.1:9050"


def proxy_data(url=BROKEN):
    return {"proxy": {"set": True, "conf": {"http": url, "https": url}}}


class FakeSession:
    """Only the parts of a session that the proxy code touches."""

    urlBase = "https://example.invalid/"
    servidor = word = username = "x"      # read only by sendToBot
    _PROXY_FIX_LIMIT = Session._PROXY_FIX_LIMIT

    def __init__(self, data=None, padre=True):
        self.data = data if data is not None else proxy_data()
        self.padre = padre

    def getSessionData(self):
        import copy
        return copy.deepcopy(self.data)

    def mutateSessionData(self, fn):
        self.data = fn(self.data)
        return self.data


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    """No "press enter" pauses, and a clean banner for every test."""
    monkeypatch.setattr(pc, "enter", lambda *a, **k: None)
    monkeypatch.setattr(config, "update_msg", "")
    yield
    config.predetermined_input = []


def answer(monkeypatch, *replies):
    monkeypatch.setattr(config, "predetermined_input", list(replies))


def proxy_works(monkeypatch, predicate):
    monkeypatch.setattr(pc, "test_proxy", lambda session, conf: predicate(conf))


# ------------------------------------------------------- at startup ---------

def test_declining_turns_the_proxy_off_and_returns(monkeypatch):
    proxy_works(monkeypatch, lambda conf: False)
    answer(monkeypatch, "n")
    session = FakeSession()

    pc.show_proxy(session)               # must not raise SystemExit

    assert session.data["proxy"]["set"] is False


def test_the_default_answer_also_carries_on(monkeypatch):
    proxy_works(monkeypatch, lambda conf: False)
    answer(monkeypatch, "")              # someone just pressing enter
    session = FakeSession()

    pc.show_proxy(session)

    assert session.data["proxy"]["set"] is False


def test_a_new_proxy_is_tested_and_kept(monkeypatch):
    proxy_works(monkeypatch, lambda conf: conf["https"] == WORKING)
    answer(monkeypatch, "y", WORKING, "")
    session = FakeSession()

    pc.show_proxy(session)

    assert session.data["proxy"]["set"] is True
    assert session.data["proxy"]["conf"]["https"] == WORKING
    assert WORKING in config.update_msg


def test_a_replacement_that_does_not_work_asks_again(monkeypatch):
    proxy_works(monkeypatch, lambda conf: False)
    answer(monkeypatch, "y", "socks5://5.6.7.8:1080", "", "n")
    session = FakeSession()

    pc.show_proxy(session)               # not stuck, and not exited

    assert session.data["proxy"]["set"] is False


def test_a_working_proxy_asks_nothing(monkeypatch):
    proxy_works(monkeypatch, lambda conf: True)
    answer(monkeypatch)                  # nothing to answer with
    session = FakeSession()

    pc.show_proxy(session)

    assert session.data["proxy"]["set"] is True
    assert BROKEN in config.update_msg


def test_the_banner_drops_the_line_when_the_proxy_goes(monkeypatch):
    proxy_works(monkeypatch, lambda conf: False)
    answer(monkeypatch, "n")
    monkeypatch.setattr(config, "update_msg", "using proxy: %s\n" % BROKEN)

    pc.show_proxy(FakeSession())

    assert "using proxy" not in config.update_msg


# ------------------------------------------------- mid-request failures -----

proxy_error = Session.__dict__["_Session__proxy_error"]


def test_a_request_failure_recovers_instead_of_exiting(monkeypatch):
    proxy_works(monkeypatch, lambda conf: False)
    answer(monkeypatch, "n")
    session = FakeSession()

    assert proxy_error(session) is True   # True: the caller retries the login
    assert session.data["proxy"]["set"] is False


def test_a_request_failure_accepts_a_new_proxy(monkeypatch):
    proxy_works(monkeypatch, lambda conf: conf["https"] == WORKING)
    answer(monkeypatch, "y", WORKING, "")
    session = FakeSession()

    assert proxy_error(session) is True
    assert session.data["proxy"]["conf"]["https"] == WORKING


def test_no_proxy_configured_is_still_a_network_error(monkeypatch):
    session = FakeSession(data={})

    with pytest.raises(SystemExit) as exc:
        proxy_error(session)

    assert "network error" in str(exc.value)


def test_a_background_process_is_not_asked_anything(monkeypatch):
    """There is nobody at the keyboard, so it reports and stops, as before."""
    sent = []
    monkeypatch.setattr("ikabot.web.session.sendToBot",
                        lambda s, m, **k: sent.append(m))
    session = FakeSession(padre=False)

    with pytest.raises(SystemExit):
        proxy_error(session)

    assert sent and "proxy" in sent[0].lower()


def test_it_stops_asking_eventually(monkeypatch):
    """A proxy that can never be fixed must not become an endless question."""
    proxy_works(monkeypatch, lambda conf: False)
    session = FakeSession()

    for _ in range(Session._PROXY_FIX_LIMIT):
        answer(monkeypatch, "n")
        session.data = proxy_data()      # broken again each time
        assert proxy_error(session) is True

    answer(monkeypatch, "n")
    session.data = proxy_data()
    with pytest.raises(SystemExit):
        proxy_error(session)
