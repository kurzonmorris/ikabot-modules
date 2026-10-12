#! /usr/bin/env python3
# -*- coding: utf-8 -*-

import sys

import requests

import ikabot.config as config
from ikabot.helpers.gui import *
from ikabot.helpers.pedirInfo import read
from ikabot.helpers.taskWatchdog import set_proxy_state


def handle_broken_proxy(session, reason=""):
    """Offer a new proxy, or turn the broken one off, and carry on.

    A proxy that has stopped working is a setting to correct, not a reason to
    end the process. Exiting here meant the instance had to be started again
    by hand, only to be asked the very same question — and answering it still
    ended the instance, so it took two restarts to get back to a menu.

    Either answer leaves the session usable: a new proxy that has been tested,
    or no proxy at all. Always returns True, meaning "something changed, the
    caller may carry on".
    """
    if reason:
        print(reason)

    # Recorded before the question is asked, not after it is answered: on an
    # unattended instance nobody answers for hours, and the point of the
    # panel's alert is to say so while it is still unnoticed.
    session_data = session.getSessionData()
    set_proxy_state(
        session, ok=False,
        url=session_data.get("proxy", {}).get("conf", {}).get("https"),
    )

    while True:
        print("Do you want to enter a new proxy? [y/N]")
        rta = read(values=["y", "Y", "n", "N", ""])

        if rta.lower() != "y":
            def _disable_proxy(sd):
                sd.setdefault("proxy", {})["set"] = False
                return sd

            session.mutateSessionData(_disable_proxy)
            set_proxy_state(session, ok=True, url=None)   # none set: nothing to alert on
            print("The proxy has been turned off; carrying on without it.")
            enter()
            return True

        proxy_dict = read_proxy(session)
        if proxy_dict is None:
            # read_proxy has already said it does not work. Ask again rather
            # than leaving as the only way out the one that ends the process.
            continue

        def _store_proxy(sd):
            sd.setdefault("proxy", {})["conf"] = proxy_dict
            sd["proxy"]["set"] = True
            # Whatever phase was chosen before is kept: a proxy replaced
            # because the old one died is the same job as the old one.
            sd["proxy"].setdefault("phase", DEFAULT_PHASE)
            return sd

        session.mutateSessionData(_store_proxy)
        set_proxy_state(session, ok=True, url=proxy_dict["https"])
        return True


PHASES = {
    "game": "from entering the game server onwards",
    "select": "from choosing the server onwards",
}
DEFAULT_PHASE = "game"


def proxy_phase(session_data):
    """Which phase the stored proxy uses. Absent reads as the old behaviour."""
    phase = session_data.get("proxy", {}).get("phase")
    return phase if phase in PHASES else DEFAULT_PHASE


def read_phase(current=DEFAULT_PHASE):
    """Ask when the proxy should start being used. Returns a phase."""
    print("\nWhen should the proxy start being used?")
    print("The Gameforge account login never uses it, either way. Gameforge")
    print("refuses a login from a hosting address, so that request always")
    print("comes from this machine.")
    print("")
    print("1) From entering the game server onwards (what ikabot has always done)")
    print("2) From choosing the server onwards — one request earlier")
    print("")
    print("Pick 2 if 1 does not work. Choosing the server and using the game")
    print("session then happen from the same address, which is what turning a")
    print("VPN on at the server list does in a browser.")
    print("")
    choice = read(min=1, max=2, default=1 if current == "game" else 2)
    return "game" if choice == 1 else "select"


def _proxy_message(session_data):
    """Keep the banner's proxy line in step with what is actually set."""
    msg = "using proxy:"
    if session_data.get("proxy", {}).get("set") is True:
        curr_proxy = "{} ({})".format(
            session_data["proxy"]["conf"]["https"],
            PHASES[proxy_phase(session_data)])
        if msg not in config.update_msg:
            # add proxy message
            config.update_msg += "{} {}\n".format(msg, curr_proxy)
        else:
            # delete old proxy message
            config.update_msg = config.update_msg.replace(
                "\n".join(config.update_msg.split("\n")[-2:]), ""
            )
            # add new proxy message
            config.update_msg += "{} {}\n".format(msg, curr_proxy)
    elif msg in config.update_msg:
        # delete old proxy message
        config.update_msg = config.update_msg.replace(
            "\n".join(config.update_msg.split("\n")[-2:]), ""
        )


def show_proxy(session):
    session_data = session.getSessionData()
    if session_data.get("proxy", {}).get("set") is True:
        curr_proxy = session_data["proxy"]["conf"]["https"]
        if test_proxy(session, session_data["proxy"]["conf"]) is False:
            handle_broken_proxy(
                session, "The {} proxy does not work.".format(curr_proxy)
            )
            # Whatever was chosen, what is set now is not what was set a
            # moment ago, so the banner is built from a fresh read.
            session_data = session.getSessionData()
        else:
            set_proxy_state(session, ok=True, url=curr_proxy)
    else:
        set_proxy_state(session, ok=True, url=None)
    _proxy_message(session_data)


def test_proxy(session, proxy_dict):
    try:
        requests.get(
            session.urlBase,
            proxies=proxy_dict,
            verify=config.do_ssl_verify,
        )
    except Exception as e:
        print('Proxy test failure. Error: ' + str(e))
        return False
    return True


def read_proxy(session):
    print(
        
        "Enter the proxy: protocol://username:password@address:port\n(examples: socks5://127.0.0.1:9050, https://45.117.163.22:8080):"
        
    )
    proxy_str = read(msg="proxy: ")
    proxy_dict = {"http": proxy_str, "https": proxy_str}
    if test_proxy(session, proxy_dict) is False:
        print("The proxy does not work.")
        enter()
        return None
    print("The proxy works and it will be used for all future requests sent by new ikabot processes.")
    enter()
    return proxy_dict


def proxyConf(session, event, stdin_fd, predetermined_input):
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
        banner()
        print("The Gameforge account login never goes through the proxy.")
        print("Gameforge refuses a login from a hosting address, so that")
        print("request always comes from this machine. Everything after it")
        print("can use any address, and that is what the proxy is for.\n")

        session_data = session.getSessionData()
        if "proxy" not in session_data or session_data["proxy"]["set"] is False:
            print("Right now, there is no proxy configured.")
            proxy_dict = read_proxy(session)
            if proxy_dict is None:
                event.set()
                return
            session_data["proxy"] = {}
            session_data["proxy"]["conf"] = proxy_dict
            session_data["proxy"]["set"] = True
            session_data["proxy"]["phase"] = read_phase()
        else:
            curr_proxy = session_data["proxy"]["conf"]["https"]
            print("Current proxy: {}".format(curr_proxy))
            print("Used {}.".format(PHASES[proxy_phase(session_data)]))
            print("What do you want to do?")
            print("0) Exit")
            print("1) Set a new proxy")
            print("2) Remove the current proxy")
            print("3) Change when the proxy starts being used")
            rta = read(min=0, max=3)

            if rta == 0:
                event.set()
                return
            if rta == 1:
                proxy_dict = read_proxy(session)
                if proxy_dict is None:
                    event.set()
                    return
                session_data["proxy"]["conf"] = proxy_dict
                session_data["proxy"]["set"] = True
                session_data["proxy"]["phase"] = read_phase(
                    proxy_phase(session_data))
            if rta == 2:
                session_data["proxy"]["set"] = False
                print("The proxy has been removed.")
                enter()
            if rta == 3:
                session_data["proxy"]["phase"] = read_phase(
                    proxy_phase(session_data))
                print("Saved. Restart the instance for it to take effect.")
                enter()

        session.setSessionData(session_data)
        event.set()
    except KeyboardInterrupt:
        event.set()
        return