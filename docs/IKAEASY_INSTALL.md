# IkaEasy — Install & Update

> **Current versions**
> - **ikabot mod build:** `v2.1.0`  (`IKABOT_MOD_VERSION` in `ikabot/config.py`)
> - **IkaEasy bundle:** `v1.0.3`  (`LITE_VERSION` in `ikabot/helpers/ikaEasyInject.py`, shown bottom-right in the browser)
>
> Compare these two numbers against this file on GitHub
> (`kurzonmorris/ikabot-modules`, branch `main`). If either number here is
> higher than what you have, there is an update.

IkaEasy is **built into this ikabot build** — it is not a separate option-30
module. Installing it = updating ikabot to a build that includes it
(mod `v2.1.0` or newer). Once installed it serves its own UI into the game
page through the ikabot web server, so it works in **any browser** with no
Chrome extension.

---

## Install / update (inside the container)

```
ika update            # pulls kurzonmorris/ikabot-modules @ main from GitHub
ika restart all       # restart every ikabot instance so the new code loads
```

That's it. `ika update` fetches the latest `main` (which contains IkaEasy),
and the restart makes the running instances pick it up.

To update from a local zip instead of GitHub:
```
ika update --from /config/main.zip
```
To undo the last update:
```
ika update --rollback && ika restart all
```

---

## Turn it on and check the version

1. In ikabot, start the web server — main menu **option 16**.
2. Open the printed `http://<host>:<port>` link in any browser and log in.
3. Play as normal. IkaEasy panels appear inside the relevant building views
   (town hall, tavern, transport/port).
4. **Bottom-right of the screen** you will see:
   - an **`IkaEasy: on`** button (click to turn the UI off/on), and
   - directly beneath it a **`IkaEasy v1.0.3`** version badge.

   The badge is the running bundle version — use it to confirm the update took
   and to spot when a newer version exists on GitHub.

---

## Quick fixes without a full rebuild (optional)

The JS/CSS bundle is read from disk on every request and searched in this
order, **per file**:

```
$IKAEASY_LITE_DIR  ->  ~/.ikabot/ikaeasy_lite  ->  packaged copy
```

`~/.ikabot` lives outside the image, so edits there survive rebuilds. To get
an editable copy:

```
python3 -m ikabot.helpers.ikaEasyInject
```

This copies the bundle to `~/.ikabot/ikaeasy_lite/`. Edit any file there
(e.g. `features/production.js`, `css/lite.css`), bump `LITE_VERSION` if you
want the on-screen badge to change, and just **refresh the browser** — no
rebuild. Only the files you place there override the packaged copy; everything
else falls back automatically.

Python changes (the bridge, injection, web-server hooks) still need
`ika restart all`.

---

## Requirements

- ikabot mod build **v2.1.0+** (this build).
- The ikabot **web server** (option 16) must be running; play through its link.
- Nothing else — no browser extension required.
