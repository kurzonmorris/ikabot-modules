#! /usr/bin/env python3
# -*- coding: utf-8 -*-

import re


def getAvailableShips(session):
    """Function that returns the total number of free (available) ships
    Parameters
    ----------
    session : ikabot.web.session.Session
        Session object

    Returns
    -------
    ships : int
        number of currently available ships
    """
    html = session.get()
    match = re.search(r'GlobalMenu_freeTransporters">(\d+)<', html)
    return int(match.group(1)) if match else 0


def getTotalShips(session):
    """Function that returns the total number of ships, regardless of if they're available or not
    Parameters
    ----------
    session : ikabot.web.session.Session
        Session object

    Returns
    -------
    ships : int
        total number of ships the player has
    """
    html = session.get()
    match = re.search(r'maxTransporters">(\d+)<', html)
    return int(match.group(1)) if match else 0


def getAvailableFreighters(session):
    """Function that returns the total number of free (available) ships
    Parameters
    ----------
    session : ikabot.web.session.Session
        Session object

    Returns
    -------
    ships : int
        number of currently available ships
    """
    html = session.get()
    match = re.search(r'GlobalMenu_freeFreighters">(\d+)<', html)
    return int(match.group(1)) if match else 0


def getTotalFreighters(session):
    """Function that returns the total number of ships, regardless of if they're available or not
    Parameters
    ----------
    session : ikabot.web.session.Session
        Session object

    Returns
    -------
    ships : int
        total number of ships the player has
    """
    html = session.get()
    match = re.search(r'maxFreighters">(\d+)<', html)
    return int(match.group(1)) if match else 0
