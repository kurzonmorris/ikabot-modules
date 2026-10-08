"""When the proxy starts being used, and the one request that must never use it.

Gameforge refuses an account login from a hosting address, which is why a
proxy that works in a browser appears to break ikabot. The authentication at
the top of __login therefore has to leave this machine's address, always, and
the proxy begins afterwards. These pin that boundary in the source itself,
because it is a property of the order the calls appear in and nothing at
runtime would notice if the order changed.
"""

import os
import re

import pytest

import ikabot.function.proxyConf as pc

_SESSION = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))),
    "ikabot", "web", "session.py",
)


@pytest.fixture(scope="module")
def lines():
    with open(_SESSION, encoding="utf-8") as f:
        return f.read().split("\n")


def line_of(lines, needle, start=0):
    for n in range(start, len(lines)):
        if needle in lines[n]:
            return n
    raise AssertionError("not found: %s" % needle)


# ------------------------------------------------------- the boundary -------

def test_the_login_session_is_built_with_no_proxy(lines):
    # A fresh requests.Session carries no proxies, and nothing sets them until
    # the authentication is over. That is what keeps the login on this address.
    fresh = line_of(lines, "self.s = requests.Session()",
                    line_of(lines, "def __login"))
    first_proxy = line_of(lines, "self.__update_proxy(", fresh)
    auth = line_of(lines, "gameforge.com/api/v1/auth/thin/sessions", fresh)
    assert fresh < auth < first_proxy, (
        "the proxy is applied at line %d, before the account login at %d"
        % (first_proxy + 1, auth + 1))


def test_every_gameforge_auth_call_happens_before_any_proxy_is_set(lines):
    start = line_of(lines, "def __login")
    first_proxy = line_of(lines, "self.__update_proxy(", start)
    for host in ("gameforge.com/api/v1/auth/thin/sessions",
                 "spark-web.gameforge.com/api/v2/authProviders"):
        for n, text in enumerate(lines):
            if host in text:
                assert n < first_proxy, (
                    "%s is requested at line %d, after the proxy is set at %d"
                    % (host, n + 1, first_proxy + 1))


def test_entering_the_game_server_is_always_proxied(lines):
    enter = line_of(lines, "html = self.s.get(url, verify=config.do_ssl_verify)")
    before = [n for n, t in enumerate(lines)
              if "self.__update_proxy(" in t and n < enter]
    assert before, "nothing applies the proxy before entering the game server"
    assert enter - max(before) < 12, (
        "the proxy is set %d lines before the game server request; something "
        "was inserted between them" % (enter - max(before)))


def test_choosing_the_server_is_proxied_only_on_the_select_phase(lines):
    post = line_of(lines, "api/users/me/loginLink\", json=data")
    window = "\n".join(lines[post - 10:post])
    assert 'phase") == "select"' in window, (
        "the server-selection request no longer checks the phase")
    assert "self.__update_proxy(" in window


# --------------------------------------------------------- the setting ------

def test_an_old_setting_with_no_phase_behaves_as_it_always_did(lines):
    assert pc.proxy_phase({"proxy": {"set": True, "conf": {}}}) == "game"
    assert pc.DEFAULT_PHASE == "game"


def test_a_stored_phase_is_used(lines):
    assert pc.proxy_phase({"proxy": {"phase": "select"}}) == "select"
    assert pc.proxy_phase({"proxy": {"phase": "game"}}) == "game"


def test_a_phase_that_is_not_one_of_ours_falls_back(lines):
    assert pc.proxy_phase({"proxy": {"phase": "whenever"}}) == "game"
    assert pc.proxy_phase({}) == "game"


def test_both_phases_have_something_to_say_on_screen(lines):
    for phase, words in pc.PHASES.items():
        assert words and phase in ("game", "select")


def test_replacing_a_broken_proxy_keeps_the_phase(lines):
    src = open(pc.__file__, encoding="utf-8").read()
    body = src[src.index("def _store_proxy"):]
    body = body[:body.index("return sd")]
    assert 'setdefault("phase"' in body, (
        "a proxy replaced after a failure would lose which phase it used")
