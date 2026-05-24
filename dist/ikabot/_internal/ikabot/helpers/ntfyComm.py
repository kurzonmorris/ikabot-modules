#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""ntfy.sh notification backend for ikabot."""

import os
import sys

from ikabot.helpers.gui import *
from ikabot.helpers.logging import getLogger
from ikabot.helpers.pedirInfo import read

logger = getLogger(__name__)

DEFAULT_SERVER = "https://ntfy.sh"


def sendToNtfy(server, topic, token, msg):
    if not topic:
        return False
    if not server:
        server = DEFAULT_SERVER
    url = "{}/{}".format(server.rstrip("/"), topic)

    lines = msg.strip().split("\n", 1)
    title = lines[0][:200]
    body = lines[1] if len(lines) > 1 else ""

    headers = {"Title": title}
    if token:
        headers["Authorization"] = "Bearer {}".format(token)

    try:
        from requests import post
        resp = post(url, data=body.encode("utf-8"), headers=headers, timeout=30)
        if 200 <= resp.status_code < 300:
            return True
        logger.warning("ntfy returned %d: %s", resp.status_code, resp.text)
        return False
    except Exception:
        logger.error("Failed to send ntfy notification", exc_info=True)
        return False


def ntfyDataIsValid(session):
    try:
        sessionData = session.getSessionData()
        return len(sessionData["shared"]["ntfy"]["topic"]) > 0
    except (KeyError, TypeError):
        return False


def getNtfyConfig(session):
    try:
        sessionData = session.getSessionData()
        ntfy_data = sessionData["shared"]["ntfy"]
        return {
            "server": ntfy_data.get("server", DEFAULT_SERVER),
            "topic": ntfy_data.get("topic", ""),
            "token": ntfy_data.get("token", ""),
        }
    except (KeyError, TypeError):
        return {"server": DEFAULT_SERVER, "topic": "", "token": ""}


def setupNtfy(session, event=None, stdin_fd=None, predetermined_input=[]):
    import ikabot.config as config
    if event is not None and stdin_fd is not None:
        sys.stdin = os.fdopen(stdin_fd)
    config.predetermined_input = predetermined_input

    banner()
    print("ntfy.sh Setup")
    print("=============\n")
    print("ntfy.sh is a simple push notification service.")
    print("Install the ntfy app on your phone (Android/iOS) to receive alerts.")
    print("You can use the public server (ntfy.sh) or self-host your own.\n")
    print("WARNING: Anyone who knows the topic name can read your notifications")
    print("on the public server. Use a long, random topic name.\n")

    topic = read(msg="Topic name: ")
    if not topic:
        if event is not None and stdin_fd is not None:
            event.set()
        return False
    topic = topic.strip()

    server = read(msg="Server URL (Enter for ntfy.sh): ")
    server = server.strip().rstrip("/") if server and server.strip() else DEFAULT_SERVER

    token = read(msg="Access token (Enter to skip if public): ")
    token = token.strip() if token else ""

    print("  Testing ntfy connection...")
    if sendToNtfy(server, topic, token, "ikabot ntfy notifications set up successfully!"):
        print(f"\n{bcolors.GREEN}ntfy setup complete!{bcolors.ENDC} A test notification was sent.")
        session.setSessionData({"ntfy": {"server": server, "topic": topic, "token": token}}, shared=True)
        print("\nIf you did not receive the test notification, check your topic name and server URL.")
        enter()
        if event is not None and stdin_fd is not None:
            event.set()
        return True
    else:
        print(f"\n{bcolors.RED}Failed to send test notification. Check your topic name and server URL.{bcolors.ENDC}")
        enter()
        if event is not None and stdin_fd is not None:
            event.set()
        return False
