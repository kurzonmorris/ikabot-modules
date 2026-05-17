#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""Discord webhook notification backend for ikabot."""

import os
import sys

from ikabot.helpers.gui import *
from ikabot.helpers.logging import getLogger
from ikabot.helpers.pedirInfo import read

logger = getLogger(__name__)


def sendToDiscord(webhook_url, msg):
    if not webhook_url:
        return False
    try:
        from requests import post
        content = msg[:2000] if len(msg) > 2000 else msg
        resp = post(webhook_url, json={"content": content}, timeout=30)
        if 200 <= resp.status_code < 300:
            return True
        logger.warning("Discord webhook returned %d: %s", resp.status_code, resp.text)
        return False
    except Exception:
        logger.error("Failed to send Discord message", exc_info=True)
        return False


def discordDataIsValid(session):
    try:
        sessionData = session.getSessionData()
        return len(sessionData["shared"]["discord"]["webhookUrl"]) > 0
    except (KeyError, TypeError):
        return False


def getDiscordWebhookUrl(session):
    try:
        sessionData = session.getSessionData()
        return sessionData["shared"]["discord"]["webhookUrl"]
    except (KeyError, TypeError):
        return ""


def setupDiscord(session, event=None, stdin_fd=None, predetermined_input=[]):
    import ikabot.config as config
    if event is not None and stdin_fd is not None:
        sys.stdin = os.fdopen(stdin_fd)
    config.predetermined_input = predetermined_input

    banner()
    print("Discord Webhook Setup")
    print("=====================\n")
    print("To set up Discord notifications:")
    print("1. Open your Discord server settings")
    print("2. Go to Integrations > Webhooks")
    print("3. Click 'New Webhook', choose a channel, and copy the webhook URL\n")

    webhook_url = read(msg="Webhook URL: ")
    if not webhook_url:
        if event is not None and stdin_fd is not None:
            event.set()
        return False
    webhook_url = webhook_url.strip()

    _valid_prefixes = (
        "https://discord.com/api/webhooks/",
        "https://discordapp.com/api/webhooks/",
        "https://ptb.discord.com/api/webhooks/",
        "https://canary.discord.com/api/webhooks/",
    )
    if not any(webhook_url.startswith(p) for p in _valid_prefixes):
        print(f"\n{bcolors.RED}That doesn't look like a valid Discord webhook URL.{bcolors.ENDC}")
        print("Expected format: https://discord.com/api/webhooks/...")
        enter()
        if event is not None and stdin_fd is not None:
            event.set()
        return False

    print("  Testing webhook...")
    if sendToDiscord(webhook_url, "ikabot Discord notifications set up successfully!"):
        print(f"\n{bcolors.GREEN}Discord setup complete!{bcolors.ENDC} A test message was sent to your channel.")
        session.setSessionData({"discord": {"webhookUrl": webhook_url}}, shared=True)
        print("\nIf you did not receive the test message, check your webhook URL and try again.")
        enter()
        if event is not None and stdin_fd is not None:
            event.set()
        return True
    else:
        print(f"\n{bcolors.RED}Failed to send test message. Check your webhook URL.{bcolors.ENDC}")
        enter()
        if event is not None and stdin_fd is not None:
            event.set()
        return False
