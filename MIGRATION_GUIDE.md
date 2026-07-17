# ikabot-modules Migration Guide
# Applying all fixes to a new upstream ikabot version
# Written for AI agent consumption — dense, precise, actionable.

---

## OVERVIEW

This document records every change made to ikabot source to produce the
ikabot-modules fork. When a new upstream ikabot version is released, apply
all changes in the order listed under WORKING ORDER. Each entry has:
- LOCATE: grep pattern to find the correct spot (line numbers will drift)
- OLD: exact text that must exist before the edit
- NEW: exact replacement text
- VERIFY: grep/check to confirm the fix landed correctly

Upstream repo: the unmodified ikabot-master
Target package path: ikabot/ (was ikafixed/ in earlier sessions — rename first)

---

## PRE-FLIGHT: PACKAGE RENAME

If upstream still uses `ikafixed/` as the package directory:
```
mv ikafixed ikabot
find . -type f -name "*.py" -exec sed -i 's/from ikafixed\./from ikabot./g; s/import ikafixed\./import ikabot./g' {} +
find . -name "*.spec" -exec sed -i 's/ikafixed/ikabot/g' {} +
```
Update ikabot.spec EXE block: `name='ikabot'`
Update any banner/version strings referencing "ikafixed".

---

## WORKING ORDER

Apply fixes in this exact sequence. Later fixes depend on earlier ones.

1.  config.py          — add IKABOT_DATA_DIR, IKABOT_SESSIONS_DIR, LOGS_DIRECTORY, version constants
2.  helpers/logging.py — full rewrite: deferred file handler, setup_file_logging()
3.  __main__.py        — add freeze_support() for Windows PyInstaller
4.  helpers/aesCipher.py — full rewrite: per-account session files, cross-process locking
5.  function/checkForUpdate.py — stub out pip search (removed in pip 21+)
6.  helpers/piratesDecaptcha.py — guard onnxruntime import
7.  helpers/process.py — fix process status defaulting logic (BUG-10)
8.  helpers/pedirInfo.py — replace silent except with debug logging (BUG-13)
9.  function/loadCustomModule.py — replace deprecated load_module() (BUG-08)
10. function/logs.py   — save log level to session, fix deprecated logging.WARN (BUG-07/15)
11. web/session.py     — all session fixes (BUG-03/09/12/13/14 + logging integration)
12. command_line.py    — menu loop, init(), start() (BUG-02/06)

---

## FIX 1 — config.py

### 1a. Add directory constants and version vars
LOCATE: `ikaFile = ".ikabot"` or the block defining LOGS_DIRECTORY_FILE

ADD after the `isWindows` line (or replace the old log path definition):
```python
IKABOT_DATA_DIR = os.getenv("APPDATA", os.path.expanduser("~")) + "\\.ikabot" if isWindows else os.path.expanduser("~/.ikabot")
IKABOT_SESSIONS_DIR = os.path.join(IKABOT_DATA_DIR, "sessions")
LOGS_DIRECTORY = os.path.join(IKABOT_DATA_DIR, "logs")
DEFAULT_LOG_LEVEL = 30  # Warning
```

REMOVE or REPLACE any `LOGS_DIRECTORY_FILE` constant (old single-file log path).

ADD version constants (adjust numbers to match current fork version):
```python
IKABOT_MOD_VERSION = "0.6.0"
IKABOT_MOD_VERSION_TAG = "ikabot-mod v" + IKABOT_MOD_VERSION
```

KEEP `ikaFile = ".ikabot"` as a stub (legacy — no longer used, kept to avoid import errors):
```python
ikaFile = ".ikabot"  # legacy — no longer used; kept to avoid import errors in old code
```

VERIFY:
```
grep -n "IKABOT_SESSIONS_DIR\|LOGS_DIRECTORY\|DEFAULT_LOG_LEVEL" ikabot/config.py
```

---

## FIX 2 — helpers/logging.py (FULL REWRITE)

REPLACE entire file content with:
```python
"""
Ikabot logging setup.

The file handler is NOT created at import time because the username and server
are not known until after login. Call setup_file_logging() once the session is
established. Until then all log output goes to stderr.
"""

import logging
import logging.handlers
import os

from ikabot.config import LOGS_DIRECTORY, DEFAULT_LOG_LEVEL

_LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
_file_logging_configured = False


class IkabotLogger(logging.Logger):
    pass


logging.setLoggerClass(IkabotLogger)

# Bootstrap: stderr only until setup_file_logging() is called.
logging.basicConfig(
    format=_LOG_FORMAT,
    level=DEFAULT_LOG_LEVEL,
    force=True,
    handlers=[logging.StreamHandler()],
)

for name in logging.root.manager.loggerDict:
    logging.getLogger(name).propagate = True
    logging.getLogger(name).handlers.clear()


def setup_file_logging(username: str, server: str, mundo: str) -> None:
    global _file_logging_configured
    if _file_logging_configured:
        return

    os.makedirs(LOGS_DIRECTORY, exist_ok=True)

    safe_username = "".join(c for c in username if c.isalnum() or c in "-_")
    safe_server = "".join(c for c in server if c.isalnum() or c in "-_")
    safe_mundo = "".join(c for c in str(mundo) if c.isalnum() or c in "-_")
    filename = os.path.join(
        LOGS_DIRECTORY,
        f"ikabot_{safe_username}_{safe_server}{safe_mundo}.log",
    )

    handler = logging.handlers.RotatingFileHandler(
        filename=filename,
        maxBytes=10 * 1024 * 1024,
        backupCount=10,
    )
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)

    _file_logging_configured = True


def get_log_file_path() -> str | None:
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            return handler.baseFilename
    return None


def getLogger(name: str) -> IkabotLogger:
    return logging.getLogger(name)
```

VERIFY:
```
grep -n "setup_file_logging\|get_log_file_path\|_file_logging_configured" ikabot/helpers/logging.py
```

---

## FIX 3 — __main__.py (FULL REWRITE)

REPLACE entire file with:
```python
import multiprocessing
import sys

if __name__ == '__main__':
    if sys.platform.startswith('win'):
        multiprocessing.freeze_support()
    from ikabot.command_line import main
    main()
```

WHY: Without freeze_support(), PyInstaller frozen exe on Windows causes the
Manager child process to re-run main(), producing garbled UTF-16 output and
duplicate process spawns.

VERIFY:
```
grep -n "freeze_support" ikabot/__main__.py
```

---

## FIX 4 — helpers/aesCipher.py (FULL REWRITE)

This is the largest single change. Replace the entire file with the version
from this repo. Key design decisions to preserve:

- Per-account session files in IKABOT_SESSIONS_DIR (not a single shared .ikabot file)
- File naming: `{16-char-email-hash}.session` initially, renamed post-login to
  `{hash}_{username}_{server}{mundo}.session` via upgrade_filename()
- Cross-process locking via atomic lock file: os.open(O_CREAT|O_EXCL|O_WRONLY)
  with 10-second timeout, 50ms retry interval
- AES-256-GCM encryption unchanged (key derivation: sha256 x 0xFFF iterations)
- Entry key = sha256("ikabot" + mail).hexdigest(); prefix = entry_key[:16]
- Nested session data structure: data[username][mundo][servidor] = {...}
- Shared data at data["shared"] = {...}

CRITICAL IMPORTS that must be explicit (not via wildcard):
```python
from ikabot.helpers.pedirInfo import read  # BUG-11: must be explicit
```

VERIFY:
```
grep -n "_acquire_lock\|_release_lock\|upgrade_filename\|IKABOT_SESSIONS_DIR" ikabot/helpers/aesCipher.py
grep -n "from ikabot.helpers.pedirInfo import read" ikabot/helpers/aesCipher.py
```

---

## FIX 5 — function/checkForUpdate.py (STUB)

LOCATE: function that calls `pip search` or subprocess pip

REPLACE entire function body with a no-op stub:
```python
def checkForUpdate():
    pass
```

WHY: `pip search` was removed in pip 21.0. Calling it causes a crash with
non-zero exit code and garbled output (especially on Windows frozen exe).

VERIFY:
```
grep -n "pip search\|subprocess.*pip" ikabot/function/checkForUpdate.py
# should return nothing
```

---

## FIX 6 — helpers/piratesDecaptcha.py

LOCATE: the onnxruntime import block, typically:
```python
from onnxruntime import InferenceSession
```

REPLACE with a guarded import:
```python
try:
    from onnxruntime import InferenceSession
except ImportError:
    InferenceSession = None
```

LOCATE: the function that uses InferenceSession (typically `_load_model` or similar)

ADD guard at top of that function:
```python
def _load_model():
    if InferenceSession is None:
        raise RuntimeError(
            "onnxruntime is not installed. Install it with: pip install onnxruntime"
        )
    # ... rest of function unchanged ...
```

WHY: Without this guard, importing the module crashes at startup if onnxruntime
is not installed, producing a blank screen (the crash wipes terminal via
finally:clear()).

VERIFY:
```
grep -n "InferenceSession = None\|RuntimeError.*onnxruntime" ikabot/helpers/piratesDecaptcha.py
```

---

## FIX 7 — helpers/process.py (BUG-10)

LOCATE: process status defaulting block. Old broken code pattern:
```python
if len([p for p in runningIkabotProcessList if "status" not in p]) == len(runningIkabotProcessList) and len(runningIkabotProcessList):
    runningIkabotProcessList[0]["status"] = "running"
```

REPLACE with:
```python
for p in runningIkabotProcessList:
    p.setdefault("status", "running")
```

WHY: Old code only set status on the first process and only when ALL processes
lacked the field. New code correctly defaults every process that lacks one.

VERIFY:
```
grep -n "setdefault.*status.*running" ikabot/helpers/process.py
```

---

## FIX 8 — helpers/pedirInfo.py (BUG-13 partial)

LOCATE: silent exception swallowing in the predetermined_input path. Pattern:
```python
except Exception:
    pass
```

REPLACE with debug logging. First add logger at top of file:
```python
from ikabot.helpers.logging import getLogger
_logger = getLogger(__name__)
```

Then replace each `except Exception: pass` in this file with:
```python
except Exception:
    _logger.debug("predetermined_input error", exc_info=True)
```

VERIFY:
```
grep -n "_logger\|exc_info=True" ikabot/helpers/pedirInfo.py
```

---

## FIX 9 — function/loadCustomModule.py (BUG-08)

LOCATE: dynamic module loading block. Old deprecated pattern:
```python
from importlib.machinery import SourceFileLoader
# ...
module = SourceFileLoader(name, path).load_module()
```

REPLACE the loader lines with:
```python
import importlib.util
# ...
spec = importlib.util.spec_from_file_location(name, path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
```

Remove the `SourceFileLoader` import if it's only used here.

VERIFY:
```
grep -n "spec_from_file_location\|exec_module" ikabot/function/loadCustomModule.py
grep -n "load_module\|SourceFileLoader" ikabot/function/loadCustomModule.py
# second grep should return nothing
```

---

## FIX 10 — function/logs.py (BUG-07 + BUG-15)

### 10a. Fix deprecated logging.WARN (BUG-07)
LOCATE: `logging.WARN` in the level_map dict
OLD: `logging.WARN`
NEW: `logging.WARNING`

### 10b. Persist log level to session data (BUG-15)
LOCATE: where new_level is set after user choice. ADD after setting the root logger:
```python
session.setSessionData({"logLevel": new_level}, shared=True)
```

### 10c. Add user-facing note about background tasks
After the log level change confirmation print, ADD:
```python
print(bcolors.WARNING + "Note: already-running background tasks are unaffected. New instances and tasks started after this change will use the new level." + bcolors.ENDC)
```

### 10d. viewLogs() — use get_log_file_path()
LOCATE: viewLogs() function. It likely opens a hardcoded log path.
REPLACE with dynamic path lookup:
```python
from ikabot.helpers.logging import get_log_file_path

def viewLogs():
    banner()
    log_path = get_log_file_path()
    if log_path is None:
        print("No log file is active for this session yet.")
        enter()
        return
    with open(log_path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - 10000))
        print(f.read().decode("utf-8"))
    print(bcolors.DARK_GREEN + f"Above are the last 10 KB of {log_path}" + bcolors.ENDC)
    enter()
```

VERIFY:
```
grep -n "logging.WARNING\|setSessionData.*logLevel\|get_log_file_path" ikabot/function/logs.py
grep -n "logging.WARN[^I]" ikabot/function/logs.py  # should return nothing
```

---

## FIX 11 — web/session.py (multiple bugs)

This is the most complex file. Apply sub-fixes in this order.

### 11a. Add imports at top of file
```python
import logging
import sys
```
(sys may already be imported; check first)

Also import setup_file_logging:
```python
from ikabot.helpers.logging import setup_file_logging
```

### 11b. Add logger instance near top of Session class or __init__:
```python
self.logger = logging.getLogger(__name__)
```

### 11c. Fix __sessionExpired() — add retry limit (BUG-03)
LOCATE: `def __sessionExpired(self):` — takes no parameters, calls itself recursively

REPLACE signature and add guard:
```python
def __sessionExpired(self, _retries=5):
    self.logger.info("__sessionExpired()")
    if _retries <= 0:
        msg = "Session could not be restored after multiple attempts. Exiting."
        self.logger.error(msg)
        if self.padre:
            print(msg)
        else:
            sendToBot(self, msg)
        sys.exit(1)
```

LOCATE: every recursive `self.__sessionExpired()` call inside the method
REPLACE with: `self.__sessionExpired(_retries - 1)`

### 11d. Fix captcha loop — add max attempts (BUG-09)
LOCATE: `while True:` inside the captcha solving block. Context:
```python
if "gf-challenge-id" in r.headers and 'token' not in r.text:
    while True:
```

REPLACE:
```python
if "gf-challenge-id" in r.headers and 'token' not in r.text:
    _captcha_attempts = 0
    _captcha_max = 5
    while _captcha_attempts < _captcha_max:
```

LOCATE: the increment point — inside the loop just before the `if captcha_sent["status"] == "solved":` block. ADD:
```python
_captcha_attempts += 1
```

LOCATE: end of the loop's else-of-while (after the loop exhausts). ADD:
```python
else:
    self.logger.error("Captcha failed after %d attempts; aborting login.", _captcha_max)
    sys.exit("Captcha error! (Too many failed attempts)")
```

### 11e. Fix __login() retries re-prompt bug (BUG-12)
LOCATE: `return self.__login(retries - 1)` — appears twice in __login()

REPLACE both occurrences with:
```python
return self.__login(retries - 1, mail=self.mail, password=self.password)
```

WHY: Without passing credentials, retry calls fall into the interactive
credential-prompt branch because mail/password args are None.

### 11f. Replace os._exit() with sys.exit() (BUG-14)
LOCATE 1: vacation mode detection block:
```python
if self.__isInVacation(html):
    msg = "The account went into vacation mode"
    if self.padre:
        print(msg)
    else:
        sendToBot(self, msg)
    os._exit(0)
```
REPLACE `os._exit(0)` with `sys.exit(0)`

LOCATE 2: login error for padre path:
```python
if self.padre:
    msg = "Login error."
    print(msg)
    os._exit(0)
```
REPLACE `os._exit(0)` with `sys.exit(msg)`

LOCATE 3: __sessionExpired() retries exhausted (added in 11c above):
```python
os._exit(1)
```
REPLACE with `sys.exit(1)`

KEEP: `os._exit(0)` in `logout()` method — this is intentional for child
process termination and must NOT be changed.

### 11g. Replace bare except/pass with debug logging (BUG-13)
LOCATE: all `except: pass` and `except Exception: pass` blocks (especially
around cookie operations, lastlogin parsing, proxy operations)

REPLACE pattern:
```python
# OLD
try:
    <something>
except Exception:
    pass

# NEW
try:
    <something>
except Exception:
    self.logger.debug("<description of what failed>", exc_info=True)
```

Bare `except:` (catches BaseException) should become `except Exception:` first,
then add the logger.debug call.

### 11h. Call setup_file_logging after successful login
LOCATE: `self.logged = True` line near the end of __login()

ADD immediately after:
```python
setup_file_logging(self.username, self.servidor, self.mundo)
self.cipher.upgrade_filename(self)
```

### 11i. Apply saved log level after login
LOCATE: just after the setup_file_logging call added in 11h

ADD:
```python
try:
    saved_level = self.getSessionData().get("shared", {}).get("logLevel")
    if saved_level is not None:
        logging.getLogger().setLevel(int(saved_level))
except Exception:
    self.logger.debug("Could not apply saved log level", exc_info=True)
```

### 11j. Fix CookieConflictError in current_lobby_token property
LOCATE: `current_lobby_token` property — likely uses `self.s.cookies.get(...)`

REPLACE with iteration to avoid CookieConflictError when duplicate cookies exist:
```python
@property
def current_lobby_token(self) -> str:
    if not hasattr(self, "s"):
        return None
    for cookie in self.s.cookies:
        if cookie.name == "gf-token-production":
            return cookie.value
    return None
```

VERIFY:
```
grep -n "sys.exit\|setup_file_logging\|upgrade_filename\|_retries\|_captcha_max\|exc_info=True" ikabot/web/session.py | head -30
grep -n "os._exit" ikabot/web/session.py
# should return only one line: the logout() method
```

---

## FIX 12 — command_line.py (BUG-02 + BUG-06)

### 12a. Fix init() — create directory not file
LOCATE: `init()` function — likely creates `.ikabot` file with `open(..., 'w')`

REPLACE file creation with directory creation:
```python
def init():
    os.makedirs(IKABOT_SESSIONS_DIR, exist_ok=True)
```

### 12b. Convert menu() from recursive to while loop (BUG-02)
LOCATE: `def menu(session, checkUpdate=True):` and its recursive calls at the end:
```python
menu(session, checkUpdate=False)
# or
menu(session)
```

REPLACE entire menu() function body so that:
- `menu_actions` dict is defined ONCE before the loop
- A `while True:` loop replaces recursion
- Sub-menu "back" choices use `continue` instead of calling `menu()` again
- `checkUpdate` flag is set False after first iteration inside the loop
- No `menu(session, ...)` recursive call anywhere in the function

Pattern:
```python
def menu(session, checkUpdate=True):
    menu_actions = { ... }  # defined ONCE here

    while True:
        if checkUpdate:
            checkForUpdate()
            checkUpdate = False

        # ... print menu, read selection ...

        if selected == 0:
            return

        if selected == 7:  # sub-menu example
            # ... show sub-menu ...
            if sub_selected == 0:
                continue  # back to main menu, NOT menu(session)
            # ...

        # launch action as subprocess
        # ... no recursive menu() call at the end ...
```

### 12c. Fix menu input ceiling (BUG-06)
LOCATE: `read(min=0, max=...)` in menu(). Old pattern uses `len(menu_actions) + 1`
which allows invalid numbers.

REPLACE with the count of actually printed top-level options:
```python
top_max = 24 if plugins else 23  # adjust number to match printed options
selected = read(min=0, max=top_max, digit=True, empty=True)
```

### 12d. Fix start() — clean exit without wiping terminal on crash
LOCATE: `start()` function, the `finally: clear()` pattern

REPLACE:
```python
# OLD (wipes terminal even on crash)
try:
    menu(session)
finally:
    clear()
    session.logout()

# NEW (only clears on clean exit)
try:
    menu(session)
    clear()
except KeyboardInterrupt:
    clear()
    raise
finally:
    session.logout()
```

VERIFY:
```
grep -n "while True\|def menu\|top_max\|IKABOT_SESSIONS_DIR" ikabot/command_line.py
grep -n "menu(session" ikabot/command_line.py
# recursive menu() calls should return zero results
```

---

## WINDOWS / PYINSTALLER SPECIFIC

1. Exe name: in ikabot.spec, EXE block must have `name='ikabot'`
2. Run command (no exe): `python -m ikabot` from the directory containing the
   `ikabot/` package folder
3. Build command: `pyinstaller ikabot.spec` from the repo root
4. UTF-16 subprocess output: process.py run() should try utf-8 then utf-16-le
   then latin-1 as decode fallback when reading subprocess stdout

---

## QUICK VERIFICATION CHECKLIST

After applying all fixes, run these greps — all should return ZERO results:

```bash
# No bare except/pass
grep -rn "except:\s*pass\|except Exception:\s*pass" ikabot/

# No os._exit outside logout()
grep -rn "os._exit" ikabot/ | grep -v "logout"

# No deprecated load_module
grep -rn "load_module\(\)" ikabot/

# No logging.WARN alias
grep -rn "logging\.WARN[^I]" ikabot/

# No recursive menu() calls
grep -n "menu(session" ikabot/command_line.py | grep -v "def menu"

# No hardcoded single log file path
grep -rn "LOGS_DIRECTORY_FILE\|/tmp/ikabot.log" ikabot/

# No pip search
grep -rn "pip search" ikabot/
```

These should return NON-EMPTY results (confirming fixes are present):
```bash
grep -n "freeze_support" ikabot/__main__.py
grep -n "setup_file_logging" ikabot/web/session.py
grep -n "upgrade_filename" ikabot/web/session.py
grep -n "_acquire_lock" ikabot/helpers/aesCipher.py
grep -n "setdefault.*status.*running" ikabot/helpers/process.py
grep -n "_captcha_max" ikabot/web/session.py
grep -n "_retries" ikabot/web/session.py
grep -n "IKABOT_SESSIONS_DIR" ikabot/config.py
```

---

## VERSION BUMP CONVENTION

After applying all fixes, bump IKABOT_MOD_VERSION in config.py:
- Minor bugfix session: +0.0.1
- Bug batch (multiple fixes): +0.1.0 (reset patch digit)
- Major feature addition: +1.0.0 (reset minor + patch)

Also rename the version marker file: `ikabot/version_v{old}` → `ikabot/version_v{new}`

---

## COMMIT ORDER (matches working order above)

Suggested git commits — one per logical group:
1. `Add IKABOT_DATA_DIR, LOGS_DIRECTORY constants and mod version (config.py)`
2. `Rewrite logging.py: deferred per-instance file handler (BUG-01)`
3. `Fix __main__.py: freeze_support for Windows PyInstaller`
4. `Rewrite aesCipher.py: per-account session files with cross-process locking (BUG-04/11)`
5. `Stub checkForUpdate: remove pip search call`
6. `Guard onnxruntime import in piratesDecaptcha.py`
7. `Fix process status defaulting logic (BUG-10)`
8. `Replace bare except/pass with debug logging in pedirInfo.py (BUG-13)`
9. `Fix deprecated load_module() in loadCustomModule.py (BUG-08)`
10. `Fix logs.py: persist log level, fix logging.WARN, viewLogs() (BUG-07/15)`
11. `Fix session.py: BUG-03/09/12/13/14 + logging integration`
12. `Fix command_line.py: menu loop, init(), clean exit (BUG-02/06)`
