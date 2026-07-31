# Auto-activation: implementation brief

Paste this whole file into a chat, then say which module to work on.

**Part A (the shared foundation) is BUILT — shipped in v1.7.4.** Do not
rebuild it. Section 4 is now a reference for the API you consume.

**Part B is what you do**, once per module.

---

## 1. Goal

A module that has been configured once can be marked **auto-start**. On the
next login it launches automatically in the background using its saved
settings, asking nothing.

Auto-start is **per account** (username + server + world), like every other
module setting.

---

## 2. Architecture you are working inside

Read these before changing anything. Do not infer the contract — it is exact.

### Module entry point

Every module in `ikabot/function/` has this signature and shape:

```python
def moduleName(session, event, stdin_fd, predetermined_input):
    sys.stdin = os.fdopen(stdin_fd)
    config.predetermined_input = predetermined_input
    banner()

    ...interactive configuration phase...

    set_child_mode(session)
    event.set()              # hands the terminal back to the parent

    try:
        do_it(session, ...)  # the long-running loop
    except Exception as e:
        sendToBot(session, f"Error in moduleName:\n\n{e}")
    finally:
        session.logout()
```

`event.set()` is the handover signal. **Nothing may prompt after it.** The
parent waits on that event before redrawing its menu.

### How the parent launches a module

`ikabot/command_line.py`, in `menu()`:

```python
event = multiprocessing.Event()
config.has_params = len(config.predetermined_input) > 0
process = multiprocessing.Process(
    target=menu_actions[selected],
    args=(session, event, sys.stdin.fileno(), config.predetermined_input),
    name=menu_actions[selected].__name__,
)
process.start()
process_list.append({"pid": process.pid, "action": ..., "date": time.time(), "status": "started"})
updateProcessList(session, programprocesslist=process_list)
while not event.wait(timeout=2):
    if not process.is_alive():
        break
```

`menu_actions` is an `int -> function` dict at the top of `menu()`.

### Saved settings

`ikabot/helpers/modulePrefs.py` — per account **and** per module, one JSON
file each under `IKABOT_DATA_DIR/module_prefs/`:

```python
load_prefs(session, module_name)      -> dict | None
save_prefs(session, module_name, d)   -> None   (best-effort, never raises)
clear_prefs(session, module_name)     -> None
prompt_use_saved(session, module_name, summary_lines) -> bool
```

`prompt_use_saved` returns `False` immediately when
`config.predetermined_input` is non-empty — under `sequenceRunner` an extra
prompt would swallow a recorded keystroke and desync the run. **Preserve that
guard.**

`ikabot/function/activateShrine.py` is the reference implementation of a
module using prefs. Copy its validate-before-replay structure.

---

## 3. Design rules

These are non-negotiable. They are the conventions the rest of this codebase
already follows.

1. **Auto-start lives in the module's existing prefs file**, under the
   reserved key `_autostart`. Not a new file, not a global config.
   Consequence, and the reason for it: clearing a module's saved settings
   clears its auto-start flag for free. Auto-start without valid settings is
   meaningless, so the two must not be separately destroyable.

2. **Underscore-prefixed keys are reserved.** `_autostart` is metadata, not a
   module setting. Any code that replays saved prefs must ignore keys
   beginning with `_`.

3. **Absent key means off.** Prefs files written before this feature must load
   and work unchanged.

4. **Validate before replaying.** Never act on a persisted dict without
   checking it — the file is plain JSON on disk and may be hand-edited, stale,
   or from an older version.

5. **A failed validation under auto-start must not fall through to prompts.**
   There is no terminal. Notify via `sendToBot`, call `event.set()`, and
   return cleanly. Falling through would hang the login.

6. **Never prompt when prompting would desync.** Auto-start and
   `predetermined_input` both mean "do not ask" — respect both.

7. **Confirm before enabling, and say what it will do.** Enabling auto-start
   changes what happens at every future login. State that plainly.

8. **Derive, don't duplicate.** If a value can be computed from another, do
   not store it twice — the two copies will disagree eventually.

9. **Presets over free text** wherever an invalid combination is expressible.

---

## 4. Part A — the foundation (ALREADY BUILT, v1.7.4)

Consume this; do not reimplement it.

### `ikabot/helpers/modulePrefs.py`

```python
AUTOSTART_KEY = "_autostart"          # reserved; ignore "_"-prefixed keys

is_autostart(session, module_name)            -> bool
set_autostart(session, module_name, enabled)  -> bool   # False if no saved settings
list_autostart_modules(session)               -> [name]  # enabled, this account
list_saved_modules(session)                   -> [name]  # any saved settings
```

The flag lives inside the module's own prefs file, so `clear_prefs()` removes
it along with the settings. Absent key means off.

### The bypass

`prompt_use_saved()` returns `True` silently when `config.autostart_active`
is set — checked **after** the `predetermined_input` guard, so a
`sequenceRunner` run still wins. **A module that already calls
`prompt_use_saved` therefore auto-starts with no change of its own.**

### `ikabot/command_line.py`

```python
_run_autostart_child(target, session, event, stdin_fd, predetermined_input)
_autostart_targets(session)   -> [(name, function)]
_launch_autostart_modules(session, process_list, announce=True) -> [name]
_autostart_menu(session)      # Options / Settings -> (10)
```

`_launch_autostart_modules` is the single shared launcher, called from both
login and the menu. It skips entirely during a `sequenceRunner` run, skips
modules already in `process_list`, passes an empty `predetermined_input`,
resolves names against `_menu_actions()`, and waits at most
`_AUTOSTART_EVENT_TIMEOUT` (30s) per module so a misbehaving one cannot hang
login.

## 5. Part B — enable it for one module (repeat per module)

For most modules this is small, because Part A did the work.

1. **Confirm the module already uses `modulePrefs`.** If not, add saved
   settings first, following `activateShrine.py`. A module cannot auto-start
   without them.

2. **Harden its validation.** The existing validate-before-replay block must
   reject anything malformed. Under auto-start a bad file must
   `sendToBot`, `event.set()`, and return — never prompt (rules 4 and 5).

3. **Confirm nothing prompts after `event.set()`**, including inside
   `do_it()`. If the module asks a question mid-run, it cannot auto-start
   until that is restructured — say so rather than working around it.

4. **Offer to enable it** at the end of the module's interactive
   configuration, after settings are saved:

   ```
   Run this automatically at login from now on? [y/N]
   ```

   Skip the offer when `config.predetermined_input` is non-empty.

5. **Check for module-specific reasons auto-start is wrong** and report them
   instead of shipping something unsafe. Anything that spends resources,
   makes irreversible moves, or assumes a fresh game state deserves a
   flag before it is made automatic.

---

## 6. Testing (required before commit)

The vault work in this repo was verified this way; match it.

Part A's own behaviour is already covered. For a module you change, test:

- The module replays saved settings with `config.autostart_active = True`
  and **prints nothing** during the config phase.
- It **ignores** `_autostart` and any other `_`-prefixed key.
- Malformed/stale saved settings under auto-start cause a clean exit
  (`sendToBot` + `event.set()` + return), **never a prompt**.
- The offer-to-enable prompt is skipped when `predetermined_input` is
  non-empty.

Test with a monkeypatched prefs directory:

```python
from ikabot.helpers import modulePrefs
modulePrefs.MODULE_PREFS_DIR = tempfile.mkdtemp()
```

Do **not** write into `~/.ikabot` during tests, and delete anything you
create there by accident.

If `ModuleNotFoundError: _cffi_backend` appears, run
`pip install cffi cryptography` — an environment gap, not a repo problem.

---

## 7. Commit and branch

- Develop on the designated feature branch; never push to another branch.
- One commit per logical change — foundation separate from each module.
- Message: what was wrong or missing, what changed, and any consequence the
  user will notice on next run. Note the verification performed.
- Bump `IKABOT_MOD_VERSION` in `ikabot/config.py` when shipping the feature.
- Do not open a PR unless asked.

---

## 8. Report back

State plainly: which modules were made auto-startable, which were **not** and
why, what was verified, and anything a user will notice at the next login.
If a module was skipped as unsafe, say so — do not quietly narrow the scope.
