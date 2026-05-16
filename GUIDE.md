# Ikabot Setup & User Guide

---

## QUICK-START CHECKLIST

Work through this list top to bottom on each account before doing anything else.

```
SETUP
─────────────────────────────────────────────────────────────
[ ] 1. Create one folder per account, e.g.:
        C:\ikabot\account-alice\   (copy the full dist\ikabot\ folder here)
        C:\ikabot\account-bob\
        C:\ikabot\account-charlie\

[ ] 2. Create a desktop shortcut for each folder
        Right-click ikabot.exe → Send to → Desktop (create shortcut)
        Right-click shortcut → Properties → set "Start in" to the account folder
        Rename the shortcut, e.g.  "Ikabot – Alice"

[ ] 3. Double-click the shortcut to launch
        FIRST LAUNCH: enter your Ikariam email and password when asked,
        then choose to save credentials to the vault.
        ALL FUTURE LAUNCHES: enter your vault master password, pick your account.

[ ] 4. Set a proxy  →  21 → 1
        Use a dedicated proxy per account if accounts will trade with each other.
        Use shared proxies if accounts are independent (cheaper).
        See Section 9 for the recommended provider.

[ ] 5. Set up notifications  →  21 → 2
        Connect Telegram, Discord or ntfy.sh so ikabot can alert you
        when tasks finish, attacks happen or wine runs low.

TASKS TO START IMMEDIATELY (leave running in background)
─────────────────────────────────────────────────────────────
[ ] 6. Start Web Server          →  16
        Opens the browser dashboard so you can see all running tasks.
        Do this before starting anything else.

[ ] 7. Start Shrine bot          →   5
        Donates to the shrine every 12 hours automatically.

[ ] 8. Start Login Daily         →   6
        Collects your daily reward and handles recurring wine deliveries.

[ ] 9. Start Miracle (if any)    →  11
        Activates your island's miracle on a timer.

EXTERNAL MODULES (optional but very useful)
─────────────────────────────────────────────────────────────
[ ] 10. Configure module folder  →  30 → 99
         Point ikabot at the folder containing the .py module files.

[ ] 11. Start Construction Manager  →  30 → (its number after refresh)
         Define your build queue and let it work through upgrades automatically.

[ ] 12. Start Resource Transport Manager  →  30 → (its number)
         Automate bulk resource shipping between cities.
```

---

## 1. Storage Layout — Multiple Instances

Each ikabot instance needs its **own folder** so the processes don't collide.
A clean layout looks like this:

```
C:\ikabot\
│
├── account-alice\        ← copy of the dist\ikabot\ folder
│   └── ikabot.exe
│   └── _internal\
│
├── account-bob\          ← another copy
│   └── ikabot.exe
│   └── _internal\
│
└── account-charlie\      ← another copy
    └── ikabot.exe
    └── _internal\
```

> **Why separate folders?**  The `_internal\` folder next to the exe contains
> all supporting Python files.  Sharing it between instances causes file-lock
> conflicts.  Copy the whole `dist\ikabot\` output folder once per account.

---

## 2. Shortcut Links — Quick Access

Instead of navigating into each folder every time, create a **desktop shortcut**
for each instance:

1. Right-click **ikabot.exe** → *Send to → Desktop (create shortcut)*
2. Right-click the new shortcut → *Properties*
3. In **Start in**, enter the account's folder path
   (e.g. `C:\ikabot\account-alice`)
4. Rename the shortcut to something obvious — `Ikabot – Alice`, `Ikabot – Bob`

You can then double-click any shortcut to open that account's ikabot in its own
console window, all running at the same time independently.

---

## 3. Logging In

### First-ever launch
You will be asked for your **Ikariam email and password** directly.
After a successful login ikabot will offer to save those credentials to the
**credential vault** — say yes.  You only need to type the game password once.

### Every launch after that
The first prompt you see is the **vault master password** — this is the password
you chose when the vault was created, *not* your Ikariam password.  Enter it,
pick your account from the list, and ikabot logs you in automatically.

> **Tip:** the vault master password protects all stored accounts.  Choose
> something strong but memorable — ikabot never writes it to disk.

---

## 4. Credential Vault

The vault stores your email, password and session tokens encrypted on disk.
On future launches you just enter your **vault master password** once and pick
your account from the list — no typing the game password every time.

- Vault file: `%APPDATA%\.ikabot_vault`
- Multiple accounts can be stored in a single vault
- Accounts are listed in natural alphabetical order
- Manage accounts via **21 → 9** (add, remove, rename, change master password)

---

## 5. Main Menu — Every Option Explained

```
╔══════════════════════════════════╗
║         IKABOT  MENU             ║
╚══════════════════════════════════╝

 (0)  Exit
 (1)  Construction list
 (2)  Send resources
 (3)  Distribute resources
 (4)  Account status
 (5)  Activate Shrine          ← START HERE
 (6)  Login daily              ← START HERE
 (7)  Alerts / Notifications
 (8)  Marketplace
 (9)  Donate
(10)  Activate vacation mode
(11)  Activate miracle         ← START HERE
(12)  Military actions
(13)  See movements
(14)  Construct building
(15)  Update Ikabot
(16)  Ikabot Web Server        ← START HERE
(17)  Auto-Pirate
(18)  Research
(19)  Attack / Grind barbarians
(20)  Dump / Monitor world
(21)  Options / Settings
(22)  Consolidate resources
(23)  Set Production of Saw mill / Luxury good
(30)  External Modules
```

| # | What it does |
|---|---|
| **1** | Shows all queued building upgrades across your cities with estimated completion times. |
| **2** | Manually sends a one-off resource shipment from one city to another. |
| **3** | Automatically balances a chosen resource across all your cities — useful for keeping wine topped up everywhere. |
| **4** | Displays a summary of all your cities: population, resources, buildings. |
| **5** | **Shrine** — donates to the shrine on a schedule (every 12 hours) to maintain divine favour. Set and forget. |
| **6** | **Login Daily** — collects your daily bonus automatically. Also handles wine deliveries and other recurring tasks. |
| **7** | Configure alerts: get notified when you are attacked or when wine is running low. |
| **8** | Buy or sell resources on the marketplace. |
| **9** | Donate resources to your alliance once or on a recurring schedule. |
| **10** | Enables vacation mode on your account. |
| **11** | **Miracle** — activates the island miracle on a timer. Very useful if your island has a beneficial miracle. |
| **12** | Military sub-menu: train army, send troops/ships, upgrade units. |
| **13** | Shows all fleet and army movements currently on the map. |
| **14** | Constructs a single new building in a chosen city slot. |
| **15** | Checks for and applies ikabot updates. |
| **16** | **Web Server** — launches the ikabot browser dashboard. Lets you monitor and control the bot from any browser on your network. Start this first so you can see what is running. |
| **17** | Auto-Pirate — runs piracy missions automatically. |
| **18** | Queues and automates technology research. |
| **19** | Automatically attacks barbarian villages to farm resources. |
| **20** | Scans and saves a snapshot of the game world for scouting. |
| **21** | Options / Settings — see section 6. |
| **22** | Pulls scattered resources from multiple cities into one. |
| **23** | Adjusts the production level of your saw mill or luxury goods building. |
| **30** | External Modules — community add-ons (see section 8). |

---

## 6. Options / Settings Menu (21)

```
 (0)  Back
 (1)  Configure Proxy
 (2)  Notification Setup
 (3)  Kill tasks
 (4)  Configure captcha resolver
 (5)  Logs
 (6)  Import / Export cookie
 (7)  Load custom ikabot module
 (8)  Developer Data
 (9)  Manage credential vault
```

| # | What it does |
|---|---|
| **1** | Set an HTTP/HTTPS proxy for this account. Each account should have its own proxy if you plan to do trading between them. |
| **2** | Connect Telegram, Discord or ntfy.sh so ikabot can send you alerts when tasks finish or attacks happen. |
| **3** | Kill a specific background task (e.g. stop the shrine bot without closing the whole program). |
| **4** | Configure a captcha-solving service for automated captcha handling. |
| **5** | View the log file for this account (useful for diagnosing issues). |
| **6** | Manually import or export a session cookie. |
| **7** | Load a one-off custom Python module. |
| **8** | Raw developer/diagnostic data. |
| **9** | Manage the credential vault: add, remove, rename accounts, change master password. |

---

## 7. The Four Things to Start Straight Away

Once logged in, launch these four tasks and leave them running in the background:

### (16) Web Server
Starts a small local web dashboard.  Open `http://localhost:PORT` in your
browser to see all running tasks, their status and logs at a glance.
Do this first — it makes managing everything else much easier.

### (5) Activate Shrine
Donates to the shrine automatically every 12 hours.  Keeps your divine favour
topped up without any manual effort.  Just select which god(s) to donate to and
it runs forever in the background.

### (6) Login Daily
Collects your daily reward automatically.  You can also configure it to handle
wine distribution to your cities and other recurring bonuses.

### (11) Activate Miracle
If your island has a beneficial miracle (forge, parthenon, etc.) this activates
it on a timer.  Select the miracle type, set the interval, and ikabot handles
the rest.

---

## 8. External Modules (30)

External modules are community-built `.py` files dropped into a folder you
configure via **30 → 99 (Configure directories)**.  Ikabot detects them
automatically and lists them from number 31 upwards.  Hit **100** to refresh
the list after adding new files.

### Construction Manager
A powerful multi-city build queue backed by a CSV file.  You define a list of
buildings to upgrade (across any number of cities) and the manager works through
them one by one — checking resources, requesting transports if needed, and
notifying you when each upgrade completes.  The queue persists across restarts.

### Resource Transport Manager (v8.1.1)
Automates bulk resource shipping between cities on a schedule.  Define routes
(source city → destination city → resource → quantity) and the manager sends
ships repeatedly, respecting fleet availability and harbour capacity.  Supports
partial notifications (summary per batch) or full per-shipment alerts.

### Tavern Manager
Monitors the satisfaction level in each of your cities and adjusts the tavern's
wine-serving level to keep happiness at your chosen target — no more manually
tweaking taverns across a dozen cities.

### Auto Recruitment Manager
Recruits military units or ships automatically.  You specify what to build and
how many; the manager spreads the order across all available barracks or
shipyards so everything finishes at roughly the same time.  Handles resource
shortages with intelligent retry logic.

### Sequence Runner *(work in progress)*
Record a list of menu inputs (e.g. `16, enter, 5, 1`) and replay them with one
keypress.  Useful for repeating a fixed startup routine across multiple accounts
without typing the same inputs every time.  Note: this module is still being
developed and some edge cases may not be fully handled yet.

---

## 9. Proxies — Running Multiple Accounts Safely

Using a proxy per account prevents Ikariam's servers from seeing multiple
accounts logging in from the same IP address, which can trigger bans.

### Setting a proxy in ikabot
Go to **21 → 1 (Configure Proxy)** and enter your proxy in the format:

```
http://username:password@proxy-host:port
```

Webshare gives credentials in `host:port:user:pass` format — just rearrange
them to `http://user:pass@host:port` when entering into ikabot.

### Recommended proxy provider — Webshare
A cost-effective option for residential/datacenter proxies is
[**Webshare**](https://www.webshare.io/?referral_code=21hx8p6qit9m) —
around £4–5 per month for a starter plan.

**Which plan to choose:**

| Scenario | Recommendation |
|---|---|
| Trading resources between your own accounts | **Individual (dedicated) proxies** — one per account. Ikariam detects trade patterns between IPs; dedicated IPs make each account look fully independent. |
| Running bots that do not trade with each other (shrine, dailies, building) | **Shared proxies** are fine and cheaper. All your accounts share a pool of IPs. The limitation is that they cannot interact with each other without raising flags. |

> **Rule of thumb:** if two of your ikabot accounts will ever send resources to
> each other or trade on the same marketplace, give them dedicated proxies.
> For purely independent accounts (different servers, or no interaction) shared
> proxies work perfectly well and save money.

---

## 10. Notifications Setup

Go to **21 → 2 (Notification Setup)** to connect one or more alert backends:

- **Telegram** — create a bot via @BotFather, paste the token and your chat ID.
- **Discord** — create a webhook in your Discord server settings, paste the URL.
- **ntfy.sh** — open-source push notifications; set a topic and optional token.

Once configured, ikabot will message you when tasks complete, attacks are
detected, or wine runs critically low — even if your PC is locked.
