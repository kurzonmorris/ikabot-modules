"""The "/menu" escape token: return to the main menu from anywhere.

A front-end driving ikabot over stdin needs a way to reach a known starting
point without knowing where the session currently is. Ctrl+C cannot do that —
it is a terminal signal rather than a character, so it cannot be written into a
pipe, and at the top level it exits ikabot instead of returning to the menu.
"""

import builtins

import pytest

from ikabot import config
from ikabot.helpers import gui
from ikabot.helpers.gui import MENU_TOKEN, ReturnToMenu, check_menu_token
from ikabot.helpers.pedirInfo import read


@pytest.fixture(autouse=True)
def queue(monkeypatch):
    """A plain list standing in for the multiprocessing manager list."""
    pending = []
    monkeypatch.setattr(config, "predetermined_input", pending)
    return pending


def _types(monkeypatch, *lines):
    it = iter(lines)
    monkeypatch.setattr(builtins, "input", lambda *a, **k: next(it))


# ------------------------------------------------------------- recognition --

def test_token_at_a_digit_prompt_raises(monkeypatch):
    """It has to work where the prompt accepts only digits — being valid
    everywhere is the whole point."""
    _types(monkeypatch, MENU_TOKEN)
    with pytest.raises(ReturnToMenu):
        read(min=1, max=9, digit=True)


def test_token_at_a_constrained_values_prompt_raises(monkeypatch):
    _types(monkeypatch, MENU_TOKEN)
    with pytest.raises(ReturnToMenu):
        read(values=["y", "n"])


def test_token_survives_surrounding_whitespace(monkeypatch):
    _types(monkeypatch, "  /menu\t")
    with pytest.raises(ReturnToMenu):
        read(digit=True)


def test_ordinary_input_is_untouched(monkeypatch):
    _types(monkeypatch, "7")
    assert read(min=1, max=9, digit=True) == 7


def test_a_path_merely_containing_the_token_is_not_an_escape(monkeypatch):
    """Proxy URLs and file paths contain slashes; only a line that *starts*
    with the token as a whole word counts."""
    _types(monkeypatch, "http://proxy.local/menu")
    assert read() == "http://proxy.local/menu"

    _types(monkeypatch, "/menuconfig")
    assert read() == "/menuconfig"


def test_check_menu_token_returns_false_for_anything_else():
    assert check_menu_token("5") is False
    assert check_menu_token("") is False
    assert check_menu_token(None) is False


# ------------------------------------------------------- queued arguments ---

def test_trailing_arguments_are_queued_for_the_menu(monkeypatch, queue):
    """"/menu 5 2 1" should both reset and drive, so one injected line is a
    whole command rather than a reset followed by keystrokes."""
    _types(monkeypatch, "/menu 5 2 1")
    with pytest.raises(ReturnToMenu):
        read(digit=True)
    assert list(queue) == [5, 2, 1]


def test_queued_digits_are_ints_so_the_menu_can_compare_them(queue):
    """The menu dispatches on int selections; a queued "5" that stayed a string
    would silently match nothing."""
    with pytest.raises(ReturnToMenu):
        check_menu_token("/menu 16 yes")
    assert list(queue) == [16, "yes"]


def test_a_bare_token_clears_any_stale_queue(queue):
    """Left-over tokens from an abandoned command would be replayed into the
    next one and desync it."""
    queue.extend([9, 9, 9])
    with pytest.raises(ReturnToMenu):
        check_menu_token(MENU_TOKEN)
    assert list(queue) == []


def test_queued_input_is_then_consumed_by_read(monkeypatch, queue):
    with pytest.raises(ReturnToMenu):
        check_menu_token("/menu 5 2")
    assert read(digit=True) == 5
    assert read(digit=True) == 2


# ---------------------------------------------------------------- unwind ----

def test_it_is_a_keyboardinterrupt_subclass():
    """Two things depend on this: `except Exception` handlers must not swallow
    it, and the `except KeyboardInterrupt: event.set(); return` ending that
    nearly every module already has is exactly the unwind we want."""
    assert issubclass(ReturnToMenu, KeyboardInterrupt)
    assert not issubclass(ReturnToMenu, Exception)


def test_a_broad_except_does_not_swallow_it(monkeypatch):
    _types(monkeypatch, MENU_TOKEN)
    with pytest.raises(ReturnToMenu):
        try:
            read(digit=True)
        except Exception:
            pytest.fail("ReturnToMenu was swallowed by 'except Exception'")


def test_a_module_style_handler_catches_it(monkeypatch):
    """Simulates the ending shared by ~30 modules."""
    _types(monkeypatch, MENU_TOKEN)
    handled = []
    try:
        read(digit=True)
    except KeyboardInterrupt:
        handled.append("returned to menu")
    assert handled == ["returned to menu"]


def test_enter_also_honours_the_token(monkeypatch, queue):
    """[Enter] prompts are one of the easiest places for a script to get stuck."""
    monkeypatch.setattr(gui, "isWindows", True)
    _types(monkeypatch, MENU_TOKEN)
    with pytest.raises(ReturnToMenu):
        gui.enter()
