#! /usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import subprocess

import psutil

from ikabot.config import *
from ikabot.helpers.logging import getLogger, setup_file_logging
from ikabot.helpers.signals import deactivate_sigint
from ikabot.helpers.varios import normalizeDicts


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


def run(command):
    ret = subprocess.Popen(
        command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ).stdout.read()
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

        if proc.name() == ika_process and isAlive:
            runningIkabotProcessList.append(process)

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
