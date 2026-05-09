# Ikabot — Bugs, Issues & Improvement Plan

Working branch: `ikafixed`
All fixes go into `/ikafixed/` (copy of `ikabot-master`).

---

## Section 1 — Bug / Issue List

Work through these one at a time. Each entry has a priority, the affected file(s), a short description, and the recommended fix approach.

---

### BUG-01 · Shared log file across instances *(PRIMARY FIX — done)*
**Priority:** High  
**Files:** `helpers/logging.py`, `config.py`, `web/session.py`

**Problem:**  
`LOGS_DIRECTORY_FILE` in `config.py:21` is hardcoded to `/tmp/ikabot.log` (Linux) or `%temp%\ikabot.log` (Windows). Every running instance — regardless of account, server, or world — writes to the same file. Log entries from different accounts are interleaved and impossible to distinguish. Rotation in one instance can discard entries from another.

**Fix (proper, not a patch):**  
- Change `LOGS_DIRECTORY_FILE` in `config.py` to a directory constant `LOGS_DIRECTORY = /tmp/` (or a dedicated `~/.ikabot/logs/` folder).
- Remove the global `RotatingFileHandler` creation from `helpers/logging.py` (it runs at *import time*, before login).
- Add `setup_file_logging(username, server, mundo)` in `helpers/logging.py`, called once after successful login. This creates a handler whose filename includes username + server + world: `ikabot_{username}_{server}{mundo}.log`.
- Until login completes, log to `stderr` only (a `StreamHandler`).
- **Status: FIXED in this branch** — see `helpers/logging.py` and `web/session.py`.

---

### BUG-02 · Recursive menu causes stack overflow
**Priority:** High  
**File:** `command_line.py`

**Problem:**  
`menu()` calls itself at the end of every action (`menu(session, checkUpdate=False)`) and also on every sub-menu "Back" press. After hundreds of menu selections the call stack grows without bound and Python raises `RecursionError`.

**Fix:**  
Convert `menu()` to use a `while True:` loop internally. Sub-menus return a value rather than recursing back. There should be zero recursive calls to `menu()`.

---

### BUG-03 · Infinite recursion in `__sessionExpired()`
**Priority:** High  
**File:** `web/session.py:960-978`

**Problem:**  
`__sessionExpired()` calls `__login(3)` and, if that raises an exception, calls `self.__sessionExpired()` again with no depth limit. If the network is consistently broken this is a silent infinite recursion.

**Fix:**  
Add a retry counter parameter (e.g., `max_retries=5`). After exhausting retries, raise a clear exception or exit with a user-facing message instead of recursing again.

---

### BUG-04 · Race condition on `.ikabot` session file
**Priority:** High  
**Files:** `helpers/aesCipher.py`

**Problem:**  
`getSessionData()` and `setSessionData()` both open `.ikabot` with separate `open()` calls and no file locking. When two child processes call `setSessionData()` simultaneously (which is common — multiple tasks run as concurrent `multiprocessing.Process` instances), one write silently overwrites the other. This can corrupt the session file or lose process-list entries.

**Fix:**  
Use `fcntl.flock()` (Linux/Mac) / `msvcrt.locking()` (Windows) around read-modify-write operations, or use Python's `filelock` third-party library (cross-platform). Wrap both `getSessionData` and `setSessionData` with the same lock so the full read-modify-write is atomic.

---

### BUG-05 · Duplicate import of `UpgradeUnits`
**Priority:** Low  
**File:** `command_line.py:54,56`

**Problem:**  
`from ikabot.function.UpgradeUnits import UpgradeUnits` appears on both line 54 and line 56. The second import is redundant and signals a copy-paste error during refactoring.

**Fix:**  
Remove the duplicate import line (line 56).

---

### BUG-06 · `menu()` max-input ceiling is wrong
**Priority:** Medium  
**File:** `command_line.py:187-188`

**Problem:**  
```python
total_options = len(menu_actions) + 1
selected = read(min=0, max=total_options, digit=True, empty=True)
```
`menu_actions` has 33 keys but the printed menu only shows 23 top-level items. A user typing a number like `30` passes the `read()` validation but then causes a `KeyError` when `menu_actions[selected]` is looked up (because `30` is not a key). The `+1` offset also makes 0 invalid as exit on some code paths.

**Fix:**  
Validate against the *printed* top-level option set, not the size of the internal dict. Either list valid top-level choices explicitly, or reindex menu_actions to use sequential integers.

---

### BUG-07 · `logging.WARN` is deprecated
**Priority:** Low  
**File:** `function/logs.py:50`

**Problem:**  
`logging.WARN` is a deprecated alias for `logging.WARNING`. It still works in Python 3.x but will eventually be removed.

**Fix:**  
Replace `logging.WARN` with `logging.WARNING`.

---

### BUG-08 · `load_module()` is deprecated (Python 3.12+)
**Priority:** Medium  
**File:** `function/loadCustomModule.py:105`

**Problem:**  
`SourceFileLoader(name, path).load_module()` triggers a `DeprecationWarning` in Python 3.4+ and was removed in Python 3.12. Ikabot will fail to load custom modules on modern Python.

**Fix:**  
Replace with the modern approach:
```python
import importlib.util
spec = importlib.util.spec_from_file_location(name, path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
```

---

### BUG-09 · Captcha loop has no escape condition
**Priority:** Medium  
**File:** `web/session.py:435`

**Problem:**  
The `while True:` captcha-solving loop only `break`s when the captcha status is `"solved"`. If the captcha service is down or returns an unexpected status, the loop runs forever with no timeout, no retry limit, and no user notification.

**Fix:**  
Add a `max_attempts` counter (e.g., 5). After exhausting attempts, exit with an informative error message offering the user a manual cookie fallback (which already exists later in the login flow).

---

### BUG-10 · Broken process-status logic
**Priority:** Medium  
**File:** `helpers/process.py:77-80`

**Problem:**  
```python
if len([p for p in runningIkabotProcessList if "status" not in p]) == len(runningIkabotProcessList) and len(runningIkabotProcessList):
    runningIkabotProcessList[0]["status"] = "running"
```
This only sets `status` on the *first* process and only when *all* processes lack the field. If a second process is added without a status, none of them get updated. The intent appears to be: "give any process without a status a default of `running`", but the code does something different.

**Fix:**  
```python
for p in runningIkabotProcessList:
    p.setdefault("status", "running")
```

---

### BUG-11 · `read()` called in `aesCipher.py` without explicit import
**Priority:** Low  
**File:** `helpers/aesCipher.py:97`

**Problem:**  
`read()` (from `pedirInfo`) is called inside `getSessionData()` but is not explicitly imported in `aesCipher.py`. It arrives via the wildcard `from ikabot.helpers.botComm import *`. This is a hidden transitive dependency — if `botComm` ever stops re-exporting `read`, `aesCipher` will silently break at runtime.

**Fix:**  
Add `from ikabot.helpers.pedirInfo import read` explicitly to `aesCipher.py` and stop relying on the wildcard chain.

---

### BUG-12 · `__login()` uses recursion for retries
**Priority:** Low  
**File:** `web/session.py:202`

**Problem:**  
`__login(retries=0)` decrements `retries` and calls `self.__login(retries-1)` for error recovery. While the depth is small (max 3), this pattern is fragile and harder to reason about than a loop.

**Fix:**  
Convert the retry logic inside `__login` to a `for attempt in range(max_retries):` loop.

---

### BUG-13 · Bare `except: pass` swallows errors silently
**Priority:** Medium  
**Files:** `web/session.py:364`, `web/session.py:493`, `web/session.py:1116`, `web/session.py:1238`; `helpers/pedirInfo.py:64`

**Problem:**  
Several places use `except Exception: pass` or bare `except: pass` with no logging. Errors disappear entirely, making debugging very difficult.

**Fix:**  
At minimum replace with `except Exception: self.logger.debug(..., exc_info=True)`. For blocks that truly must never fail, document *why* with a comment and log at DEBUG level.

---

### BUG-14 · `os._exit(0)` used instead of clean shutdown
**Priority:** Low  
**File:** `command_line.py:323`, `web/session.py:932`

**Problem:**  
`os._exit(0)` immediately kills the process without running `finally` blocks, `atexit` handlers, or flushing buffers. The `command_line.py` use is intentional (to leave child processes running), but `session.py:932` uses it inside vacation-mode detection inside a `with` / session context where cleanup should happen.

**Fix:**  
For `session.py:932`, raise a custom exception (`VacationModeError`) and handle it in the caller so cleanup runs properly. The `command_line.py` usage is acceptable but should have a comment explaining why `os._exit` is required.

---

### BUG-15 · Log-level change in child process has no effect
**Priority:** Low (known, commented)  
**File:** `function/logs.py:33` (TODO comment already exists)

**Problem:**  
The "Set log level" menu option changes the log level in the child process that runs `logs()`, but because background tasks are separate processes (not threads), the root logger in those processes is unaffected.

**Fix:**  
Store the desired log level in session data (`.ikabot` file). Each background process should read it on startup and apply it. This requires the multiprocessing→multithreading refactor noted in the TODO, or a shared config approach. For now: display a warning that the change only affects the current interactive session.

---

## Section 2 — File Structure Analysis & Recommendations

### Current structure

```
ikabot/
  __init__.py
  __main__.py          ← entry point (1 line)
  command_line.py      ← login, menu, process launch (376 lines)
  config.py            ← constants + mutable state + debug flags (135 lines)
  function/            ← 40+ files, mix of interactive + background tasks
  helpers/             ← 16 utility/infrastructure files
  locale/              ← i18n strings
  web/
    session.py         ← HTTP session, login, file I/O (1293 lines)
```

### Problems with the current structure

1. **`config.py` is overloaded.** It mixes true compile-time constants (`materials_names`), runtime mutable state (`ids_cache`, `cities_cache`, `predetermined_input`), feature flags (`enable_CustomPort`), and debug switches (`debugON_*`). This means every module does `from ikabot.config import *` and gets access to mutable shared state, making side-effects invisible.

2. **`function/` has no sub-categorisation.** 40+ files, each completely flat. Interactive one-shot actions (`donate.py`), long-running background daemons (`autoBarbarians.py`), and utility launchers (`logs.py`, `update.py`) all sit in the same folder. Navigation and onboarding are harder than they need to be.

3. **`web/session.py` does too much.** It handles: HTTP requests, cookie management, login (including full captcha solving, MFA, proxy config, and retry logic), AES session-file I/O delegation, and developer runtime info tracking. It is 1293 lines. This should be split.

4. **`command_line.py` owns the entire menu system.** Every new feature requires editing this one file to add an import, a dict entry, and a print statement. There is no way to add a feature without touching it.

5. **No plugin/extension mechanism.** Custom modules require manual path registration via the Settings menu. They live nowhere predictable and are not version-controlled alongside ikabot.

### Recommended new structure

```
ikabot/
  __init__.py
  __main__.py

  config/
    constants.py       ← true constants (names, URLs, timeouts)
    state.py           ← runtime mutable state (caches, predetermined_input)
    debug.py           ← debug flags

  core/
    session.py         ← HTTP layer only (get, post, cookie management)
    auth.py            ← login, MFA, captcha, cookie fallback
    session_file.py    ← .ikabot read/write (currently in aesCipher.py)

  menu/
    command_line.py    ← menu loop only; discovers menu items dynamically
    registry.py        ← central dict of all menu actions + metadata

  function/
    interactive/       ← actions that complete in one user-facing session
      donate.py
      sendResources.py
      constructBuilding.py
      ...
    background/        ← long-running daemons that fork and loop
      autoBarbarians.py
      autoPirate.py
      alertAttacks.py
      donationBot.py
      ...
    settings/          ← configuration & admin functions
      logs.py
      proxyConf.py
      decaptchaConf.py
      killTasks.py
      importExportCookie.py
      ...

  helpers/             ← pure utility (no game logic, no session dependency)
    logging.py
    gui.py
    varios.py
    naval.py
    market.py
    resources.py
    planRoutes.py
    ...

  plugins/             ← NEW: drop a .py here → auto-appears in Plugins submenu
    README.md
    (user modules go here)

  locale/
  web/
```

---

## Section 3 — Plugin Auto-Load System (Design)

### Goal
Drop a `.py` file into the `plugins/` directory → it automatically appears as an option under a "Plugins" submenu in the main menu. No manual registration required.

### Contract a plugin file must satisfy
1. Filename is the module name (e.g., `myFeature.py`).
2. Contains a function with the **same name as the file** (e.g., `def myFeature(session, event, stdin_fd, predetermined_input):`).
3. Optionally defines `MENU_LABEL = "My Feature Description"` at module level. If absent, the filename is used.
4. Optionally defines `MENU_ORDER = 10` (integer) to control position within the submenu.

### Implementation (in `menu/command_line.py` or `function/loadCustomModule.py`)
- At menu startup, scan the `plugins/` directory for `*.py` files (excluding `__init__.py`).
- For each file, dynamically import it and verify it has the required entry-point function.
- Add valid plugins to a "Plugins" submenu automatically.
- Errors during plugin load are caught, logged, and reported — the rest of the menu continues to work.

### Security note
Display a one-time warning (stored in session data so it only shows once per install) that plugins run with full session access. This is the same warning already shown in the current custom module loader.

---

## Section 4 — Work Order

| # | ID | Description | Effort |
|---|-----|-------------|--------|
| 1 | BUG-01 | Per-instance log files | Small ✅ done |
| 2 | BUG-05 | Remove duplicate UpgradeUnits import | Tiny |
| 3 | BUG-07 | Fix deprecated logging.WARN | Tiny |
| 4 | BUG-08 | Fix deprecated load_module() | Small |
| 5 | BUG-10 | Fix process status logic | Small |
| 6 | BUG-11 | Explicit import of read() in aesCipher | Tiny |
| 7 | BUG-06 | Fix menu input ceiling | Small |
| 8 | BUG-13 | Replace bare except/pass with logging | Medium |
| 9 | BUG-02 | Convert menu() recursion to loop | Medium |
| 10 | BUG-03 | Fix __sessionExpired() infinite recursion | Medium |
| 11 | BUG-04 | Add file locking to .ikabot read/write | Medium |
| 12 | BUG-09 | Add captcha loop escape | Medium |
| 13 | BUG-12 | Convert __login() retries to loop | Medium |
| 14 | BUG-14 | Replace os._exit with clean exception | Medium |
| 15 | BUG-15 | Log-level propagation to child processes | Large |
| 16 | STRUCT | Split config.py into constants/state/debug | Medium |
| 17 | STRUCT | Split session.py (auth + HTTP + file I/O) | Large |
| 18 | STRUCT | Sub-categorise function/ folder | Medium |
| 19 | PLUGIN | Implement plugins/ auto-load system | Medium |
