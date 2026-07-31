# Module auto-activation — implementation brief

Paste this whole file into a chat, then name the module(s) to work on.

> **The shared foundation (Part A) is BUILT — shipped in v1.7.4. Do not
> rebuild it.** Section 5 is a reference for the API you consume.
> **Your job is Part B (section 6), once per module.**

If anything here contradicts the code, **the code wins** — read it and say so.

---

## 1. Goal

A module that has been configured once can be marked **auto-start**. At the
next login it launches in the background using its saved settings, asking
nothing.

Auto-start is **per account** (username + server + world), like every other
module setting.

---

## 2. Module status

**Auto-start works today** (these have settings memory, so the foundation
already covers them — Part B for these is only steps 2 and 4):

| Module | Menu |
|---|---|
| `activateShrine` | (5) |
| `loginDaily` | (6) |
| `donationBot` | (9) → (2) |
| `activateMiracle` | (11) |

**Needs settings memory added first** (the larger job — do that before
auto-start; see step 1 of Part B):

`UpgradeUnits`, `alertAttacks`, `alertLowWine`, `alertMessages`,
`attackBarbarians`, `autoBarbarians`, `autoPirate`, `buyResources`,
`consolidateResources`, `constructBuilding`, `constructionList`,
`decaptchaConf`, `developer`, `distributeResources`, `donate`, `dumpWorld`,
`getStatus`, `importExportCookie`, `inactivePlayersRadiusMonitor`,
`killTasks`, `loadCustomModule`, `logs`, `modifyAcademyWorkers`,
`modifyProduction`, `notificationSetup`, `proxyConf`,
`reorganizeCityBuildings`, `research`, `searchForIslandSpaces`,
`sellResources`, `sendCulturalTreatyRequests`, `sendResources`,
`shipMovements`, `stationArmy`, `trainArmy`, `update`, `vacationMode`,
`webServer`

Several of those are interactive tools or one-shot actions that should
**never** auto-start (`logs`, `killTasks`, `developer`, `update`,
`importExportCookie`, `loadCustomModule`, `proxyConf`, `decaptchaConf`,
`notificationSetup`, `getStatus`). Say so rather than adding it.

---

## 3. Architecture you are working inside

Do not infer these contracts — they are exact.

### Module entry point

Every module in `ikabot/function/` has this shape:

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

`event.set()` is the handover signal. **Nothing may prompt after it.**

### Saved settings — `ikabot/helpers/modulePrefs.py`

Per account **and** per module, one JSON file each under
`IKABOT_DATA_DIR/module_prefs/`, named
`{username}_{servidor}{mundo}_{module}.json`.

```python
load_prefs(session, module_name)      -> dict | None
save_prefs(session, module_name, d)   -> None   # best-effort, never raises
clear_prefs(session, module_name)     -> None
prompt_use_saved(session, module_name, summary_lines) -> bool
```

`prompt_use_saved` returns `False` immediately when
`config.predetermined_input` is non-empty — under `sequenceRunner` an extra
prompt would swallow a recorded keystroke and desync the run. **Preserve that
guard.**

`ikabot/function/activateShrine.py` is the reference implementation. Copy its
validate-before-replay structure.

### Why not `sequenceRunner`

`sequenceRunner` (CLI args → `config.predetermined_input` → popped by
`read()`) replays **menu keystrokes**. It is the wrong substrate here: adding
a menu item invalidates every stored sequence, it has no per-account scoping,
and it cannot express "start these five modules". Settings memory stores
*what was chosen*, not *which key was pressed*. Keep `sequenceRunner` for
scripted one-shot CLI runs.

---

## 4. Design rules

Non-negotiable. These are the conventions the codebase already follows.

1. **Auto-start lives in the module's existing prefs file**, under the
   reserved key `_autostart`. Clearing a module's settings therefore clears
   its auto-start flag for free — auto-start without settings is meaningless,
   so the two must not be separately destroyable.

2. **Underscore-prefixed keys are reserved.** `_autostart` is metadata, not a
   setting. Code replaying prefs must ignore `_`-prefixed keys. Never
   `**saved` or iterate all keys — read named keys only.

3. **Absent key means off.** Prefs files written before this feature must
   load and work unchanged.

4. **Validate before replaying.** The file is plain JSON on disk and may be
   hand-edited, stale, or from an older version.

5. **A failed validation under auto-start must not fall through to prompts.**
   There is no terminal. `sendToBot`, `event.set()`, return. Falling through
   leaves the module hung.

6. **Never prompt when prompting would desync.** `autostart_active` and
   `predetermined_input` both mean "do not ask" — respect both.

7. **Confirm before enabling, and say what it will do.** Enabling changes
   what happens at every future login.

8. **Derive, don't duplicate.** No second registry, no copied string-building.

9. **Presets over free text** wherever an invalid combination is expressible.

10. **Never write to `~/.ikabot` in tests.** Monkeypatch the prefs dir.

---

## 5. Part A — the foundation (ALREADY BUILT, v1.7.4)

Consume this; do not reimplement it.

### `ikabot/helpers/modulePrefs.py`

```python
AUTOSTART_KEY = "_autostart"

is_autostart(session, module_name)            -> bool
set_autostart(session, module_name, enabled)  -> bool   # False if no saved settings
list_autostart_modules(session)               -> [name] # enabled, this account
list_saved_modules(session)                   -> [name] # any saved settings
```

### The bypass — why Part B is small

`prompt_use_saved()` returns `True` **silently** when
`config.autostart_active` is set, checked **after** the
`predetermined_input` guard so a `sequenceRunner` run still wins.

**A module that already calls `prompt_use_saved` auto-starts with no change
of its own.**

### `ikabot/command_line.py`

```python
_AUTOSTART_EVENT_TIMEOUT = 30.0
_run_autostart_child(target, session, event, stdin_fd, predetermined_input)
_autostart_targets(session)   -> [(name, function)]
_launch_autostart_modules(session, process_list, announce=True) -> [name]
_autostart_menu(session)      # Options / Settings -> (10)
```

`_launch_autostart_modules` is the single shared launcher, called from both
login and the menu. It skips entirely during a `sequenceRunner` run, skips
modules already in `process_list`, passes an **empty** `predetermined_input`,
resolves names against `_menu_actions()`, and waits at most 30s per module so
one that wrongly prompts cannot hang login.

`config.autostart_active` defaults `False` and is set **only** inside the
child by `_run_autostart_child`. It is module-level and picklable because
Windows spawns rather than forks.

---

## 6. Part B — per module (your job)

### Step 1 — does it have settings memory?

If the module does not call `prompt_use_saved`, add settings memory first,
following `activateShrine.py:139-210`. That is the bulk of the work. A module
cannot auto-start without it.

### Step 2 — make failure headless-safe (required)

This is the one real gap in the current code. The existing pattern falls
through to prompts when validation fails, which under auto-start means
prompting with no terminal. Restructure to:

```python
saved = load_prefs(session, _MODULE)
use_saved = False
if saved:
    try:
        _ids = [int(g) for g in saved["godids"]]
        assert _ids and all(1 <= g <= 6 for g in _ids)
        ...
        use_saved = prompt_use_saved(session, _MODULE, _summary)
    except Exception:
        use_saved = False

# Auto-start has no terminal: never fall through to the questions.
if config.autostart_active and not use_saved:
    sendToBot(session, f"{_MODULE}: saved settings are missing or invalid, "
                       "auto-start aborted. Reconfigure the module.")
    event.set()
    return
```

Place it immediately after the validate/replay block and **before** the first
prompt. `event.set()` is mandatory — without it the parent waits the full 30s.

### Step 3 — confirm nothing prompts after `event.set()`

Including inside `do_it()`. If the module asks a question mid-run it cannot
auto-start until restructured — **say so rather than working around it.**

### Step 4 — offer to enable it

After settings are saved, in the interactive path only:

```python
from ikabot.helpers.modulePrefs import is_autostart, set_autostart

if len(config.predetermined_input) == 0 and not is_autostart(session, _MODULE):
    print("\nRun this automatically at login from now on?")
    print("It will start in the background using these settings.")
    if read(values=["y", "Y", "n", "N", ""], empty=True, default="n",
            msg="[y/N]: ").lower() == "y":
        set_autostart(session, _MODULE, True)
```

Must come **after** `save_prefs` — `set_autostart` is a no-op when there are
no saved settings.

### Step 5 — judge whether it should auto-start at all

Anything that spends resources, makes irreversible moves, assumes fresh game
state, or is an interactive tool rather than a background task should be
flagged, not shipped. Report it; do not quietly narrow scope.

---

## 7. Testing (required before commit)

### Environment

```bash
pip install cffi cryptography python-dotenv psutil requests
```

Missing deps show as `ModuleNotFoundError: _cffi_backend` / `dotenv` /
`psutil`. Environment gaps, not repo problems.

### Always monkeypatch the prefs dir

```python
import tempfile
from ikabot.helpers import modulePrefs as mp
mp.MODULE_PREFS_DIR = tempfile.mkdtemp()
```

`prefs_path()` reads the module global at call time, so this works. **Never
write to `~/.ikabot`**; if you create something there by accident, delete it
and say so.

### Minimal session stub

```python
class S:
    username, servidor, mundo = "tester", "s55", "en"
    _d = {}
    def getSessionData(self): return dict(self._d)
    def setSessionData(self, d, shared=False): self._d = d
```

### What to test for a module you changed

- Replays saved settings with `config.autostart_active = True` and **prints
  nothing** during the config phase.
- **Ignores** `_autostart` and any other `_`-prefixed key.
- Malformed/stale settings under auto-start → `sendToBot` + `event.set()` +
  return, **never a prompt**.
- The offer-to-enable prompt is skipped when `predetermined_input` is
  non-empty, and does not fire when already enabled.
- `set_autostart` still returns `False` before `save_prefs` has run.

### End-to-end harness (proves the whole chain)

Write a fake module to a temp dir on `PYTHONPATH`:

```python
# fakemod.py
import os, json
from ikabot import config
from ikabot.helpers.modulePrefs import load_prefs, prompt_use_saved
OUT = os.environ["AUTOSTART_TEST_OUT"]

def fakeModule(session, event, stdin_fd, predetermined_input):
    config.predetermined_input = predetermined_input
    saved = load_prefs(session, "fakeModule")
    used = prompt_use_saved(session, "fakeModule", ["summary"])
    json.dump({"autostart_active": config.autostart_active,
               "used_saved": used, "replayed": saved if used else None,
               "predetermined_input": list(predetermined_input)}, open(OUT, "w"))
    event.set()
```

Then:

```python
import ikabot.command_line as cl
cl._menu_actions = lambda: {999: fakemod.fakeModule}
started = cl._launch_autostart_modules(s, [], announce=False)
# assert the child saw autostart_active=True, replayed the settings,
# and received an empty predetermined_input
```

Part A's own behaviour is already covered by this harness — re-run it if you
touch the foundation.

---

## 8. Anti-patterns

- Rebuilding Part A because you did not check whether it exists.
- A second name→function registry instead of `_menu_actions()`.
- Setting `config.autostart_active` in the parent (breaks on Windows spawn,
  and leaks into interactive runs).
- A lambda/closure as the `Process` target (unpicklable on Windows).
- Passing the real `predetermined_input` to an auto-started child.
- `**saved` or iterating prefs keys — `_autostart` will break it.
- Printing during the config phase without guarding on `autostart_active`.
- An unbounded wait on the module's event.
- Storing the auto-start flag anywhere other than the module's prefs file.

---

## 9. Commit and branch

- Develop on the branch designated in that chat; never push to another.
- One commit per logical change — one per module, not one big commit.
- Message: what was missing, what changed, the consequence a user will notice
  at next login, and the verification performed.
- Bump `IKABOT_MOD_VERSION` in `ikabot/config.py`.
- Do not open a PR unless explicitly asked.

---

## 10. Report back

State plainly:

- Which modules were made auto-startable.
- Which were **not**, and why — especially any judged unsafe (step 5).
- What was verified, and anything that was **not**.
- What a user will notice at their next login.

Do not report completion for work that was skipped. If part of the scope was
blocked, finish everything else and say explicitly what was left out.
