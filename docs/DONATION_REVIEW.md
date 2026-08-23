# Option (9) Donate — code review, failure modes, and language audit

Reviewed: `ikabot/function/donate.py` (9 → 1) and `ikabot/function/donationBot.py`
(9 → 2), against ikabot 7.5.1 / mod v1.8.5. Nothing in this document changes code —
it is a findings list.

---

## 0. What option 9 actually does

Worth stating plainly, because two of the language problems below come from this
not being written down anywhere.

Every island has two upgradeable island buildings: the **Saw mill** (the "forest")
and the island's **one luxury building** (Vineyard / Quarry / Crystal Mine /
Sulfur Pit). Any player with a city on that island can donate towards the next
level. Raising the level raises production **for everyone on the island**,
including you.

Three consequences that drive everything else:

1. **Donations are always paid in wood.** Both targets. You never donate marble,
   wine, crystal or sulfur. Neither screen says so.
2. **Donations are irreversible.** There is no withdraw.
3. Donating is a *permanent spend of wood*, not a transfer to your alliance.

- `9 → 1` **Donate once** — one city, one target, one amount, then exits.
- `9 → 2` **Donate automatically** — background process, every city, on a timer.

---

## 1. "Set donations to 100% of what I currently have"

**Yes, it is possible, and the reason it is hard to find is a wording inversion.**

### One-off, right now (9 → 1)

At the `Amount (number, all, half):` prompt, type `all`.

That donates 100% of the **wood in that city's warehouse**. It does *not* include
wood held in your Ikariam inventory, even though the screen above the prompt
prints a "Total wood available for donation" figure that includes it — see
finding **D-2**, this is a bug, not your misreading.

### Every city, on a schedule (9 → 2)

```
(9) Donate  →  (2) Donate automatically
    Enter how often you want to donate in minutes        →  e.g. 1440
    Which donation method                                →  1
    Apply same configuration to all cities               →  1
    ... donate to forest / trading good / both / none    →  f, t or b
    "maximum percentage of your storage capacity that
     you wish to keep occupied"                          →  0      ← this is the answer
```

**`0` at that last prompt = "keep nothing, donate everything I hold."** The amount
donated each cycle is `(city wood + inventory wood) − 0`.

The reason this is unguessable:

| You type | Method 1 (storage %) | Method 2 (production %) | Method 3 (fixed amount) |
|---|---|---|---|
| `0`   | **donate everything**   | turn donation off | turn donation off |
| `100` | **turn donation off**   | donate all production | (n/a) |

`0` means "off" in two of the three methods and "give away literally everything"
in the third. The prompt is phrased as *how much to keep*, while the other two are
phrased as *how much to give*. That inversion is the whole problem.

Method 3 cannot express "everything" at all — it is a fixed number.

### Two warnings before you set `0`

- It empties the city's wood **every cycle**. Nothing in `donationBot` consults the
  Resource Reservation System (`RRS_INTEGRATION_GUIDE.md`), so wood that
  Construction Manager or Auto Recruitment is holding for a queued job gets
  donated out from under them.
- Neither module checks how much the building still *needs*. Once the island
  building is at max level, or your donation exceeds the remaining requirement,
  the surplus is at best wasted and at worst rejected outright — and because of
  finding **B-2**, a rejected donation is still reported to you as donated.

A safer equivalent of "donate everything spare" is method 1 with a small non-zero
number, e.g. `5`, which keeps 5% of warehouse capacity as a floor.

---

## 2. Bugs and failure modes — `donate.py` (9 → 1)

**D-1 · The city list prints as one run-on line. (regression, 5 days old, affects
every city chooser in ikabot)**
`ikabot/helpers/pedirInfo.py:185` reads `"{: >2}: {}{}{}\ n"` — a literal
backslash, space, `n`, not a newline. Commit `fb86162` (18 Aug 2026, sequence
runner input delay) changed `\n` to `\ n`. The chooser now renders as:

```
 1: Athens  (W)\ n 2: Sparta  (M)\
```

`menu_cities[:-1]` then chops the trailing `n`, leaving a dangling `\ `. This is
the first thing you see after picking 9 → 1. One-character fix, highest priority
item in this document.

**D-2 · Inventory wood is advertised but cannot be donated.**
`donate.py:146` prints `Total wood available for donation: <city + inventory>`,
then `donate.py:167-173` caps input at `max=woodAvailable` — city wood only — and
resolves `all` to `woodAvailable`. So the screen shows a number the prompt refuses
to accept, and the only two things that *can* spend inventory wood (`all`, or
typing the total) both silently fall back to city wood. If a city has 0 wood in
store and 50 000 in inventory, the prompt accepts nothing but `0`.

**D-3 · "Donation successful." is printed unconditionally.**
`donate.py:189-203` fires the POST and never looks at the response. Rejected
because the building is mid-upgrade, session expired, amount exceeded what the
game would take — all print `Donation successful.` `donationBot` at least checks
for `provideFeedback` type 11; the interactive screen does not.

**D-4 · Any malformed island response crashes the process with a traceback.**
The whole body is wrapped in `except KeyboardInterrupt` only. Concretely:
- `donate.py:80` `wood_total_needed, wood_donated = wood_matches[:2]` → `ValueError`
  if the resource view yields fewer than 2 `<li class="wood">` matches.
- `donate.py:125` `tradegood_total_needed, tradegood_donated = tradegood_matches`
  → no `[:2]` slice here, so *any* count other than exactly 2 or 3 raises.
- `donate.py:132` `int(re.sub(r'[^0-9]', '', wood_on_inventory))` → `ValueError`
  on a match containing no digits.
- The regex `r'<li class="wood">(.*?)</li>'` has no `re.DOTALL`, so a server-side
  reformat that puts a newline inside the `<li>` breaks all of the above at once.

`event.set()` is never reached on these paths. This fork survives it — the parent
loop at `command_line.py:438` breaks out when the child dies — but you get a raw
traceback followed by the cheerful lie `'donate' is now running in the background.`
On vanilla upstream the same crash hangs the menu forever.

**D-5 · `all` / `half` on an empty city donate 0.**
`donate.py:174-180`: the `amount == 0` early return is checked *after* `all` and
`half` are resolved, so with 0 wood in the city, `all` → 0 → a pointless POST and
`Donation successful.`

**D-6 · Upgrade countdowns may be showing an absolute timestamp.**
`donate.py:51-58` takes `resourceEndUpgradeTime` straight from `backgroundData` and
feeds it to `daysHoursMinutes()` as if it were a remaining duration, printed as
`(upgrading, ends in: ...)`. If the game returns a unix timestamp rather than
seconds-remaining, that renders as tens of thousands of days. Worth eyeballing
once against a live upgrade — it is inherited from upstream and may be correct,
but nothing in the code subtracts `time.time()`.

**D-7 · No `set_child_mode()` / `session.logout()`.**
`donate.py` never calls either, unlike every long-running function. Harmless for a
one-shot, but it means the run is invisible in the process list and leaves a
server-side session behind.

---

## 3. Bugs and failure modes — `donationBot.py` (9 → 2)

**B-1 · Every error in the main loop is swallowed in silence.**
`donationBot.py:421-422` — `except Exception: continue`, with no logging and no
`sendToBot()`. This is the single worst reliability problem in the module: a bot
that fails on every city, every cycle, forever, looks *identical* to a bot that is
working. There is no log line, no notification, no status change. `sendToBot()` on
loop exceptions is item 7 of the review checklist in `Explained-user_kurzon.md`.

Things that reach this `continue` in normal operation:
- `getProductionPerHour()` raising `ValueError`/`AttributeError` when the city page
  HTML shifts (method 2 only) — `resources.py:96,108`.
- `json.loads()` on an HTML error page or a session-expiry redirect (`:387`).
- `r[0]` on a response element that is a dict rather than a list (`:388`).
- `cities_dict[cityId]["island"]` → `KeyError`, whenever the one-time island lookup
  at `:320-326` failed for that city. That failure is permanent: the lookup runs
  once at startup and is never retried, so one flaky request at launch means that
  city never donates again for the life of the process.

**B-2 · A rejected donation is counted as a successful one.**
`donationBot.py:405`:

```python
return max(0, spent) if spent > 0 else int(d_amount)
```

The verification does: compare inventory before/after; if unchanged, compare city
wood before/after; if *that* is also unchanged — i.e. demonstrably nothing was
donated — return the full requested amount anyway. So the running total and the
`Donated: X | Total: Y` status line inflate without bound while nothing happens.
Only the specific `provideFeedback` type 11 rejection is caught; a 500, a silent
refusal, an amount the game clamps to zero, all read as full success.
(`max(0, spent) if spent > 0` is also redundant — `spent` is already > 0 there.)

**B-3 · "both" mode double-counts.**
`wood_init` is captured once per city per cycle (`:341`). In `both` mode
(`:408-412`) the fallback in `execute_donation` computes `wood_init − current wood`
for the *second* donation too, which includes the wood the *first* donation already
spent. Every "both" city over-reports by roughly the first half's amount.

**B-4 · Nothing is capped against what is actually available or actually needed.**
- Method 1 `to_donate = (city wood + inventory wood) − keep` (`:354-355`)
- Method 2 `to_donate = production × pct × hours` (`:357-358`)
- Method 3 `to_donate = the fixed number` (`:359-360`)

None of the three is clamped to the wood in the city, and none looks at the
building's remaining requirement (which `donate.py` already knows how to parse).
Method 3 with `10000` in a city holding `800` posts a donation of 10 000 — which
the game will refuse — and per **B-2** reports 10 000 donated.

**B-5 · Method 1 mixes two different pools.**
`current_total_init` adds inventory wood to city wood, but `max_keep` is a
percentage of **warehouse capacity**, which inventory wood does not occupy.
Storage 10 000, keep 80% (= 8 000), city wood 8 000, inventory 5 000 → the bot
decides to donate 5 000. If the game does not in fact spend inventory wood
(see **B-7**), that 5 000 comes out of the warehouse and leaves you at 3 000 —
5 000 below the floor you asked it to respect.

**B-6 · Three full inventory fetches per city per cycle.**
`getInventoryItem()` (`:344`, `:371`, `:396`) each performs a complete
`view=inventory` request. With 10 cities in `both` mode that is 50 extra requests
per cycle, purely for accounting, on top of the `wait(1)` after every donation.

**B-7 · The inventory-wood premise is untested.**
Both modules assume item `2201` is spendable wood that a donation will draw on
(`RELEASE_NOTES.md`: "Donation amounts now include wood held in the player's
inventory"). `donate.py` displays it but won't spend it (**D-2**); `donationBot`
spends against it but its verification can't distinguish "inventory was used" from
"nothing happened" (**B-2**). Nothing anywhere confirms the game actually deducts
inventory wood on an `IslandScreen&function=donate` call. This is one manual test:
donate with a known inventory count and watch whether the count drops.

**B-8 · `_get_donation_config()` is duplicated inline.**
`:19-90` is the helper; `:201-267` is a second hand-copied version used by the
per-city branch. They have already drifted (`beeing` vs `being`). Any fix to the
prompts has to be made twice, and one of the two will eventually be missed.

**B-9 · Cosmetic / house-style.**
- `session.setStatus(f"Donated: {city_total} ...")` (`:418`, `:331`) — raw ints, no
  `addThousandSeparator()`, contrary to §9 rule 5 of `Explained-user_kurzon.md`.
- `wait(waiting_time * 60)` (`:424`) — no `maxrandom`, so the cycle lands on a
  perfectly regular clock. Every other loop in the fork jitters.
- The loop waits *after* processing, so the real period is `waiting_time + runtime`
  and drifts. Method 2 sizes its donation on `waiting_time` exactly, so it
  systematically under-donates by the drift.
- No `banner()` between config screens; the prompts scroll.

---

## 4. Language and first-run experience

Judged as someone who has never used ikabot and has only played Ikariam casually.

### Wrong information

**L-1 · `GUIDE.md:190` says option 9 is "Donate resources to your alliance."**
It is not. It donates wood to island buildings. A new user reads that line, decides
they are not in an alliance or that they do not want to gift resources away, and
never opens the menu — or worse, opens it expecting an alliance transfer and
donates their wood into an island upgrade. This is the most damaging single line
of text in the donation feature. The `GUIDE.md` entry also never mentions the
`(1) once / (2) automatically` submenu or any of the three methods.

### Prompts that cannot be answered without reading the source

**L-2 · The `0` / `100` inversion** — see §1. The fix is to phrase all three
methods as "how much to give" and to state the two edge cases in the prompt itself:

> `What percentage of your wood storage do you want to KEEP? (0 = donate everything, 100 = donate nothing, default 80)`

**L-3 · "the forest" vs "Saw mill".**
`donationBot` says *forest* (`:39`); `donate.py` says *Saw mill*
(`tradegoods_names[0]`). The game's island screen labels it differently again.
Three names for one building across two adjacent menu entries.

**L-4 · `[f/t/b/n]` with no key given.**
`In Athens (W), Do you wish to donate to the forest, to the trading good, to both
or none? [f/t/b/n]` — the mapping is inferable, but `n` for "none" sits one key
away from destroying nothing vs everything, and `(W)` is unexplained. `(W)` is the
city's trade good initial, but `Wood` and `Wine` share an initial, so a new player
reasonably reads `(W)` as wood.

**L-5 · Three near-identical wood labels on the one-shot screen** (`donate.py:145-147`):

```
Wood on inventory available: 12.000
Total wood available for donation: 62.000
Wood available: 50.000
```

Printed in that order — inventory, total, city. The last one, "Wood available", is
the only figure the prompt below will actually accept, and it is the least
distinctly named of the three. Rename to `In this city's warehouse:` /
`In your inventory:` / `Total:`, order them warehouse → inventory → total, and mark
which one the amount is capped to.

**L-6 · "What is the amount would you like to donate?"** (`:82`, `:253`) — broken
grammar, and "amount" here means *wood*, unstated. Also `rigth` (`donate.py:90`) and
`beeing` (`donationBot.py:235`).

**L-7 · "Will donate 5.000 to the Saw mill?"** (`donate.py:181`) — a statement
punctuated as a question, immediately followed by the actual question
`Proceed? [Y/n]`. Drop the `?`.

**L-8 · Nothing states the two facts a beginner most needs.**
Neither screen says *donations are paid in wood* or *donations cannot be undone*.
The auto bot's only confirmation is `I will donate every 1440 minutes.` — it never
plays back *what* it will donate, to *which* buildings, in *which* cities, before
starting a process that will empty warehouses on a schedule. Every other
settings-memory module in the fork prints a summary; this one prints a summary only
on the *reuse-saved-settings* path (`:138-148`), which is exactly the path where the
user has already seen it once.

**L-9 · No mention of the RRS interaction anywhere in the UI or the guide** — see §1.

---

## 5. Suggested order of work

| # | Item | Why first |
|---|---|---|
| 1 | **D-1** `\ n` → `\n` in `pedirInfo.py:185` | One character, visible regression, breaks every city chooser in the app |
| 2 | **L-1** correct `GUIDE.md:190` | Actively misinforming; costs nothing |
| 3 | **B-1** log + `sendToBot()` instead of bare `continue` | Everything else is undiagnosable until this lands |
| 4 | **B-2/B-3** stop crediting unverified donations | The status line is currently fiction |
| 5 | **L-2** re-word the three percentage prompts, state `0`/`100` inline | The actual reported problem |
| 6 | **D-2** let the one-shot spend inventory wood, or stop printing a total it won't accept | Consistency between the two screens |
| 7 | **B-4** clamp to available wood and to the building's remaining need | Stops silent waste |
| 8 | **D-3** check the POST response before claiming success | |
| 9 | **B-7** verify item 2201 behaviour against a live account | Decides how 6 and B-5 should be resolved |
| 10 | **D-4** widen the `except`, slice defensively, `re.DOTALL` | |
| 11 | **B-8** delete the duplicated config block | |
| 12 | **L-3/4/5/6/7/8**, **B-9** wording and house-style pass | |
