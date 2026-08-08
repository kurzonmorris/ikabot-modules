"""Smoke test: build every dialog to catch Tk errors before shipping.

Widget options like pady accept only a single value, while pack() and
grid() also accept a (top, bottom) pair. Mixing them up raises
TclError: bad screen distance only when the dialog is actually built,
so it cannot be caught by reading the code or importing the module.

Run on a machine with tkinter:
    python gui_smoke_test.py            (Windows)
    xvfb-run -a python3 gui_smoke_test.py   (headless Linux)
"""
import importlib.util, tempfile
from pathlib import Path
import tkinter as tk

spec = importlib.util.spec_from_file_location("inst", "/home/user/ikabot-modules/installer/ikabot-mod-install.py")
inst = importlib.util.module_from_spec(spec); spec.loader.exec_module(inst)

# Build dialogs without blocking on the event loop
tk.Tk.mainloop = lambda self, *a, **k: None

ok = fail = 0
def t(name, fn):
    global ok, fail
    try:
        fn(); print(f"  PASS  {name}"); ok += 1
    except Exception as e:
        print(f"  FAIL  {name}: {type(e).__name__}: {e}"); fail += 1

online = {"installer": {"local":"2.1.1","remote":"2.1.1","ok":True},
          "ikabot":    {"local":"7.4.5","remote":"7.4.5","ok":True},
          "mod":       {"local":"1.6.9","remote":"1.7.5","ok":False},
          "online": True, "error": None}
offline = {"installer": {"local":"2.1.1","remote":None,"ok":True},
           "ikabot":    {"local":None,"remote":None,"ok":False},
           "mod":       {"local":"1.6.9","remote":None,"ok":False},
           "online": False, "error": "no network"}

print("maintenance menu:")
t("with versions (mixed green/red)", lambda: inst._maintenance_menu(online))
t("offline (grey)",                  lambda: inst._maintenance_menu(offline))
t("no version data",                 lambda: inst._maintenance_menu(None))

print("other dialogs:")
t("ask_choice",        lambda: inst.ask_choice("T", "msg", ["A", "B"]))
t("ask_count_or_skip", lambda: inst.ask_count_or_skip("T", "msg", "5"))

d = Path(tempfile.mkdtemp())
(d / "modules").mkdir()
(d / inst.MODULES_TEMPLATE).mkdir()
# installed copies: one current, one behind, one absent -> green / red / red
(d / inst.MODULES_TEMPLATE / "current_v2.1.9.py").write_text("")
(d / inst.MODULES_TEMPLATE / "behind_v2.1.3.py").write_text("")
(d / "modules" / "current.py").write_text("")
(d / "modules" / "behind.py").write_text("")
listing = [
    {"name": "current_v2.1.9.py", "url": "u", "base": "current.py", "ver": "2.1.9"},
    {"name": "behind_v2.1.9.py",  "url": "u", "base": "behind.py",  "ver": "2.1.9"},
    {"name": "missing_v1.0.0.py", "url": "u", "base": "missing.py", "ver": "1.0.0"},
    {"name": "bulkdistribution.csv", "url": "u", "base": "bulkdistribution.csv", "ver": ""},
]
t("modules dialog (green/red/red/grey)", lambda: inst._modules_dialog(listing, d))
t("modules dialog (empty)", lambda: inst._modules_dialog([], d))

print(f"\n{ok} passed, {fail} failed")
raise SystemExit(1 if fail else 0)
