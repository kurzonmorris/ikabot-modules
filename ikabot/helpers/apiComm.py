#! /usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import traceback

from requests import get, post

from ikabot.config import *
from ikabot.helpers.dns import getAddress
from ikabot.helpers.logging import getLogger

logger = getLogger(__name__)

# The public API can be slow, but it must not be able to hang a login for the
# best part of an hour: without a bounded wait there is nothing to fail over
# *from*. Token generation drives a real browser, so keep it generous.
API_TIMEOUT = int(os.getenv("IKABOT_API_TIMEOUT", "120"))


def getFallbackEndpoints():
    """Self-hosted API base URLs to try after the public one, in order."""
    raw = os.getenv("IKABOT_API_FALLBACK", "")
    return [url.strip().rstrip("/") for url in raw.split(",") if url.strip()]


def getEndpoints():
    """Ordered (base_url, headers) pairs for an API call.

    The public server comes first and the self-hosted ones after it. The API
    key is only ever sent to the latter — a shared secret handed to a third
    party is no longer a secret.
    """
    endpoints = []
    try:
        endpoints.append((getAddress(publicAPIServerDomain), {}))
    except Exception:
        logger.warning("Could not resolve the public API address: ", exc_info=True)

    key = os.getenv("IKABOT_API_KEY", "").strip()
    headers = {"X-API-Key": key} if key else {}
    for url in getFallbackEndpoints():
        endpoints.append((url, dict(headers)))

    assert endpoints, (
        "No ikabot API endpoint available: the public server could not be "
        "resolved and IKABOT_API_FALLBACK is not set."
    )
    return endpoints


def callApi(attempt):
    """Run *attempt* against each endpoint in turn, returning the first result.

    *attempt* takes (base_url, headers). Any exception it raises — a dead
    server, a timeout, a 500, a malformed body — moves on to the next
    endpoint. The last failure is re-raised if none of them work.
    """
    endpoints = getEndpoints()
    last_error = None
    for index, (address, headers) in enumerate(endpoints):
        try:
            result = attempt(address, headers)
            if index > 0:
                logger.info("Served by fallback API %s", address)
            return result
        except Exception as e:
            last_error = e
            logger.warning("API request to %s failed: ", address, exc_info=True)
    raise last_error


def requestBlackBoxToken(address, headers, user_agent, params):
    """One /v1/token request against a single API server."""
    response = get(
        address + "/v1/token",
        params=params,
        headers=headers,
        verify=do_ssl_verify,
        timeout=API_TIMEOUT,
    )

    # Older API deployments don't know the locale/timezone parameters — retry
    # with just the user agent so the fork keeps working against them.
    if response.status_code in (400, 422) and len(params) > 1:
        response = get(
            address + "/v1/token",
            params={"user_agent": user_agent},
            headers=headers,
            verify=do_ssl_verify,
            timeout=API_TIMEOUT,
        )
    if response.status_code == 400 and "Unsupported user_agent" in response.text:
        response = get(
            address + "/v1/token",
            headers=headers,
            verify=do_ssl_verify,
            timeout=API_TIMEOUT,
        )
    assert response.status_code == 200, (
        "API response code is not OK: "
        + str(response.status_code)
        + "\n"
        + response.text
    )
    response = response.json()
    # A successful response is the token string.  Anything dict-shaped is an
    # error envelope — testing `"status" in response` on a str would silently
    # do a substring match instead.
    if isinstance(response, dict):
        if response.get("status") == "error":
            raise Exception(response["message"])
        raise Exception("Unexpected API response: " + str(response))
    # Strip any prefix the API already applied so the result is never "tra:tra:".
    return "tra:" + response.replace("tra:", "")


def getNewBlackBoxToken(session):
    """This function returns a newly generated blackbox token from the API
    Parameters
    ----------
    session : ikabot.web.session.Session
        Session object

    Returns
    -------
    token : str
        blackbox token
    """
    # Send the same regional context the login will use.  Gameforge rejects
    # tokens generated in a different locale/timezone than the login request,
    # so these must match session.locale / session.timezone_id exactly.
    # Upstream #418: the token must be generated for the user agent the API
    # knows about, which is not necessarily the one a manual payload set on the
    # session.  api_user_agent holds the former; fall back to user_agent.
    user_agent = getattr(session, "api_user_agent", None) or session.user_agent
    params = {"user_agent": user_agent}
    locale = getattr(session, "locale", None)
    timezone_id = getattr(session, "timezone_id", None)
    if locale:
        params["locale"] = locale
    if timezone_id:
        params["timezone_id"] = timezone_id

    return callApi(
        lambda address, headers: requestBlackBoxToken(
            address, headers, user_agent, params
        )
    )


def requestPiratesCaptchaSolution(address, headers, image):
    """One /v1/decaptcha/pirate request against a single API server."""
    response = post(
        address + "/v1/decaptcha/pirate",
        files={"image": image},
        headers=headers,
        verify=do_ssl_verify,
        timeout=API_TIMEOUT,
    )
    assert response.status_code == 200, (
        "API response code is not OK: "
        + str(response.status_code)
        + "\n"
        + response.text
    )
    response = response.json()
    if "status" in response and response["status"] == "error":
        raise Exception(response["message"])
    return response


def getPiratesCaptchaSolution(session, image):
    """This function returns the solution of the pirates captcha
    Parameters
    ----------
    session : ikabot.web.session.Session
        Session object
    image : bytes
        the image to be solved

    Returns
    -------
    solution : str
        solution of the captcha
    """
    return callApi(
        lambda address, headers: requestPiratesCaptchaSolution(address, headers, image)
    )
