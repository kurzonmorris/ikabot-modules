# Ikariam & Ikabot — Complete Reference for AI-Assisted Module Development

> **Purpose:** Read this file at the start of every session. After reading it you need no further context about Ikariam, ikabot, or how the user (Kurzon) operates. Just ask what needs to be built.

---

## 1. The Game — Ikariam

Ikariam is a browser-based city-building strategy game by Gameforge (ikariam.gameforge.com). Players build and manage ancient Greek island cities, trade resources, recruit armies, research technologies, and fight other players.

### Resources
Five resources used everywhere in the game:

| Index | Name    | Tech name | Building    |
|-------|---------|-----------|-------------|
| 0     | Wood    | wood      | Saw mill    |
| 1     | Wine    | wine      | Vineyard    |
| 2     | Marble  | marble    | Quarry      |
| 3     | Crystal | glass     | Crystal Mine|
| 4     | Sulfur  | sulfur    | Sulfur Pit  |

Wood is the base resource — every island produces it. Each island has exactly one luxury resource (Wine/Marble/Crystal/Sulfur). Players must trade or transport to get what they don't produce locally.

### Cities
A player can own many cities, each on a different island (or the same island up to the island cap). Each city has:
- A grid of up to 16 building slots (`position[0..15]`)
- A set of available resources (`availableResources[0..4]`)
- Coordinates (`x`, `y`) — world map is 0–100 × 0–100
- A unique `id` (cityId) used in all API calls

### Islands
Each island has:
- A fixed luxury `tradegood` (1=Wine, 2=Marble, 3=Crystal, 4=Sulfur)
- A wonder (`wonder` int, `wonderName` string)
- A list of `cities` — each with player name, alliance, activity state (`active`/`inactive`/`banned`)
- Coordinates (`x`, `y`)

### Activity / Inactivity
Players who haven't logged in for a set number of days are marked `inactive`. Their cities can be farmed. The `state` field on a city object holds this.

### Buildings
Key buildings relevant to modules:

| Building name (code)  | What it does                               |
|-----------------------|--------------------------------------------|
| `townhall`            | Sets city level / population cap           |
| `warehouse`           | Increases storage capacity                 |
| `port`                | Ships, trade range                         |
| `branchOffice`        | Marketplace — buy/sell to other players    |
| `barracks`            | Recruit land units                         |
| `shipyard`            | Recruit ships                              |
| `tavern`              | Wine consumption / satisfaction            |
| `museum`              | Cultural treaties                          |
| `temple`              | Miracle activation                         |
| `academy`             | Research                                   |
| `palace` / `colony`   | Allows founding new cities                 |

A building slot dict looks like:
```python
{
    "building": "warehouse",   # internal name
    "name": "Warehouse",       # display name
    "level": 12,
    "isBusy": False,
    "canUpgrade": True,
    "isMaxLevel": False,
    "position": 3,             # slot index 0-15
}
```

### The Game's HTTP API
Ikariam is fully browser-driven via `index.php` with query parameters. All game actions are GET or POST requests to:
```
https://s{N}-{country}.ikariam.gameforge.com/index.php?{params}
```
Common patterns:
- `view=city&cityId=123` — fetch city HTML/JSON
- `view=island&islandId=456` — fetch island data
- `action=CityScreen&function=...` — perform city actions
- `action=UpgradeExistingBuilding&...` — upgrade a building
- `action=WorldMap&function=getJSONArea&x_min=0&x_max=50&y_min=0&y_max=50` — world map data
- `ajax=1` on most requests returns JSON instead of full HTML
- `actionRequest=REQUESTID` — CSRF token, stored in `config.actionRequest`

Responses are often a JSON array of commands like:
```json
[["changeView", ["templateName", "<html...>"]], ["updateGlobalData", {...}]]
```

### Sessions and Authentication
Players log in through Gameforge's lobby (`lobby.ikariam.gameforge.com`), which issues a cookie. The cookie is stored encrypted in ikabot's session file. Sessions expire and must be refreshed. ikabot handles re-login automatically.

---

## 2. Ikabot — What It Is

Ikabot is an open-source Python automation bot for Ikariam, maintained by the Ikabot-Collective on GitHub (`Ikabot-Collective/ikabot`). It runs as a terminal application, spawns background worker processes for long-running tasks, and communicates updates via Telegram, Discord, or ntfy.sh.

**This repository** is a fork — "modded by kurzon" — that adds features, integrates unreleased upstream pull requests, and adds external modules. It is not the upstream vanilla ikabot.

---

## 3. Version Numbers

### ikabot Base Version (`IKABOT_VERSION` in `config.py`)
Tracks the upstream ikabot version this mod is based on. Currently `7.4.5`.
Read the live value from `ikabot/config.py` rather than trusting this line.

### Mod Version (`IKABOT_MOD_VERSION` in `config.py`)
Tracks changes made in this fork. Currently `1.7.6`. Banner displays
`modded by kurzon v1.7.6`. Read the live value from `ikabot/config.py`.

### External Module Version (filename suffix — REMOVED at load time)
External modules (`.py` files in the external modules directory) have a version number **in the filename** only:
```
resourceTransportManager_v10.3.1.py
constructionManager.py          ← no version = internal/dev
```
The suffix is stripped by the **installer** when it copies the file into the
user's modules folder — *not* by the module loader. `MODULE_NAME` is the display
name shown in the menu; `MODULE_ENTRY` is the function ikabot calls. The version
also drives the manager's installed-vs-available display. See section 11 for the
full pipeline and why the stripping is load-bearing.

**Rule:** Every external module file must have a version in its filename. Internal modules (inside `ikabot/function/`) do not use filename versions.

---

## 4. Repository Structure

```
ikabot-modules/
├── ikabot/
│   ├── config.py                  ← All global constants and state
│   ├── command_line.py            ← Main menu, process spawning, vault/login
│   ├── function/                  ← Built-in menu functions (one file = one feature)
│   │   ├── constructionList.py
│   │   ├── sendResources.py
│   │   ├── alertMessages.py       ← Added in this mod (PR #389)
│   │   ├── inactivePlayersRadiusMonitor.py  ← Added (PR #386)
│   │   ├── sendCulturalTreatyRequests.py    ← Added (PR #398)
│   │   └── ...
│   ├── helpers/                   ← Shared utilities (session, JSON, GUI, comms)
│   │   ├── aesCipher.py           ← Session file encryption/locking
│   │   ├── botComm.py             ← sendToBot(), Telegram/Discord/ntfy
│   │   ├── credentialStore.py     ← Vault (AES-GCM encrypted credentials)
│   │   ├── getJson.py             ← getCity(), getIsland(), getWorldMapIslands()
│   │   ├── gui.py                 ← banner(), enter(), bcolors
│   │   ├── pedirInfo.py           ← read(), chooseCity(), getIdsOfCities()
│   │   ├── planRoutes.py          ← executeRoutes() for resource transport
│   │   ├── process.py             ← set_child_mode(), updateProcessList()
│   │   ├── signals.py             ← setInfoSignal(), deactivate_sigint()
│   │   └── varios.py              ← wait(), addThousandSeparator(), getDateTime()
│   └── web/
│       └── session.py             ← Session class — all HTTP calls go through here
├── resourceTransportManager_v10.3.1.py ← External module
├── constructionManager.py              ← External module
├── tavernManager.py                    ← External module
├── autoRecruitment.py                  ← External module
├── sequenceRunner.py                   ← External module (WIP)
├── GUIDE.md                            ← End-user guide
├── RELEASE_NOTES.md                    ← Changelog vs upstream
└── ikariam_ikabot_explained.md         ← This file
```

---

## 5. config.py — Global State

```python
IKABOT_VERSION = "7.3.3"
IKABOT_MOD_VERSION = "0.9.4"
IKABOT_MOD_VERSION_TAG = "modded by kurzon v0.9.4"

IKABOT_DATA_DIR   # Windows: %APPDATA%\.ikabot  Linux: ~/.ikabot
IKABOT_SESSIONS_DIR = os.path.join(IKABOT_DATA_DIR, "sessions")
LOGS_DIRECTORY    = os.path.join(IKABOT_DATA_DIR, "logs")

actionRequest     # CSRF token string — include in every game action POST
city_url          # "view=city&cityId="   — prepend to cityId for city fetch
island_url        # "view=island&islandId=" — prepend to islandId

materials_names         # ["Wood","Wine","Marble","Crystal","Sulfur"]
materials_names_english # same
materials_names_tec     # ["wood","wine","marble","glass","sulfur"]
miracle_names_english   # list of wonder names indexed by wonder int

predetermined_input     # shared multiprocessing list — drives automated input
isWindows               # bool
```

Import with `from ikabot.config import *` or `import ikabot.config as config`.

---

## 6. The Session Object

Every function receives a `session` object (instance of `ikabot.web.session.Session`). It handles authentication, cookies, proxies, and logging. **All game communication goes through this object.**

### Key attributes
```python
session.username   # player username string
session.servidor   # server string e.g. "s70-en"
session.mundo      # world/server name
session.urlBase    # "https://s70-en.ikariam.gameforge.com/index.php?"
session.padre      # True if this is the parent (menu) process
```

### Key methods
```python
# GET request — url appended to urlBase
html = session.get("view=city&cityId=123")

# GET with noIndex — strips index.php (for static files etc)
data = session.get("somepath", noIndex=True)

# POST request — url or params dict
resp = session.post("action=UpgradeExistingBuilding&...")
resp = session.post(params={"action": "Messages", "function": "send", ...})

# Session data — per-account persistent storage (encrypted in session file)
data = session.getSessionData()     # returns dict
session.setSessionData({"key": val})  # merges into session data
session.setSessionData({"key": val}, shared=True)  # stores in shared sub-dict

# Status line (shown in process table)
session.setStatus("Running — processed 3/10 cities")

# Logout
session.logout()
```

### Important: session.get() with no arguments
`session.get()` with no URL argument hits `index.php?` (bare). **Some servers return 404 for this.** Always wrap bare `session.get()` calls in try/except or use a known view. Do not rely on the bare URL working.

### HTTP response format
Most AJAX responses are JSON arrays. Parse with:
```python
import json
data = json.loads(session.post(url), strict=False)
# data[0][1] often contains global update data (time, resources)
# data[1][1][1] often contains the view HTML
# data[1][1][2]["viewScriptParams"] often contains JS parameters
```

---

## 7. Key Helpers — What Each Does

### `ikabot.helpers.pedirInfo`
```python
read(min=None, max=None, digit=False, msg=prompt, values=None,
     empty=False, additionalValues=None, default=None)
# Reads validated user input. If config.predetermined_input is non-empty,
# pops from front instead of asking. Always use this — never input() directly.

chooseCity(session)
# Interactive city picker. Returns city dict with full city data.

getIdsOfCities(session)
# Returns (city_ids_list, cities_dict). Fastest way to enumerate own cities.
```

### `ikabot.helpers.getJson`
```python
getCity(html)     # Parse city HTML/JSON → city dict
getIsland(html)   # Parse island HTML → island dict
getWorldMapIslands(raw)  # Parse world map JSON → list of shallow island dicts
```

City dict keys: `id`, `name`, `cityName`, `x`, `y`, `islandId`, `position` (list of building slot dicts), `availableResources` (list of 5 ints), `storageCapacity`, `rango` (trade range), `pos` (market building position).

### `ikabot.helpers.varios`
```python
wait(seconds, maxrandom=0)   # Sleep with optional random extra seconds
addThousandSeparator(num)    # 3000 → "3.000"
daysHoursMinutes(seconds)    # 3661 → "1H 1M"
getDateTime()                # Current datetime string
getCurrentCityId(session)    # Returns current city ID int
normalizeDicts(data)         # Normalises nested dicts from session data
```

### `ikabot.helpers.botComm`
```python
sendToBot(session, message)          # Send to ALL configured backends
sendToBotDebug(session, msg, flag)   # Only sends if flag is True
checkTelegramData(session)           # Returns False and shows error if not configured
telegramDataIsValid(session)         # Returns bool silently
notificationDataIsValid(session)     # Returns bool — any backend configured?
```
Backends: Telegram, Discord webhook, ntfy.sh. All configured in `(21) Options → (2) Notification Setup`.

### `ikabot.helpers.gui`
```python
banner()         # Clear screen + show ikabot ASCII banner with version tags
enter()          # Wait for Enter (respects predetermined_input)
bcolors.RED      # Terminal colour codes: RED, GREEN, YELLOW, WARNING, ENDC
```

### `ikabot.helpers.process`
```python
set_child_mode(session)         # Call this in every spawned child before doing work
updateProcessList(session)      # Read/write the running process list
```

### `ikabot.helpers.signals`
```python
setInfoSignal(session, info)    # Register SIGABRT handler to send info to Telegram
deactivate_sigint()             # Make Ctrl+C do nothing (called by set_child_mode)
```

### `ikabot.helpers.planRoutes`
```python
executeRoutes(session, routes)  # Execute a list of resource transport routes
# Routes are dicts describing origin city, destination city, amounts, ships
```

### `ikabot.helpers.naval`
```python
getAvailableShips(session, cityId)       # Returns available ship count
getAvailableFreighters(session, cityId)  # Returns available freighter count
```

### `ikabot.helpers.market`
Market helper utilities for price/offer calculations.

---

## 8. The Process Model

ikabot uses `multiprocessing` to run tasks in the background. The main process shows the menu; each task runs as a separate child process.

### How a function is spawned (command_line.py)
```python
multiprocessing.Process(
    target=menu_actions[selected],  # the function
    args=(session, event, stdin_fd, predetermined_input)
)
```

### Required function signature (ALL module entry points)
```python
def myFunction(session, event, stdin_fd, predetermined_input):
    sys.stdin = os.fdopen(stdin_fd)               # reconnect stdin
    config.predetermined_input = predetermined_input  # enable automation
    try:
        # --- interactive config phase ---
        banner()
        # ... read() calls for configuration ...
        enter()
    except KeyboardInterrupt:
        event.set()    # MUST set event to unblock parent
        return

    set_child_mode(session)   # MUST call before doing any real work
    event.set()               # MUST set event to unblock parent menu

    setInfoSignal(session, "Brief description of what this task does")

    try:
        do_it(session, ...)   # actual work in a separate function
    except Exception:
        msg = "Error:\n{}".format(traceback.format_exc())
        sendToBot(session, msg)
    finally:
        session.logout()
```

**Critical rules:**
- `event.set()` MUST be called exactly once — either on early return or after `set_child_mode()`. If it is never called, the parent menu hangs forever.
- `set_child_mode()` MUST be called before doing any game actions in the child.
- Configuration (reading user input with `read()`) must happen BEFORE `set_child_mode()` and `event.set()`.
- Never call `event.set()` twice.
- `session.logout()` in `finally` is good practice to clean up server-side session.

---

## 9. The `predetermined_input` Automation System

`config.predetermined_input` is a `multiprocessing.Manager().list()` shared across all processes.

`read()` checks it first — if non-empty, it pops from the front and returns that value without prompting the user. This is how the **Sequence Runner** module works: it pre-loads a list of values that drive the menu automatically.

**Rules:**
- Always use `read()` for user input — never `input()` directly.
- The pop mechanism respects all the same `min`/`max`/`digit` validation as interactive input.

---

## 10. Adding a Built-in Function to the Menu

Built-in functions live in `ikabot/function/` and must be registered in `command_line.py`.

### Step 1: Create the file
`ikabot/function/myFeature.py` — follow the signature in section 8.

### Step 2: Import in command_line.py
```python
from ikabot.function.myFeature import myFeature
```

### Step 3: Add to menu_actions dict
```python
menu_actions = {
    ...
    25: sendCulturalTreatyRequests,
    26: myFeature,   # next available number
}
```

### Step 4: Add display text
```python
print("(26) My feature description")
```

### Step 5: Add to a submenu if needed (follow existing submenu pattern)
```python
if selected == 7:   # Alerts submenu
    ...
    print("(3) Alert in-game messages")
    print("(4) My alert feature")
    selected = read(min=0, max=4, digit=True)
    if selected == 0:
        continue
    selected += 700
# menu_actions[704] = myFeature
```

### Current menu map
| Number | Label                              | Submenu keys      |
|--------|------------------------------------|-------------------|
| 1      | Construction list                  |                   |
| 2      | Send resources                     |                   |
| 3      | Distribute resources               |                   |
| 4      | Account status                     |                   |
| 5      | Activate Shrine                    |                   |
| 6      | Login daily                        |                   |
| 7      | Alerts / Notifications             | 701, 702, 703     |
| 8      | Marketplace                        | 801, 802          |
| 9      | Donate                             | 901, 902          |
| 10     | Activate vacation mode             |                   |
| 11     | Activate miracle                   |                   |
| 12     | Military actions                   | 1201, 1202, 1203  |
| 13     | See movements                      |                   |
| 14     | Construct building                 |                   |
| 15     | Update ikabot                      |                   |
| 16     | Ikabot Web Server                  |                   |
| 17     | Auto-Pirate                        |                   |
| 18     | Research                           |                   |
| 19     | Attack / Grind barbarians          | 1901, 1902        |
| 20     | Dump / Monitor world               | 2001, 2002, 2003  |
| 21     | Options / Settings                 | 2101–2108 + vault |
| 22     | Consolidate resources              |                   |
| 23     | Set Production                     |                   |
| 25     | Send cultural treaty requests      |                   |
| 24     | Plugins (if any)                   |                   |
| 30     | External Modules                   | dynamic 31+       |

---

## 11. External Modules

External modules are `.py` files dropped into a configured folder (global or per-account). They appear under `(30) External Modules` in the menu, numbered starting at 31.

### File naming convention
```
myModule_v1.2.3.py     ← version goes BEFORE .py, never after
```
The version suffix is **not** removed by the module loader. It is removed by the
installer when it copies the file into the user's modules folder. That
distinction matters — see "The version suffix is load-bearing" below.

### Required module metadata
```python
MODULE_NAME  = "My Module"       # display name shown in the menu
MODULE_ENTRY = "myModuleEntry"   # name of the entry function in this file
```

### ⚠ Always declare `MODULE_ENTRY`

The loader resolves the entry function like this:

```python
name    = os.path.basename(path).replace(".py", "")   # filename stem
fn_name = getattr(module, "MODULE_ENTRY", None) or name
fn      = getattr(module, fn_name)                    # AttributeError if wrong
```

If `MODULE_ENTRY` is missing it falls back to the **filename stem**, so the file
name and the function name must match exactly. With a version still in the name
that fallback resolves to nonsense:

```
resourceTransportManager_v10.3.1.py  ->  looks for  resourceTransportManager_v10.3.1()  ✗
resourceTransportManager.py          ->  looks for  resourceTransportManager()          ✓
```

Five of the eight current modules omit `MODULE_ENTRY` and depend entirely on the
installer having stripped the version first. A user who copies a versioned file
into their modules folder by hand gets an `AttributeError` on launch.

**Declaring `MODULE_ENTRY` makes the module immune to this** — the filename then
does not matter at all. Do it in every new module.

### The version suffix is load-bearing

`_vX.Y.Z` in the filename is not decoration. The installer parses it with
`^(.*?)_v(.+)$` against the stem to drive the Modules screen:

| Filename in repo `modules/` | base | version |
|---|---|---|
| `resourceTransportManager_v10.3.1.py` | `resourceTransportManager.py` | `10.3.1` |
| `noVersion.py` | `noVersion.py` | *(none)* |

A module without the suffix still installs, but shows "no version" in the
manager and can never display an update-available state.

### How modules reach users

There is **no release or zip step** — the installer reads the repo directly via
the GitHub Contents API:

```
repo modules/  and  repo config-examples/          (versioned filenames)
        │  GitHub Contents API — no release needed
        ▼
<install>/Ikabot Modules template/                 (versions KEPT, for reference)
        │  copy, stripping _vX.Y.Z
        ▼
<install>/modules/                                 (versions REMOVED — what ikabot loads)
```

Consequences for a module author:

- **Committing to `modules/` on `main` publishes it.** Users see it on their next
  Modules refresh. No release, no zip, no version bump of ikabot itself.
- Bump the filename version when you want users to see an update is available.
- Old versions are deleted from both folders on update, matched by base name —
  so renaming the base of a module orphans the old file rather than replacing it.
- The template folder is where the manager reads installed versions from, which
  is why Status can list every module with its version.

### CSV and config files

- Files in `config-examples/` are downloaded alongside modules into the same
  modules folder.
- **CSVs are never version-stripped or renamed.** A module expecting
  `bulkdistribution.csv` gets exactly that name.
- `bulkdistribution.csv` is treated as user data: the installer asks before
  overwriting it, because it holds hand-entered settings. If your module ships a
  CSV that users edit, expect the same treatment and never assume yours was
  freshly overwritten.

### Storage location for module data
External modules should store their data files in `IKABOT_DATA_DIR`:
```python
from ikabot.config import IKABOT_DATA_DIR
MY_DATA_FILE = os.path.join(IKABOT_DATA_DIR, "my_module_data.json")
```
This puts data at `%APPDATA%\.ikabot\` (Windows) or `~/.ikabot/` (Linux),
alongside sessions, logs, and the vault. Never write data next to the module
file — the installer deletes and replaces files there on update.

---

## 12. Module Interaction — Using Other Modules

Modules can and should use functionality from other modules. Import them directly:

```python
# Use resource transport to move materials before building
from ikabot.helpers.planRoutes import executeRoutes

# Get available ships
from ikabot.helpers.naval import getAvailableShips

# Use marketplace data
from ikabot.function.buyResources import getOffers, calculateCost

# Use construction data
from ikabot.helpers.getJson import getCity
```

### Common cross-module use cases

| Scenario | What to use |
|----------|------------|
| Need resources in a city before upgrading | `executeRoutes()` from planRoutes, or call the Resource Transport Manager's transport logic |
| Need to know if ships are available | `getAvailableShips(session, cityId)` from naval.py |
| Need to buy resources from market | `getOffers()` / `calculateCost()` from buyResources.py |
| Need city/island data | `getCity()`, `getIsland()` from getJson.py |
| Need to notify user | `sendToBot()` from botComm.py |
| Need to enumerate all cities | `getIdsOfCities(session)` from pedirInfo.py |
| Need to wait with randomisation | `wait(seconds, maxrandom=N)` from varios.py |

**Principle:** Never duplicate logic that already exists in a helper or another module. Import and reuse. This keeps behaviour consistent and reduces bugs.

---

## 13. Existing External Modules (Kurzon's)

### Resource Transport Manager (`resourceTransportManager_v10.3.1.py`)
Automates moving resources between cities. Handles ship routing, multiple legs, partial loads, retry on failure, Telegram notifications per shipment. Has configurable notification levels (partial/all/errors-only). Uses `executeRoutes()` internally.

**Key exports used by other modules:** transport logic can be re-invoked by directly calling `executeRoutes()` from `ikabot.helpers.planRoutes`.

### Construction Manager (`constructionManager_v2.1.9.py`)
CSV-backed multi-city construction queue. Reads a CSV file specifying which buildings to upgrade in which cities. Polls periodically, triggers upgrades, handles resource shortages by waiting or requesting transport.

### Tavern Manager (`tavernManager_v2.0.1.py`)
Monitors wine consumption and satisfaction across all cities. Adjusts tavern settings automatically to keep satisfaction at target level.

### Auto Recruitment Manager (`autoRecruitmentManager_v2.12.1.py`)
Automates training units and ships across multiple barracks/shipyards. Distributes recruitment for synchronised completion times. Handles resource shortages with retry logic.

Version noted in docstring: `1.08`.

### Sequence Runner (`sequenceRunner_v1.1.2.py`)
Stores named input sequences in `~/.ikabot/sequences.json`. When a sequence is run, it pre-loads `predetermined_input` with the stored values and triggers `event.set()`, causing the main menu loop to consume them automatically. This replaces AutoHotkey scripts.

---

## 14. Notifications — Three Backends

All notification calls go through `sendToBot(session, message)`. The function routes to all configured backends.

| Backend  | Config key   | Setup menu            |
|----------|--------------|-----------------------|
| Telegram | `telegram`   | (21) Options → (2)    |
| Discord  | `discord`    | (21) Options → (2)    |
| ntfy.sh  | `ntfy`       | (21) Options → (2)    |

Check before starting long tasks:
```python
if not notificationDataIsValid(session):
    print("No notifications configured — continuing without alerts.")
# or for Telegram specifically:
if checkTelegramData(session) is False:
    event.set()
    return
```

Discord webhook URL must start with one of:
- `https://discord.com/api/webhooks/`
- `https://discordapp.com/api/webhooks/`
- `https://ptb.discord.com/api/webhooks/`
- `https://canary.discord.com/api/webhooks/`

---

## 15. Credential Vault

The vault (`credentialStore.py`) stores game account credentials encrypted with AES-256-GCM under a PBKDF2 master password.

- **Location:** `%APPDATA%\.ikabot\vault` (Windows) / `~/.ikabot/vault` (Linux)
- **Migration:** Old `~/.ikabot_vault` is moved automatically on first run
- **Concurrency:** PID-based lock file prevents corruption under simultaneous access
- **Key validation:** `verify_password()` decrypts the first account to confirm the password before showing the account list
- **Wrong password:** Shows error before listing accounts (never displays accounts for wrong password)

Vault data includes: `email`, `password`, `blackbox` token, `lobby_token`.

---

## 16. Logging

Per-account log files are written to `LOGS_DIRECTORY` (`~/.ikabot/logs/`). Children set up file logging via `set_child_mode(session)` which calls `setup_file_logging(username, servidor, mundo)`.

Get a logger in any file:
```python
from ikabot.helpers.logging import getLogger
logger = getLogger(__name__)
logger.info("...")
logger.warning("...")
logger.error("...")
logger.debug("...")
```

Log levels: DEBUG=10, INFO=20, WARNING=30 (default), ERROR=40.

---

## 17. Vanilla ikabot vs This Mod — Compatibility

Modules must work correctly when running from **both** the vanilla upstream ikabot and this modded version. Differences to be aware of:

| Feature | Vanilla ikabot | Kurzon mod |
|---------|---------------|------------|
| Vault   | None — credentials entered each time | Encrypted vault in IKABOT_DATA_DIR |
| Notifications | Telegram only | Telegram + Discord + ntfy.sh |
| Data dir | `~/.ikabot/` (no APPDATA on Windows) | `%APPDATA%\.ikabot\` (Windows) |
| Menu options | Up to 23 + plugins | 23 + 25 (treaties) + 30 (external) |
| New functions | Not present | alertMessages, inactivePlayersRadiusMonitor, sendCulturalTreatyRequests |
| Session locking | Basic | PID-based stale lock removal |

When writing external modules, only import from `ikabot.config`, `ikabot.helpers.*`, and `ikabot.function.*`. Do not depend on mod-specific internals. Use `notificationDataIsValid(session)` rather than assuming Telegram is the only backend.

---

## 18. Visual and Structural Consistency

All modules — internal and external — must look and behave the same way.

### Visual rules
1. Every interactive screen starts with `banner()` — this clears the screen and shows the ikabot ASCII art with both version tags.
2. Every section header within a module should use a consistent box style. External modules use the double-line box style:
   ```
   ╔══════════════════════════════════════════════════╗
   ║          MY MODULE NAME                          ║
   ╚══════════════════════════════════════════════════╝
   ```
   Or simply rely on `banner()` + a printed title line.
3. Use `bcolors.GREEN`, `bcolors.RED`, `bcolors.WARNING`, `bcolors.ENDC` for coloured feedback.
4. Menus always start at `(0) Back` or `(0) Exit`.
5. Numbers formatted with `addThousandSeparator()`.
6. Time durations formatted with `daysHoursMinutes()`.
7. All user-facing `read()` calls must have correct `min`/`max`/`digit` constraints.

### Structural rules
1. Entry function → interactive config → `set_child_mode()` + `event.set()` → `do_it()`.
2. Separate the interactive config from the working loop — never mix them.
3. `do_it()` function handles the main loop or one-shot action.
4. Long-running tasks use `while True:` with `wait()` between iterations.
5. All errors caught and sent to Telegram via `sendToBot()`.
6. Process status updated via `session.setStatus("...")` and `updateProcessList()`.
7. `setInfoSignal(session, info)` called after `set_child_mode()` so Telegram can query task status.

---

## 19. Common Patterns and Pitfalls

### Getting cities
```python
city_ids, cities = getIdsOfCities(session)
for city_id in city_ids:
    html = session.get(city_url + str(city_id))
    city = getCity(html)
```

### Making a game action POST
```python
url = (
    "action=SomeAction&function=doThing"
    "&cityId={}&position={}&actionRequest={}&ajax=1"
).format(city_id, position, actionRequest)
response = session.post(url)
```

### Parsing AJAX changeView response
```python
data = json.loads(response, strict=False)
for item in data:
    if isinstance(item, list) and item[0] == "changeView":
        html_content = item[1][1]
```

### Waiting safely
```python
wait(3600)           # wait 1 hour exactly
wait(3600, maxrandom=300)  # wait 1 hour + up to 5 random minutes (anti-detection)
```

### Pitfalls to avoid
- **Bare `session.get()`** — hits `index.php?` which 404s on some servers. Always pass a view.
- **`input()` directly** — breaks `predetermined_input` automation. Always use `read()`.
- **Forgetting `event.set()`** — parent menu hangs forever.
- **Doing work before `set_child_mode()`** — logging and signal handling not properly set up.
- **Hardcoded city IDs** — always derive from session data or user selection.
- **Not catching exceptions** in long-running loops — one error kills the whole task.
- **Endless loops without `wait()`** — hammers the server and risks ban. Always wait between iterations.
- **Assuming resources are available** — always check before attempting transport or building.

---

## 20. Mandatory Post-Coding Review Protocol

After every coding session, before declaring the work complete:

### Step 1: Syntax check
```bash
python3 -c "import ast; ast.parse(open('myfile.py').read()); print('OK')"
```
Run for every modified file.

### Step 2: Review checklist
- [ ] `event.set()` called exactly once in every code path
- [ ] `set_child_mode()` called before any game actions
- [ ] No bare `session.get()` calls (always pass a view parameter)
- [ ] All `read()` calls have appropriate `min`/`max`/`digit` constraints
- [ ] No `input()` calls — only `read()`
- [ ] All loops have a `wait()` call to prevent server hammering
- [ ] All exceptions caught in long-running loops with `sendToBot()` notification
- [ ] No endless loops possible (all loops have a termination condition or `wait()`)
- [ ] `session.logout()` in `finally` block
- [ ] New functions imported and registered in `command_line.py` if built-in
- [ ] `MODULE_NAME` and `MODULE_ENTRY` defined if external module
- [ ] Version number in filename if external module
- [ ] Data files stored under `IKABOT_DATA_DIR`
- [ ] `banner()` called at start of each interactive screen

### Step 3: Commit and push
All changes committed to `claude/fix-ikabot-logging-MfYdW` branch and pushed to `kurzonmorris/ikabot-modules`.

### Step 4: Report
Provide a summary covering:
1. What was coded (each function/change listed briefly)
2. Result of the review checklist (any issues found and fixed)
3. Any design decisions or trade-offs made
4. Any known limitations or future considerations

---

## 21. How Kurzon Operates — Session Behaviour Notes

Understanding the user's patterns helps avoid misunderstandings:

### Workflow
- Kurzon runs multiple ikabot instances simultaneously — one per game account. Each opens separately and enters the vault password. Concurrent vault access is expected and handled.
- External modules are the primary way new features are delivered. They are dropped into a shared folder, not merged into core ikabot files.
- Automation sequences (AutoHotkey scripts historically, Sequence Runner going forward) pre-load a fixed set of menu selections to perform a "daily routine" without manual input.

### Communication style
- Short, direct requests. Does not explain context unless asked.
- Requests often say "increase version", "add X to Y", "fix this error" — expect to find the relevant files yourself.
- Shows log output or tracebacks verbatim when reporting bugs.
- When a version bump is requested: only change what was explicitly asked — do not change other version numbers.
- "Revert" means revert the last commit with `git revert HEAD --no-edit`.
- After every completed task: commit and push to the branch without being asked.

### Version update rules
- `IKABOT_VERSION` — only changes when syncing with upstream ikabot releases.
- `IKABOT_MOD_VERSION` — increments when significant features or fixes are added to the mod.
- External module filename version — increments when the module has a releasable update.
- Changing a version number means: update `config.py` AND rename the version marker file (`ikabot/version_vX.Y.Z`).
- **Never change version numbers without being explicitly told the new version number.**

### Code standards the user expects
- No unnecessary comments — only comment when the WHY is non-obvious.
- No docstrings beyond a one-liner maximum.
- No error handling for impossible cases.
- No feature flags or backwards-compat shims.
- Minimal, focused changes — fix exactly what was asked, nothing more.
- External modules must be self-contained — not require changes to ikabot core files unless truly necessary.

### Frequently requested operations
- "Check the logs" — read from `LOGS_DIRECTORY` for the relevant account
- "Add to the guide" — update `GUIDE.md` (end-user facing, no build/compile references)
- "Add to release notes" — update `RELEASE_NOTES.md`
- "Make it work like RTM" — follow the Resource Transport Manager's pattern for that feature
- "Add notification support" — use `notificationDataIsValid()` + `sendToBot()`, not Telegram-specific calls

---

## 22. Quick Reference — Starting a New Module

```python
#! /usr/bin/env python3
# -*- coding: utf-8 -*-

MODULE_NAME  = "My Feature"      # shown in external modules menu
MODULE_ENTRY = "myFeature"       # entry function name

import os
import sys
import traceback

import ikabot.config as config
from ikabot.config import actionRequest, city_url, IKABOT_DATA_DIR
from ikabot.helpers.botComm import sendToBot, notificationDataIsValid
from ikabot.helpers.getJson import getCity
from ikabot.helpers.gui import banner, enter, bcolors
from ikabot.helpers.pedirInfo import read, chooseCity, getIdsOfCities
from ikabot.helpers.process import set_child_mode
from ikabot.helpers.signals import setInfoSignal
from ikabot.helpers.varios import wait, addThousandSeparator, getDateTime

_DATA_FILE = os.path.join(IKABOT_DATA_DIR, "my_feature_data.json")


def myFeature(session, event, stdin_fd, predetermined_input):
    sys.stdin = os.fdopen(stdin_fd)
    config.predetermined_input = predetermined_input

    try:
        banner()
        # --- interactive configuration ---
        print("(0) Back")
        print("(1) Option A")
        choice = read(min=0, max=1, digit=True)
        if choice == 0:
            event.set()
            return

        enter()
    except KeyboardInterrupt:
        event.set()
        return

    set_child_mode(session)
    event.set()

    setInfoSignal(session, "My feature is running")

    try:
        _do_it(session, choice)
    except Exception:
        sendToBot(session, "Error in My Feature:\n{}".format(traceback.format_exc()))
    finally:
        session.logout()


def _do_it(session, choice):
    while True:
        # ... actual work ...
        wait(3600, maxrandom=300)
```

---

*Last updated: 2026-08-02. Reflects ikabot 7.4.5 / mod v1.7.6.*
