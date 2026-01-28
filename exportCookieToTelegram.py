#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Export Cookie to Telegram Module for Ikabot

This module exports the current session cookie to Telegram in a JavaScript
format that can be used to import the cookie into a browser console.

Usage:
    Load this module via Ikabot menu: 21 -> 8 -> (path to this file)
"""

import json
import os
import sys
import time

from ikabot.config import *
from ikabot.helpers.botComm import *
from ikabot.helpers.gui import *
from ikabot.helpers.pedirInfo import read


def wait_for_keypress_or_timeout(timeout_seconds):
    """Wait for a keypress or timeout, whichever comes first.
    
    Parameters
    ----------
    timeout_seconds : int
        Maximum seconds to wait before returning
    
    Returns
    -------
    bool
        True if a key was pressed, False if timeout occurred
    """
    if isWindows:
        # Windows implementation using msvcrt
        import msvcrt
        start_time = time.time()
        while (time.time() - start_time) < timeout_seconds:
            if msvcrt.kbhit():
                msvcrt.getch()  # Consume the keypress
                return True
            time.sleep(0.1)
        return False
    else:
        # Unix/Linux implementation using select
        import select
        import termios
        import tty
        
        old_settings = termios.tcgetattr(sys.stdin)
        try:
            tty.setcbreak(sys.stdin.fileno())
            rlist, _, _ = select.select([sys.stdin], [], [], timeout_seconds)
            if rlist:
                sys.stdin.read(1)  # Consume the keypress
                return True
            return False
        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)


def exportCookieToTelegram(session, event, stdin_fd, predetermined_input):
    """Export the session cookie to Telegram in JavaScript format.
    
    Parameters
    ----------
    session : ikabot.web.session.Session
        Session object
    event : multiprocessing.Event
        Event to signal completion
    stdin_fd : int
        File descriptor for stdin
    predetermined_input : multiprocessing.managers.SyncManager.list
        List of predetermined inputs
    """
    sys.stdin = os.fdopen(stdin_fd)
    config.predetermined_input = predetermined_input
    
    try:
        banner()
        
        # Check if Telegram is configured
        if not telegramDataIsValid(session):
            print("Telegram is not configured.")
            print("You need to set up Telegram to use this feature.\n")
            
            # Prompt user to configure Telegram
            result = updateTelegramData(session)
            
            if not result:
                print("\nTelegram setup was not completed. Cannot export cookie.")
                enter()
                event.set()
                return
            
            banner()
            print("Telegram configured successfully!\n")
        
        # Get fresh cookie
        session.get()  # Refresh session to ensure cookie is valid
        ikariam_cookie = session.getSessionData()["cookies"]["ikariam"]
        
        # Format the JavaScript code
        cookie_dict = {"ikariam": ikariam_cookie}
        js_code = 'cookies={};i=0;for(let cookie in cookies){{document.cookie=Object.keys(cookies)[i]+"="+cookies[cookie];i++}}'.format(
            json.dumps(cookie_dict)
        )
        
        # Send to Telegram
        sendToBot(session, js_code)
        
        # Display success message
        banner()
        print("Press any key to return to main menu\n")
        print("{}Sent successfully!{}".format(bcolors.GREEN, bcolors.ENDC))
        print("\nThe cookie has been sent to your Telegram bot.")
        print("Paste it into the browser console while on the Ikariam website.\n")
        
        # Wait for keypress or 10 seconds
        wait_for_keypress_or_timeout(10)
        
        event.set()
        
    except KeyboardInterrupt:
        event.set()
        return
