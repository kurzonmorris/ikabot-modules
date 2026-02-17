# Spy Tool Roadmap Plan

## Goal
Implement a phased upgrade path for `spyTool.py` inside ikabot to support:
1. Hideout/spy-capacity auditing across your empire.
2. Semi-automated offensive spy dispatch based on target report files and success/risk optimization.
3. Mission execution workflow choices.
4. Persistent autonomous spying loops with report updates.

---

## Architecture Strategy (applies to all phases)

### A. Keep feature boundaries clean
- Add a **new top-level submenu tree** under Spy Tool:
  - `(5) Empire Spy Capacity Audit` (Phase 1)
  - `(6) Targeted Spy Dispatch from Report` (Phase 2)
  - `(7) Mission Planner & Execution` (Phase 3)
  - `(8) Persistent Spy Ops` (Phase 4)
- Keep existing player intel scan/export code intact; integrate with shared helpers only.

### B. Introduce focused helper layers
- **Data collection helpers** (game page/API parsing).
- **Computation helpers** (capacity, out-of-city, defender estimates, success/risk scoring).
- **Action helpers** (dispatch spies / confirm / dry-run).
- **Persistence helpers** (load reports, write operation logs/results).

### C. Safety and control defaults
- Every action that spends resources or sends units must support:
  - **Preview mode (default)**
  - **Confirmation prompt**
  - **Abort path**
- Batch operations should support `single / selected / all` selections.

### D. Testability hooks
- Keep formula code pure and isolated to allow fast local tests.
- Add debug logging around every dispatch calculation and send result.

---

## Phase 1 — Empire Hideout & Spy Capacity Audit

## User outcome
Scan all your cities and generate a report showing per city:
- Hideout level
- Registered spies (total assigned to city)
- Spies currently in city
- Spies currently out of city
- Free slots remaining for more spies

## Implementation tasks

1. **City traversal and hideout detection**
   - For each owned city ID (`getIdsOfCities(session)`):
     - Open city page / building data and locate hideout building.
     - Extract hideout level and any spy counters available.

2. **Spy count extraction**
   - Parse hideout view/action responses to capture:
     - Total spies registered in that city.
     - Spies currently at home.
     - Spies currently on assignments / away.
   - If game only returns derived numbers, compute missing values deterministically.

3. **Capacity computation**
   - Determine maximum spy capacity for each hideout level from game data/rules.
   - Compute:
     - `spies_out = registered - in_city`
     - `free_slots = max_capacity - registered`

4. **Report generation**
   - Terminal table summary.
   - JSON export to storage folder, e.g. `spy_capacity_audit_<timestamp>.json`.
   - HTML export (similar style to existing reports), e.g. `spy_capacity_audit_<timestamp>.html`.

5. **Phase 1 menu integration**
   - Add menu action to run audit and then:
     - View summary
     - Export
     - Return

## Validation checks (Phase 1)
- Cross-check at least 1 city manually vs in-game hideout page.
- Ensure no negative computed fields.
- Ensure totals are consistent (`registered = in_city + out_city`).

---

## Phase 2 — Dispatch Spies Using Existing Target Report Files

## User outcome
Select a saved target JSON report, choose target cities (`one / many / all`), choose spy counts, preview expected outcomes/costs/time, then confirm sending.

## Inputs
- Existing target JSON files in storage (currently `storage/players/*.json`).

## Implementation tasks

1. **File discovery and selection UI**
   - List JSON files with index, name, timestamp, city count.
   - Let user select one.

2. **Target city extraction**
   - Read selected report and extract all target cities + coordinates + metadata.
   - Show list and let user select:
     - Single city
     - Multi-select list
     - All cities

3. **Source city candidate generation**
   - From your own cities, identify those with available spies/free capacity for dispatch.
   - Compute travel-time estimates to each target city (prefer shortest-time options).

4. **Defender estimate model**
   - If report includes hideout level for target city, use it.
   - Else estimate hideout with requested formula:
     - `estimated_hideout = (50 - townhall_level) / 2 + townhall_level`
     - equivalent: `townhall_level + (50 - townhall_level)/2`
   - Use game equations + known town hall/hideout assumptions to estimate defending spies.
   - Show estimate clearly as approximation.

5. **Send-plan optimizer**
   - For each selected target city, compute candidate attack plans:
     - number of main spies
     - minimal decoys needed to maintain target success/risk thresholds
   - Objective priority (as requested):
     1) maximize success chance
     2) minimize discovery risk
     3) minimize decoys
     4) minimize travel time
   - Show recommendation and alternatives.

6. **Cost calculation**
   - Gold cost estimate for dispatch/recruit usage.
   - Wine cost estimate where applicable by game mechanics/maintenance.
   - Show per-city and total.

7. **User confirmation and execution**
   - Present final plan summary.
   - Prompt yes/no to send.
   - On yes: execute dispatch requests and capture results.
   - On no: abort cleanly.

8. **Result logging**
   - Save send plan + results JSON:
     - selected file
     - selected cities
     - computed probabilities/estimates
     - costs
     - actual send responses

## Validation checks (Phase 2)
- Dry-run mode for all calculations without sending.
- Compare at least one computed success/risk value to game UI values.
- Ensure no dispatch above available spy counts.

---

## Phase 3 — Mission Choice and Execution

## User outcome
After spies are in place, choose mission types per city and execute missions with clear expected risk/reward.

## Implementation tasks
1. Enumerate available spy missions from hideout/spy interface.
2. Let user choose mission set per target city (single template or per-city custom).
3. Pre-check mission prerequisites (spy presence, cooldowns, action points, etc.).
4. Execute mission queue and capture outcomes.
5. Update target report JSON/HTML with mission findings.

## Validation checks (Phase 3)
- Handle mission failure/discovery gracefully.
- Persist mission history with timestamps.

---

## Phase 4 — Persistent Spying Loop

## User outcome
Run recurring spy operations for configured duration, auto-replacing dead/lost spies and refreshing intelligence reports continuously.

## Implementation tasks
1. **Persistent job config**
   - Duration (`days/hours`), mission set, interval, retry policy, risk limits.
2. **Loop engine**
   - Schedule next actions per city.
   - Detect dead/missing spies and trigger replacement dispatch.
3. **Adaptive behavior**
   - If risk spikes or repeated losses occur, throttle or pause.
4. **Continuous report update**
   - Append mission outcomes and city intel deltas into JSON.
   - Regenerate HTML report snapshots.
5. **Stop/resume controls**
   - Manual stop, auto-stop at duration end, resume from saved state.

## Validation checks (Phase 4)
- Long-run resilience (network/session interruptions).
- Ensure no infinite loops without sleep/backoff.

---

## Data model additions (planned)

- `spy_capacity_audit` documents:
  - `timestamp`, `cities[]`, aggregate totals.
- `dispatch_plan` documents:
  - source/target mapping, probabilities, costs, chosen strategy.
- `mission_log` documents:
  - per mission execution entries.
- `persistent_job_state`:
  - runtime checkpoints for resume/recovery.

---

## Known risks / technical unknowns to confirm during implementation

1. Exact request endpoints and payloads for:
   - hideout details,
   - sending spies,
   - mission execution,
   differ by ikabot version/server and must be verified against current in-repo ikabot flow.
2. Some probability formulas may have server/version-specific modifiers.
3. Cost components (especially wine) may differ by server rules; must be validated against UI values.

---

## Acceptance criteria by phase

### Phase 1 done when
- User can run one command/menu flow and get per-city hideout/spy capacity report + JSON/HTML export.

### Phase 2 done when
- User can pick a saved JSON target report, select targets, preview optimized send plan with success/risk/cost/time, then confirm to execute.

### Phase 3 done when
- User can select and run missions against infiltrated cities and results are persisted.

### Phase 4 done when
- User can schedule repeated spying for a set duration with automatic replacement and report refresh.

---

## Open questions for your approval before coding Phase 1

1. For Phase 1 output filenames, do you want a single rolling file (overwrite latest) or timestamped files each run?
2. For Phase 2 thresholds, should we expose default targets (e.g., success >= 85%, discovery <= 10%) as user-editable settings?
3. For multi-city sends, should we allow per-city custom spy count, or one global spy count with optional per-city overrides?
4. For persistent mode, should the default behavior be conservative (pause on first high-loss event) or aggressive (continue unless manual stop)?
