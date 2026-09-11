#! /usr/bin/env python3
# -*- coding: utf-8 -*-

import getpass
import os

from ikabot import config
from ikabot.config import *

# Set to True by set_child_mode() so background children never clear the
# shared terminal (os.system("cls") bypasses sys.stdout redirection).
_child_mode = False

# Optional redraw hook: modules can register a callable via set_redraw_hook()
# that redraws their current UI.  Ctrl+' (and the refresh path in read())
# calls redraw() which invokes the hook if set, otherwise falls back to banner().
_redraw_hook = None


# ---------------------------------------------------------------------------
# "Return to the main menu" escape token
# ---------------------------------------------------------------------------
#
# Typing this at ANY ikabot prompt abandons whatever is in progress and lands
# back on the main menu.  It exists so a script or a web front-end driving
# ikabot over stdin has a reliable way to reach a known starting point without
# knowing where the session currently is.
#
# Ctrl+C cannot serve that purpose: it is a terminal signal, not a character,
# so it cannot be written into a pipe, and at the top level it exits ikabot
# rather than returning to the menu.
#
# Anything after the token is queued as input for the menu, so one line can
# both reset and drive:  "/menu 5 2 1".
MENU_TOKEN = "/menu"


class ReturnToMenu(KeyboardInterrupt):
    """Raised at any prompt to abandon the current module and show the menu.

    Deliberately a subclass of KeyboardInterrupt, for two reasons:

    * KeyboardInterrupt derives from BaseException, so the broad
      ``except Exception`` handlers scattered through the modules do not
      swallow it;
    * nearly every module already ends with ``except KeyboardInterrupt:
      event.set(); return``, which is exactly the unwind we want — so this
      works in modules that were written years before it existed.
    """


def queue_menu_input(tokens):
    """Replace the pending scripted input with *tokens*.

    Digit strings become ints, matching how command-line arguments are parsed
    in start(), because the menu compares its selection numerically.
    """
    parsed = []
    for token in tokens:
        try:
            parsed.append(int(token))
        except ValueError:
            parsed.append(token)
    try:
        # config.predetermined_input is a multiprocessing manager list shared
        # with child processes, so mutate it in place rather than rebinding.
        while len(config.predetermined_input):
            config.predetermined_input.pop()
        if parsed:
            config.predetermined_input.extend(parsed)
    except Exception:
        pass
    return parsed


def check_menu_token(raw):
    """Raise ReturnToMenu if *raw* is the escape token. Otherwise return False.

    Matches only a line that *starts* with the token as a whole word, so a
    proxy URL or a file path that merely contains "/menu" is left alone.
    """
    if raw is None:
        return False
    parts = str(raw).strip().split()
    if not parts or parts[0] != MENU_TOKEN:
        return False
    queue_menu_input(parts[1:])
    raise ReturnToMenu()


def set_redraw_hook(fn):
    """Register a function that redraws the current module's UI.

    Call this from your module with a zero-argument callable.  When the user
    presses Ctrl+' at any read() prompt, your function is called instead of
    the generic ikabot banner, restoring whatever your module last showed.

    Call set_redraw_hook(None) to deregister (the parent does this automatically
    when it redraws the menu after a module goes to background).

    Example::

        def _my_screen():
            banner()
            print("My Module")
            print("(1) Do thing")

        set_redraw_hook(_my_screen)
        choice = read(min=0, max=1)
    """
    global _redraw_hook
    _redraw_hook = fn


def redraw():
    """Redraw the current screen.

    Calls the registered redraw hook (set via set_redraw_hook) if one exists,
    otherwise falls back to the standard ikabot banner.  No-op in child mode.
    """
    if _child_mode:
        return
    if _redraw_hook:
        try:
            _redraw_hook()
            return
        except Exception:
            pass
    banner()

def enter():
    """Wait for the user to press Enter"""
    try:
        if len(config.predetermined_input) > 0:
            return
    except Exception:
        pass
    if isWindows:
        typed = input("\n[Enter]")  # TODO improve this
    else:
        typed = getpass.getpass("\n[Enter]")
    # An [Enter] prompt is one of the easiest places for a script to get stuck,
    # so the escape token has to work here too.
    check_menu_token(typed)


def clear():
    """Clears all text on the console"""
    if _child_mode:
        return
    if isWindows:
        os.system("cls")
    else:
        os.system("clear")


def banner():
    """Clears all text on the console and displays the Ikabot ASCII art banner"""
    if _child_mode:
        return
    clear()
    bner = f"""
    `7MMF'  `7MM                       `7MM\"\"\"Yp,                 mm
      MM      MM                         MM    Yb                 MM
      MM      MM  ,MP'   ,6\"Yb.          MM    dP    ,pW\"Wq.    mmMMmm
      MM      MM ;Y     8)   MM          MM\"\"\"bg.   6W'   `Wb     MM
      MM      MM;Mm      ,pm9MM          MM    `Y   8M     M8     MM
      MM      MM `Mb.   8M   MM          MM    ,9   YA.   ,A9     MM
    .JMML.  .JMML. YA.  `Moo9^Yo.      .JMMmmmd9     `Ybmd9'      `Mbmo
                                                            {IKABOT_VERSION_TAG}
                                                            {IKABOT_MOD_VERSION_TAG}"""
    print("\n{}\n\n{}\n{}".format(bner, config.infoUser, config.update_msg))


def printChoiceList(list):
    """Prints the list with padded numbers next to each list entry.
    Parameters
    ----------
    list : list
        list to be printed
    """
    [
        print("{:>{pad}}) ".format(str(i + 1), pad=len(str(len(list)))) + str(item))
        for i, item in enumerate(list)
    ]


class bcolors:
    HEADER = "\033[95m"
    STONE = "\033[37m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    WARNING = "\033[93m"
    RED = "\033[91m"
    BLACK = "\033[90m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"
    DARK_RED = "\033[31m"
    DARK_BLUE = "\033[34m"
    DARK_GREEN = "\033[32m"
    CYAN = "\033[36m"
    YELLOW = "\033[33m"
