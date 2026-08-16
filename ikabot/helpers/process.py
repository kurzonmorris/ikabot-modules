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


_console_detached = False


def _redirect_child_stdout():
    """Redirect stdout/stderr to the log file (or /dev/null) so print() calls
    from a background child go to the log instead of the shared terminal.

    Safe to call multiple times.
    """
    try:
        log_path = get_log_file_path()
        if log_path:
            _f = open(log_path, "a", buffering=1, encoding="utf-8", errors="replace")
        else:
            _f = open(os.devnull, "w")
        sys.stdout = _f
        sys.stderr = _f
        if not isWindows:
            try:
                os.dup2(_f.fileno(), 1)
                os.dup2(_f.fileno(), 2)
            except (OSError, ValueError):
                pass
    except Exception:
        pass


def detach_console():
    """Permanently detach this process from the shared terminal/console.

    This is the root-cause fix for the 'black screen' problem.  Every ikabot
    task runs in its own process but they all share ONE console.  On Windows,
    os.system("cls") (and anything touching the console API) clears the SHARED
    console directly, bypassing sys.stdout redirection — so a background task
    could wipe the foreground menu.

    After this call the process has no console:
      * Windows : kernel32.FreeConsole() removes the console attachment, so
                  cls / console writes from this process (or anything it
                  spawns) can no longer reach the parent's console.
      * POSIX   : os.setsid() detaches from the controlling terminal and
                  stdout/stderr are redirected to the log file.

    Once detached, the process can ONLY report status via session.setStatus()
    (rendered by the parent's process table).  This is the intended channel
    for background tasks.  Safe to call multiple times.
    """
    global _console_detached
    if _console_detached:
        return

    # Stop Python-level output reaching the terminal first.
    _redirect_child_stdout()

    try:
        if isWindows:
            import ctypes
            # Detach from the shared console.  After FreeConsole the process
            # has no console, so cls/console output can't touch the parent's.
            ctypes.windll.kernel32.FreeConsole()
        else:
            # Detach from the controlling terminal so a stray clear()/tput
            # can't reach it.  Ignored if already a session leader.
            try:
                os.setsid()
            except OSError:
                pass
    except Exception:
        pass

    # From now on clear()/banner() are no-ops in this process too (belt and
    # braces in case the module keeps a reference to the old stdout).
    try:
        import ikabot.helpers.gui as _gui
        _gui._child_mode = True
    except Exception:
        pass

    _console_detached = True


def _silence_child_terminal():
    """Backwards-compatible alias — full detach from the shared console."""
    detach_console()


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
    detach_console()


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
    # Held across the whole read-prune-write so it cannot race with another
    # process doing the same, or with session.setStatus(). Previously the read
    # happened outside the lock, so two concurrent callers could each write a
    # list built from a stale snapshot and silently drop each other's entries —
    # a live background task would vanish from the task table while still
    # running, with no way left to stop it.
    ika_process = psutil.Process(pid=os.getpid()).name()
    runningIkabotProcessList = []

    def _prune_and_merge(sessionData):
        fileList = sessionData.get("processList") or []

        del runningIkabotProcessList[:]
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

        sessionData["processList"] = runningIkabotProcessList
        return sessionData

    session.mutateSessionData(_prune_and_merge)

    # normalize process list (all processes must have properties pid, action, date and status)
    normalized_processes = normalizeDicts(runningIkabotProcessList)
    # remove dupes by pid
    return list({d["pid"]: d for d in normalized_processes}.values())
