# Implementation Plan - resourceTransportManager v5

Target file: Create new `resourceTransportManager.py` (overwrite existing) based on v4 file.
Branch: `claude/review-transport-manager-jOuee`

---

## PHASE 1: Shared Helper Functions (extract from duplicated code)

### 1A. `wait_for_ships(session, useFreighters, status_prefix, max_wait=3600)`
- Extract the ship-waiting loop used in all 5 modes
- ALWAYS has a timeout (default 1h) — fixes Critical Bug #2 (infinite wait)
- Returns `available_ships` count (0 if timed out)
- Updates session status while waiting

### 1B. `send_shipment(session, route, useFreighters, telegram_enabled, status_prefix)`
- Combines: wait_for_ships → acquire_lock → verify_ships → executeRoutes → verify → release_lock → notify
- Returns dict: `{success: bool, error: str|None, ships_used: int}`
- Has proper try/finally so lock is ALWAYS released — fixes Critical Bug #4 (lock leak on `continue`)
- Retry logic for lock acquisition (3 attempts) built in
- Ship verification before and after built in

### 1C. `get_notification_config(telegram_enabled, event)`
- Shared notification prompt used by all modes
- Returns a simple dict: `{"level": "all"|"partial"|"none"}` — fixes Significant Bug #6 (confusing None/True/False)
- Replaces the overloaded `telegram_enabled` variable with clean semantics
- `should_notify(config, event_type)` helper: event_type in ("start", "each", "error", "complete")

### 1D. `schedule_loop(callback, interval_hours, session, status_prefix)`
- Proper scheduling: calculates `next_run = now + interval`, sleeps exactly `(next_run - now).total_seconds()` — fixes Critical Bug #3 (timer drift)
- For one-time (interval=0): calls callback once and returns
- For recurring: loop with accurate sleep

### 1E. Fix `acquire_shipping_lock` race condition — Critical Bug #1
- Use `os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)` for atomic file creation
- This is a single OS call that fails if file exists — no race window
- Keep stale lock detection (10 min) and retry logic

### 1F. Shipment Log System (new feature #6)
- `log_shipment(session, log_dir, source, destination, resources, status, next_shipment=None)`
- CSV file per account: `shipment_log_{username}.csv`
- Stored at user-specified path or same dir as bulkdistribution.csv (ask once at startup, store in config)
- Columns: Date, Time, Account, Mode, Source_City, Source_Island, Dest_City, Dest_Island, Dest_Player, Wood, Wine, Marble, Crystal, Sulphur, Total_Resources, Ships_Used, Ship_Type, Status, Error, Next_Shipment
- Append-only (open in "a" mode), create with header if file doesn't exist
- Called from `send_shipment()` so ALL modes automatically log

---

## PHASE 2: Critical & Significant Bug Fixes

### 2A. Fix bare `except:` clauses (Minor #12)
- In `acquire_shipping_lock` lines 120, 122: change to `except Exception:`
- In `release_shipping_lock` lines 155, 157: change to `except Exception:`

### 2B. Fix even distribution empty list crash — Significant Bug #7
- In `do_even_distribution`: check `if not senders or not receivers:` at the top
- Send "already balanced" notification and return early
- Same check in `evenDistributionMode` before calling `do_even_distribution`

### 2C. Fix `wait()` undefined — Significant Bug #9
- Replace `wait(3600)` and `wait(interval_hours * 3600)` with `time.sleep()`
- There are 3 occurrences in `do_it_mass_distribution` at lines 2464, 2476, 2678

### 2D. Fix origin_city variable overwrite — Significant Bug #10
- In `do_it` line 1364: rename to `origin_city_data = getCity(html)` and use that for the rest of the iteration
- Same pattern in `do_it_distribute` for `destination_city` at line 1621

### 2E. `getShipCapacity` — add try/except with clear error (Minor #15)
- Wrap the HTML parsing in try/except, raise with "Could not read ship capacity from game server"

---

## PHASE 3: New Features

### 3A. Dry Run / Preview Mode
- At every final "Proceed? [Y/n]" confirmation, add option: `(D) Dry run - preview what would be sent`
- Dry run: runs through all the calculation logic, displays a full summary table of every shipment that WOULD happen, but doesn't call `executeRoutes`
- For consolidate: show per-city breakdown of what would be sent
- For distribute: show per-destination what they'd receive
- For even dist: show the full sender→receiver plan
- For auto send: already has review screen, add "(D) Dry run" there too
- For mass dist: scan CSV, show all matches and what would ship
- After dry run display, offer: "(Y) Proceed for real | (N) Cancel"

### 3B. Resource Minimums for Internal Destinations
- In `consolidateMode` and `distributeMode`, after destination is selected, if destination is internal (own city):
  - Ask: "Set minimum thresholds for destination? (only send if destination has less than X) [y/N]"
  - If yes, prompt for each resource: "Minimum {resource} (send only if dest has less than this):"
  - Store as `dest_minimums` list
- In `do_it` and `do_it_distribute`, when calculating `sendable`:
  - Check `destination_city['availableResources'][i]` against `dest_minimums[i]`
  - If dest already has >= minimum, set sendable = 0 for that resource
  - If dest has less than minimum, send only enough to reach the minimum: `sendable = min(sendable, minimum - dest_current)`
- Default: None (no minimums, behaves as before)

### 3C. Multi-Resource Even Distribution
- Replace single `resource_choice` with multi-select
- Change prompt: "Select resources to balance (comma-separated, e.g. 1,3,5):"
- Parse comma-separated input into list of resource indices
- For each selected resource, calculate target_per_city independently
- Build combined shipment plan: each sender→receiver route carries ALL selected resources at once
- This is more complex: need to match senders/receivers per resource and merge routes
- Approach:
  1. Calculate per-resource: who sends what, who receives what
  2. For each unique (sender_city, receiver_city) pair across all resources, combine into one route
  3. Display combined plan, execute combined routes
- Fallback: if merging is too complex, just run each resource sequentially (simpler, still saves user effort)
- Go with sequential approach — simpler, still eliminates the "run the mode 5 times" problem

### 3D. Shipment Log Implementation
- At module startup (`resourceTransportManager` main entry), ask for log location:
  - "Shipment log location (press Enter for default: {csv_dir}/shipment_log_{username}.csv):"
  - Store path, pass to all mode functions
- Integrate `log_shipment()` call in `send_shipment()` helper
- Log both successes and failures

---

## PHASE 4: Menu Streamlining (keep confirmations, reduce clutter)

### 4A. Combine resource display with resource input
- Currently shows available resources table, then asks for each resource separately
- Instead: show current amount inline with each prompt: `Wood (available: 45,000): `
- Saves several print lines per mode

### 4B. Compact island legend
- Replace `"Island Luxury: (W) Wine | (M) Marble | (C) Crystal | (S) Sulfur"` — this is shown via chooseCity already, remove the redundant print before calling it

### 4C. Inline mode descriptions
- Instead of separate banner + description prints, put description in the banner subtitle
- Already partially done, just be consistent

---

## PHASE 5: Implementation Order

Working in the v4 file, produce new clean file:

1. Copy v4 as base → new `resourceTransportManager.py`
2. Add imports: `csv` (already there), `math` (already there), `fcntl` (for atomic lock on Linux)
3. Write helper functions (1A-1F) at top of file after existing helpers
4. Rewrite `acquire_shipping_lock` with atomic create (1E)
5. Fix bare excepts (2A)
6. Fix `wait()` calls (2C)
7. Add `log_shipment` function and log_path setup in main entry (3D, 1F)
8. Rewrite `do_it` to use `send_shipment` helper + `schedule_loop` (1B, 1D)
9. Rewrite `do_it_distribute` same way
10. Rewrite `do_it_auto_send` same way
11. Rewrite `do_even_distribution` same way + fix empty list (2B)
12. Rewrite `do_it_mass_distribution` same way + fix wait() (2C)
13. Add dry run option to all final confirmations (3A)
14. Add resource minimums to consolidate + distribute (3B)
15. Modify evenDistributionMode for multi-resource (3C)
16. Apply menu streamlining (4A-4C)
17. Fix variable overwrites (2D)
18. Test by reading through all code paths mentally

---

## Key Constraints
- Keep ALL existing confirmation prompts (user request)
- Keep all 5 modes and their general flow
- File stays self-contained (single .py file)
- Must work with existing ikabot imports
- `fcntl` may not be available on Windows — use try/import with fallback to current method
- Don't break CSV format compatibility with existing bulkdistribution.csv files
