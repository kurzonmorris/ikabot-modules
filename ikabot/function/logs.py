#! /usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import sys

from ikabot.config import *
from ikabot.helpers.gui import *
from ikabot.helpers.logging import get_log_file_path
from ikabot.helpers.pedirInfo import enter, read


def logs(session, event, stdin_fd, predetermined_input):
    """
    Parameters
    ----------
    session : ikabot.web.session.Session
    event : multiprocessing.Event
    stdin_fd: int
    predetermined_input : multiprocessing.managers.SyncManager.list
    """
    sys.stdin = os.fdopen(stdin_fd)
    config.predetermined_input = predetermined_input
    try:
        while True:
            banner()
            print("0) Back")
            print("1) Set log level")
            print("2) View logs")
            choice = read(min=0, max=2, digit=True)
            if choice == 0:
                event.set()
                return
            elif choice == 1:
                # NOTE: log-level changes here only affect this child process.
                # Background task processes have their own logger instances and
                # are unaffected until a multithreading refactor is done (BUG-15).
                banner()
                print(
                    "The current log level is: "
                    + logging.getLevelName(logging.getLogger().getEffectiveLevel())
                    + "\n"
                )
                print("0) DEBUG")
                print("1) INFO")
                print("2) WARNING")
                print("3) ERROR")
                choice = read(min=0, max=3, digit=True)
                level_map = {0: logging.DEBUG, 1: logging.INFO, 2: logging.WARNING, 3: logging.ERROR}
                new_level = level_map[choice]
                logging.getLogger().setLevel(new_level)
                session.setSessionData({"logLevel": new_level}, shared=True)
                print(
                    "The log level has been set to: "
                    + logging.getLevelName(new_level)
                    + "\n"
                )
                print(bcolors.WARNING + "Note: already-running background tasks are unaffected. New instances and tasks started after this change will use the new level." + bcolors.ENDC)
                enter()
            else:
                viewLogs()

    except KeyboardInterrupt:
        event.set()
        return


def viewLogs():
    banner()

    log_path = get_log_file_path()
    if log_path is None:
        print("No log file is active for this session yet.")
        enter()
        return

    with open(log_path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 10000))
        print(f.read().decode("utf-8"))

    print(bcolors.DARK_GREEN + f"Above are the last 10 KB of {log_path}" + bcolors.ENDC)
    enter()
