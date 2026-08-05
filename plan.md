# Messaging Hub — Build Plan

> Module: `modules/messagingHub_v<X.Y.Z>.py` (external module, installs as `messagingHub.py`)
> Status: **Phases 1 and 3 built in `messagingHub_v1.0.0.py`** (nothing has been
> released to users yet, so it is all still one unshipped 1.0.0 — bump the
> filename when it first goes to `main`). Next: Phase 2, which is waiting on
> real inbox data — see `docs/MESSAGING_HUB_DATA_REQUEST.md`.
> This file is the working spec across sessions.
> Read `Explained-ikariam_ikabot.md` and `Explained-user_kurzon.md` before touching it.

---

## 1. What this is

A single external module that turns ikabot into a **messaging hub**:

1. **Forwards in-game Ikariam messages** (player mail, combat, espionage, piracy,
   construction, shipments, news …) out to Discord / Telegram / ntfy.
2. **Sends independently of ikabot's own notification setup** — the hub owns its
   own destinations, so it can post to a different server, bot or account than
   the one `sendToBot()` uses. Routing to ikabot's normal path stays available
   as one destination among many.
3. **Routes per message type to a different destination** — combat to one
   channel, construction to another, shipments to a third, and so on.
4. **Monitors city resources** and fires a message when a resource crosses a
   threshold, per city or as a global rule across all cities.
5. **Grows into a general trigger engine** — "when X happens in game, send a
   message to the place I chose for X" — with message types and resource
   thresholds being the first two kinds of trigger.

### Decisions already made (do not re-litigate)

| Decision | Choice |
|---|---|
| Process model | **One** background hub process, watchers toggled inside it |
| Discord transport | **Webhook URL per channel** (no bot to host) |
| In-game read state | **Never touched** — the hub only reads; dedupe is local |
| Phase 1 scope | Core hub + message forwarding + per-type routing |
| File naming | Every hub file carries `{username}_{servidor}{mundo}` — multiple instances never collide |
| Storage location | Default `IKABOT_DATA_DIR`, user-settable; a `messaging_hub/` folder is created inside whichever base is chosen |
| Combat reports | **Summary** by default, full report toggleable |
| Alliance messages | Own event type, **routed to the player-mail destination** by default, can be pointed anywhere |
| Multi-account | A **global config** shared by every instance pointing at the same folder; each account chooses global or its own, per section |
| Message body | **Full, truncated at 900 characters**, limit configurable |

---

## 2. Compatibility — vanilla ikabot *and* the kurzon mod

The hub must run on both. Everything mod-only is guarded or bundled.

| Thing | Vanilla | Mod | Hub does |
|---|---|---|---|
| `helpers/modulePrefs.py` | absent | present | guarded import, own JSON fallback |
| `botComm.notificationDataIsValid` | absent | present | guarded, falls back to `telegramDataIsValid` |
| `helpers/discordComm.py`, `ntfyComm.py` | absent | present | **not used** — hub has its own senders |
| `sendToBot()` multi-backend | Telegram only | 3 backends | only used for the `ikabot` destination |
| `config.autostart_active` | absent | present | `getattr(config, "autostart_active", False)` |
| `function/alertMessages.py` | absent | present | **not imported** — scraper is bundled |
| Module loading | `plugins/` via `pluginLoader` | `(30) External Modules` | satisfies both (below) |

**Loader compatibility.** Vanilla's `pluginLoader` resolves the entry function by
filename stem and reads `MENU_LABEL` / `MENU_ORDER`; the mod's loader reads
`MODULE_ENTRY` / `MODULE_NAME`. Declare all four, and name the entry function
`messagingHub` so the filename-stem fallback also lands correctly:

```python
MODULE_NAME  = "Messaging Hub"
MODULE_ENTRY = "messagingHub"
MENU_LABEL   = "Messaging Hub"
MENU_ORDER   = 50
```

**Import discipline.** Only `ikabot.config`, `ikabot.helpers.*` and stdlib +
`requests`. Nothing from `ikabot.function.*` — those differ between the two
trees. The message scraper is **copied and adapted** from `alertMessages.py`
into the module rather than imported, so there is exactly one code path on both
ikabot versions.

---

## 3. Files on disk

The base folder defaults to `IKABOT_DATA_DIR` and is changeable at
**(7) Storage & global configuration**. Whichever base is chosen, a
`messaging_hub/` folder is created inside it. The chosen path is kept in the
account's session data (`messagingHubDir`), so it is per account and no two
instances fight over a pointer file.

```
<base>/messaging_hub/
├── {username}_{servidor}{mundo}_config.json   ← this account's config
├── {username}_{servidor}{mundo}_state.json    ← seen ids, counters, cooldowns
├── global_config.json                         ← shared by every account using this folder
└── capture/
    └── {username}_{servidor}{mundo}_capture_YYYYMMDD_HHMMSS.txt
```

Every file carries the account key so several instances can share one folder
without collisions. **`global_config.json` is the one deliberate exception** —
being shared is its entire purpose (§4.1).

Config and state are **separate files on purpose**: state churns every poll, config
holds hand-entered webhook URLs. A corrupt state file must never cost the config.
Both written atomically (`.tmp` + `os.replace`).

> **The config file contains secrets** — webhook URLs and bot tokens are stored in
> plain JSON, same as ikabot's own session data. Say so in `GUIDE.md`. Never write
> credentials to logs, notifications, or the capture dumps (redact them there).

`modulePrefs` is still written when available — a minimal `{"config_version": N}`
record — purely so `set_autostart()` has a file to flag and the mod's
**Options → (10) Auto-start modules** screen can see the hub.

---

## 4. Config schema

```jsonc
{
  "config_version": 1,
  "use_global": { "routing": false, "formatting": false },
  "destinations": [
    {
      "id": "d1",
      "name": "ikariam-general",
      "kind": "discord",              // discord | telegram | ntfy | ikabot
      "enabled": true,
      "discord":  { "webhook_url": "...", "username": "Ikabot", "use_embeds": true },
      "telegram": { "bot_token": "...", "chat_id": "...", "thread_id": null },
      "ntfy":     { "server": "https://ntfy.sh", "topic": "...", "token": "",
                    "priority": 3, "tags": [] }
    }
  ],
  "routes": {
    "player_message":    ["d1"],
    "alliance_message":  ["d1"],
    "combat":            ["d2"],
    "espionage":         ["d2"],
    "piracy":            ["d3"],
    "construction":      ["d4"],
    "shipment_internal": ["d5"],
    "shipment_external": ["d5"],
    "news":              ["d6"],
    "treaty":            ["d1"],
    "research":          ["d4"],
    "resource_alert":    ["d7"],
    "other":             ["d1"],      // nothing is ever silently dropped
    "hub_status":        ["d1"]       // hub's own errors — kept off the busy channels
  },
  "type_enabled": { "player_message": true, "combat": true, "…": true },
  "watchers": {
    "messages":  { "enabled": true,  "interval_minutes": 10, "notify_existing": false },
    "resources": { "enabled": false, "interval_minutes": 30 }
  },
  "resource_rules": [ /* §7 */ ],
  "formatting": {
    "include_body": true,
    "body_max_chars": 900,
    "combat_full_report": false,      // false = summary line only
    "mutes": [],
    "quiet_hours": { "enabled": false, "from": "23:00", "to": "07:00", "types": [] }
  },
  "classification_overrides": [
    { "match": "subject_contains", "value": "Warehouse", "type": "construction" }
  ],
  "seen_retention_days": 14
}
```

`routes` maps **event type → list of destination ids** (fan-out is allowed: one
type can go to several places). A missing or empty list means that type is not
forwarded. Destination **ids**, never list positions — see §23 of the Explained doc.
Ids that no longer resolve are skipped and shown in red on the routing screen
rather than silently dropping messages into nowhere.

### 4.1 Global vs individual configuration

Point several accounts at the same storage folder and they share
`global_config.json`. Each account then decides, **per section**, whether to use
it — so the throwaway accounts can run one shared setup while the main account
keeps its own:

| Section | Covers | Why grouped |
|---|---|---|
| `routing` | `destinations` + `routes` + `type_enabled` | routes hold destination ids; splitting them would leave dangling references |
| `formatting` | body limits, combat detail, quiet hours, mutes | independent of ids, safe to share alone |

`(7) Storage & global configuration` also copies an account's settings **into**
the global file, and the global file back **into** an account — so a working
setup on the main account becomes the shared baseline in one keypress.

### 4.2 Concurrency — the shared file with many instances

Several ikabot instances, possibly on different machines, read and write one
`global_config.json`. The governing rule: **a locking problem must never stop
messages being forwarded.** Worst case is "your edit was not saved", never a
crash and never a corrupt file.

**Reads are lock-free.** Writers replace the file atomically (`.tmp` +
`os.replace`), so a reader always sees one whole version — never a half-written
one. The hub loop reads the config every poll and therefore never blocks on the
lock, no matter what the other instances are doing.

**Only writes lock**, and the critical section is read-modify-write and nothing
else. Never a prompt, never a network request inside the lock — that is the
structural fix for "an instance holds it too long". Menus collect all input
first, then take the lock for the milliseconds it takes to merge and write.

Lock file: `global_config.json.lock`, holding a random **token**, pid, hostname,
account and timestamp.

| Failure | Handling |
|---|---|
| Holder crashed | Reclaimed after `LOCK_STALE_SECONDS` (45s) |
| Holder's process is gone | Reclaimed immediately — but **only if the lock's hostname is ours**. A pid from another machine means nothing here |
| Wrong clock on another machine | Age is the **youngest** of the embedded timestamp and the file mtime. Erring young means occasionally waiting for a lock we could have taken — far better than stealing a live one. Future timestamps cannot make a lock immortal |
| `O_EXCL` not atomic (SMB/NFS) | After creating or stealing, re-read the file and confirm **our token** is in it before believing we hold it |
| Another instance steals our lock mid-write | Ownership is re-checked immediately before `os.replace`. If it is gone we **abandon the write** (never clobber the thief's data), then re-acquire and redo the whole read-modify-write, up to 3 times |
| Release after being stolen | We only ever delete a lock file that still carries our token — we never remove someone else's |
| Corrupt lock file | Respected while fresh (something wrote it), reclaimed once stale |
| Nested acquisition | The lock is re-entrant per process (depth counter), so nested calls cannot self-deadlock |
| Read-only or missing share | Raises `_LockUnavailable`; the menu says the folder cannot be locked and the hub keeps forwarding |
| Thundering herd | Randomised 0.2–0.7s backoff between attempts |
| Lost update | Saves diff against the snapshot taken at load and write **only the sections this instance actually changed**, so two instances editing different sections both survive. A genuine same-section clash is last-writer-wins and logged |
| Corrupt config file | `.bak` of the last good version is kept; a broken file falls back to it automatically |

**Diagnostics → (6) Shared configuration lock** shows who holds it, its age,
whether it is live or reclaimable, and the config's revision and last writer. It
offers a force-release for the case nothing else can fix — with a warning,
because a stale lock is already reclaimed automatically.

The per-account state file needs no lock: only that account writes it, and the
hub reloads it every poll, so a counter or seen-id reset from the menu is picked
up by the running hub instead of being overwritten by it.

---

## 5. Event types

The taxonomy the whole module is built around. Every event carries
`{id, type, title, body, sender, city, date, source, priority}`.

| Type | Source | Notes |
|---|---|---|
| `player_message` | inbox `message###` rows | mail from other players |
| `alliance_message` | inbox, alliance circulars | may need its own tab |
| `combat` | `militaryAdvisorCombatList` | can be enriched with battlefield detail |
| `espionage` | inbox `gmessage` + spy reports | |
| `piracy` | inbox `gmessage` | pirate raid results / pirate attacks |
| `construction` | inbox `gmessage` | building or upgrade finished |
| `shipment_internal` | inbox `gmessage` | transports between your own cities |
| `shipment_external` | inbox `gmessage` | deliveries/trades from outside the empire |
| `news` | inbox `gmessage` | Gameforge announcements, events, server news |
| `treaty` | inbox `gmessage` | cultural treaty / trade agreement requests |
| `research` | inbox `gmessage` | research completed |
| `other` | anything unmatched | always routed, never dropped |
| `resource_alert` | resource watcher (§7) | not a game message |
| `hub_status` | the hub itself | start/stop, delivery failures, errors |

---

## 6. Message pipeline

```
poll → fetch payloads → parse rows → normalise → dedupe (seen ids)
     → classify → filter (type enabled, quiet hours, mutes)
     → format per destination → send with retry → record state
```

### 6.1 Fetching
Adapted from `alertMessages.py`: page HTML from `view=mail`, `view=messages`,
`view=advisor&activeTab=tabMessages`, `view=diplomacyAdvisor…`, plus the advisor
AJAX endpoints with `currentCityId` and `actionRequest`, plus
`militaryAdvisorCombatList` for combat. Every fetch wrapped — one dead endpoint
must not kill the poll. Never a bare `session.get()`.

### 6.2 Parsing
`<tr id="message###">` (player) and `<tr id="gmessage###">` (game-generated) rows;
sender from `.avatarName`, subject from `.subject`, town/date from the trailing
`<td>`s, body from `tbl_mail###` / `tbl_gmail###` `.msgText`. Canonical ids
`m:###` / `g:###`, combat ids composed from combat id + date + rounds so a
continuing battle re-notifies.

### 6.3 Classification — layered, in this order
1. **Source** — combat list ⇒ `combat`; `message###` ⇒ `player_message`;
   `gmessage###` ⇒ system, continue below.
2. **User overrides** from `classification_overrides` (highest authority after
   source, so a wrong guess is always fixable without a code change).
3. **Icon / CSS class** on the row — Ikariam marks system message rows by kind.
   Mapping table to be filled from captured real data (§9).
4. **Keyword table**, keyed by language with English fallback. Kurzon's accounts
   are not guaranteed English and `session.gf_lang` varies, so keywords live in a
   **data table**, never inline in code.
5. Unmatched ⇒ `other`, routed to its own destination so nothing vanishes.

> Layers 3 and 4 cannot be written accurately from guesswork. Phase 1 therefore
> ships the **capture diagnostic** (§9); Phase 2 turns that real data into the
> mapping tables.

### 6.4 Dedupe
`seen_ids: {id: first_seen_epoch}` in the state file, pruned by
`seen_retention_days` and hard-capped (oldest evicted past ~5000 entries) so it
cannot grow without bound.

**First run:** `notify_existing` defaults to No — the first poll records every
existing message as seen and forwards nothing. Avoids a 200-message flood on day
one. `(6) Diagnostics → reset seen ids` re-arms it deliberately.

**Crash order:** state is written *after* a successful send. A crash mid-poll
re-sends a message rather than losing it. Duplicates are annoying; missed combat
reports are not acceptable.

---

## 7. Resource monitor

One rule list. A rule is either scoped to a city or global across all cities.

```jsonc
{
  "id": "r1",
  "enabled": true,
  "scope": "city",              // city | global
  "city_id": 12345,             // null when scope=global
  "resource": "wine",           // wood | wine | marble | crystal | sulfur | any
  "mode": "absolute",           // absolute | percent | hours_left
  "direction": "below",         // below | above
  "threshold": 5000,
  "destinations": ["d7"],
  "cooldown_minutes": 120,
  "rearm_margin_percent": 10,
  "notify_on_recovery": false
}
```

- **`absolute`** — raw amount. "Wine in Athens below 5.000."
- **`percent`** — percent of warehouse capacity. The useful form of an *above*
  rule: "wood above 90% of storage" catches waste before it caps out.
  Uses `getWarehouseCapacity()`.
- **`hours_left`** — wine only, hours until it runs out, via
  `getWineConsumptionPerHour()` and `getProductionPerHour()`. Mirrors
  `alertLowWine.py`; this is the rule that actually matters day to day.
- **`scope: global`** — evaluated per city; the message names the city that
  breached. Cities that no rule applies to are never fetched.

Each rule carries its own `destinations`; leaving them empty falls back to the
`resource_alert` route. Rules are a shareable section (`use_global.resources`),
with the caveat that a **city-scoped** rule from the global file only applies to
the account that created it — city ids are per account. Global-scope rules work
everywhere, which is the multi-account case.

### Hysteresis — the important part
A resource sitting on a threshold must not alert every poll.

- Fire only on the **transition** ok → breached (per rule, per city).
- Re-arm only when the value recovers past `rearm_margin_percent` beyond the
  threshold, **and** `cooldown_minutes` has elapsed.
- Rule/city breach state lives in the state file so a hub restart does not
  re-fire everything. Keys for deleted rules and sold cities are pruned each
  sweep so the file cannot grow forever.
- `notify_on_recovery` optionally sends a "back above X" message.
- A city that cannot be read is skipped with a warning; the sweep continues.

Cost is one `getCity()` per city per poll; default 30 min interval, minimum 5.
`(4) → (6)` runs every rule against live cities and prints ALERT/ok per city
without sending anything.

---

## 8. Transports

`_send(destination, event) -> bool`, never raises into the loop.

| Kind | Mechanics |
|---|---|
| **Discord** | Webhook POST. Embed with a per-type colour and title, fields for sender/city/date, body in the description. 2000-char content / 4096-char description limits enforced; falls back to plain content if an embed is rejected. Honours `429` `retry_after`. Up to 10 embeds batched per request when several events route to the same webhook in one poll. |
| **Telegram** | `sendMessage` with the hub's **own** bot token and chat id — independent of ikabot's Telegram setup. Optional `message_thread_id` gives forum-topic routing, the Telegram equivalent of per-channel splitting. 4096-char chunking. |
| **ntfy** | POST to `{server}/{topic}`, `Title` header from the event title, body as payload, `Priority` and per-type `Tags` (⚔ combat, 🏗 construction, 🚢 shipments, 🏴‍☠️ piracy …). Bearer token when set. |
| **ikabot** | Passthrough to `sendToBot(session, msg)` — routes through whatever the normal ikabot notification setup is. Costs no extra configuration and is the vanilla-friendly default. |

Retry 3× with 2/4/8s backoff. Failures are logged, counted, and summarised to the
`hub_status` destination — **rate-limited**, so a dead webhook cannot itself
become a spam source.

---

## 9. Menus

Every screen: `banner()`, double-line box header, `(0) Back` first, `read()` with
`min`/`max`/`digit`, `addThousandSeparator()` for numbers,
`daysHoursMinutes()` for durations.

```
Messaging Hub
 (0) Back
 (1) Start hub                  — launch the background watchers
 (2) Destinations               — add / rename / enable / test / delete
 (3) Message forwarding & routing — enable, interval, first-run behaviour, per-type routing
 (4) Resource monitor           — on/off, interval, rules CRUD, check-now
 (5) Formatting & filters       — body limit, combat detail, quiet hours, mutes
 (6) Diagnostics                — test destinations, dry-run scan, capture raw
                                  messages, reset seen ids, counters
 (7) Storage & global configuration — folder, global/individual per section
 (8) Import / export config     — move a working setup between accounts
```

**Menu numbers are frozen.** Slot 4 is held empty for the resource monitor rather
than renumbering later — `sequenceRunner` replays recorded keystrokes, so a
shifting menu would silently break saved sequences.

**`'` at any prompt returns to the hub's main menu**, from any depth. It is
passed through `read()`'s `additionalValues`, which is checked before any digit
or range validation, so it works even on menus that otherwise accept only
numbers. Every screen shows the hint under its title.

**Per-type routing screen** — the core of the module. Lists every event type with
its current destinations, on/off state, and lets one type be pointed at any set
of destinations:

```
  #  Type                 Enabled  Destinations
  1  Player messages        [ON]   ikariam-general
  2  Combat reports         [ON]   combat-log, phone-push
  3  Construction           [ON]   build-channel
  4  Shipments (internal)   [ON]   logistics
  …
```

**Capture raw messages** (diagnostic, Phase 1): dumps the raw fetched payloads and
parsed rows — with webhook URLs and tokens redacted — to
`messaging_hub/capture/`. This is how the icon-class and keyword tables in §6.3
get built from real data instead of guesses.

---

## 10. Runtime shape

Standard external-module structure (Explained §8, §18):

```
messagingHub(session, event, stdin_fd, predetermined_input)
  ├─ stdin reattach, predetermined_input wired
  ├─ load config
  ├─ saved-settings prompt (prompt_use_saved / local fallback)
  ├─ autostart guard: config.autostart_active and no valid config
  │    → notify, event.set(), return  (never prompt without a terminal)
  ├─ interactive menu (nothing here after event.set())
  ├─ set_child_mode(session); event.set()      ← exactly once, every path
  ├─ setInfoSignal(session, "Messaging hub is running")
  └─ _do_it()  →  try / except sendToBot / finally session.logout()
```

`_do_it()` is one loop with per-watcher next-run timestamps, sleeping to the
earliest — the same shape as `alertMessages.do_it()`. Every iteration ends in a
`wait()` with jitter. `session.setStatus()` updated each pass:
`"Hub: 3 forwarded, 0 failed, 2 rules armed"`. No prompt anywhere inside the loop
(that is what makes auto-start possible).

---

## 11. Phases

| Phase | Version | Contents |
|---|---|---|
| **1** ✅ | `1.0.0` | Skeleton, config + state store, destination book, all four transports, bundled scraper, classification v1 (source + keywords), per-type routing, dedupe, hub loop, diagnostics + **capture**, global/individual config |
| **2** | `1.1.0` | Classification refinement from captured data: icon/CSS map, per-language keyword tables, user-taught overrides UI, per-type test fixtures. **Blocked on data** — `docs/MESSAGING_HUB_DATA_REQUEST.md` |
| **3** ✅ | — | Resource monitor: rules CRUD, three modes, hysteresis + cooldown + re-arm margin, recovery notices, global and per-city rules, per-rule destinations, dry-run check |
| **4** | `1.3.0` | Formatting: Discord embeds + colours, templates, batching, quiet hours, mutes, delivery counters, rate-limited failure reporting |
| **5** | `2.0.0` | General trigger engine — any in-game condition → rule → destination: incoming attack, idle ships, city under siege, research done, pirate raid ready, treaty requests |
| **6** | `2.1.0` | Other modules emit hub events (`hub_emit(session, type, text)`), so RTM / construction manager / recruitment notifications route through the hub instead of one flat channel |

Ship each phase as its own filename version bump; committing to `modules/` on
`main` publishes it (Explained §11).

---

## 12. Definition of done, per session

Per the mandatory protocol (`Explained-user_kurzon.md` §8):

1. `python3 -c "import ast; ast.parse(open(f).read())"` on every modified file.
2. Checklist: `event.set()` exactly once per path · `set_child_mode()` before any
   game action · no bare `session.get()` · `read()` constrained · no `input()` ·
   `wait()` in every loop · exceptions caught and reported · `session.logout()` in
   `finally` · `MODULE_NAME` + `MODULE_ENTRY` present · version in filename ·
   data under `IKABOT_DATA_DIR` · `banner()` on every screen.
3. Commit and push to the working branch.
4. Report: what was coded, checklist result, decisions, limitations.

Update `GUIDE.md` (setup: creating a Discord webhook, choosing per-type channels,
the fact that the config file holds secrets) and `RELEASE_NOTES.md` when a phase
ships.

---

## 13. Known risks

| Risk | Handling |
|---|---|
| Keyword classification is language-dependent | Keywords as a per-language data table, English fallback, user overrides always win |
| Ikariam DOM changes break the scraper | Multiple endpoints tried per poll; unparsed-but-unread count still triggers a fallback "you have N new messages" notice |
| Secrets in plain JSON | Documented; file lives in `IKABOT_DATA_DIR` beside the session data; redacted from logs and captures |
| Discord rate limits on chatty types | Batching + `retry_after` handling + per-destination failure counters |
| Message flood on first run | `notify_existing` defaults to No |
| Threshold flapping | Transition-only firing, re-arm margin, cooldown, state persisted across restarts |
| Vanilla ikabot missing mod helpers | Every mod-only import guarded with a local fallback; scraper bundled, not imported |
| Many instances on one shared config | Lock-free reads, short locked writes, stale reclaim, token-verified ownership, section-level merge (§4.2) |

---

## 14. Answered — Phase 1 behaviour

1. **Combat detail** — summary by default; `formatting.combat_full_report`
   switches to the full battle export excerpt (costs extra requests per report).
2. **Alliance circulars** — their own event type, so they can be split out, but
   pointed at the player-mail destination by default.
3. **Multi-account** — shared `global_config.json` plus a per-section
   global/individual switch per account (§4.1), on top of file export/import.
4. **Message body** — full text, truncated at 900 characters, limit configurable
   and body suppressible entirely.

## 15. Testing

Phase 1 logic is covered by an offline harness that stubs every ikabot import
(so it also proves the vanilla-ikabot fallback path loads): storage paths and
account-key naming, config normalisation of hand-edited files, global/individual
merging, routing including disabled and dangling destinations, classification for
every type, row parsing, formatting limits, quiet hours, seen-id pruning and the
hard cap, delivery success/failure/retry, redaction, and the full poll pipeline
(first-run suppression, dedupe, `notify_existing`, dead server).

Locking is covered too: acquire/release, re-entrancy, stale reclaim by time and
by dead pid, refusal to trust a pid from another host, clock skew in both
directions, corrupt and hostile lock files, never deleting someone else's lock,
lock stolen mid-write (abandoned then retried), busy holder timing out with a
message, unusable location, section-level merge including "a stale reader must
not roll back a section it never edited", `.bak` recovery, and the guarantee
that an unreachable share still leaves message forwarding working.

The resource monitor is covered end to end: reading a city in all three modes
and the cases that are *not* measurable (percent with no capacity, hours-left on
a non-wine resource, wine that is not draining), threshold and re-arm-margin
maths in both directions, and the full anti-spam cycle — breach fires once, a
still-breached poll sends nothing, recovery inside the margin does not re-arm,
flapping across the line sends nothing, a clear recovery re-arms and reports,
and breaching again then fires. Plus cooldown gating, scope, per-rule
destinations, breach-key pruning, surviving a restart without re-alerting, one
unreadable city not aborting the sweep, and rule validation.

Two things to watch when writing tests here: the container often runs as
**root**, so "unwritable directory" must be simulated with a path whose parent
is a file rather than a missing directory; and a merge test only exercises a
conflict if each writer's value actually differs from the snapshot it loaded.

Keep it running against future phases — the pipeline tests are what stop a
refactor silently losing messages.
