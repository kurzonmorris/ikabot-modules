# Ikabot — Modded by Kurzon v0.9.4
### Based on Ikabot 7.4.0 (Ikabot-Collective/ikabot)

---

## Changes vs. Original Ikabot

### Upstream 7.4.0 Changes Integrated

**Human-like wait jitter (`varios.py`)**
- `wait()` now uses a log-normal distribution for random delays instead of uniform random, producing more natural human-like timing patterns.

**French locale number parsing fix (`attackBarbarians.py`, `constructBuilding.py`)**
- Resource and gold values now strip non-breaking spaces (`\xa0`) and regular spaces in addition to commas, fixing parsing on French-locale game servers.

**Set Academy workers (`modifyProduction.py`)**
- New `modifyAcademyWorkers()` function sets the scientist percentage for academy buildings across selected cities.
- Rate-limiting `wait(3,4)` added between city iterations to avoid API flooding.
- Accessible via `(23) City Management → (2) Set Academy workers`.

**Reorganize city buildings (`reorganizeCityBuildings.py`)**
- New function: select a template city and apply its building layout to other cities automatically.
- Simulates realistic drag-and-drop delays between moves.
- Accessible via `(23) City Management → (3) Reorganize city buildings`.

**Dockyard building support (`constructBuilding.py`)**
- `"dockyard"` added as a valid space type when listing buildable buildings.
- Building list now colour-coded: green = can afford, red = insufficient resources.
- Missing resource details shown when an unaffordable building is selected.

**Combined resource shipments + rounding (`constructionList.py`)**
- When transporting resources for a building upgrade, resources from the same origin city are now batched into a single shipment instead of one per resource type.
- Optional "round up" prompt rounds transport amounts to reduce trips.

**URL module download (`loadCustomModule.py`)**
- Custom modules can now be loaded by pasting an HTTPS URL; the file is downloaded to a local `custom/` subfolder automatically.
- File existence is validated before the module is added to the session.

**Inventory-aware donations (`donate.py`, `donationBot.py`, `getJson.py`)**
- `getInventory()` and `getInventoryItem()` added to retrieve player inventory.
- Donation amounts now include wood held in the player's inventory (item 2201), not just city storage.
- `donationBot` verifies each donation was accepted before crediting it to the total; rejected donations (e.g., building upgrading) are retried cleanly.

**CRNN pirate captcha model (`piratesDecaptcha.py`)**
- Replaced YOLOv8 object-detection model with a CRNN + CTC model (`ikaptcha.onnx`).
- Accuracy improves from ~81% to ~97%.
- `SuppressStderr` context manager suppresses onnxruntime GPU-discovery log spam on startup.

---

## Fork-Specific Changes vs. Original Ikabot

**Self-hosted API failover (`apiComm.py`)**
- ikabot depends on one public server for blackbox login tokens; when it is
  down, no account can log in. `IKABOT_API_FALLBACK` takes a comma-separated
  list of your own API servers, tried in order after the public one whenever a
  request to it fails — refused, timed out, or an error response.
- `IKABOT_API_KEY` is sent as `X-API-Key` to those servers only, never to the
  public server.
- `IKABOT_API_TIMEOUT` (default 120s) bounds each server's attempt. It was
  previously 900s, which meant a hung server blocked a login for fifteen
  minutes with nothing to fail over to.
- `(2108) Developer` now reports the fallback addresses, whether a key is set,
  and the timeout.
- `docker/ikabot-api/` deploys the upstream API on Unraid behind an API-key
  gate and a Cloudflare Tunnel, so no inbound port is opened. Setup in
  `docs/SELF_HOSTED_API.md`.
- The bundled `SupportedUserAgents.json` adds this fork's user agents, none of
  which appear in the upstream server's list — against the public server every
  token is therefore minted with a random agent and the default region while
  the login presents ikabot's own, the mismatch upstream #414/#416 set out to
  remove.

**Encrypted Credential Vault**
- Stores game account credentials (email, password, blackbox token, lobby cookie) encrypted on disk using AES-256-GCM with a PBKDF2 master password.
- Wrong master password is detected before showing the account list.
- Vault is stored alongside sessions and logs in `%APPDATA%\.ikabot\` (Windows) or `~/.ikabot/` (Linux) instead of the home directory root.
- Existing vault at the old location (`~/.ikabot_vault`) is migrated automatically on first run.
- Safe for concurrent use — PID-based lock file prevents corruption when multiple instances open simultaneously.

**Sequence Runner (External Module — WIP)**
- External module that stores named input sequences and replays them automatically against the ikabot menu, replacing the need for external AutoHotkey scripts.

### Bug Fixes

**Session file locking**
- PID-based stale lock detection: lock files left by crashed processes are cleaned up automatically on startup instead of causing a `TimeoutError`.

**Windows credential input**
- `getpass` on Windows includes a trailing `\r`; all password prompts now strip `\r\n` to prevent transient "wrong password" / vault corruption errors.

**Web Server**
- Added `SIGINT` ignore and `use_reloader=False` to prevent crashes on shutdown.
- Fixed `AttributeError: 'str' object has no attribute 'text'` when a wrong request ID retry occurred — all original parameters (`fullResponse`, `noQuery`, etc.) are now forwarded correctly.

**Discord webhook validation**
- Extended accepted webhook URL prefixes to cover `discordapp.com`, `ptb.discord.com`, and `canary.discord.com` in addition to `discord.com`.

**Session PermissionError (Windows)**
- Retry loop (20 attempts, 50 ms gap) on `os.replace()` for session files, preventing `WinError 32` when a concurrent reader briefly holds the file.

**Marketplace offer parsing (PR #380 — unreleased upstream)**
- Switched from a single complex regex to row-by-row parsing for buy/sell marketplace offers.
- Strips both `.` and `,` as thousand separators so locale-specific number formats are handled correctly.
- Buy offers are now sorted cheapest-first; sell offers highest-first.

**Building upgrade retry (PR #383 — unreleased upstream)**
- If a building upgrade request does not result in the building becoming busy, the bot retries up to 3 times with a random 10–20 second wait between attempts before giving up, with a Telegram notification on each failure.

**Blackbox token endpoint fallback (PR #405 — unreleased upstream)**
- If the token API returns HTTP 400 "Unsupported user_agent", the request is automatically retried without the `user_agent` parameter.

### New Functions (Unreleased Upstream PRs)

**Monitor Inactive Players in Radius (PR #386)**
- Scans all islands within a configurable radius around a selected city for players in inactive status.
- Supports luxury resource filtering (Wine / Marble / Crystal / Sulfur) and optional Telegram notifications.
- Accessible via `(20) Dump / Monitor world → (3) Monitor inactive players in radius`.

**In-Game Message Alerts (PR #389)**
- Polls the in-game mailbox at a configurable interval and sends Telegram alerts for new player messages and/or combat reports.
- Includes battlefield details, military movement tracking, and deduplication so already-seen messages are not re-alerted.
- Accessible via `(7) Alerts / Notifications → (3) Alert in-game messages`.

**Send Cultural Treaty Requests (PR #398)**
- Automatically sends cultural treaty requests (msgType=77) to nearby players who do not already have a treaty.
- Skips players with existing confirmed treaties or pending museum requests.
- Respects server rate limit of 5 outgoing messages per 5 minutes, reserving one slot for manual use.
- Accessible via `(25) Send cultural treaty requests`.

**Account status rewritten (`getStatus.py`)**
- The account is now read in a single pass (two requests per city) and cached per account for 10 minutes, so browsing the status screens costs no further requests. Stale caches are re-read automatically; `(3) Refresh data` forces a re-read.
- New menu: `(1) Building levels` shows every city's buildings as a width-aware table or as a per-city list with slot positions, `(2) City details` opens any city without re-scanning the account.
- Shows pirate fortress capture points and crew strength when a fortress is present, reading it once after the scan instead of per city.
- Per-city production is now parsed from the city page that was already downloaded, removing one request per city view.
- Gold production tolerates servers that omit `godGoldResult` / `badTaxAccountant`.
- Note: the sub-menu changes the keystrokes for option `(4)`, so Sequence Runner sequences that include Account status need re-recording.

### Distribution / Build

- PyInstaller spec updated to onedir format (`ikabot.exe` + `_internal/` folder).
- Added `dotenv` to PyInstaller `hiddenimports` to prevent startup crash.
- `GUIDE.md` added — end-user guide covering setup, all menu options, external modules, proxy configuration, and utility scripts.
