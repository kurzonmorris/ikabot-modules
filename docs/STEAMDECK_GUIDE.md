# Running ikabot on a Steam Deck

> For a Steam Deck with Docker already installed. Everything you type is in a
> grey box — copy the whole box, paste it, press Enter.
>
> If Docker is **not** installed yet, do Appendix A first.

---

## Part 0 — What you are about to get

The same container that runs on the server: all your instances in one Docker
container, a web control panel, and a web terminal. The Deck is a normal
x86-64 Linux PC underneath, so nothing is cut down here — `--network=host`
works, which means the per-instance web servers (ikabot menu option 16) are
reachable, exactly as on Unraid and unlike on Windows.

Two things are genuinely different about a Deck, and both are covered below:

- **It goes to sleep.** A sleeping Deck runs nothing. Part 6 turns that off.
- **A SteamOS update wipes anything installed with `pacman`.** Your accounts
  and settings are not affected. Part 10 gets you back in two commands.

> **Read Part 11 before you log any account in** if these are accounts you
> already run on the server. Running one account in two places at once is the
> one thing that can actually cost you resources.

---

## Part 1 — Get to a desktop and a terminal

1. Press the **STEAM** button
2. **Power**
3. **Switch to Desktop**

The Deck reboots into a normal KDE desktop. Open **Konsole** — it is in the
taskbar, or press the **Steam** icon (bottom-left) → **System** → **Konsole**.

Everything from here happens in Konsole.

> **Typing.** Press **STEAM + X** for the on-screen keyboard. A USB or
> Bluetooth keyboard is far less painful for this — it is a one-off job.

---

## Part 2 — Check Docker is actually running

Installed is not the same as running. Paste this:

```bash
docker info > /dev/null 2>&1 && echo "Docker is ready" || echo "Docker is NOT ready"
```

If it says **ready**, skip to Part 3.

If it says **NOT ready**, these two commands fix the usual causes — the
service not being switched on, and your user not being allowed to talk to it:

```bash
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

Then **log out and back in** (Steam icon → Leave → Log Out), reopen Konsole,
and run the check again. The group change does not apply to a terminal that
was already open.

> **If `sudo` asks for a password you never set**, set one now with `passwd`.
> A fresh Deck has no password for the `deck` user, and `sudo` will not work
> without one.

---

## Part 3 — Get the installer

Download the newest `ikabot-docker_vX.Y.Z.zip` from the releases page:

**https://github.com/kurzonmorris/ikabot-modules/releases**

**The easy way.** Open the releases page in Firefox on the Deck, tap the zip to
download it, then in Dolphin right-click it → *Extract* → *Extract archive
here*. Then in Konsole:

```bash
cd ~/Downloads/ikabot-docker*
ls
```

**Or in Konsole.** On the releases page, right-click the zip → *Copy Link*,
and paste it in place of the URL below:

```bash
cd ~/Downloads
curl -L -o ikabot-docker.zip "PASTE_THE_LINK_HERE"
file ikabot-docker.zip
rm -rf ikabot-docker
unzip -q ikabot-docker.zip -d ikabot-docker
cd ikabot-docker
ls
```

`file` must say **Zip archive data**. If it says *HTML document*, the link
pointed at GitHub's page for the file rather than the file — use the
`raw.githubusercontent.com` address, or the *Download raw file* button.

The `rm -rf` matters when you are updating: unzipping over an old copy leaves
the previous `docker/ika-panel_v*` behind next to the new one, and the build
then has two panels to choose from.

You should see `INSTALL.bat`, `install.sh`, `README.txt` and a `docker`
folder. Ignore `INSTALL.bat` — that one is for Windows.

---

## Part 4 — Run the installer

```bash
chmod +x install.sh
./install.sh
```

It asks three questions:

| Question | Answer |
|---|---|
| Where should ikabot keep its data? | Press Enter for `/home/deck/ikabot` |
| How many accounts will you run? | However many you want on the Deck |
| Choose a password for the web pages | Anything — you will need it in a moment |

Take the default folder. `/home/deck` is on the partition that survives
SteamOS updates; almost nothing else on the Deck is.

The build takes a few minutes the first time. When it finishes it prints your
two addresses:

```
  Control panel : http://192.168.1.42:7682
  Terminal      : http://192.168.1.42:7681
```

> **How many instances?** The Deck has 16 GB of RAM and 4 cores, so it can
> take a lot — but start with 4 or 5, get them logged in and watch it for a
> day before going higher. The 64 GB base model is also tight on disk: the
> image is about 1 GB.

---

## Part 5 — Log your accounts in

Open the **terminal** address in Firefox on the Deck — or just
`http://localhost:7681`, which works from the Deck itself. Sign in with
username `ikabot` and the password you chose.

You get the ikabot screens, one per account:

| Key | Does |
|---|---|
| **Ctrl-B** then **W** | List the instances, pick one with the arrow keys |
| **Ctrl-B** then **N** | Next instance |
| **Ctrl-B** then **D** | Leave them all running and detach |

Log each account in once. After that the control panel at port **7682** does
the day-to-day work — restarts, updates, modules, logs.

> **No vault on this Deck?** There will not be one on a fresh install, so
> nothing logs itself in. Rather than typing every account into every window,
> open the control panel on port **7682**, go to **Accounts**, paste them all
> in and save once. Then press **Restart all** and each window starts on its
> own account. Full details in `docs/DOCKER_GUIDE.md`.

---

## Part 6 — Stop the Deck going to sleep

**This is the one that matters.** A suspended Deck runs nothing: the container
freezes, and your bots stop until you wake it.

In Desktop Mode:

1. **System Settings** → **Power Management**
2. **Energy Saving**
3. Turn **off** *Suspend session*
4. Leave *Screen Energy Saving* on if you like — a dark screen is fine, it is
   only suspend that stops everything

Then, so it does not sleep the moment you are not touching it:

- Keep it **plugged in**. On battery the Deck suspends far more eagerly, and
  it will flatten itself overnight anyway.
- **Stay in Desktop Mode.** Game Mode has its own sleep behaviour and will
  suspend the Deck for you.
- Do not press the power button — that suspends it immediately.

To check it really stayed awake overnight, look for a gap in the times in one
of your logs — the **Files** section of the control panel shows them as a
table, newest at the top. Hours missing in the middle means it slept. A
container that says `Up 14 hours` in `docker ps` does **not** prove it was
awake: a suspended Deck still counts the time.

---

## Part 7 — Surviving reboots

Already handled: the container was started with `--restart unless-stopped`,
and Part 2 enabled the Docker service. So after a reboot into Desktop Mode
everything comes back on its own.

Check it with:

```bash
docker ps
```

If the container is not listed after a reboot, the Docker service did not
start — run `sudo systemctl enable --now docker` again.

---

## Part 8 — Reaching it from your phone or PC

The addresses the installer printed use the Deck's LAN IP, so any device on
the same network can open the control panel. If you have forgotten the IP:

```bash
docker exec ikabot ika panel
```

For access from outside the house, Tailscale works the same way here as on the
server — install it, and use the Deck's Tailscale address instead of the LAN
one. Note the Deck's LAN IP will change if it gets a new DHCP lease; Tailscale
addresses do not.

---

## Part 9 — Everyday commands

Identical to the server. All of them work from Konsole:

| Do | Command |
|---|---|
| Open the instances | `docker exec -it ikabot ika attach` |
| See what is running | `docker exec -it ikabot ika status` |
| Restart instance 3 | `docker exec -it ikabot ika restart 3` |
| Restart the crashed ones | `docker exec -it ikabot ika restart dead` |
| Update ikabot | `docker exec -it ikabot ika update` |
| Update the modules | `docker exec -it ikabot ika modules` |
| Update the control panel | `docker exec -it ikabot ika panel upgrade` |
| Stop everything | `docker stop ikabot` |
| Start it again | `docker start ikabot` |

Typing `docker exec -it ikabot` every time gets old. This makes `ika` work on
its own, and survives reboots:

```bash
echo "alias ika='docker exec -it ikabot ika'" >> ~/.bashrc
source ~/.bashrc
```

Then it is just `ika status`, `ika restart 3`, and so on.

---

## Part 10 — After a SteamOS update

SteamOS replaces its whole system partition when it updates, so anything
installed with `pacman` — including Docker — can disappear. What does **not**
disappear is `/home/deck`, which is where your accounts, sessions, modules and
settings live.

So if Docker is missing after an update:

1. Reinstall it — Appendix A
2. Re-run the installer from the folder you unzipped:

```bash
cd ~/Downloads/ikabot-docker
./install.sh
```

Give it the **same folder** (`/home/deck/ikabot`) when it asks. It rebuilds the
image and reuses the config that is already there, so your accounts come back
logged in. Nothing is lost and you do not set anything up again.

> Worth doing once now, while you remember: keep a copy of
> `/home/deck/ikabot/config` somewhere else. It contains your vault. A backup
> of that folder is a backup of everything that matters.

---

## Part 11 — Do not run the same account in two places

If these are accounts you already run on the server, read this before logging
any of them in here.

**One ikabot account should run in one place at a time.** Two copies of the
same account, on two different machines, will both try to ship, build and
recruit — and the lock files that stop that happening are per-machine, so they
cannot help you across two boxes. The result is duplicated actions and lost
resources.

So pick one:

- **Different accounts on the Deck** — safe, nothing to think about
- **Moving accounts from the server to the Deck** — stop them on the server
  first (**Stop tasks**, then close those instances) before starting them here
- **The Deck as a spare** — set it up, log in, then `docker stop ikabot` and
  leave it. Start it only if the server is down

Copying `config` across from the server brings the vault with it, which saves
re-entering logins — but it does not make running both at once any safer.

---

## Appendix A — Installing Docker on SteamOS

Only needed if `docker` is missing. This unlocks the read-only system
partition, which is normal on a Deck but worth knowing you are doing.

```bash
passwd                                    # only if you have never set one
sudo steamos-readonly disable
sudo pacman-key --init
sudo pacman-key --populate archlinux holo
sudo pacman -S --noconfirm docker
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

Log out and back in, then check:

```bash
docker info > /dev/null 2>&1 && echo "Docker is ready"
```

You can leave the filesystem unlocked or re-lock it with
`sudo steamos-readonly enable` — either way, the next SteamOS update undoes
all of this, which is what Part 10 is for.

---

## If something goes wrong

| Symptom | Why | Fix |
|---|---|---|
| `permission denied ... docker.sock` | Your user is not in the docker group, or you have not logged out since | `sudo usermod -aG docker "$USER"`, then log out and back in |
| `Cannot connect to the Docker daemon` | Service not running | `sudo systemctl enable --now docker` |
| `sudo: no password` / it rejects you | The `deck` user has no password | `passwd` |
| Bots stop overnight | The Deck suspended | Part 6 |
| Everything vanished after a system update | SteamOS re-imaged the system partition | Part 10 — your data is fine |
| `docker: command not found` after an update | Same cause | Appendix A, then Part 10 |
| Container keeps stopping | Something failed at startup | `docker logs ikabot` and read the last few lines |
| Build fails on the base 64 GB model | Out of disk | `docker system prune -a`, or move the data to the SD card |

---

*Companion to `docs/DOCKER_GUIDE.md`, which covers the container itself, the
control panel and the maintenance commands in full.*
