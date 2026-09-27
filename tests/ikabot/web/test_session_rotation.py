"""Session takeover detection.

Ikariam allows one session per account. A login from a browser or another
device closes this one. Upstream added detection for that (#448) and then had
to fix it (#470), because the first version searched the page body for
"lobby.ikariam.gameforge.com" and "consent.gameforge.com" — strings an
ordinary page with a cookie banner also carries. It reported a takeover on a
healthy session, and the response to a takeover is to stop the process.

These tests pin the host-based check that replaced it, and the parts of
upstream's handling this fork deliberately does not copy.
"""

import re

import pytest

from ikabot.web.session import Session


class FakeResponse:
    def __init__(self, url="", history=()):
        self.url = url
        self.history = list(history)


class FakeRedirect:
    def __init__(self, location):
        self.headers = {"Location": location}


def _rotated(session, html, response=None):
    return session._Session__isSessionRotated(html, response=response)


@pytest.fixture
def session():
    return Session.__new__(Session)


# ------------------------------------------------------------ true cases ---

def test_reload_command_to_the_lobby_is_a_takeover(session):
    html = ('[["custom",["reload",{"link":"https://lobby.ikariam.gameforge.com'
            '/es_ES","target":"_self"}]]]')
    assert _rotated(session, html) is True


def test_a_followed_redirect_to_the_lobby_is_a_takeover(session):
    """requests follows the 302 itself, so the final status is 200 and the
    redirect only survives in response.history. Checking the status code alone
    almost never fired."""
    response = FakeResponse(
        url="https://lobby.ikariam.gameforge.com/en_GB",
        history=[FakeRedirect("https://lobby.ikariam.gameforge.com/en_GB")],
    )
    assert _rotated(session, "<html>lobby</html>", response) is True


def test_the_final_url_alone_is_enough(session):
    response = FakeResponse(url="https://lobby.ikariam.gameforge.com/en_GB")
    assert _rotated(session, "", response) is True


# ----------------------------------------------------- false-positive bar ---

def test_a_cookie_banner_is_not_a_takeover(session):
    """The exact shape that made upstream #470 necessary."""
    html = (
        '<html><body><div id="cookie_banner">'
        'See https://consent.gameforge.com for details. '
        'Back to lobby.ikariam.gameforge.com</div></body></html>'
    )
    assert _rotated(session, html) is False


def test_the_word_reload_alone_is_not_a_takeover(session):
    html = '<script>function reload(){location.href="index.php";}</script>' \
           '<a href="https://lobby.ikariam.gameforge.com">lobby</a>'
    assert _rotated(session, html) is False


def test_an_ordinary_game_page_is_not_a_takeover(session):
    response = FakeResponse(url="https://s70-en.ikariam.gameforge.com/index.php?view=city")
    assert _rotated(session, "<html>city</html>", response) is False


def test_a_lobby_lookalike_host_is_not_a_takeover(session):
    """Match the host exactly. A substring test would accept this."""
    response = FakeResponse(url="https://lobby.ikariam.gameforge.com.evil.test/x")
    assert _rotated(session, "", response) is False


def test_non_text_bodies_are_handled(session):
    assert _rotated(session, None) is False
    assert _rotated(session, b"bytes") is False


def test_a_broken_response_object_does_not_raise(session):
    class Broken:
        @property
        def url(self):
            raise RuntimeError("no url")
        history = []

    assert _rotated(session, "", Broken()) is False


# ------------------------------------------------- what we do NOT inherit ---

def _code_of(func):
    """The function body with its docstring removed.

    The docstrings here name the upstream behaviour we avoid, so a plain
    substring search over the source matches the explanation instead of the
    code.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    body = tree.body[0].body
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    return "\n".join(ast.unparse(node) for node in body)


def test_handling_does_not_wait_for_a_keypress():
    """Upstream calls enter() before closing. Nobody answers a prompt on a
    headless instance, so it would hang for ever rather than stop."""
    assert "enter()" not in _code_of(Session._Session__handleSessionRotated)


def test_handling_does_not_kill_the_parent_or_siblings():
    """Upstream terminates the parent process and every sibling task. Here one
    task losing its session must not take the account's other tasks with it —
    the menu already breaks out of its wait when a child dies, and taskWatchdog
    reports it."""
    src = _code_of(Session._Session__handleSessionRotated)
    assert "terminate" not in src
    assert "children" not in src


def test_a_404_still_re_logins_rather_than_stopping():
    """Upstream replaced the re-login on a 404 at index.php with an exit. For
    an unattended instance that turns a recoverable expiry into a dead
    instance, so the AssertionError path stays."""
    import inspect
    assert ("404 Not Found on index.php - Session likely expired"
            in inspect.getsource(Session.get))
