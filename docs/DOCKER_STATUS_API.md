# Reading live ikabot task state from Docker

For the maintenance web page: how to show, per instance, what each ikabot is
currently doing. Hand this file to whoever is building the panel.

Available from **mod v1.8.1**.

---

## 1. Where the data is

Each running ikabot writes a JSON file for its account:

```
$IKABOT_STATUS_DIR/<username>_<server><world>.json
```

which defaults to `$IKABOT_DATA_DIR/status/` — `~/.ikabot/status/` on Linux,
`%APPDATA%\.ikabot\status\` on Windows.

**No HTTP, no auth, no port discovery.** Point every ikabot container at one
shared status directory and mount it into the panel read-only:

```yaml
services:
  ikabot-1:
    environment:
      - IKABOT_STATUS_DIR=/shared/status
    volumes:
      - ikabot-1-data:/root/.ikabot     # PER CONTAINER — see the warning below
      - ikabot-status:/shared/status
  panel:
    volumes:
      - ikabot-status:/data/status:ro
```

One file per account. `ls /data/status/*.json` enumerates every instance
without needing to be told how many there are.

> **Do not share `~/.ikabot` itself between containers.** Earlier revisions of
> this document suggested mounting one `ikabot-data` volume at `/root/.ikabot`
> in every container. That directory also holds **the credential vault**, and
> putting several ikabots on one vault file risks losing writes and corrupting
> the account list. Give each container its own data volume and share only the
> status directory (above) and the decaptcha seats (§7), which are designed for
> it.
>
> The vault has since been hardened against concurrent writers — writes now merge
> under the lock instead of overwriting, and the stale-lock check no longer
> compares PIDs across containers — but a shared vault is still a single point
> of failure with nothing to gain, so keep it per-container.

---

## 2. File format

```json
{
  "schema": 1,
  "account": "kurzon_s55en",
  "username": "kurzon",
  "server": "s55",
  "world": "en",
  "ikabot_version": "7.5.1",
  "mod_version": "1.8.1",
  "pid": 41,
  "updated": 1755624901,
  "task_count": 2,
  "tasks": [
    { "pid": 102, "action": "alertAttacks", "status": "watching",            "started": 1755620001 },
    { "pid": 137, "action": "webServer",    "status": "running on :43000",   "started": 1755620500 }
  ]
}
```

| Field | Meaning |
|---|---|
| `schema` | Bump if the format changes. Ignore files with a schema you do not know. |
| `account` | Stable instance id. Matches the filename stem. |
| `updated` | Unix seconds when the file was last written — **this is your liveness signal** (see §3). |
| `pid` | The main ikabot menu process. |
| `task_count` | `len(tasks)`, provided so the panel does not have to parse the array for a badge. |
| `tasks[].action` | Module name, e.g. `webServer`, `alertAttacks`. |
| `tasks[].status` | Free text the module sets via `session.setStatus()`. Display as-is; do not parse. |
| `tasks[].started` | Unix seconds. |

Writes are atomic (temp file + `os.replace`), so a reader never sees a partial
file and **no locking is needed**. Just read it.

---

## 3. Important: `updated` is a heartbeat, not a clock

The file is rewritten **each time the ikabot main menu redraws**. That happens
on startup, after launching or killing a task, and whenever the user interacts.

It does **not** tick on a timer. An instance sitting idle at the menu with
tasks running happily can have an `updated` several hours old. That is normal.

So:

- **Do not** mark an instance "dead" purely because `updated` is old.
- **Do** use `updated` as "last confirmed state".
- To check the process is actually alive, test the pid:
  `/proc/<pid>` exists in that container, or `docker inspect` the container.

If you want a real heartbeat, ask and we can add a periodic writer — it is a
small change, but it means an extra background thread per instance, so it is
not there by default.

---

## 4. Suggested panel behaviour

| Condition | Show |
|---|---|
| File missing | Instance has never started, or the volume is not mounted |
| `task_count == 0` | Idle — logged in, nothing running |
| `task_count > 0` | List `action` + `status` per task |
| Container down (per Docker, not the file) | Stopped |

Example read:

```python
import json, glob, time

for path in sorted(glob.glob("/data/status/*.json")):
    with open(path) as f:
        s = json.load(f)
    if s.get("schema") != 1:
        continue
    age = int(time.time()) - s["updated"]
    print(s["account"], f"{s['task_count']} tasks", f"(state {age}s old)")
    for t in s["tasks"]:
        print("   ", t["action"], "-", t["status"])
```

---

## 5. Failure notifications already exist

Separately from this file, a watchdog notices when a task's process dies
without completing and sends a message through the configured notification
backends (Telegram / Discord / ntfy). The panel does not need to detect that
itself — but if you want it on-page, the simplest addition would be an
`events` array in this file. Ask and it can be added.

---

## 6. What to ask for if you need more

The file is deliberately minimal. These are cheap to add if the panel wants
them — say which and they can go in a follow-up:

- Periodic heartbeat writer (real liveness without pid checks)
- `events` array of recent task starts/stops/failures
- Last login time and session-expiry state
- Per-task progress where the module knows it (e.g. "3 of 10 buildings")

---

## 7. Also relevant to the compose file: decaptcha worker seats

From **mod v1.8.3** the local captcha solver sizes its worker pool to the
machine rather than running a fixed pool per account, coordinating through
`flock` "seat" files so several instances do not all grab every core.

**The seats must be on a path shared by every ikabot container**, or each one
sees an empty seat directory, believes it has the machine to itself, and the
coordination does nothing. Measured here with 6 instances on 4 cores: shared
directory → 4 get seats and 2 correctly fall back to serial; per-container
directories → all 6 claim seats.

They default to `$IKABOT_DATA_DIR/decaptcha_seats`. With the per-container data
volumes of §1 that default is **not** shared, so set it explicitly — this is
required, not optional:

```yaml
environment:
  - IKABOT_DECAPTCHA_SEAT_DIR=/shared/decaptcha_seats
volumes:
  - decaptcha-seats:/shared/decaptcha_seats
```

The directory must be on a filesystem where `flock` works — a Docker volume or
bind mount is fine, NFS generally is not. Seat locks are released by the
kernel when a process exits, so a crashed instance cannot leave a seat stuck.

**Container limits are respected.** Worker count and memory headroom come from
the cgroup (`memory.max` / `cpu.max`, and the v1 equivalents) rather than
`/proc/meminfo` and `os.cpu_count()`, which report the host from inside a
container. A container with `--memory=300m` plans zero workers and solves
serially instead of being OOM-killed. No action needed — just be aware that
tightening `--cpus` or `--memory` will make captcha solving slower rather than
failing.

### Scaling numbers (measured, 4 cores, worst case)

Every instance solving a captcha *at the same moment* — the pathological case,
not the norm, since captchas are sporadic.

| Instances | Wall | Median per solve | Throughput | Correct |
|---|---|---|---|---|
| 1 | 7.0 s | 7.0 s | 0.14 /s | 1/1 |
| 2 | 11.4 s | 11.4 s | 0.18 /s | 2/2 |
| 4 | 13.3 s | 13.2 s | **0.30 /s** | 4/4 |
| 8 | 25.5 s | 25.0 s | 0.31 /s | 8/8 |
| 12 | 37.5 s | 36.9 s | 0.32 /s | 12/12 |
| 20 | 67.3 s | 66.4 s | 0.30 /s | 20/20 |

**Throughput saturates at the core count** (~0.31 solves/s here) and stays
flat. Past that, extra instances buy nothing and simply queue: latency grows
linearly.

Rules of thumb, per core count `C`:

- **Aggregate ceiling** ≈ `0.08 × C` solves per second.
- **Median latency** ≈ `(N / C) × 13 s` when all N solve at once.
- **RAM** ≈ `N × 316 MB` peak while solving, `N × 70 MB` once idle for 120 s.

So on 4 cores, 12 instances is comfortable (~37 s worst case) and 20 is
usable (~67 s). Double the cores and both halve. Since a pirate mission runs
for minutes to hours, even a minute of captcha delay rarely matters — but if
it does, add cores, not instances.

Accuracy never degraded: every solve decoded correctly at all N.

To measure your own box, set `DECAPTCHA_TIMING_LOG = True` in `config.py`.
Each solve then logs one `[decaptcha-timing]` line with worker count, free RAM
and CPU topology.

Memory per instance: **~316 MB while solving**, dropping to **~70 MB** after
the 120 s idle release (verified: the weights really are returned to the OS,
not just freed inside Python). So 20 instances cost ~1.4 GB idle and up to
~6.3 GB if they all happen to solve at once.

From **v1.8.4** a solve is refused outright when there is not enough free
memory to hold the weights, and the caller falls back to the remote decaptcha
API. That turns the worst case from "OOM-killed container" into "solved
remotely, a bit slower".

If you run many instances on a small box, either give the host enough RAM for
the peak, or set `USE_MULTIPROCESSING_DECAPTCHA = False` and accept serial
solving.
