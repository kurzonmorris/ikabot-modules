# RRS Integration Briefing (for Claude)

## Context

You are working in `/home/user/ikabot-modules/`, a fork of ikabot (Python game bot).
The file `resourceReservationSystem.py` is a shared resource-reservation data layer
used by multiple modules. This document tells you everything needed to integrate it
into an existing module correctly.

---

## What RRS does and does not do

**Does:** tracks which resources in which cities are reserved by which modules,
via a per-account CSV file with cross-process file locking.

**Does not:** ship resources, retry operations, sleep, schedule anything. All of
that stays in the calling module.

---

## Non-negotiable integration constraint

RRS is **optional**. The calling module must work identically whether or not RRS
is installed. Every RRS call is guarded by `if RRS_AVAILABLE:`. Never add a hard
import or make RRS a required dependency.

---

## Detection block — copy verbatim at module top, after stdlib imports

```python
try:
    from resourceReservationSystem import (
        reserve,
        update_reservation,
        release,
        release_all_for_module,
        release_all_for_city,
        get_available,
        get_total_reserved,
        get_reservation_snapshot,
        get_summary,
        get_reservations,
        is_city_excluded,
        get_excluded_cities,
        get_config as rrs_get_config,
    )
    RRS_AVAILABLE = True
except ImportError:
    RRS_AVAILABLE = False

MODULE_NAME = "yourModuleFilenameWithoutDotPy"  # e.g. "constructionManager"
```

`MODULE_NAME` must be stable across runs — it is the ownership key for reservations.

---

## Full public API

### `reserve(session, city_id, city_name, resource_index, amount, module_name, reason, release_at) -> int`

Creates a reservation. Returns `reservation_id` (int). Raises `ValueError` if
`amount <= 0` or `resource_index` not in 0–4. Prints a warning (does not raise)
if `release_at` is already in the past.

**`release_at` is a unix timestamp, not a duration.** Always:
```python
release_at = time.time() + estimated_seconds   # CORRECT
release_at = estimated_seconds                 # WRONG — will immediately expire
```

`hard_expires_at` is set automatically to `release_at + 86400` (24 h safety net).

---

### `update_reservation(session, reservation_id, module_name, release_at) -> True | False | None`

Extends an existing active reservation. Use this when an operation runs long —
**never release then re-reserve** (that opens a gap another module can exploit).

---

### `release(session, reservation_id, module_name) -> True | False | None`

Marks one reservation released. Returns:
- `True` — found, owned, released
- `False` — not found or already inactive
- `None` — found but caller doesn't own it

Use `"__admin__"` as `module_name` only in interactive menus to bypass ownership.

**Always check with `is True` / `is None` / `is False`, not truthy/falsy.**

---

### `release_all_for_module(session, module_name) -> int`

Releases all active reservations owned by `module_name`. Returns count. Use in
`finally` blocks for cleanup. Does not distinguish cities.

---

### `release_all_for_city(session, city_id, module_name="__admin__") -> int`

Releases all active reservations in a city. Default `"__admin__"` releases all
modules' reservations. Pass a specific `module_name` to restrict to that module.

---

### `get_available(session, city_id, resource_index, actual_amount) -> int`

Returns `max(0, actual_amount − sum_of_active_reservations)`.
`actual_amount` is the live figure from `city["availableResources"][resource_index]`.

---

### `get_total_reserved(session, city_id, resource_index) -> int`

Raw sum of active reservations for city+resource. Does not need live amount.

---

### `get_reservation_snapshot(session, city_id, resource_index, actual_amount) -> (available, reserved)`

Both values in one lock acquisition. **Use this instead of calling `get_available()`
and `get_total_reserved()` separately.**

---

### `get_summary(session) -> dict`

```python
{city_id: {resource_index: total_reserved_amount}, ...}
```

Single lock acquisition for all cities. **When scanning many cities, call this
once and subtract per city. Never call `get_available()` inside a city loop.**

```python
summary = get_summary(session)
for city in cities:
    reserved = summary.get(int(city["id"]), {}).get(resource_index, 0)
    free = city["availableResources"][resource_index] - reserved
```

---

### `get_reservations(session, city_id=None, module_name=None, active_only=True) -> list[dict]`

Returns reservation rows. Keys: `reservation_id, city_id, city_name,
resource_index, reserved_amount, module_name, reason, created_at, release_at,
hard_expires_at, status`.

---

### City exclusion

```python
is_city_excluded(session, city_id) -> bool
get_excluded_cities(session) -> list[dict]   # [{city_id, city_name}, ...]
exclude_city(session, city_id, city_name="")
include_city(session, city_id)
```

Users configure excluded cities via the RRS interactive menu. A city on this
list should be treated as if all its resources are reserved. **Always check
exclusion before scanning a city for available resources.**

---

### Config

```python
rrs_get_config(session) -> dict
# Keys: min_ship_capacity (int, default 500), retry_hours (float, default 6)
```

`min_ship_capacity`: skip a city if free resources fall below this.
`retry_hours`: how long to wait before retrying when resources are short.
Read this value instead of hardcoding your own wait time.

---

## Resource index

| 0 | 1 | 2 | 3 | 4 |
|---|---|---|---|---|
| Wood | Wine | Marble | Crystal | Sulphur |

Matches `city["availableResources"][index]` and `ikabot.config.materials_names`.

---

## Integration patterns

### Single-city check before consuming resources

```python
def usable_amount(session, city, resource_index, amount_needed):
    actual = city["availableResources"][resource_index]
    if not RRS_AVAILABLE:
        return min(actual, amount_needed)
    if is_city_excluded(session, city["id"]):
        return 0
    available, _ = get_reservation_snapshot(session, city["id"], resource_index, actual)
    min_take = rrs_get_config(session)["min_ship_capacity"]
    if available < min_take:
        return 0
    return min(available, amount_needed)
```

### Multi-city scan

```python
def build_plan(session, cities, resource_index, total_needed):
    plan = []
    remaining = total_needed

    if RRS_AVAILABLE:
        summary  = get_summary(session)
        excluded = {e["city_id"] for e in get_excluded_cities(session)}
        min_take = rrs_get_config(session)["min_ship_capacity"]
    else:
        summary, excluded, min_take = {}, set(), 500

    for city in cities:
        if remaining <= 0:
            break
        cid = int(city["id"])
        if cid in excluded:
            continue
        actual   = city["availableResources"][resource_index]
        reserved = summary.get(cid, {}).get(resource_index, 0)
        free     = max(0, actual - reserved)
        if free < min_take:
            continue
        take = min(free, remaining)
        plan.append((city, take))
        remaining -= take

    return plan, remaining   # remaining > 0 → short; caller decides retry logic
```

### Reserving before a long operation

```python
reservation_ids = []
if RRS_AVAILABLE:
    for idx, cost in enumerate(resource_costs):
        if cost > 0:
            rid = reserve(
                session,
                city_id=city["id"],
                city_name=city["name"],
                resource_index=idx,
                amount=cost,
                module_name=MODULE_NAME,
                reason=f"{operation_label}",
                release_at=time.time() + estimated_duration_seconds,
            )
            reservation_ids.append(rid)
```

Store `reservation_ids` in your own CSV/state row if you need to release
them precisely. If you can't store them, fall back to `release_all_for_module`.

### Releasing on completion

```python
if RRS_AVAILABLE:
    for rid in reservation_ids:
        release(session, rid, MODULE_NAME)
```

### Cleanup in finally block

```python
try:
    run_worker(...)
finally:
    if RRS_AVAILABLE:
        release_all_for_module(session, MODULE_NAME)
```

### Extending when an operation runs long

```python
if RRS_AVAILABLE:
    for rid in reservation_ids:
        result = update_reservation(session, rid, MODULE_NAME, new_finish_ts)
        if result is None:
            pass   # log: wrong owner (should never happen with MODULE_NAME)
        elif result is False:
            pass   # log: reservation already expired; create a new one if needed
```

### Retry wait using config

```python
retry_hours = rrs_get_config(session)["retry_hours"] if RRS_AVAILABLE else 6
time.sleep(retry_hours * 3600)
```

---

## Mistakes to avoid

| Wrong | Right |
|---|---|
| `release_at = build_seconds` | `release_at = time.time() + build_seconds` |
| `if release(...)` | `if release(...) is True` |
| `get_available()` in a city loop | `get_summary()` once, subtract in loop |
| `get_available()` + `get_total_reserved()` | `get_reservation_snapshot()` |
| release + re-reserve to extend | `update_reservation()` |
| bare `import resourceReservationSystem` | detection block with `try/except ImportError` |
| skipping `is_city_excluded()` | always check before touching a city's resources |

---

## Checklist before committing

- [ ] Detection block present; `RRS_AVAILABLE` guards every call
- [ ] `MODULE_NAME` defined as a stable string constant
- [ ] `is_city_excluded()` (or `get_excluded_cities()` set) checked before city scan
- [ ] `get_summary()` used for multi-city scans, not per-city `get_available()`
- [ ] `reserve()` called after operation confirmed; `release_at = time.time() + duration`
- [ ] `release()` or `release_all_for_module()` in `finally`
- [ ] `update_reservation()` used to extend, not release + re-reserve
- [ ] `release()` return value compared with `is True / is None / is False`
- [ ] Retry wait reads `rrs_get_config(session)["retry_hours"]` when available
- [ ] Existing code path completely unchanged when `RRS_AVAILABLE` is `False`
