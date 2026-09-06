# Ikariam & Ikabot — Complete Reference for AI-Assisted Module Development

> **Purpose:** Read this file at the start of every session. After reading it you need no further context about Ikariam, ikabot, or how the user (Kurzon) operates. Just ask what needs to be built.

### Companion documents

Read this file first. Open the others only when the task touches them.

| File | When you need it |
|---|---|
| `Explained-user_kurzon.md` | How Kurzon works, branch naming, what he expects back |
| `docs/UPSTREAM_PARITY.md` | **Before any upstream sync.** Which upstream PRs are in, which were deliberately adapted, and what a port must never remove |
| `docs/AUTOSTART_BRIEF.md` | Making a module start automatically at login (§24) |
| `RRS_INTEGRATION_GUIDE.md` | Any module that reserves or spends resources (§26) |
| `MIGRATION_GUIDE.md` | Applying the fork's changes onto a fresh upstream tree |
| `GUIDE.md` | End-user setup — not needed for coding |

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
Tracks the upstream ikabot version this mod is based on. Currently `7.4.5`;
the fork is at **full parity** with upstream 7.4.5 (see `docs/UPSTREAM_PARITY.md`).
Read the live value from `ikabot/config.py` rather than trusting this line.

### Mod Version (`IKABOT_MOD_VERSION` in `config.py`)
Tracks changes made in this fork. Currently `1.7.6`. Banner displays
`modded by kurzon v1.7.6`. Read the live value from `ikabot/config.py`.

**Bump it on every change you ship.** Patch for fixes, minor for new features.

### External Module Version (filename suffix — REMOVED at load time)
External modules (`.py` files in the external modules directory) have a version number **in the filename** only:
```
resourceTransportManager_v10.3.1.py
constructionManager_v2.2.8.py
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
session.username   # player name on this world, e.g. "StDa"
session.servidor   # community / language code ONLY, e.g. "en"  (not "s70-en")
session.mundo      # world NUMBER as a string, e.g. "70"
session.word       # world NAME, e.g. "Nereus"
session.host       # "s{mundo}-{servidor}.ikariam.gameforge.com"
session.urlBase    # "https://s70-en.ikariam.gameforge.com/index.php?"
session.padre      # True if this is the parent (menu) process
```

> **`servidor` is not the full server string and `mundo` is not a name.**
> The banner shows `Server:en, World:Nereus, Player:StDa` — that is
> `servidor`, `word`, `username`. Verify against
> `ikabot/web/session.py` (`self.host = "s{}-{}"`) before assuming.

Getting these two wrong is exactly why per-account filenames used to omit the
world and collide across worlds — see **§27, "Per-instance filenames must
include the world"** for the naming rule. If you change an existing naming
scheme, migrate the old filenames, or users silently lose their saved data.

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
file — the installer deletes and replaces files there on update, and in a
container that directory may be read-only or part of the image.

**Name per-instance files with server + world + username**, never server +
username: a player name is only unique within a world. See §27.

In Docker, whether these files survive a container rebuild depends on what
is mounted. If `$HOME` is the mounted volume (e.g. `HOME=/config`), files
written to `~` persist; if only `~/.ikabot` is mounted, anything written to
`~` directly is lost. `IKABOT_DATA_DIR` is inside `.ikabot`, so it is safe
under either layout.

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

Current files in `modules/`. **Check the directory for the live version — the
numbers below drift.**

| Module | Does |
|---|---|
| `resourceTransportManager_v10.3.1.py` | Moves resources between cities: ship routing, multiple legs, partial loads, retry, per-shipment notifications with configurable levels. Uses `executeRoutes()` from `planRoutes`. |
| `constructionManager_v2.2.8.py` | CSV-backed multi-city construction queue. Polls, triggers builds/upgrades, and handles shortages by waiting or requesting transport. Selectable queue strategy (wait in order / skip ahead), per-city resource requirements report, and a queue that re-aligns itself with buildings done by hand. See §27. |
| `autoRecruitmentManager_v2.12.1.py` | Trains units/ships across barracks and shipyards, synchronised completion, retry on shortage. **The working RRS integration example.** |
| `tavernManager_v2.0.1.py` | Keeps satisfaction at target by adjusting wine. **The best settings-memory example (§23)** — namespaced per flow, validates, re-resolves city ids. |
| `resourceProductionManager_v1.0.3.py` | Manages production/luxury assignment per city. Own persistence, predates `modulePrefs`. |
| `islandColonizeMonitor_v1.5.0.py` | Watches islands for free colonisation slots. |
| `resourceReservationSystem_v1.0.0.py` | Shared reservation data layer, not a user-facing module. See §26. |
| `sequenceRunner_v1.1.2.py` | Stores named input sequences and replays them through `predetermined_input` (§9). Replaces the AutoHotkey scripts. |
| `schedulerMonitor_v1.0.0.py` | Watches the worker locks of constructionManager, resourceTransportManager and autoRecruitmentManager on a timer and relaunches any scheduler that is down while work is still queued. Starts them headlessly — through the module's own auto-start path where it has one, otherwise by driving its worker loop directly. |

**Before writing a new module, check whether one of these already does part of
the job** — §12's rule against duplicating logic applies to modules too.

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

### ⚠ You cannot fetch another player's city

`session.get(city_url + <id>)` for a city you do not own does **not** return
that city — the game returns **your own currently-active city**. `getCity()`
parses it happily, so the code silently continues with the wrong city:

- the confirmation shows *your* city name,
- the schedule stores *your* city id as the destination,
- shipments intended for another player are delivered to yourself,
- and the destination's warehouse space reads as *yours*, so a full
  warehouse clamps every shipment to zero and the run looks empty.

Detect it by comparing ids, and build foreign cities from **island data**
(`getIsland`) instead — that is what `chooseForeignCity` does:

```python
city = getCity(session.get(city_url + str(dest_id)))
if str(city.get("id", "")) != str(dest_id):
    ... # foreign: use the island entry, skip warehouse-space checks
```

### `getCity` / `getIsland` / `getWorldMapIslands` raise `RuntimeError`

They now raise when the page cannot be parsed — an expired session, a login
redirect, a maintenance page. Older code caught only
`(AttributeError, TypeError, KeyError)`, so one bad response killed a whole
background cycle instead of skipping one city. Catch `RuntimeError` too.

### A guarded parser is worthless if it calls an unguarded one

`getCity()` guards its own regex and raises a readable `RuntimeError` — then
calls `getWarehouseCapacity()`, which did `re.search(...).group(1)` with no
guard at all. Any page without a warehouse block (a city that is not ours, an
ajax fragment, a maintenance page) came back as:

```
'NoneType' object has no attribute 'group'
```

reported to the user as an unexplained shipment failure. When you harden a
parser, follow it into everything it calls. `getShipCapacity()` in
`pedirInfo.py` had the same hole.

**And pick the right failure value.** `getWarehouseCapacity` now returns `0`
for "unknown", which `getCity` turns into `freeSpaceForResources` of all
zeros. Read naively that says *the warehouse is full*, and the sender sits in
an hourly retry forever. Anywhere free space is used, unknown must be treated
like a foreign city — no destination clamp at all:

```python
foreign = (str(dest["id"]) != str(wanted_id)) or not dest.get("storageCapacity")
```

A sentinel that is indistinguishable from a real, meaningful value is a second
bug wearing the first one's clothes.

### Pitfalls to avoid
- **Bare `session.get()`** — hits `index.php?` which 404s on some servers. Always pass a view.
- **`input()` directly** — breaks `predetermined_input` automation. Always use `read()`.
- **Forgetting `event.set()`** — parent menu hangs forever.
- **Doing work before `set_child_mode()`** — logging and signal handling not properly set up.
- **Hardcoded city IDs** — always derive from session data or user selection.
- **Not catching exceptions** in long-running loops — one error kills the whole task.
- **Endless loops without `wait()`** — hammers the server and risks ban. Always wait between iterations.
- **Assuming resources are available** — always check before attempting transport or building.
- **Saving a menu index instead of an id** — lists get re-sorted; the saved
  position silently points at something else next run (§23).
- **Replaying saved settings without validating them** — the file is editable
  JSON on disk. Wrap the load in `try/except` and fall back to asking.
- **Iterating all saved keys / `**saved`** — `_`-prefixed keys belong to the
  prefs layer (`_autostart`) and will break you. Read named keys only.
- **Prompting when `config.autostart_active` is set** — there is no terminal;
  the module hangs. Notify, `event.set()`, return (§24).
- **Prompting when `config.predetermined_input` is non-empty** — it swallows a
  recorded keystroke and desyncs the whole `sequenceRunner` sequence.
- **Hardcoding `Accept-Language` or a locale** — it must match the session's
  region or Gameforge rejects the login. Use `session.accept_language` (§25).
- **Relying on a 404 to raise** — since mod 1.7.5 only Ikariam-host 404s do
  (§25).
- **Writing to `~/.ikabot` in tests** — monkeypatch
  `modulePrefs.MODULE_PREFS_DIR` to a temp dir instead.

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
from ikabot.helpers.modulePrefs import (
    load_prefs, save_prefs, prompt_use_saved, is_autostart, set_autostart,
)
from ikabot.helpers.process import set_child_mode
from ikabot.helpers.signals import setInfoSignal
from ikabot.helpers.varios import wait, addThousandSeparator, getDateTime

_DATA_FILE = os.path.join(IKABOT_DATA_DIR, "my_feature_data.json")


def myFeature(session, event, stdin_fd, predetermined_input):
    sys.stdin = os.fdopen(stdin_fd)
    config.predetermined_input = predetermined_input

    _MODULE = "myFeature"          # prefs key; namespace per flow if needed

    try:
        banner()

        # --- settings memory: offer to replay the last run (see section 23) ---
        saved = load_prefs(session, _MODULE)
        use_saved = False
        if saved:
            try:
                choice = int(saved["choice"])          # validate before trusting
                assert choice in (1,)
                use_saved = prompt_use_saved(session, _MODULE, [f"Option: {choice}"])
            except Exception:
                use_saved = False

        # Auto-start has no terminal — never fall through to the questions.
        if config.autostart_active and not use_saved:
            sendToBot(session, f"{_MODULE}: saved settings invalid, auto-start aborted.")
            event.set()
            return

        if not use_saved:
            # --- interactive configuration ---
            print("(0) Back")
            print("(1) Option A")
            choice = read(min=0, max=1, digit=True)
            if choice == 0:
                event.set()
                return

            save_prefs(session, _MODULE, {"choice": int(choice)})

            if len(config.predetermined_input) == 0 and not is_autostart(session, _MODULE):
                print("\nRun this automatically at login from now on?")
                if read(values=["y", "Y", "n", "N", ""], empty=True,
                        default="n", msg="[y/N]: ").lower() == "y":
                    set_autostart(session, _MODULE, True)

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

## 23. Settings Memory — "repeat last run"

**Kurzon asks for this in almost every module. Build it in from the start.**

`ikabot/helpers/modulePrefs.py` stores a module's last answers per account, so
a repeat run is one keypress instead of re-answering every prompt.

```python
from ikabot.helpers.modulePrefs import (
    load_prefs, save_prefs, clear_prefs, prompt_use_saved,
)

load_prefs(session, name)                 -> dict | None
save_prefs(session, name, prefs)          -> None   # best-effort, never raises
clear_prefs(session, name)                -> None
prompt_use_saved(session, name, lines)    -> bool   # True = replay, False = ask
```

One JSON file per (account, module) at
`IKABOT_DATA_DIR/module_prefs/{username}_{servidor}{mundo}_{name}.json`, so
settings never leak between accounts or worlds.

`prompt_use_saved` prints the summary lines you pass and offers:

```
(1) Use these settings   [just press ENTER]
(2) Reconfigure
(3) Delete saved settings and reconfigure
```

### The rules

1. **Store ids, never list positions.** Menus get re-sorted; a saved index
   silently points at the wrong thing later.
2. **Validate before replaying.** The file is plain JSON on disk and may be
   hand-edited, stale, or from an older version of your module. Wrap the whole
   load in `try/except` and fall back to asking.
3. **Re-resolve against the live account.** Cities get sold, miracles change
   level. Drop what no longer exists, and *tell the user* in the summary.
4. **Never store live game objects** — save an id, re-fetch on replay.
5. **Keys starting with `_` are reserved** by this layer (`_autostart`). Read
   named keys only — never `**saved` or iterate all keys.
6. **Namespace per flow** when a module asks different questions in different
   modes, so one flow's answers do not clobber the other's:
   ```python
   _PREFS_SET         = "tavernManager.set"
   _PREFS_EQUILIBRIUM = "tavernManager.equilibrium"
   ```

### Reference implementations

- `modules/tavernManager_v2.0.1.py` — **best example.** Namespaced per flow,
  validates shape, resolves saved city ids against the account and reports
  how many were dropped.
- `ikabot/function/activateShrine.py` — the built-in equivalent.
- `ikabot/function/activateMiracle.py` — shows schema migration: it reads both
  the old singular `wonder` key and the current `wonders` list.

> `modules/resourceProductionManager_*.py` and `autoRecruitmentManager_*.py`
> predate this helper and roll their own persistence. Do not copy them for new
> work — use `modulePrefs`.

---

## 24. Auto-start — running at login

A module with saved settings can be flagged to launch automatically at login,
replaying those settings silently. Toggled at
**Options / Settings → (10) Auto-start modules**.

**If your module already calls `prompt_use_saved`, it auto-starts for free.**
`prompt_use_saved` returns `True` silently when `config.autostart_active` is
set, so no extra work is needed for the happy path.

Two things you must handle:

```python
# 1. Auto-start has NO TERMINAL. Never fall through to prompts.
if config.autostart_active and not use_saved:
    sendToBot(session, f"{_MODULE}: saved settings invalid, auto-start aborted.")
    event.set()          # mandatory, or the parent waits the full 30s
    return

# 2. Only offer to enable it in the interactive path.
if len(config.predetermined_input) == 0 and not is_autostart(session, _MODULE):
    ...ask, then set_autostart(session, _MODULE, True)
```

Nothing may prompt after `event.set()` — including inside your work loop. A
module that asks questions mid-run cannot auto-start; say so rather than
working around it.

Full details, API and testing recipe: **`docs/AUTOSTART_BRIEF.md`**.

---

### External modules and auto-start *(mod 1.7.7)*

Auto-start originally resolved names against the built-in menu table only,
so enabling it for an **external** module silently did nothing. It now also
matches external modules on the `MODULE_NAME` they declare — which must be
the same name the module saves its settings under, or the auto-start menu
and the launcher will disagree.

Windows **spawns** rather than forks, so a `multiprocessing` target must be
picklable and importable *by name* in the child. A module loaded from a file
path is not in `sys.modules`, so you cannot target one of its functions —
pass the **path** and let `_run_external_module_child` load it, which is why
that indirection exists.

---

## 25. Session Internals a Module Should Know

Behaviour that is easy to get wrong because it is invisible from the call site.

### 404s are no longer always fatal *(upstream #406, mod 1.7.5)*

`session.get()` / `session.post()` only treat a 404 as an expired session when
it comes from the Ikariam host — and in `get()`, only on `index.php`. A 404
from the local web server or an external URL is returned normally. Do not
write code that relies on any 404 raising.

### actionRequest token caching

`Session` caches the `actionRequest` token scraped from the last response
(`_cached_token`), so a POST does not need a separate page fetch. It is
invalidated automatically when the server reports a wrong token. Just use
`actionRequest` from config as normal — but know that a POST may be using a
token from an earlier page.

### Regional context — do not hardcode locale

`session.locale`, `session.gf_lang`, `session.timezone_id` and
`session.accept_language` must stay mutually consistent, or Gameforge rejects
the login. They come from `.env` / `config.IKABOT_LOCALE` or a per-account
region in the vault.

**Never hardcode `Accept-Language` or a locale in a module.** If you build
headers by hand, use `session.accept_language`.

### The vault

Accounts can be stored encrypted with per-account blackbox/lobby tokens and a
per-account region. Relevant to a module only in that `session.mail` and
friends may come from there — never write credentials to your own files.

---

## 26. Resource Reservation System (RRS)

If your module **spends or reserves resources**, integrate RRS so modules do
not double-spend the same wood.

`modules/resourceReservationSystem_v1.0.0.py` is a shared data layer: a
per-account CSV with cross-process locking that tracks which resources in
which cities are reserved by which module.

It **does not** ship resources, retry, sleep, or schedule — all of that stays
in your module.

`autoRecruitmentManager` is the working integration example. Full contract:
**`RRS_INTEGRATION_GUIDE.md`**.

---

---

## 27. Concurrency, Locks and Multi-Instance Safety

Learned the hard way while hardening `resourceTransportManager` (v10.4.1 →
v10.9.0) across Windows and Docker. Every rule below caused a real,
observed failure.

### ⚠ NEVER use `os.kill(pid, 0)` to test if a process is alive

On Windows there is no signal 0. Per the `os.kill` docs, any sig other than
`CTRL_C_EVENT`/`CTRL_BREAK_EVENT` "will cause the process to be
unconditionally killed by the TerminateProcess API". So the liveness *probe*
**terminates the process it is checking**, then returns without raising —
and the caller concludes it is alive.

Symptom: background workers dying whenever any menu screen checked whether
they were running.

```python
import psutil

def _is_pid_alive(pid):
    try:
        proc = psutil.Process(int(pid))
        # A container running ikabot as pid 1 may not reap its children,
        # so a dead worker can linger as a zombie. pid_exists() says yes
        # to those, which keeps a dead lock alive forever.
        if proc.status() == psutil.STATUS_ZOMBIE:
            return False
        return True
    except psutil.NoSuchProcess:
        return False
    except Exception:
        return True     # can't tell — assume alive, never steal a lock
```

`psutil` is already an ikabot dependency (`ikabot/helpers/process.py`).

### A pid is only meaningful inside its own namespace

With Docker — especially several containers sharing one mounted config
volume — a pid read from a lock file written by another container means
nothing locally. It either matches no process (so you **steal a live
lock**, and two workers run at once) or matches an unrelated one (so a
**dead lock is never broken**).

Record who wrote the lock, and only trust the pid when it is yours:

```python
def _instance_id():
    parts = [socket.gethostname()]
    try:
        parts.append(os.readlink("/proc/self/ns/pid"))   # Linux/Docker
    except Exception:
        pass                                             # Windows: hostname only
    return "|".join(parts)

def _holder_liveness(data):
    """True / False / None — None means 'cannot tell, use the heartbeat'."""
    if data.get("host") != _instance_id():
        return None
    ...
```

When you cannot judge the pid, fall back to the heartbeat timestamp, which
is meaningful everywhere.

### File-lock rules that matter

- **`stale_after` MUST be less than `timeout`.** An orphaned lock can only
  be broken after `stale_after`; if that exceeds the wait, every waiter is
  *guaranteed* to fail. A 30s timeout with a 60s staleness window is a
  permanent "could not acquire lock" bug.
- **Match ownership on a token, not a pid.** Threads in one process share a
  pid, so a pid check lets thread A's late release delete thread B's lock.
  Write a unique token (`f"{os.getpid()}-{threading.get_ident()}-{n}"`) at
  acquisition and only remove the file when it still matches.
- **Serialise in-process first.** A per-path `threading.RLock` with a depth
  counter collapses every thread into a single contender for the file lock.
  Removes same-pid races, cuts churn that starves waiters, and makes
  nesting safe.
- **Heartbeat any hold that can outlast `stale_after`.** Otherwise a waiter
  declares a *live* holder stale. A dead holder stops refreshing and still
  ages out normally.
- **Treat a future timestamp as stale.** `now - held_at` is negative if the
  stored time is ahead of the reader's clock (skew, VM resume), so the lock
  never ages out and is unbreakable forever.
- **Give an unreadable lock a grace period (~2s), not instant deletion.**
  `O_CREAT|O_EXCL` creates the file before the payload is written, so an
  empty lock is often one being born. Deleting immediately steals it;
  never deleting means a genuinely corrupt lock blocks everyone forever.
- **Use `time.monotonic()` for timeouts.** An NTP or DST correction
  mid-wait otherwise cuts it short or stretches it enormously.
- **Refresh only what you can prove is yours.** If reading the lock fails,
  do nothing — writing anyway either recreates a lock you no longer hold or
  stamps your name on someone else's.
- **Wrap every `os.remove` in its own try/except.** An exception raised
  inside an `except FileExistsError` handler escapes the whole retry loop,
  and Windows raises when the holder still has the file open.

### Per-instance filenames must include the world

`session.servidor` is the community (`en`); `session.mundo` is the world
number. **A player name is only unique within a world.** Server + username
alone collides when the same name exists on two worlds — both instances
then share one queue, one cache and one set of locks.

```python
def _account_suffix(session):
    return f"{_safe(session.servidor)}{_safe(session.mundo)}_{_safe(session.username)}"
```

This applies to *everything* per-instance: data files, locks, flags, caches
**and logs**. A single shared log across many instances is contention by
design; give each account its own file (see RTM `_account_log_path`).

Shared *preferences* have the same trap: one global "last used path" key
offered whichever value was typed last in **any** account, so pressing
Enter silently attached another account's file. Key remembered paths per
account.

---

## 28. Background Workers and Schedulers

Also from `resourceTransportManager`. If your module runs a long-lived
worker that repeats work on a timer, these are the failure modes that
actually happen.

### Never mark work "done" because a cycle *finished*

A cycle can run start to finish and accomplish nothing — no free ships, no
action points, a blockade. If completion is judged on "the function
returned", a one-shot task is closed as **"done, 0 sent"** and silently
discarded. Judge on what was actually achieved, retry when it was nothing,
and give up loudly after a bound rather than retrying forever.

Equally: **distinguish "the cycle failed" from "the cycle did nothing".**
Catching an exception and returning `0` makes a crash indistinguishable
from a successful empty run. Return a distinct failure signal.

### Recurring work needs its progress reset

If you record per-item progress so an interrupted run can resume, something
must clear it when a run *completes*, or the second cycle finds everything
already done and the task silently becomes one-shot. Distinguish the cases:
nothing pending = last pass finished, start fresh; some pending = last pass
was cut short, resume.

### Schedule the next run from completion, not from tick start

Capturing `now` at the top of the tick and then setting
`next_run = now + interval` means a cycle lasting longer than its own
interval is due again the instant it ends, and runs back-to-back forever.

### Identifiers must be monotonic

`max(existing) + 1` reuses the id of a deleted item. A late write from the
old holder of that id then corrupts the new one. Keep a high-water mark in
a sidecar, and allocate the id **inside** the same lock as the append.

### Guard against implausible timestamps

An absolute `next_run` written while the clock was wrong (VM resume, bad
NTP) and later corrected leaves work dated years out — permanently not due
with no way back. Treat anything beyond a sane horizon (e.g. 30 days) as
due.

### A supervisor must own the lock it runs under

If a restart re-acquires the worker lock, **check the result**. Ignoring it
runs a second worker for the same account when another process won the
race.

### Reporting

- The worker cannot report its own death. Detect it on the *next* start —
  a lock on disk whose holder is gone and no stop was requested — and say
  so then.
- A process killed outright leaves no trace; supervise the loop in-process
  for crashes, and use auto-start for reboots.
- "RUNNING" is ambiguous once instances share a config volume: say *who*
  holds the lock (pid, and which host), or a worker alive in another
  container reads as a phantom.
- Report rather than act when you cannot verify: silently clearing an
  unverifiable lock risks killing a live worker elsewhere.
- Strict priority means low-priority work can starve indefinitely. That may
  be the intended rule — report it rather than silently overriding it.

### Hold, don't block, on a busy shared resource

A trading port loads **one shipment at a time**. Send a second one while it is
loading and it queues behind the first, so the sender either sits there for
the whole loading time or gets rejected. The same shape shows up anywhere a
city-level resource is serialised.

Blocking is the wrong answer — it burns the cycle deadline on one order while
every other city sits idle. Instead:

1. **Ask before committing.** The transport view carries `queueTime`, an
   absolute epoch for when the port is next free (`getTransportLoadingAndTravelTime`
   in `getJson.py` reads it). 0 or in the past means free.
2. **Unknown is not busy.** If the value is missing, unreadable, or absurdly
   far out, treat the port as free. A detection you cannot trust must degrade
   to today's behaviour, never to a hold.
3. **Record it per city**, so the other twenty orders out of that city cost no
   further requests until the hold expires.
4. **Hold the order and start the next one.** Nothing is lost — the resources
   never left the source city.
5. **Come back in priority-then-age order.** Stable-sorting the held queue on
   priority alone gives exactly that: the queue is already in age order, so
   the sort only reshuffles bands. Don't add a timestamp key you then have to
   keep correct.
6. **Wake on the shortest hold**, not on a fixed retry interval — a
   two-minute loading queue should not cost a five-minute sleep.

Also: after *you* dispatch a shipment, that port is now busy loading yours.
Invalidate whatever you cached about it rather than trusting a reading taken
before you queued work on it.

### Testing module internals without importing ikabot

External modules import the whole ikabot stack, which makes them awkward to
unit-test. Extract just the functions under test with `ast` and exec them
into a namespace of stubs:

```python
tree = ast.parse(open("modules/myModule_v1.0.0.py").read())
keep = [n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name in WANTED]
ns = {"os": os, "json": json, "time": time, ...stubs...}
exec(compile(ast.Module(body=keep, type_ignores=[]), "m", "exec"), ns)
```

Run the same tests against the **old** code to prove the diagnosis, not
just the fix. Several times here a test "failure" was the harness missing a
newly added global — always confirm which side is wrong before believing a
red result.

---


---

## 29. Building Costs, Game Data and Queue Semantics

*Learned building `constructionManager` v2.1.7 → v2.2.8. For locks, worker
supervision and multi-instance safety see §27–28 — this section is the
game-data and queue-behaviour half.*

### 29.1 Reading building costs from the ikipedia

The in-game help ("ikipedia") holds the per-level cost table for every
building. **Request the building's detail view directly:**

```python
cost_url = (
    "view=buildingDetail&buildingId={bid}&helpId=1"
    "&backgroundView=city&currentCityId={cid}"
    "&templateView=buildingDetail&actionRequest={ar}&ajax=1"
).format(bid=building_id, cid=city["id"], ar=actionRequest)
html_costs = json.loads(session.post(cost_url), strict=False)[1][1][1]
```

`helpId=1` is a **constant** for every building — it is not derived from
`buildingId`.

**Do not scrape the listing page for the building links.** The old approach
loaded `view=ikipedia&helpId=0` and regexed `class="button_building <slug>"`.
Those icons are rendered client-side by JS, so the XHR body contains **zero**
`button_building` matches and the scrape can never succeed, no matter how the
regex is patched. Use the map below.

**slug → buildingId** (verified against a live account; keys lowercased
because city JSON uses camelCase like `townHall`):

```
townhall 0    port 3        academy 4      shipyard 5     barracks 6
warehouse 7   wall 8        tavern 9       museum 10      palace 11
embassy 12    branchoffice 13              workshop 15    safehouse 16
palacecolony 17             forester 18    stonemason 19  glassblowing 20
winegrower 21 alchemist 22  carpentering 23              architect 24
optician 25   vineyard 26   fireworker 27  temple 28      dump 29
piratefortress 30           blackmarket 31 marinechartarchive 32
dockyard 33   shrineofolympus 34          chronosforge 35
```

IDs **1, 2 and 14 return HTTP 500 — they do not exist.** `forester` (18) is
**absent from the ikipedia grid** but its detail page works when requested
directly; it was found by probing the gaps. When probing by hand, note that a
failed request leaves the previous panel in place, so a stale panel reads as a
false positive — clear the network log per call.

### 29.2 Cost table columns are identified by image hash, not filename

Header cells carry no text identifier at all:

```html
<th class="costs"><img src="//gf2.geo.gfsrv.net/cdn19/c3527b2f694fb882563c04df6d8972.png"></th>
```

No `alt`, no `title`, and the filename is an opaque MD5. Data rows are equally
generic — every cost cell is just `<td class="costs">`. Reconstruct the full
hash from the CDN path (2-char `cdnXX` prefix + 30-char filename = 32-char MD5)
and match it against `config.material_img_hash`:

```python
th_srcs = re.findall(r'<th class="costs"><img src="(.*?)"', html_costs)
for src in th_srcs[:-1]:          # last <th class="costs"> is the time icon
    m = re.search(r'/cdn([0-9a-f]{2})/([0-9a-f]+)\.png', src, re.IGNORECASE)
    idx = material_img_hash.index(m.group(1) + m.group(2)) if m else -1
```

`material_img_hash` is ordered `materials_names_tec` = wood, wine, marble,
glass(=crystal), sulfur. **Never assume column position** — a building only
renders columns for resources it actually costs, so a barracks table is not a
prefix of a town hall table.

### 29.3 City slot data (`getCity`)

`getCity()` post-processes each entry of `city["position"]`:

- `position["position"]` — the slot index, added by ikabot (safe to read)
- `position["isBusy"]` — True when the raw building string contained
  `constructionSite`; the suffix is then stripped, so `building` stays the
  plain slug
- empty slots become `building == "empty"`, `name == "empty"`
- `position["canUpgrade"]` — **the game's own gate.** False means the POST
  will be refused (citizens, wine/happiness, or its resource check). Check it
  before spending a request.
- a busy slot carries `completed` (unix timestamp)

**One build per city at a time.** If any slot is busy, a build POST for a
different slot is refused — check for a busy slot first.

### 29.4 Distinguish transient failure from permanent absence

The worst bug in this module: a cost helper returned `{}` both when a building
genuinely had no data **and** when the lookup failed (request error, unexpected
response, parse error). The caller treated both as "no data" and cancelled the
queued row. One network blip permanently killed queued work — and the next tick
did the same to the next row, so a city's queue drained into `skipped` while
other cities kept running. The symptom reported was "the scheduler is on but
some cities never build".

**Rule: a helper must let callers tell "nothing there" from "I could not
look".** Return `None` for a failed lookup and `{}` for a genuine absence (or
raise). Retry the first; only cancel on the second.

The same discipline applies to actions:

- a POST that fails to *send* — retry; do not cancel. Re-check state on the
  next tick instead (it can adopt the action if it did land, so no double-fire).
- an action that does not *appear* to have started — that is also what a slow
  server looks like. Retry a bounded number of times before cancelling.
- give the user a bulk **"retry cancelled items"** action. Anything
  auto-cancelled is otherwise unrecoverable, and re-entering it by hand is the
  thing they will ask for next.

### 29.5 Long-running queues must reconcile with manual play

The user still plays the game by hand. A queue that stores one row per level
and executes each as "do one upgrade" will overshoot: build two levels
manually, and the queue's remaining rows push the building **past** the
requested target. Before acting, drop queued items the live city has already
reached, so the next item is always current + 1 — that also keeps cost lookups
and shipped amounts correct. Mark items whose slot now holds a *different*
building as skipped-with-a-note rather than deleting them silently.

### 29.6 Prompt and table gotchas

- **`read()` re-asks silently on out-of-range input.** It erases the line and
  recurses, printing nothing. A user typing a value your `min=`/`max=` rejects
  sees the prompt blink and concludes the module is broken. State the accepted
  range in the prompt text, and accept a sentinel for "no change" rather than
  refusing it (e.g. allow the current level to mean *skip this one*).
- `read()` returns an `int` for digit input and the raw `str` for anything in
  `additionalValues`; `additionalValues` is matched **before** digit
  validation, and matching is exact — include every case variant you accept.
- **`getDateTime()` returns `YYYY-mm-dd_HH-MM-SS`**, so the common
  `getDateTime(ts)[8:]` is **11 characters** (`dd_HH-MM-SS`). Size table
  columns accordingly.
- Collapse repetitive rows in list views. A build-to-level request stores one
  row per level; showing `5 → 10` on one line instead of six rows cut a sample
  queue from 52 lines to 28. Keep a detail toggle rather than deleting the
  verbose view.
- When a scheduler defers work, **write the reason where the user will see
  it.** "Pending with no ETA" and no explanation is indistinguishable from a
  broken scheduler. Prefix worker-written notes (e.g. `waiting: `) so they can
  be replaced and cleared without ever overwriting a note the user typed, and
  only rewrite when the text changes so a long wait does not churn the file.

---

*Last updated: 2026-09-06. Reflects ikabot 7.4.5 / mod v1.7.7.*
