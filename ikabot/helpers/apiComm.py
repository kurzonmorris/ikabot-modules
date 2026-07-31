#! /usr/bin/env python3
# -*- coding: utf-8 -*-

import traceback

from requests import get, post

from ikabot.config import *
from ikabot.helpers.dns import getAddress


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
    address = getAddress(publicAPIServerDomain) + "/v1/token"
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

    response = get(address, params=params, verify=do_ssl_verify, timeout=900)

    # Older API deployments don't know the locale/timezone parameters — retry
    # with just the user agent so the fork keeps working against them.
    if response.status_code in (400, 422) and len(params) > 1:
        response = get(
            address,
            params={"user_agent": user_agent},
            verify=do_ssl_verify,
            timeout=900,
        )
    if response.status_code == 400 and "Unsupported user_agent" in response.text:
        response = get(address, verify=do_ssl_verify, timeout=900)
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
    address = getAddress(publicAPIServerDomain) + "/v1/decaptcha/pirate"
    files = {"image": image}
    response = post(address, files=files, verify=do_ssl_verify, timeout=900)
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
