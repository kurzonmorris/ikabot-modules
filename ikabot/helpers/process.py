#! /usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import subprocess
import sys

import psutil

from ikabot.config import *
from ikabot.helpers.logging import getLogger, get_log_file_path, setup_file_logging
from ikabot.helpers.signals import deactivate_sigint
from ikabot.helpers.varios import normalizeDicts


def _silence_child_terminal():
    """Redirect this process's stdout/stderr away from the shared terminal
    and disable clear()/banner() so no background child can wipe the screen.

    Safe to call multiple times.
    """
    import ikabot.helpers.gui as _gui
    _gui._child_mode = True  # makes clear() and banner() no-ops in this process

    try:
        log_path = get_log_file_path()
        if log_path:
            _f = open(log_path, "a", buffering=1, encoding="utf-8", errors="replace")
        else:
            _f = open(os.devnull, "w")
        # Redirect Python-level stdout/stderr
        sys.stdout = _f
        sys.stderr = _f
        # Also redirect the underlying OS file descriptors (fd 1 & 2) so that
        # subprocesses spawned by this child (e.g. os.system calls on Unix)
        # also lose access to the terminal.
        if not isWindows:
            os.dup2(_f.fileno(), 1)
            os.dup2(_f.fileno(), 2)
    except Exception:
        pass


def set_child_mode(session):
    """
    Parameters
    ----------
    session : ikabot.web.session.Session
    """
    session.padre = False
    deactivate_sigint()
    # On spawn (Windows) or when fork didn't carry the handler, the child
    # process starts with only the bootstrap stderr handler. Re-run
    # setup_file_logging so all child log output goes to the per-account file.
    setup_file_logging(session.username, session.servidor, session.mundo)
    _silence_child_terminal()


def run(command):
    ret = subprocess.run(
        command, shell=True, capture_output=True
    ).stdout
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            return ret.decode(enc).strip()
        except Exception:
            continue
    return ret


def updateProcessList(session, programprocesslist=[]):
    """This function will return data about all the active ikabot processes. If it is passed the ``programprocesslist`` argument, it will write new processes from that list to the .ikabot file
    Parameters
    ----------
    session : ikabot.web.session.Session
        Session object
    programprocesslist : list[dict]
        a list of dictionaries containing relevant data about a running ikabot process ('pid', 'proxies' and 'action')

    Returns
    -------
    runningIkabotProcessList : list[dict]
        a list of dictionaries containing relevant data about a running ikabot process ('pid', 'proxies' and 'action')
    """
    # read from file
    sessionData = session.getSessionData()
    try:
        fileList = sessionData["processList"]
    except KeyError:
        fileList = []

    # check it's still running
    runningIkabotProcessList = []
    ika_process = psutil.Process(pid=os.getpid()).name()
    for process in fileList:
        try:
            proc = psutil.Process(pid=process["pid"])
        except psutil.NoSuchProcess:
            continue

        # windows doesn't support the status method
        isAlive = True if isWindows else proc.status() != "zombie"

        try:
            if proc.name() == ika_process and isAlive:
                runningIkabotProcessList.append(process)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # add new to the list and write to file only if it's given
    for process in programprocesslist:
        if process not in runningIkabotProcessList:
            runningIkabotProcessList.append(process)

    for p in runningIkabotProcessList:
        p.setdefault("status", "running")

    # write to file
    sessionData["processList"] = runningIkabotProcessList
    session.setSessionData(sessionData)

    # normalize process list (all processes must have properties pid, action, date and status)
    normalized_processes = normalizeDicts(runningIkabotProcessList)
    # remove dupes by pid
    return list({d["pid"]: d for d in normalized_processes}.values())
