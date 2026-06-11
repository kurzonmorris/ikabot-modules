# Construction Manager Improvements

Suggested improvements to `constructionList.py` to align it with the transport manager's CSV-based persistence and worker patterns.

## Current State

`constructionList.py` (ikabot 7.2.5) forks a child process per construction job. Each child:
- Polls for construction completion (`waitForConstruction`)
- Optionally ships resources to the build city (`sendResourcesMenu`)
- Has no crash recovery — if ikabot restarts, in-progress construction queues are lost
- Has no schedule viewer or editor

## Proposed Improvements

### 1. CSV-Based Construction Schedule

Add a construction schedule CSV at `~/.ikabot_construction_{server}_{username}.csv` with columns:

| Column | Type | Description |
|--------|------|-------------|
| schedule_id | int | Auto-increment ID |
| city_id | str | Target city ID |
| city_name | str | Human-readable city name |
| building_name | str | Building to upgrade |
| building_position | int | Position index in city layout |
| current_level | int | Level at time of scheduling |
| target_level | int | Desired final level |
| auto_transport | str | "yes"/"no" — ship resources automatically |
| ship_type | str | "m" (merchant) / "f" (freighter) |
| source_city_ids | str | JSON list of supplier city IDs |
| status | str | pending/active/building/waiting_resources/completed/error |
| last_checked | int | Unix timestamp of last poll |
| created_at | int | Unix timestamp |
| notes | str | User-editable label |
| schema_version | int | For future migrations |

### 2. Shared Utilities

Extract these into a shared module (e.g. `ikabot/helpers/csv_utils.py`) so both transport and construction managers use identical implementations:

```python
def _safe(value):
    """Sanitize a value for use in filenames."""
    return re.sub(r'[^\w.-]', '_', str(value))

def _account_suffix(session):
    """Build server_username suffix for per-account files."""
    return f"{_safe(session.servidor)}_{_safe(session.username)}"

class CsvLock:
    """Cross-process file lock using atomic O_CREAT|O_EXCL."""

def csv_load(path, coerce_fn=None):
    """Load CSV rows with optional type coercion."""

def csv_save_all(path, fieldnames, rows):
    """Atomic write via temp file + os.replace()."""

def csv_modify(path, fn, coerce_fn=None):
    """Load, apply fn(rows), save under lock."""

def enforce_schema_or_abort(schema_path, expected_version):
    """Schema sidecar version check."""
```

This eliminates the copy-paste between the two modules and ensures bug fixes apply everywhere.

### 3. Single Worker Process

Replace the per-job child process with a single construction worker (same pattern as the transport worker):

```
def construction_worker(session, resume_mode):
    while True:
        # Check stop flag
        # Re-read CSV each iteration
        # For each active schedule:
        #   - Check if construction is complete
        #   - If resources needed and auto_transport="yes", create a one-shot
        #     transport schedule (see integration below)
        #   - If building complete and level < target, queue next upgrade
        #   - If level == target, mark completed
        # Sleep until next check (adaptive, max 60s)
```

Benefits:
- One process to manage instead of N
- Clean restart/resume semantics
- Resource usage scales with polling interval, not job count

### 4. Resume on Restart

When the construction worker starts, offer the same two resume modes as the transport manager:

- **Continue as scheduled**: Check all active builds immediately, resume normal polling
- **Start fresh**: Re-scan all cities for current state, reconcile with CSV

The "start fresh" option is particularly valuable for construction since building levels may have changed while offline (e.g., user manually built something in the browser).

### 5. Transport Integration (JIT Resource Shipping)

When a construction schedule needs resources shipped:

```python
# Instead of inline sendResourcesMenu, create a transport schedule
from resourceTransportManager_v7 import (
    build_schedule_row, transport_csv_append
)

schedule = build_schedule_row(
    mode="consolidate",
    source_city_ids=supplier_ids,
    dest_city_ids=[build_city_id],
    resource_config=missing_resources,
    interval_hours=0,  # one-shot
    notes=f"JIT for {building_name} L{target_level}",
)
transport_csv_append(session, schedule)
```

This reuses the transport worker's route planning, ship allocation, and error handling instead of the simpler `sendResourcesMenu` implementation. The construction worker would then poll until resources arrive before issuing the upgrade.

### 6. Schedule Management Menu

Add a construction schedule viewer/editor accessible from the main menu:

```
Construction Schedule Manager
CSV: ~/.ikabot_construction_s42_myuser.csv
2 active, 1 completed

(1) View schedules
(2) Edit schedule (modify target level, transport, notes)
(3) Delete schedule(s)
(4) Activate worker
(5) Stop worker
(6) Back
```

### 7. Notes Field

Add a user-editable `notes` column (already in the proposed schema above). Editable from the schedule management menu option (2). This matches the transport manager's notes field and helps users track why they queued each build.

## Migration Path

1. Start with the CSV layer and schedule save (no behavior change — existing `constructionList` still works)
2. Add the worker process alongside the existing fork-per-job approach
3. Once stable, deprecate the fork-per-job path
4. Add transport integration as an optional enhancement

This incremental approach lets each step be tested independently without breaking existing functionality.
