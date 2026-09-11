"""API failover: when the public server is down, a self-hosted one takes over.

ikabot cannot log in without a blackbox token, and the token comes from one
volunteer-run public server. These tests cover the ordering, the key handling
and the "everything failed" path.
"""

import json

import pytest

from ikabot.helpers import apiComm


class Response:
    def __init__(self, status_code=200, body="TOKEN", text=""):
        self.status_code = status_code
        self._body = body
        self.text = text or json.dumps(body)

    def json(self):
        return self._body


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("IKABOT_API_FALLBACK", "IKABOT_API_KEY", "CUSTOM_API_ADDRESS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(apiComm, "getAddress", lambda domain: "http://public")


class Session:
    api_user_agent = "UA"
    user_agent = "UA"
    locale = "en-GB"
    timezone_id = "Europe/London"


# ------------------------------------------------------------- endpoints --

def test_public_server_comes_first(monkeypatch):
    monkeypatch.setenv("IKABOT_API_FALLBACK", "https://mine.example.com")
    assert [url for url, _ in apiComm.getEndpoints()] == [
        "http://public",
        "https://mine.example.com",
    ]


def test_fallbacks_are_tried_in_the_order_given(monkeypatch):
    monkeypatch.setenv("IKABOT_API_FALLBACK", "https://a.example.com, https://b.example.com")
    assert [url for url, _ in apiComm.getEndpoints()][1:] == [
        "https://a.example.com",
        "https://b.example.com",
    ]


def test_trailing_slashes_are_stripped(monkeypatch):
    """The paths are joined with `+`, so a trailing slash would give //v1/token."""
    monkeypatch.setenv("IKABOT_API_FALLBACK", "https://mine.example.com/")
    assert apiComm.getEndpoints()[1][0] == "https://mine.example.com"


def test_the_key_never_goes_to_the_public_server(monkeypatch):
    """A shared secret handed to a third party is not a secret any more."""
    monkeypatch.setenv("IKABOT_API_FALLBACK", "https://mine.example.com")
    monkeypatch.setenv("IKABOT_API_KEY", "s3cret")
    endpoints = apiComm.getEndpoints()
    assert endpoints[0][1] == {}
    assert endpoints[1][1] == {"X-API-Key": "s3cret"}


def test_a_dead_dns_lookup_still_leaves_the_fallback(monkeypatch):
    """DNS resolution of the public server is itself a thing that fails."""
    def boom(domain):
        raise Exception("no TXT record")

    monkeypatch.setattr(apiComm, "getAddress", boom)
    monkeypatch.setenv("IKABOT_API_FALLBACK", "https://mine.example.com")
    assert [url for url, _ in apiComm.getEndpoints()] == ["https://mine.example.com"]


def test_no_endpoints_at_all_is_an_error(monkeypatch):
    monkeypatch.setattr(apiComm, "getAddress", lambda d: (_ for _ in ()).throw(Exception()))
    with pytest.raises(AssertionError):
        apiComm.getEndpoints()


# --------------------------------------------------------------- failover --

def test_a_dead_primary_falls_through_to_the_fallback(monkeypatch):
    monkeypatch.setenv("IKABOT_API_FALLBACK", "https://mine.example.com")
    tried = []

    def attempt(address, headers):
        tried.append(address)
        if address == "http://public":
            raise ConnectionError("refused")
        return "ok"

    assert apiComm.callApi(attempt) == "ok"
    assert tried == ["http://public", "https://mine.example.com"]


def test_a_working_primary_is_not_bypassed(monkeypatch):
    monkeypatch.setenv("IKABOT_API_FALLBACK", "https://mine.example.com")
    tried = []

    def attempt(address, headers):
        tried.append(address)
        return "ok"

    assert apiComm.callApi(attempt) == "ok"
    assert tried == ["http://public"]


def test_the_last_failure_is_raised_when_everything_is_down(monkeypatch):
    monkeypatch.setenv("IKABOT_API_FALLBACK", "https://mine.example.com")

    def attempt(address, headers):
        raise RuntimeError("down: " + address)

    with pytest.raises(RuntimeError, match="mine.example.com"):
        apiComm.callApi(attempt)


# ------------------------------------------------------------------ token --

def test_token_request_sends_the_regional_context(monkeypatch):
    seen = {}

    def fake_get(url, params=None, headers=None, **kwargs):
        seen["url"] = url
        seen["params"] = params
        seen["headers"] = headers
        return Response(body="ABC123")

    monkeypatch.setattr(apiComm, "get", fake_get)
    assert apiComm.getNewBlackBoxToken(Session()) == "tra:ABC123"
    assert seen["url"] == "http://public/v1/token"
    assert seen["params"] == {
        "user_agent": "UA",
        "locale": "en-GB",
        "timezone_id": "Europe/London",
    }


def test_token_prefix_is_not_doubled(monkeypatch):
    monkeypatch.setattr(
        apiComm, "get", lambda *a, **k: Response(body="tra:ABC123")
    )
    assert apiComm.getNewBlackBoxToken(Session()) == "tra:ABC123"


def test_a_500_from_the_primary_is_a_failover(monkeypatch):
    """An erroring server is as useless as an absent one, so treat it the same."""
    monkeypatch.setenv("IKABOT_API_FALLBACK", "https://mine.example.com")
    calls = []

    def fake_get(url, params=None, headers=None, **kwargs):
        calls.append((url, headers))
        if url.startswith("http://public"):
            return Response(status_code=500, text="boom")
        return Response(body="FROMMINE")

    monkeypatch.setattr(apiComm, "get", fake_get)
    monkeypatch.setenv("IKABOT_API_KEY", "s3cret")
    assert apiComm.getNewBlackBoxToken(Session()) == "tra:FROMMINE"
    assert calls[-1][1] == {"X-API-Key": "s3cret"}


def test_unsupported_user_agent_still_retries_without_params(monkeypatch):
    """Upstream's list does not contain this fork's agents — that retry is
    what keeps the public server usable at all."""
    attempts = []

    def fake_get(url, params=None, headers=None, **kwargs):
        attempts.append(params)
        if params:
            return Response(status_code=400, text="Unsupported user_agent")
        return Response(body="ABC")

    monkeypatch.setattr(apiComm, "get", fake_get)
    assert apiComm.getNewBlackBoxToken(Session()) == "tra:ABC"
    assert attempts == [
        {"user_agent": "UA", "locale": "en-GB", "timezone_id": "Europe/London"},
        {"user_agent": "UA"},
        None,
    ]


def test_an_error_envelope_is_reported(monkeypatch):
    monkeypatch.setattr(
        apiComm,
        "get",
        lambda *a, **k: Response(body={"status": "error", "message": "nope"}),
    )
    with pytest.raises(Exception, match="nope"):
        apiComm.getNewBlackBoxToken(Session())


# ---------------------------------------------------------------- captcha --

def test_captcha_falls_over_and_resends_the_same_image(monkeypatch):
    """The image is bytes, so the retry must carry the picture, not an
    already-drained stream."""
    monkeypatch.setenv("IKABOT_API_FALLBACK", "https://mine.example.com")
    sent = []

    def fake_post(url, files=None, headers=None, **kwargs):
        sent.append(files["image"])
        if url.startswith("http://public"):
            raise ConnectionError("refused")
        return Response(body="abc12")

    monkeypatch.setattr(apiComm, "post", fake_post)
    assert apiComm.getPiratesCaptchaSolution(Session(), b"PNGDATA") == "abc12"
    assert sent == [b"PNGDATA", b"PNGDATA"]
