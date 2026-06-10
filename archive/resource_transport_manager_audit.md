# Resource Transport Manager Audit + Updated Recovery Plan (Option 5)

## Scope reviewed
- `resourceTransportManager.py_v4(match city name only case insensitive).py_v4(match city name only case insensitive).py_v4(match city name only case insensitive)`
- `bulkdistribution.csv`
- Focus: Option 5 (`massDistributionMode` / `do_it_mass_distribution`), loop safety, validation, matching quality, exit behavior, and resumability.

---

## Executive summary
Option 5 is operational, but it currently has a few reliability gaps that can cause either bad targeting (wrong city), run instability (looping behavior), or weak crash recovery (no persistent progress checkpoint). The right fix is to combine stronger matching, strict interval validation, sensible `'` exits, and a lightweight run-log checkpoint system.

---

## Script-breaking issues (must fix)

1. **City matching is too weak for production safety (name-only).**
   - Current logic matches only city name (case-insensitive), ignoring `Player` and `City_Location`.
   - Risk: false-positive match when duplicate city names exist on the same island.
   - Current location: lines 2316–2321.

2. **`Hours=0` can create a hot-loop recurrence pattern.**
   - Current flow parses `Hours` and later sleeps for `interval_hours * 3600`.
   - If interval is `0`, loop can re-run immediately with no cooldown.
   - Current locations: line 2222 and recurring loop around lines 2282–2463.

3. **No persistent per-row success checkpoint for crash recovery.**
   - If run fails mid-cycle (example: around row ~16), there is no durable marker to resume safely.
   - Result: user cannot reliably distinguish sent-vs-unsent rows for that cycle.

4. **`'` back/exit support is inconsistent across decision points.**
   - Some prompts support `'`; others do not.
   - Requirement is to support `'` where sensible to escape to menu cleanly.

---

## Will make it work better (should fix)

1. **Improve row validation transparency before dispatch.**
   - Add pre-flight summary: valid rows, skipped rows, reason categories.

2. **Normalize numeric fields instead of silent coercion.**
   - Parse values with trimming/comma cleanup and hard fail invalid values per row.

3. **Add cycle-level status output and log detail.**
   - Improve trust/debuggability for long runs and large CSV sets.

---

## Updated implementation plan (with your new requirements)

### Phase 1 — Matching correctness (Player + City + Location)
1. Replace name-only match with strict ordered strategy:
   - **Primary match**: `City` (case-insensitive) + exact `Player` + exact `City_Location`.
   - **Fallback (optional)**: `City + Player` only if uniquely matched.
   - Otherwise: skip row as mismatch and log reason.
2. Keep all mismatch details for terminal + telegram summary.
3. Do not dispatch any row marked ambiguous.

### Phase 2 — Interval hardening (`Hours`)
1. Validate `Hours` at load:
   - must be integer,
   - must be `>= 1` for recurring option 5.
2. If `Hours <= 0`:
   - show explicit error,
   - return to menu (no worker mode start).
3. Add clear message in UI explaining why `0` is rejected in option 5.

### Phase 3 — Sensible `'` escape coverage
1. Add `'` handling where user is actively making choices or entering values.
2. Keep `'` behavior consistent: set event and return to menu before worker starts.
3. Do **not** inject `'` in background worker loops where no user input exists.
4. Add a quick checklist covering prompts in all modes touched by this module.

### Phase 4 — 7-slot checkpoint/resume system (your new idea)

#### 4.1 Data model
1. Add 7 checkpoint columns in CSV header (example names):
   - `Run_1, Run_2, Run_3, Run_4, Run_5, Run_6, Run_7`
2. Each row gets `X` in selected run column only when shipment is confirmed sent for current cycle.
3. Keep existing shipment fields unchanged.

#### 4.2 Run-slot selection UX
1. At Option 5 startup, after CSV load:
   - ask user:
     - `(1) Start fresh run slot (clear one selected column)`
     - `(2) Resume from existing run slot`
2. Show slot names and completion counts (e.g., `Run_3: 16/68 sent`).
3. User selects one slot for this execution context.

#### 4.3 Dispatch behavior with checkpoints
1. For selected slot:
   - skip rows already marked `X`.
2. After each successful `executeRoutes(...)`:
   - write `X` into selected slot for that row,
   - flush/save file immediately (or batched every N rows with fsync-safe approach).
3. On restart/resume:
   - script reads same slot and continues only unsent rows.

#### 4.4 Integrity and safety rules
1. Write updates atomically:
   - write to temp file and replace original to avoid partial corruption.
2. Keep backup copy before each cycle (`.bak`).
3. Lock CSV update region if concurrent writer risk exists (simple file lock).

### Phase 5 — Telemetry and auditing
1. Add cycle summary:
   - total eligible rows,
   - skipped (already sent in slot),
   - sent success,
   - failed.
2. Telegram summary includes selected run slot and completion ratio.
3. On failure, include next actionable step (“resume `Run_4`”).

---

## Non-breaking constraints (must preserve)
- Preserve `materials_names` to CSV mapping order: `Wood, Wine, Marble, Crystal, Sulphur`.
- Preserve route tuple structure used by `executeRoutes(...)`.
- Preserve shipping lock strategy (`acquire_shipping_lock` / `release_shipping_lock`).
- Preserve current session lifecycle (interactive -> child mode -> worker -> logout).

---

## Clarifying questions before implementation
1. **Checkpoint columns format:** do you want fixed names `Run_1..Run_7`, or date-based names (e.g., `2026-03-26_AM`)?
2. **Fresh run behavior:** when starting fresh, should selected run column be fully cleared first, or only clear rows matching current coordinates/player filters?
3. **Success definition:** should a row be marked `X` only when `executeRoutes` succeeds, or only after additional verification (e.g., ship count decrease check)?
4. **Player matching strictness:** should `Player` compare be case-insensitive exact text match after trim, or support aliases?
5. **`City_Location` strictness:** should location be mandatory strict match, or optional fallback when blank in CSV?
6. **Retry policy for failed rows:** in same cycle, retry immediately (N times) or defer to next cycle/resume?
7. **CSV update timing:** write after every successful row (safer) vs batch write every N rows (faster).
