# Reading live ikabot task state from Docker

For the maintenance web page: how to show, per instance, what each ikabot is
currently doing. Hand this file to whoever is building the panel.

Available from **mod v1.8.1**.

---

## 1. Where the data is

Each running ikabot writes a JSON file for its account:

```
$IKABOT_DATA_DIR/status/<username>_<server><world>.json
```

`IKABOT_DATA_DIR` is `~/.ikabot` on Linux (`%APPDATA%\.ikabot` on Windows), so
in a container it is normally:

```
/root/.ikabot/status/kurzon_s55en.json
```

**No HTTP, no auth, no port discovery.** Mount that directory (or its parent)
into the panel container read-only and read the files directly:

```yaml
volumes:
  - ikabot-data:/root/.ikabot          # in each ikabot container
  - ikabot-data:/data/ikabot:ro        # in the panel container
```

One file per account. `ls /data/ikabot/status/*.json` enumerates every instance
without needing to be told how many there are.

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

for path in sorted(glob.glob("/data/ikabot/status/*.json")):
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

They default to `$IKABOT_DATA_DIR/decaptcha_seats`, which is already the
shared volume if you mount `ikabot-data:/root/.ikabot` in every container — so
**with the mount from §1 this works with no extra configuration**.

If the data dir is *not* shared, point them somewhere that is:

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
