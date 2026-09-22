# IkaEasy — Roadmap to a Full In-Web-Server Copy

> **Goal:** run **every** feature of the IkaEasy extension inside the ikabot web
> server, so the whole extension works in any browser with no Chrome extension.
> The extension is abandoned (the author's email account is closed), so there is
> no update to track and no support to rely on. This fork now owns it.

---

## 1. The decision: full mode, not 57 rewrites

There are two ways to bring the extension into the web server.

- **Full mode** — ikabot serves the real, unmodified extension files. A small
  shim gives the extension the Chrome APIs it expects. This *is* a full copy,
  because it runs the actual code. One code base to maintain.
- **Lite rewrite** — reimplement each feature in plain page code. This is a
  reimplementation, not a copy. The extension has 57 view modules, 5 empire
  tabs and 16 helpers. Rewriting all of them takes far longer and drifts from
  the original.

**We take full mode.** Lite stays as a safe, small fallback for the few panels
that are already stable, and for any view full mode cannot render.

---

## 2. What exists today (done)

- The web server serves the whole extension at `/ikaeasy-full/*`
  (`ikabot/helpers/ikaEasyFull.py`), with path-traversal protection.
- A page-world `chrome.*` shim exists (`ikaeasy_lite/full/chrome-shim.js`):
  `runtime.getURL`, `sendMessage`, `getManifest`, `storage.local` on
  `localStorage`, and stubs for the background-only APIs.
- A boot entry (`ikaeasy_lite/full/full-mode.js`) loads the shim, the manifest,
  the libraries, then the extension's own `initModule.js`.
- Full mode is opt-in: add `?ikaeasy_full=1` to the URL. The control button is
  an escape hatch back to lite.
- The extension marks the page, so full mode and the installed extension never
  run at the same time.

Full mode is **not proven in a browser yet.** The phases below make it work,
one testable step at a time.

---

## 3. Phases

Each phase has a goal, the work, the test that proves it, and the risk it
clears. Ship each phase on its own. Bump the bundle version each time; Kurzon
sets the number.

### Phase A — Boot without crashing
**Goal:** the extension loads in the page and shows its UI on at least one view.

**Work**
1. Fix the library clash. The game page already has its own jQuery. The
   extension needs its own jQuery, lodash and moment. Load the extension copies
   in a private scope, or call `jQuery.noConflict(true)` and hand the private
   copy to the extension. Neither the game nor the extension may lose its `$`.
2. Fix the template sandbox. The extension compiles EJS templates inside
   `sandbox.html` in an iframe. Serve that page same-origin from ikabot. If the
   game page content-security-policy blocks the compile, send a relaxed CSP
   header for `/ikaeasy-full/sandbox.html` only.
3. Confirm the boot chain runs: `initModule` → sandbox `ready` → `navigation`.

**Test:** open a city with `?ikaeasy_full=1`. The console shows no fatal error.
The IkaEasy panels appear on the town hall.

**Risk cleared:** the two unknowns that block everything — library clash and
template compile.

### Phase B — Real data on every view
**Goal:** the extension reads live game state and fills its panels.

**Work**
1. Confirm the page hook (`inner/ikaeasy.js`) reads `ikariam.model` and posts
   the data. In full mode everything is one page world, so the message bridge
   still works.
2. Confirm storage reads and writes through the shim.
3. Walk the common views: town hall, tavern, port, transport, warehouse,
   barracks, shipyard, academy, museum, temple, palace, safehouse.

**Test:** each view shows correct numbers. No missing-data errors in the
console.

**Risk cleared:** the data pipeline that every feature depends on.

### Phase C — Options and settings
**Goal:** the IkaEasy options screen works and its toggles take effect.

**Work**
1. Make the options views render (`options`, `optionsAccount`,
   `optionsNotification`, `optionsIPSharing`) and the settings sandbox load.
2. Confirm each toggle saves to `localStorage` and changes behaviour.

**Test:** change a setting, reload, confirm it stuck and changed the UI.

**Risk cleared:** users can turn features on and off, as in the real extension.

### Phase D — Network features and dead-author cleanup
**Goal:** features that call outside servers work, or fail cleanly. Remove
features that point at the abandoned project.

**Work**
1. ikalogs.ru: the extension called it through its background worker to avoid
   the browser's cross-origin block. Add ikabot bridge endpoints that forward
   these requests server-side.
2. Remove or disable dead features that reach the abandoned author:
   - the support form (emails a closed account),
   - the "what's new" fetch (points at the author's server),
   - the extension's own update check and any Patreon or donate links.
3. Keep diplomacy and message-send features, which talk to the game itself.

**Test:** ikalogs sync works or shows a clear message. No feature tries the
dead email or update server.

**Risk cleared:** cross-origin blocks, and dead endpoints that hang or error.

### Phase E — Full view coverage
**Goal:** all 57 view modules work, plus the four background modules (city,
island, world map) and the five empire overlay tabs.

**Work**
1. Walk every view and every empire tab in order. Fix each breakage.
2. Confirm the Empire View resources tab renders in full — the one lite only
   approximates.
3. Confirm the world map and island overlays draw their markers.

**Test:** a written checklist, one line per view, each marked pass. No console
error on any view.

**Risk cleared:** the long tail of view-specific bugs.

### Phase F — Make full mode the default
**Goal:** full mode is stable enough to be the normal experience.

**Work**
1. Turn full mode on by default. Keep lite as the fallback and keep the escape
   hatch button.
2. Show the full-mode version in the on-screen badge.
3. Confirm the editable-without-rebuild path works for the full bundle
   (`~/.ikabot/ikaeasy_full`).
4. Update `docs/IKAEASY_INSTALL.md`.

**Test:** a fresh account, no extension, sees the full extension through the web
server, on every device Kurzon uses.

**Risk cleared:** the last step from "works when I force it on" to "just works".

---

## 4. Bug to fix on the way

`ikabot/helpers/ikaEasyBridge.py` builds its per-account file name from server +
username only. The real modules use server + **world** + username (§27 of the
technical reference). So the bridge reads the wrong Construction CSV and writes
the wrong RTM CSV on a normal account. Fix the suffix to match the modules,
with the legacy fallback, during Phase D (bridge hardening).

---

## 5. Effort and order

- **Phase A** is the hard gate. Until the library clash and the template
  sandbox work, nothing else can. Do it first, and test it in a browser before
  building further.
- **Phases B and C** are steady work with clear tests.
- **Phase D** is small but removes dead weight.
- **Phase E** is the largest by volume but the lowest risk — it is a long list
  of small fixes, each independent.
- **Phase F** flips the switch.

Full mode reuses one code base, so most of the effort is integration and
testing, not new feature code. Kurzon's browser extension can capture the live
page when a view misbehaves, which is the fastest way to fix Phase E items.

---

*Full mode is the vehicle. Lite is the fallback. The extension is abandoned, so
this fork is now its home.*

---

## 6. Crossovers — reuse ikabot instead of re-fetching

ikabot already gathers most of the game state IkaEasy shows. Reuse it. This
avoids a second fetch, keeps the numbers the same as ikabot's own screens, and
keeps the logic in one place.

**Already applied**

- **`getStatus.collectData(session)`** — one scan of the account. It returns
  every city's resources, production, wine consumption, storage, gold, ships,
  and buildings, plus totals. The resources overview now uses this instead of
  its own city fetch. `getStatus.cityProduction(data, cid)` gives the hourly
  wood and luxury production per city from the same scan.

**To apply as full mode grows**

| ikabot capability | Where it lives | IkaEasy feature it serves |
|---|---|---|
| `collectData` totals + `cityProduction` | `function/getStatus.py` | Empire resources tab: production, wine, gold, ships, storage — not just amounts |
| `getWineConsumptionPerHour` + `wineConsumptionPerHour` | `helpers/resources.py`, city dict | Tavern: hours of wine left, wine countdown across cities |
| `getProductionPerHour` | `helpers/resources.py` | Construction time-to-start, read on the server instead of the browser model |
| `getTransportLoadingAndTravelTime` | `helpers/getJson.py` | Transport/RTM: port busy time and travel time; hold, do not block (§28) |
| `naval.getAvailableShips` / `getAvailableFreighters` | `helpers/naval.py` | Transport/RTM: show free ships before sending |
| `planRoutes.executeRoutes` | `helpers/planRoutes.py` | Send resources now, not only schedule them |
| `market` helpers | `helpers/market.py` | Marketplace panels: prices and offers |
| `updateProcessList` | `helpers/process.py` | Show running ikabot tasks in the page (already used for processes) |
| `getIdsOfCities` | `helpers/pedirInfo.py` | Any all-cities panel (already used) |

**Rule:** before a bridge endpoint fetches game data itself, check
`helpers/` and `function/getStatus.py` for a function that already gathers it.

