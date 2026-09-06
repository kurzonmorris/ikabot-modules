# Kurzon — How I Work & What I Need

> **Purpose:** Read this file at the start of every session to understand how Kurzon operates, what he expects, and what regular tasks look like. Pair with `Explained-ikariam_ikabot.md` for full technical context.

---

## 1. Communication Style

- Short, direct requests. Does not explain context unless asked.
- Requests often say "increase version", "add X to Y", "fix this error" — find the relevant files yourself.
- Shows log output or tracebacks verbatim when reporting bugs.
- "Revert" means `git revert HEAD --no-edit`.
- After every completed task: **commit and push to the branch without being asked.**

### GitHub Branch Naming
- Branch names must reflect the **topic or feature** being worked on — the name should tell you what the branch is for without needing to look inside it.
- The key descriptive word(s) must appear in the branch name. Examples:
  - Work on documentation → branch contains `documentation`
  - Work on the tavern manager → branch contains `tavern-manager`
  - A bug fix for transport routing → branch contains `transport-routing-fix`
- Use hyphens to separate words, all lowercase.
- **An explicitly requested branch name always wins over this rule.** Kurzon
  sometimes asks for a specific name (e.g. `ikabot-modified-<random letters>`);
  use exactly what he asked for and do not "correct" it to a topic name.
- Once a branch is named, keep using it until told otherwise — do not push to
  `main` or to a previous branch without being asked.

---

## 2. Platform & Environment

### Devices
- **Primary device:** Steam Deck (SteamOS, desktop mode) — and soon a Steam Machine running the same.
- **Server:** Unraid. Historically a Windows VM; **now also a Linux Docker
  container**, which is where much of the running happens.
- **Both must keep working.** Code and instructions have to be correct on
  Windows *and* in the Linux container — do not "fix" one by breaking the
  other.
- **Development instructions** should still default to **Windows** (paths like `%APPDATA%`, `.bat` scripts, PowerShell, Windows path separators) unless Linux/SteamOS/Docker is explicitly requested.

### Docker specifics
- The appdata volume is mounted at **`/config`**, and `HOME=/config`, so
  anything a module writes to `~` lands there and persists. Confirm with
  `echo $HOME` before assuming.
- Modules live at `/config/modules/`, so data written beside a module also
  persists — but see §27 of the ikabot reference: prefer `IKABOT_DATA_DIR`.
- **Many instances run at once** (around 24 accounts). Assume concurrency:
  per-account file names, no shared mutable files, and pid checks that are
  namespace-aware. See §27 of the ikabot reference.

### OS context when giving instructions
- If explaining how to run or install something: assume the user is on the **Windows VM** unless told otherwise.
- If the task is server-related: assume **Unraid**.
- If the task is desktop/gaming related: assume **SteamOS**.

---

## 3. File Naming Conventions

- File names should be **descriptive and self-explanatory** — the name alone should tell you what the file does. Examples: `resourceTransportManager`, `constructionManager`, `tavernManager`.
- Use **camelCase** for multi-word names.
- Version numbers go **before the file extension**, separated by `_v`:
  ```
  resourceTransportManager_v10.3.1.py
  constructionManager_v2.1.9.py
  ```
  This exact form matters — the installer parses `_vX.Y.Z` out of the stem to
  show installed-vs-available versions, and strips it when copying the file
  into the user's modules folder. See section 11 of
  `Explained-ikariam_ikabot.md` for why the stripping is load-bearing.
- Version number format: `MAJOR.MINOR.PATCH`
  - **MAJOR** — significant/breaking update
  - **MINOR** — new feature or meaningful improvement
  - **PATCH** — bug fix

---

## 4. Workflow

- Kurzon runs multiple ikabot instances simultaneously — one per game account. Each opens separately and enters the vault password. Concurrent vault access is expected and handled.
- External modules are the primary way new features are delivered. They are dropped into a configured folder, not merged into core ikabot files.
- Automation sequences (AutoHotkey scripts historically, Sequence Runner going forward) pre-load a fixed set of menu selections to perform a "daily routine" without manual input.

---

## 5. Version Update Rules

- `IKABOT_VERSION` — only changes when syncing with upstream ikabot releases.
- `IKABOT_MOD_VERSION` — increments when significant features or fixes are added to the mod.
- External module filename version — increments when the module has a releasable update.
- Changing a version number means: update `config.py` AND rename the version marker file (`ikabot/version_vX.Y.Z`).
- **Never change version numbers without being explicitly told the new version number.**
- When a version bump is requested: only change what was explicitly asked — do not change other version numbers.

---

## 6. Code Standards Kurzon Expects

- No unnecessary comments — only comment when the WHY is non-obvious.
- No docstrings beyond a one-liner maximum.
- No error handling for impossible cases.
- No feature flags or backwards-compat shims.
- Minimal, focused changes — fix exactly what was asked, nothing more.
- External modules must be self-contained — not require changes to ikabot core files unless truly necessary.
- Don't add features, refactor, or introduce abstractions beyond what the task requires.
- Don't design for hypothetical future requirements.

---

## 7. Frequently Requested Operations

| What Kurzon says | What it means |
|------------------|---------------|
| "Check the logs" | Read from `LOGS_DIRECTORY` (`~/.ikabot/logs/`) for the relevant account |
| "Add to the guide" | Update `GUIDE.md` (end-user facing, no build/compile references) |
| "Add to release notes" | Update `RELEASE_NOTES.md` |
| "Make it work like RTM" | Follow the Resource Transport Manager's pattern for that feature |
| "Add notification support" | Use `notificationDataIsValid()` + `sendToBot()`, not Telegram-specific calls |
| "Revert" | `git revert HEAD --no-edit` |
| "Increase version" | Only change the version explicitly named — nothing else |

---

## 8. Mandatory Post-Coding Review Protocol

After every coding session, before declaring work complete:

### Step 1: Syntax check
```bash
python3 -c "import ast; ast.parse(open('myfile.py').read()); print('OK')"
```
Run for every modified file.

### Step 2: Review checklist
- [ ] `event.set()` called exactly once in every code path
- [ ] `set_child_mode()` called before any game actions
- [ ] No bare `session.get()` calls (always pass a view parameter)
- [ ] All `read()` calls have appropriate `min`/`max`/`digit` constraints
- [ ] No `input()` calls — only `read()`
- [ ] All loops have a `wait()` call to prevent server hammering
- [ ] All exceptions caught in long-running loops with `sendToBot()` notification
- [ ] No endless loops possible (all loops have a termination condition or `wait()`)
- [ ] `session.logout()` in `finally` block
- [ ] New functions imported and registered in `command_line.py` if built-in
- [ ] `MODULE_NAME` and `MODULE_ENTRY` defined if external module
- [ ] Version number in filename if external module
- [ ] Data files stored under `IKABOT_DATA_DIR`
- [ ] `banner()` called at start of each interactive screen

### Step 3: Commit and push
All changes committed to the working branch and pushed to `kurzonmorris/ikabot-modules`.

### Step 4: Report
Provide a summary covering:
1. What was coded (each function/change listed briefly)
2. Result of the review checklist (any issues found and fixed)
3. Any design decisions or trade-offs made
4. Any known limitations or future considerations

---

## 9. Visual & Structural Consistency Rules

All modules — internal and external — must look and behave the same way.

### Visual rules
1. Every interactive screen starts with `banner()` — clears screen, shows ASCII art with both version tags.
2. Section headers use the double-line box style:
   ```
   ╔══════════════════════════════════════════════════╗
   ║          MY MODULE NAME                          ║
   ╚══════════════════════════════════════════════════╝
   ```
3. Use `bcolors.GREEN`, `bcolors.RED`, `bcolors.WARNING`, `bcolors.ENDC` for coloured feedback.
4. Menus always start at `(0) Back` or `(0) Exit`.
5. Numbers formatted with `addThousandSeparator()`.
6. Time durations formatted with `daysHoursMinutes()`.
7. All `read()` calls must have correct `min`/`max`/`digit` constraints.

### Structural rules
1. Entry function → interactive config → `set_child_mode()` + `event.set()` → `do_it()`.
2. Separate interactive config from the working loop — never mix them.
3. `do_it()` handles the main loop or one-shot action.
4. Long-running tasks use `while True:` with `wait()` between iterations.
5. All errors caught and sent via `sendToBot()`.
6. Process status updated via `session.setStatus("...")` and `updateProcessList()`.
7. `setInfoSignal(session, info)` called after `set_child_mode()`.

---

## 10. Notification Rules

- Always use `notificationDataIsValid(session)` — not Telegram-specific checks — unless Telegram is specifically required.
- All notification calls go through `sendToBot(session, message)`.
- Three backends supported: Telegram, Discord, ntfy.sh. Never assume only Telegram.

---

## 11. Existing Modules (Reference)

| Module | File | What it does |
|--------|------|--------------|
| Resource Transport Manager | `modules/resourceTransportManager_v10.9.0.py` | Resource movement, ship routing, notifications. Priority scheduling (1–5), trading-port hold list, cycle deadlines, supervised background worker, per-account logs |
| Resource Reservation System | `modules/resourceReservationSystem_v1.0.0.py` | Reserves resources across cities to prevent over-spending. See `RRS_INTEGRATION_GUIDE.md` |
| Resource Production Manager | `modules/resourceProductionManager_v1.0.3.py` | Manages production assignment across cities |
| Construction Manager | `modules/constructionManager_v2.1.9.py` | CSV-backed multi-city building upgrade queue |
| Tavern Manager | `modules/tavernManager_v2.0.1.py` | Monitors wine and satisfaction, auto-adjusts tavern settings |
| Auto Recruitment Manager | `modules/autoRecruitmentManager_v2.12.1.py` | Automates unit and ship training across barracks/shipyards |
| Island Colonize Monitor | `modules/islandColonizeMonitor_v1.5.0.py` | Monitors islands for colonisation opportunities |
| Sequence Runner | `modules/sequenceRunner_v1.1.2.py` | Stores named input sequences to automate daily routines |
| Scheduler Monitor | `modules/schedulerMonitor_v1.0.0.py` | Watches other modules' background workers and restarts any that stopped |

**Filenames drift.** Always `ls modules/` rather than trusting this table — the
versions move faster than the docs.

---

## 12. Repository Layout (Reference)

```
ikabot-modules/
├── ikabot/                   ← mod source (core ikabot + kurzon changes)
├── modules/                  ← active external modules (drop-in folder)
├── installer/                ← mod installer scripts
├── tools/                    ← standalone utilities
├── releases/                 ← downloadable zips
├── config-examples/          ← example CSV/config files
├── archive/                  ← deprecated/reference material
├── wip/                      ← non-functioning or prototype code
├── GUIDE.md                  ← end-user guide
├── RELEASE_NOTES.md          ← changelog
├── MIGRATION_GUIDE.md
├── RRS_INTEGRATION_GUIDE.md
├── Explained-ikariam_ikabot.md  ← full technical reference
└── Explained-user_kurzon.md     ← this file
```

---

*Last updated: 2026-08-02. Reflects ikabot 7.4.5 / mod v1.7.6.*
