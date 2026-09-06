# Running ikabot in Docker — Unraid Setup Guide

> Replaces the Windows VM. Runs all 24 instances plus the maintenance tools in
> one container. Written for someone who has never used Docker before.
>
> **Everything you need to type is in a grey box. Copy the whole box, paste it,
> press Enter.**

---

## Part 0 — What changes, and why it will be faster

### What you have now

Your Windows VM runs 24 copies of `ikabot.exe`, each from its own
`ikariam 1` … `ikariam 24` folder that the installer created. Every one of
those is a separate PyInstaller executable that unpacks its own private copy
of Python into memory. Nothing is shared between them.

Worse, **Windows starts background tasks the expensive way.** When you start a
module (Transport Manager, Construction Manager…), ikabot creates a background
process. On Windows, Python has to use a method called `spawn` — it launches a
brand-new Python interpreter and re-imports everything from scratch. Every
running module costs you another ~50 MB that is shared with nothing.

### What changes on Linux

Linux uses `fork` instead. A background task starts as a *copy* of its parent
and shares almost all of its memory with it. It costs a few MB instead of ~50.
On top of that, all 24 instances run the same Python from the same files, so
the interpreter itself is loaded into memory **once** and shared by all of them.

### The measured numbers

I ran this exact ikabot code on Linux and measured it properly (shared-aware
"PSS", not the inflated per-process figure):

| | Measured |
|---|---|
| One ikabot instance, idle at the menu | **42 MB** |
| 24 instances | **≈ 1.0 GB** |

For comparison, on your Windows VM today (estimated from how PyInstaller and
`spawn` behave — I could not measure your VM directly):

| | Estimated |
|---|---|
| Windows itself | ~2.0 GB |
| 24 × `ikabot.exe` sitting at the menu | ~1.7 GB |
| Background modules, at ~50 MB each, nothing shared | 1–3 GB |
| **Total** | **~5–7 GB of your 8 GB** |

That is why it lags. You are at or over the limit, so the server starts
swapping to disk, and everything crawls.

**Expected saving: roughly 2.5–4 GB, plus all the CPU Windows spends on
itself.** You also get back ~800 MB of disk, because you stop keeping 24
copies of the same program.

### An honest warning

Docker removes the Windows tax. It does not make 24 bots free. The i7-2700K
and 8 GB are still your ceiling.

**The single biggest upgrade you can make is RAM, and it is cheap.** One
correction to what you said: the i7-2700K is Sandy Bridge on socket LGA1155,
which takes **DDR3**, not DDR2. It officially supports up to 32 GB. DDR3 is
nearly worthless second-hand now — going from 8 GB to 16 or 32 GB will cost
very little and will do more for you than any amount of tuning.

Do the Docker move first (it is free), then add RAM.

### How the 24 instances will work

On Windows you have 24 console windows. In Docker you get the same thing using
a tool called **tmux** — 24 terminal "windows" inside one container, which you
flick between with a keyboard shortcut. You can close your SSH connection and
they all keep running.

---

## Part 1 — Before you start

You need:

- [ ] Your Unraid server on and reachable
- [ ] Your Windows VM still working (do **not** delete it until Docker is proven)
- [ ] About 30 minutes
- [ ] Your ikabot vault master password

Nothing here touches your Windows VM. If Docker does not work out, you just
start the VM again.

---

## Part 2 — Open a terminal on Unraid

1. Open the Unraid web interface in a browser.
2. Top-right of the page, click the **`>_`** icon.

A black terminal window opens. **That is where every command in this guide
goes.** Leave it open.

> If you prefer SSH, that works identically.

---

## Part 3 — Create the folders

Paste this:

```bash
mkdir -p /mnt/user/appdata/ikabot/build \
         /mnt/user/appdata/ikabot/app \
         /mnt/user/appdata/ikabot/config
cd /mnt/user/appdata/ikabot
ls -la
```

You should see `app`, `build` and `config`.

What each one is for:

| Folder | Holds | Same as on Windows |
|---|---|---|
| `app` | ikabot's program code | `ikariam 1`…`24` folders (but **one** copy, not 24) |
| `config` | Your vault, sessions, logs, settings, modules | `%APPDATA%\.ikabot` |
| `build` | The Docker recipe files | nothing — this is new |

---

## Part 4 — Download the ikabot code

Paste this:

```bash
cd /mnt/user/appdata/ikabot
wget -O repo.zip https://github.com/kurzonmorris/ikabot-modules/archive/refs/heads/main.zip
unzip -q repo.zip
ls ikabot-modules-main
```

You should see a listing including `ikabot`, `modules`, `docker`, `installer`.

> **If `wget` fails** (404 or "unauthorised"), the repository needs a login.
> Instead: on your Windows PC open the repo on github.com, click the green
> **Code** button → **Download ZIP**. Extract it, then copy the extracted
> `ikabot-modules-main` folder into `\\TOWER\appdata\ikabot\` over the network,
> and carry on from Part 5. (Replace `TOWER` with your server's name.)

---

## Part 5 — Put the code where it belongs

Paste this:

```bash
cd /mnt/user/appdata/ikabot
cp -r ikabot-modules-main/ikabot           app/
cp -r ikabot-modules-main/modules          app/
cp -r ikabot-modules-main/config-examples  app/
cp    ikabot-modules-main/docker/*         build/
ls app && echo "--- build ---" && ls build
```

`app` should contain `ikabot`, `modules`, `config-examples`.
`build` should contain `Dockerfile`, `requirements.txt`, `entrypoint.sh`,
`ika`, `ika-modules`, `docker-compose.yml`.

> Those five build files are already written for you — that is why you are not
> pasting them by hand. Their contents are listed in Appendix A if you ever
> need to recreate them.

---

## Part 6 — Bring your existing accounts across

This copies your **vault** (all 24 saved logins), your sessions, your logs and
your module settings, so you do not have to set anything up again.

**On your Windows VM**, open File Explorer, paste this into the address bar and
press Enter:

```
%APPDATA%\.ikabot
```

You should see `vault`, `sessions`, `logs`, and probably `module_prefs`.

Now, in another Explorer window, go to:

```
\\TOWER\appdata\ikabot\config
```

(Replace `TOWER` with your server name.)

**Create a folder there called `.ikabot`** — note the dot at the front — and
copy everything from the first window into it.

When you are done, check it from the Unraid terminal:

```bash
ls -la /mnt/user/appdata/ikabot/config/.ikabot
```

You want to see `vault` in that list. If you do, all 24 accounts came across.

> **Can't see the appdata share?** In Unraid: **Shares → appdata → SMB →**
> set Export to **Yes**. Or skip this Part entirely and set your accounts up
> fresh inside the container later — it just means retyping them.

> **Why the dot?** On Linux, ikabot reads its data from `$HOME/.ikabot`, and
> the container's home folder is `/config`. So `config/.ikabot` on the server
> is exactly `%APPDATA%\.ikabot` on Windows.

---

## Part 7 — Build the container image

This is the one command that takes a few minutes. It downloads Python and
ikabot's libraries and packages them up.

```bash
cd /mnt/user/appdata/ikabot/build
docker build -t ikabot-mod:latest .
```

Wait for it. The last line should say:

```
Successfully tagged ikabot-mod:latest
```

Check it exists:

```bash
docker images | grep ikabot-mod
```

> You only ever do this again if the Python libraries change. Updating ikabot
> itself does **not** need a rebuild — see Part 11.

---

## Part 8 — Start it

Paste the whole block:

```bash
docker run -d \
  --name ikabot \
  --init \
  --restart unless-stopped \
  -e TZ=Europe/London \
  -e INSTANCES=24 \
  -e IKABOT_LOCALE=en-GB \
  -e IKABOT_GF_LANG=en \
  -e IKABOT_TIMEZONE_ID=Europe/London \
  -v /mnt/user/appdata/ikabot/app:/app \
  -v /mnt/user/appdata/ikabot/config:/config \
  ikabot-mod:latest
```

Check it started:

```bash
docker logs ikabot
```

You want to see:

```
[entrypoint] starting 24 ikabot instance(s)
[entrypoint] ready — attach with:  docker exec -it ikabot ika attach
```

And check all 24 are alive:

```bash
docker exec -it ikabot ika status
```

You should get a list `ika01` … `ika24`, all saying **running**, and a line
telling you how much memory they are using in total.

---

## Part 9 — Log in to your instances

Open the instance screens:

```bash
docker exec -it ikabot ika attach
```

You are now looking at instance **ika01**, showing the ikabot banner and asking
for your vault master password. Type it, and it will jump straight to the
account for that window — window `ika03` picks vault account 3, and so on.

Then move to the next one and repeat.

### The only four keys you need

You press **Ctrl-B**, let go of both keys, *then* press the second key.

| Keys | Does |
|---|---|
| **Ctrl-B** then **W** | Show the list of all 24 — arrow keys to pick, Enter to go |
| **Ctrl-B** then **N** | Next instance |
| **Ctrl-B** then **P** | Previous instance |
| **Ctrl-B** then **D** | **Leave the screens.** Everything keeps running |

`Ctrl-B` then `W` is the one you will use most.

To come back at any time:

```bash
docker exec -it ikabot ika attach
```

> **Important:** `Ctrl-B` then `D` is how you leave. Do not just close the
> terminal window mid-typing, and never press `Ctrl-C` inside an instance —
> that kills that instance, exactly like closing its console on Windows.
> (If you do, `ika restart 7` brings it back.)

> Scrolling: the mouse wheel works. Press `q` to stop scrolling.

---

## Part 10 — Everyday maintenance

Your installer's maintenance menu is replaced by the `ika` command. Every one
of these is run from the Unraid terminal:

| Installer did | Now type |
|---|---|
| Open all instances | `docker start ikabot` |
| Close all instances | `docker stop ikabot` |
| See the instance screens | `docker exec -it ikabot ika attach` |
| Show status | `docker exec -it ikabot ika status` |
| Show module versions | `docker exec -it ikabot ika list` |
| Update modules | `docker exec -it ikabot ika modules` |
| Update ikabot itself | `docker exec -it ikabot ika update` |
| Restart one instance | `docker exec -it ikabot ika restart 7` |
| Restart only crashed instances | `docker exec -it ikabot ika restart dead` |
| See why an instance died | `docker exec -it ikabot ika crash 7` |
| List instance web server URLs | `docker exec -it ikabot ika web` |
| Stop all instance web servers | `docker exec -it ikabot ika web --stop` |
| Restart all instances | `docker exec -it ikabot ika restart all` |
| Read a log | `docker exec -it ikabot ika logs` |
| Open the control panel | `docker exec -it ikabot ika panel` |

Full list of commands:

```bash
docker exec -it ikabot ika help
```

> **Tip — make it shorter.** Paste this once and you can just type `ika status`
> instead of the whole thing:
> ```bash
> echo "alias ika='docker exec -it ikabot ika'" >> /boot/config/go
> ```
> It takes effect after your next Unraid reboot.

---

## Part 11 — Updating

### Updating your modules

Exactly what the installer's Modules screen did — downloads from GitHub,
strips the `_v10.5.1` from the filename, drops them in the modules folder:

```bash
docker exec -it ikabot ika modules
```

Then restart the instances so they pick the new ones up:

```bash
docker exec -it ikabot ika restart all
```

Your `bulkdistribution.csv` is treated as your data and is **never**
overwritten, same as the installer. Add `--force-csv` if you actually want the
example version back.

### Updating ikabot itself

One command. No rebuild — ikabot's code lives in the `app` folder, not inside
the image:

```bash
docker exec -it ikabot ika update
```

It shows you what you have and what is available, asks before touching
anything, then downloads and installs:

```
  installed : ikabot 7.4.5 / mod 1.7.6
  available : ikabot 7.4.5 / mod 1.7.7

This replaces ikabot's code in /app.
Running instances keep going until you restart them.
Continue? [y/N]:
```

Modules are refreshed at the same time. Then load the new code:

```bash
docker exec -it ikabot ika restart all
```

Your instances keep running on the old code until that restart, so nothing
breaks mid-update — but you will be re-entering vault passwords afterwards, so
do it when you have a few minutes.

**If an update goes wrong**, the previous copy is kept:

```bash
docker exec -it ikabot ika update --rollback
docker exec -it ikabot ika restart all
```

Only one previous copy is kept, so roll back before updating again.

> Skip the confirmation with `ika update --yes`. To pull from a branch other
> than `main`, add `-e IKABOT_BRANCH=some-branch` when you create the container.

**It only downloads the code.** This repository is a quarter of a gigabyte —
`releases`, `dist`, `build` and `archive` account for nearly all of it — while
ikabot itself is under a megabyte. The update lists the repository once and
fetches only `ikabot`, `modules` and `config-examples`, so it moves about half
a megabyte rather than 250.

If that listing cannot be reached — GitHub's API allows 60 unauthenticated
calls an hour — it falls back to downloading the whole archive, streamed to
disk and retried if the transfer drops. That is slow but it works. Either way
a failed download leaves `/app` exactly as it was; nothing is replaced until
the new copy is complete.

### Updating with no usable connection

On a connection too slow to finish the download at all, fetch the archive by
any means you like — another machine, a phone, a USB stick — drop it in the
config folder, and point the updater at it:

```bash
docker exec -it ikabot ika update --from /config/ikabot.zip
```

`/config` inside the container is the `config` folder in your data directory,
so anything you put there is immediately visible to it.

It takes whatever shape the archive happens to have: the repository zip that
unpacks to `ikabot-modules-main/ikabot`, a release zip with `app/ikabot`, or a
folder you have already extracted. It looks for `ikabot/config.py` and works
back from there, and says so plainly rather than half-updating if the archive
turns out to be something else. Everything after that is the ordinary update —
version comparison, the backup for `--rollback`, and the module refresh.

### Changing the number of instances

Say you want 12 instead of 24:

```bash
docker rm -f ikabot
```

…then re-run the Part 8 block with `-e INSTANCES=12`. Your accounts and
settings are in `config` and are not touched.

---

## Part 12 — Surviving reboots

`--restart unless-stopped` is already in the Part 8 command, so the container
comes back on its own after a reboot or a crash.

**But**: your vault password is not stored anywhere, so after a restart the 24
instances will all be sitting at the password prompt waiting for you. That is
the same as it is on Windows today.

If you have modules set to auto-start (Options → Auto-start modules), they will
launch by themselves once you have typed the password for that instance.

---

## Part 13 — If something goes wrong

**Start here, always:**

```bash
docker logs --tail 50 ikabot
docker exec -it ikabot ika status
```

| What you see | What it means | Fix |
|---|---|---|
| Container keeps stopping | Something failed at startup | `docker logs ikabot` and read the last few lines |
| An instance says **STOPPED** | That one crashed or was closed | `ika crash 7` to see why, then `ika restart 7` |
| Several web servers on different ports for one account | Older ikabot started a new one on each retry instead of reporting the existing one | Fixed in mod 1.8.x; clear strays with `ika web --stop` |
| Several instances STOPPED | Crashes | `ika restart dead` — restarts only those, leaves the rest logged in |
| `sh: clear: not found` | Image built wrong | Rebuild: Part 7 |
| Boxes show as `?????` | Your terminal is not on UTF-8 | Use the Unraid web terminal, it is correct by default |
| `No modules found` in ikabot | Modules folder empty | `docker exec -it ikabot ika modules` |
| It asks for the account number instead of jumping to it | No vault found | Check `ls /mnt/user/appdata/ikabot/config/.ikabot/vault` — see Part 6 |
| `Cannot connect to the Docker daemon` | Docker service is off | Unraid **Settings → Docker → Enable = Yes** |

**Start completely over** (keeps all your accounts and settings):

```bash
docker rm -f ikabot
docker build -t ikabot-mod:latest /mnt/user/appdata/ikabot/build
```

…then Part 8 again.

**Go back to the Windows VM:** just stop the container (`docker stop ikabot`)
and start the VM. Nothing was changed on it.

---

## Part 14 — Later: moving to HexOS

HexOS is built on TrueNAS SCALE, which runs Docker too. The move is easy
because everything that matters is in two folders.

1. Copy `/mnt/user/appdata/ikabot` to the new server (any pool/dataset path).
2. Adjust the two paths in `docker/docker-compose.yml` to match the new
   location.
3. Run `docker compose up -d` from the `build` folder.

Everything else — the image, the commands, tmux, `ika` — behaves identically.
`docker-compose.yml` is included for this reason: HexOS/TrueNAS custom apps are
Compose-based, whereas plain `docker run` suits Unraid 6.12 better.

---

## Part 15 — Getting the most from 8 GB

**Do these in order:**

1. **Shut the Windows VM down for good** once Docker is proven. That is your
   biggest single win — it frees the RAM *and* the CPU cores it was pinned to.
   Unraid: **VMS → ikabot VM → Stop**, and turn off autostart.

2. **Give the container a memory ceiling** so a runaway instance can never take
   the whole server down. Add this line to the Part 8 command and recreate:
   ```
   --memory=5g --memory-swap=5g \
   ```
   Only do this after you have watched `ika status` for a day and know your
   normal usage — setting it too low makes Linux kill instances.

3. **Stagger your modules.** 24 instances all polling at once is a CPU spike.
   The modules already randomise their waits (`wait(3600, maxrandom=300)`), so
   this mostly takes care of itself — but do not set very short intervals on
   all 24.

4. **Buy DDR3.** As above: LGA1155, up to 32 GB, very cheap. This is the real
   fix.

To watch what it is actually using:

```bash
docker stats ikabot
```

Press `Ctrl-C` to stop watching.

---

## Part 16 — A permanent browser tab

Optional. Puts a terminal on a web page inside the container, so a bookmark
opens straight onto your instances — no popup, no typing the attach command.

### Turn it on

The web terminal **will not start without a password**. Recreate the container
with a port and a password added:

```bash
docker rm -f ikabot
```

Then the Part 8 block with two extra lines (pick your own password):

```bash
docker run -d \
  --name ikabot \
  --init \
  --restart unless-stopped \
  -p 7681:7681 \
  -e TTYD_USER=kurzon \
  -e TTYD_PASS='choose-a-long-one' \
  -e TZ=Europe/London \
  -e INSTANCES=24 \
  -e IKABOT_LOCALE=en-GB \
  -e IKABOT_GF_LANG=en \
  -e IKABOT_TIMEZONE_ID=Europe/London \
  -v /mnt/user/appdata/ikabot/app:/app \
  -v /mnt/user/appdata/ikabot/config:/config \
  ikabot-mod:latest
```

Check it came up:

```bash
docker logs ikabot | grep "web terminal"
```

You want `web terminal listening on port 7681`. Now open this in a tab and
bookmark it — replace `TOWER` with your server's name or IP:

```
http://TOWER:7681
```

It asks for the username and password, then drops you on instance `ika01`.
The same `Ctrl-B` keys work. Detaching just re-attaches, so the tab always
shows the bots.

> Closing the tab is safe. It disconnects the viewer; the instances keep
> running, exactly like detaching.

### Reaching it from outside the house

**This is a shell.** Anyone who reaches that URL and gets past the password can
run commands as root inside the container — your vault file and all 24 sessions
are in there. It is *not* a shell on Unraid itself, which limits the damage, but
it is not something to leave open to the internet on plain HTTP, where the
password crosses the network in the clear and scanners find open ports within
minutes.

**Use Tailscale instead of port forwarding.** It is less work, not more.

> **On Unraid 6.12 you cannot use the Tailscale plugin** — it requires Unraid
> 7.0 or later, which is why it never appears in Community Applications on
> 6.12. Use the container below instead; with `--network=host` it does the same
> job of putting the whole server on your tailnet.

Run this on Unraid:

```bash
mkdir -p /mnt/user/appdata/tailscale
docker run -d \
  --name tailscale \
  --restart unless-stopped \
  --network=host \
  --cap-add=NET_ADMIN \
  --cap-add=NET_RAW \
  --device=/dev/net/tun \
  -v /mnt/user/appdata/tailscale:/var/lib/tailscale \
  -e TS_STATE_DIR=/var/lib/tailscale \
  -e TS_USERSPACE=false \
  -e TS_ACCEPT_DNS=false \
  -e TS_HOSTNAME=tower \
  tailscale/tailscale:latest
```

`TS_USERSPACE=false` is essential — left at its default the container does its
own userspace networking and the host's ports stay invisible. `TS_ACCEPT_DNS=false`
stops Tailscale rewriting Unraid's DNS, which you do not need.

Get the sign-in link and authenticate:

```bash
docker logs tailscale 2>&1 | grep -i "login.tailscale.com"
```

> Open that link in a **private/incognito window**. The sign-in page silently
> reuses whatever Google/GitHub session your browser already has, which is an
> easy way to attach the server to the wrong account.

Then at **login.tailscale.com → DNS**, enable **MagicDNS** and **HTTPS
Certificates**, and under **Machines → tower → ⋯**, disable **key expiry** so it
does not drop off the tailnet in six months.

### Serve it over HTTPS

Do not use `http://<tailscale-ip>:7681`. Reaching a Docker-published port across
the tailnet depends on the DNAT/forward path and is unreliable. Let Tailscale
terminate the connection instead:

```bash
docker exec tailscale tailscale serve --bg 7681
docker exec tailscale tailscale serve status
```

That prints your permanent URL — no port number, real certificate, private to
your tailnet:

```
https://tower.tailXXXXX.ts.net/
```

Install Tailscale on your phone and laptop, sign in with the same account, and
that URL works from anywhere. The setting persists across restarts.

### ⚠ If the page will not load on a phone

**Turn off Private DNS on Android.** This is the one that will waste your
evening: **Settings → Network & internet → Private DNS → Off**. If it is set to
a hostname (`dns.adguard.com`, NextDNS, Cloudflare), Android sends every lookup
straight there and MagicDNS never sees it, so `.ts.net` names do not resolve —
while ordinary websites keep working perfectly, which makes it look like a
server problem.

Chrome and Firefox have their own equivalent: **Settings → Privacy and security
→ Use secure DNS → off** (Chrome), **Settings → DNS over HTTPS → off** (Firefox).

If you used Private DNS for ad-blocking, put the filtering back at the tailnet
level instead: **login.tailscale.com → DNS → Nameservers**, add NextDNS or
similar as a global nameserver. You keep the filtering *and* MagicDNS.

To confirm the server side is fine before blaming it, this proves the whole
chain without needing DNS:

```bash
curl -sS -o /dev/null -w "serve: %{http_code}\n" \
  --resolve tower.tailXXXXX.ts.net:443:100.x.x.x \
  https://tower.tailXXXXX.ts.net/
```

`401` means TLS, certificate, proxy and ttyd are all working and the problem is
on the client.

If you specifically want a public `https://` address later, Tailscale Funnel
and Cloudflare Tunnel both do that with a real certificate — still without
opening a port on your router.

**If you port-forward anyway**, do not forward `7681` directly. Put it behind a
reverse proxy with a real certificate (SWAG or Nginx Proxy Manager, both in
Community Applications), forward only `443`, and use a long random password.
That is considerably more work than the Tailscale route above, which is why I
am recommending Tailscale.

### Turning it off

Recreate the container without `-e TTYD_PASS`. With no password set it does not
listen at all.

---

## Part 17 — The per-instance web servers

Each instance can run its own web server (menu option **16**) that lets you play
that account in a browser without logging ikabot out. If you use these, one
change is needed.

### Why they are unreachable by default

The address ikabot prints — something like `172.17.0.2:44214` — is the
**container's** internal Docker address. The server binds every interface
*inside the container*, and since only 7681 is published, nothing else gets
out. That port is not reachable from the host, your LAN, or Tailscale.

The port itself is derived from your email, host and username, landing in
**43000–44999**. It is stable for an account and only shifts if two instances
collide, in which case the second takes the next free number.

### The fix: host networking

Recreate the container so it shares the server's network stack. Every instance
web server then binds the real interfaces, including `tailscale0`.

```bash
docker rm -f ikabot
```

```bash
docker run -d \
  --name ikabot \
  --init \
  --restart unless-stopped \
  --network=host \
  -e TTYD_USER=kurzon \
  -e TTYD_PASS='your-password' \
  -e TZ=Europe/London \
  -e INSTANCES=24 \
  -e IKABOT_LOCALE=en-GB \
  -e IKABOT_GF_LANG=en \
  -e IKABOT_TIMEZONE_ID=Europe/London \
  -v /mnt/user/appdata/ikabot/app:/app \
  -v /mnt/user/appdata/ikabot/config:/config \
  ikabot-mod:latest
```

`-p 7681:7681` is **gone** — with host networking it means nothing, and ttyd
binds the host's 7681 directly. An existing `tailscale serve` setup keeps
working, because it proxies to `127.0.0.1:7681` on the host either way.

A side benefit: ikabot works out its address by asking the OS which interface
reaches the internet. On host networking it prints your real IP instead of
`172.17.0.2`, so the address it shows you becomes one that actually works.

### Finding all of them at once

```bash
docker exec -it ikabot ika web
```

```
INSTANCE   PORT     URL
----------------------------------------------------
ika01      43001    http://100.122.72.17:43001
ika03      43117    http://100.122.72.17:43117
```

It walks each tmux window's process tree to find which instance owns which
listening port, and prints your Tailscale address when it can find one — so
the URLs work from anywhere on your tailnet. Override the address with
`-e IKA_WEB_HOST=...` if you want LAN addresses printed instead.

### ⚠ These web servers have no password

They proxy your logged-in Ikariam session — anyone who opens one is playing
that account. Inside the container they were unreachable; on host networking
they are exposed to your whole LAN as well as your tailnet, and there is no
bind-address setting to restrict them to Tailscale only.

On a home network with only your own devices that is fine. If anything less
trusted shares that network, weigh it before making the change.

---

## Part 18 — The control panel

Everything the `ika` command does, as buttons in a browser. Same idea as the
Windows installer's maintenance screen, but reachable from your phone, laptop
or Steam Deck.

It runs automatically whenever the web terminal does — same password, no extra
setup. Find its address:

```bash
docker exec -it ikabot ika panel
```

Or go straight there:

```
http://100.122.72.17:7682
```

Sign in with the same username and password as the terminal.

### What is on it

| Section | Does |
|---|---|
| **Instances** | Every instance with running/crashed state and its web server port. Restart one, restart only the crashed ones, restart all, or ask a dead one **Why?** to see its traceback. **Open all web servers** opens a tab per running web server, in instance order |
| **Modules** | Every module in the repo, installed or not — installed version next to the one on GitHub. **Green** up to date, **red** update available, **blue** published but not installed yet, with an **Install** button. Update one, update all, or reinstall from the app folder |
| **ikabot** | Installed version of ikabot and the mod next to what is published — **red** when a newer one exists, **green** when current. Download and install an update, or roll the last one back |
| **Active processes** | Per instance, a dropdown listing what that account is actually running — module name, its status line, and how long it has been going. Click one to stop it |
| **Accounts** | Paste every account in at once and save them to ikabot's vault in one press — for a fresh install, or a machine that has no vault yet |
| **Lock files** | Per instance and across all of them, clear the lock files modules leave behind so a module can start fresh |
| **Files** | Read any CSV, JSON, TXT or LOG file under `/config` in the browser — CSVs as a table you can search — or download it to your PC. Each instance card also carries a button straight to that account's own files |
| **Buttons** | Your own named buttons — create, edit and delete them; each presses a sequence of menu options in one instance or in every instance it applies to |
| **Terminal** | The instance screens, embedded — the same thing as opening port 7681 |
| **Web servers** | Pick an instance from a numbered strip and read its web server full size |
| **Output** | What the command you just pressed actually printed — always on screen, whichever section you are in |

### Getting around

The sections are behind a menu down the left rather than stacked one after
another, with a count beside the ones worth a glance — how many instances are
running, how many web servers are up, how many modules need an update.

Everything stays loaded and keeps refreshing whichever section you are looking
at, so switching is instant and nothing reloads. Your choice is remembered, so
the panel opens where you left it. **Output** sits below the lot and is always
visible.

The **‹** at the top of the menu narrows it to a strip of short labels —
`Inst`, `Term`, `Web` — which still switch sections in one press, so narrowing
it costs you nothing but width. **›** widens it again, and which way you left
it is remembered.

On a phone the menu becomes a row of chips you swipe along, and instance cards
collapse to their name and state — tap one to open its buttons. On a desktop
nothing has changed.

### Two embedded sections

**Terminal** puts the instance screens in the page, the same as opening port
7681 yourself. A framed page has its own sign-in, so it asks for the username
and password once more; if it stays blank, the link beside it opens the
terminal in its own tab.

Above it are **⤒ Top**, **▲ Page up**, **▼ Page down** and **⤓ Bottom**, with
how far back you are beside them. On a desktop the mouse wheel does the same
job, but on a phone nothing does: the scrollback belongs to tmux, and reaching
it means entering copy mode, which a touch drag never does. So these drive tmux
from the panel's side instead, which works whatever the browser makes of touch
— the terminal only ever draws what tmux tells it to.

They follow whichever screen is on show, and **⤓ Bottom** puts you back at the
prompt. Scrolling back does not stop you typing: reaching the bottom leaves
the scroll view by itself.

**Web servers** is one page at a time, read properly, rather than a wall of
thumbnails.

Down the right is a strip of small numbered squares, five to a row, one per
instance and in instance order. A **green** square has a web server running; a
dim one does not and cannot be pressed. Press a square and that account's page
opens in the large panel to its left, filling most of the window.

An instance is loaded the first time you press it and then kept, hidden, so
going back to one you have already opened is instant instead of a fresh load.
**Unload all** frees the lot when you are done. **Reload this one** refetches
just the page you are looking at, and **Open this one in a tab** does what it
says.

#### Page width

The account page is Ikariam's own layout, and a page on another port is
another origin — so the panel cannot measure how wide it wants to be. Instead
you tell it: **Page width** is how many pixels the page is laid out in, and the
whole of that width is then scaled to land exactly on the panel. Nothing falls
off the right and there is never a sideways scrollbar. Up and down still scroll
normally.

`1400` covers Ikariam's layout including its right-hand column. If anything
is still cut off, press **+** until it is not; if the page looks smaller than
it needs to, press **−**. It steps by 100 between 700 and 2400 and is
remembered, so it is a once-only adjustment.

The **›** at the top of the strip retracts it, giving the page the full width;
the **‹** brings it back. The choice is remembered.

#### On a phone

The strip becomes a single column down the right that scrolls on its own, so
all twenty-four are reachable with a thumb without moving the page.

The account page itself is laid out at a desktop width and then scaled down to
fit, so the whole thing is on screen at once with **nothing to scroll inside
it** — the awkward part of a framed page on a small screen. Pinch to zoom in on
whatever you want to read, the way you would with any other page.

> On a phone the frame is given 1500px of height to work with, so the whole
> page renders and the outer page does the scrolling. A status page fits inside
> that comfortably; the cost is a little blank space below a short one.

This replaces keeping twenty-four browser tabs open. It needs the ports to be
reachable from your browser, which they are on Unraid, TrueNAS, Linux and a
Steam Deck; on Windows and Mac Docker cannot share the host's network, so only
the panel and terminal are reachable there.

### The instance grid

The instance cards sit five to a row, and the page stays the same width
whatever the screen: on a tablet it drops to three, on a phone to two, and it
never scrolls sideways.

The list refreshes every five seconds, and each web server port is a link
straight into that account.

**Open all web servers** remembers what it already opened, so pressing it again
after starting more only opens the new ones — no duplicate tabs. Your browser
will likely block all but the first tab the first time: allow pop-ups for the
page and press it again.

Remote versions come from GitHub, which allows 60 unauthenticated calls an
hour, so they are cached rather than re-fetched every few seconds. **Check for
updates** refreshes them on demand. If GitHub cannot be reached the panel says
so and shows the versions as "not checked" rather than guessing.

### Active processes

Each instance card carries a task count and an **Active processes** dropdown
listing what that account is running — `alertAttacks`, `webServer` and so on —
with the status line each module sets, and how long it has been going.

This reads the status files ikabot writes to `/config/.ikabot/status/`, so
nothing needs mounting: the panel lives in the same container. Files are
matched to windows by walking the recorded process up to its tmux pane, so the
tasks land on the right instance without anything having to be told how many
instances there are.

> **Needs ikabot mod 1.8.1 or later** — verified against the writer in 1.8.2.
> Earlier versions do not write those files, and the panel says so rather than
> showing an empty list. Get it with `ika update`.

The dropdown stays open while you read it — the list refreshes underneath
without closing it.

A task count that is blank means no status file — either the instance has never
finished starting, or ikabot is older than 1.8.1. `0 tasks` genuinely means
idle at the menu.

### Stopping processes

Three ways, all of them asking **yes or no** first:

| To stop | Press |
|---|---|
| One process | Click it in that instance's **Active processes** list |
| Everything on one account | **Stop tasks** on that instance's card |
| Everything, everywhere | **Stop all processes**, in the row at the top |

**Stop tasks** and the per-instance buttons only appear when that account
actually has something running, so an idle card stays uncluttered.

This is the same thing as ikabot's own *kill tasks* menu option — the process is
sent `SIGKILL`, exactly as that menu does — except you do not have to be sitting
in the instance to do it, and you can do all 24 at once.

> **It stops tasks, not instances.** The account stays logged in and stays at
> the menu; only the work it had started is stopped. To restart the instance
> itself, use **Restart**.

The list corrects itself the moment something stops. ikabot only rewrites its
status file when its menu redraws, which on an idle account can be hours, so the
panel checks each recorded process against `/proc` and shows only the ones
genuinely still alive. A stopped task disappears on the next refresh rather than
lingering as a ghost entry.

Nothing outside that list can be reached: a stop request has to name a process
the panel is currently showing for that instance, and an instance's own ikabot
process is excluded from the list, so these buttons cannot kill an instance even
if a status file claims otherwise.

### Adding accounts in one go

Normally the vault is already there and instances log themselves in. On a
machine with no vault — a fresh install, a new Deck — every account has to be
typed into every instance by hand. The **Accounts** section does the lot in one
press.

1. Paste your accounts into the box, one per line:

   ```
   StDa en70, stda@example.com, hunter2
   14thfloor en70, floor@example.com, hunter3
   ```

   Tab-separated works too, so three columns copied from a spreadsheet paste
   straight in — and that is the safer form if a password contains a comma.

2. Press **Read the lines**. Each line becomes an editable row, so you can
   check them and fix anything the split got wrong before it is saved
3. Enter the **vault master password**. If there is no vault yet you are asked
   for it twice, and the one you choose becomes the vault's password
4. **Save all to the vault**, confirm, done

The heading says **no vault yet** or **vault ready**, so you can see which case
you are in.

Afterwards press **Restart all** in the Instances section. Each window then
starts on its matching account — window `ika03` on vault account 3 — because
the account order is the order you saved them in.

**Name in ikabot** is the label the account list shows, so make it something
you can tell apart at a glance: `StDa en70` rather than `account 1`.

### Keeping them in the order you typed them

ikabot lists vault accounts **sorted by name**, not in the order they were
added — so its `(1) (2) (3)` follows the alphabet. Enter `StDa`, `14thfloor`,
`Kurzon`, `aardvark` and ikabot numbers them `14thfloor, aardvark, Kurzon,
StDa`: the one you typed first is number 4, and window `ika01` lands on it.

**Number them, so ikabot lists them in the order you entered them** — ticked by
default — fixes that by writing the position into the label:

```
01 StDa en70
02 14thfloor en70
03 Kurzon en135
```

The sort then agrees with the order you typed, so the first row is account 1
and window `ika01` gets it. Two digits, so 10 comes after 9 rather than after
1. A second batch carries on from the highest number already there rather than
starting again at 1.

The panel shows what is in the vault under the form, numbered exactly as
ikabot will ask for them, so you can check the mapping without opening an
instance.

Untick the box if you would rather name them yourself. The same account is
recognised either way — the number is ignored when checking for duplicates, so
`StDa en70` will not go in twice as `01 StDa en70` and `07 StDa en70`.

### Emptying the vault

**Empty the vault** removes every account from it. The vault file and its
master password stay, so you can save a new set straight afterwards — which is
the point: it is for starting the list again, not for scrapping the vault.

It asks for three things, because it cannot be undone:

1. The **master password**, in the field above — a wrong one removes nothing
2. A confirmation naming how many accounts will go
3. The word **DELETE**, typed

Instances already logged in keep running; they hold their session, not the
vault. They will have nothing to log into next time they restart, so save the
new set before restarting them.

What it will not do:

- **Overwrite anything.** A label already in the vault is skipped and named in
  the result, so pressing Save twice cannot duplicate an account
- **Save half a batch.** A row missing a field, or with a control character in
  it, stops the whole save with the row number — nothing is written
- **Write under the wrong password.** With an existing vault the password is
  checked against it first. Saving under a wrong password would encrypt those
  accounts so that ikabot itself could never read them, and the vault would
  look perfectly fine until the day you needed one, so it refuses instead

The rows and the master password exist only in the page and in the one request
that saves them. Nothing is logged — checked by searching the panel's log and
every file it writes for a password that had just been saved, and finding it
only inside the encrypted vault.

> **The panel speaks plain HTTP.** Its own password already crosses the network
> in the clear on every request, and so does anything typed into the web
> terminal, so this form is no new exposure — but it is still worth using over
> Tailscale, or on a network you trust, rather than over a forwarded port on
> the open internet.

### Clearing lock files

Modules that must not run twice at once — the transport manager, the
construction manager, recruitment, reservations, the island monitor — take a
lock file while they work. Normally they clean it up on the way out. After a
crash, or after an update that changes how a module stores its state, a lock
can be left behind and the module will not start again until it is gone.

| To clear | Press |
|---|---|
| One account's locks | **Clear locks (n)** on that instance's card |
| Every lock, everywhere | **Clear all lock files (n)**, in the row at the top |

Both show you the count, and the per-instance one lists the exact filenames in
the confirmation box before anything is deleted. Neither button appears unless
there is actually something to clear.

**Stop the tasks first.** Both actions refuse while the instance has anything
running, and name what is running so you know what to stop. That is deliberate:
a lock deleted while its owner is still working stops being a lock, and two
copies of the transport manager shipping at once is a genuine way to lose
resources. So the order is:

1. **Stop tasks** on that instance — or **Stop all processes** for all of them
2. **Clear locks**
3. Start the module again from the instance, or with one of your buttons

Which files belong to which instance is worked out from the account name each
instance is logged in as, so an account on two worlds of the same server keeps
its locks separate — `en135` and `en120` do not clear each other's.

Locks that belong to no logged-in account — the messaging hub's shared one, or
leftovers from an account not running right now — are only removed by **Clear
all lock files**.

> **Session locks are left alone**, deliberately. ikabot takes those for
> milliseconds at a time and clears a dead one after thirty seconds by itself,
> so they are never what stops a module restarting, and deleting one
> mid-write is the only way this could do harm.

### Reading a module's CSV

Module data files live inside the container, owned by root, in folders under
`/config` — which makes them awkward to look at while 24 instances are busy in
tmux. The **Files** section reads them in the browser.

1. Pick the file from the dropdown — every data file under `/config` is there,
   shown with its folder and size, e.g. `modules/bulkdistribution.csv (11.3 KB)`
2. It opens as a table: proper columns, the header exactly as the file spells
   it, and the line number from the file down the left
3. **Find in rows** filters as you type. The line numbers stay the file's own,
   so a row you find here is the row you will find in the file
4. **Download** saves it to whatever you are browsing from — the file arrives
   byte for byte, so you can open it in Excel or LibreOffice

Text in the table selects and copies normally, which is the easy way to get a
few values out without downloading anything.

### Per-account logs, straight from the card

A module that writes one file per account names it after the account, the way
the shipment log does:

```
/config/shipment_log_en70_StDa.csv
/config/shipment_log_en70_14thfloor.csv
```

Any file named like that shows up as a button on that account's own instance
card — labelled with the part of the name that is not the account, so the
button above just says **shipment_log**. Press it and the file opens in the
**Files** section below, already selected. One press, from the card, no
hunting through a dropdown of 24 similar names.

Only `.csv`, `.tsv`, `.log` and `.txt` earn a button, and any file with *log*
in its name is listed first. A card is a shortcut to the one or two files you
actually read, not a listing: modules write a `.schema` sidecar and a `.json`
of their settings beside every CSV, so without that restriction an account
running four modules would sprout nine buttons. Everything else is still in
the **Files** section, which lists the lot.

Matching is on the account itself — server, world and player, exactly as the
modules build their filenames — so `en70_StDa` and `ar42_StDa` are two
different buttons on two different cards even though the player name is the
same. Older files that leave the world out (`_en_StDa`) are matched too, and a
player name containing an underscore is handled correctly.

A file with no account in its name — an old shared `shipment_log.csv`, say —
belongs to no instance and so appears on no card. It is still in the **Files**
dropdown.

A comma inside a quoted field stays one cell, rows with more columns than the
header still show every value, and a file that is not valid CSV is shown as
plain text with a note saying so rather than being silently mangled.

Very large files are offered as a download instead of being loaded into the
page, and a CSV over 3000 rows shows the first 3000 with a note — download it
for the rest. **Refresh list** picks up files created since the page loaded.

> **What it will not show you.** Only data files: `.csv`, `.tsv`, `.json`,
> `.txt`, `.log`, `.md` and a few config extensions, and only below `/config`.
> The vault and the session files are excluded by name — those are your account
> credentials, and nothing about reading a CSV needs them. A path that resolves
> outside `/config`, including through a symlink, is refused rather than served,
> and never appears in the list either.

### Buttons

The **Buttons** section is a small editor. Buttons you create appear on every
instance card automatically, and in the **run everywhere** row at the top.

| Field | Means |
|---|---|
| **Name** | What the button says — `Daily login`, not `6` |
| **Entries** | What it types, in order, each followed by Enter. Not just numbers — anything a prompt accepts, such as `s`, `all`, `y` or `a-20k`. Separate them with commas, so a value can contain spaces. Write `enter` for a bare Enter — `6,enter` runs option 6 then clears the "[Enter] to continue" screen |
| **Applies to** | *All instances* (the default), or only the instance numbers you list |
| **Pause between presses** | A slider, `0`–`10` seconds. How long to wait before the next press so the menu can redraw |
| **Takes** | How long one press of that button takes on one instance |
| **All of them** | The same button across every instance it applies to, marked *(estimated)* |
| **Start at the main menu** | On by default. Sends ikabot's `/menu` before the entries, so the button works from whatever screen that instance was left on |

#### Starting from a known screen

A button used to assume the instance was sitting at the main menu. If it was
halfway through a module, or on an `[Enter] to continue` screen, the entries
landed wherever they landed.

Two things now happen before a button's own entries, so it does not matter what
was on screen:

1. **The scroll view is closed.** Scrolling a pane with the wheel puts tmux
   into copy mode, and while it is there tmux gives keys to the viewer instead
   of to ikabot — so a button pressed on an instance somebody had scrolled up
   did **nothing at all**, silently. That is now cleared first, every time,
   whether or not the box below is ticked.
2. **`/menu` is sent**, which is ikabot's own command for returning to the main
   menu from anywhere.

**Start at the main menu** controls the second one and is on by default,
including for every button that already existed. Untick it for a button meant
to answer a prompt where the instance already is — a bare `y`, say — which is
what the **From** column in the table shows: *main menu* or *where it is*.

> If you used to write `q,/menu,…` at the start of your entries by hand, drop
> both: the panel does them now, and a leftover `q` would be typed at the main
> menu, where it is not a valid option. The editor points this out if it sees
> one.

So a change to a global button changes it everywhere at once, and an instance
that needs something different gets its own button scoped just to it.

Each number is sent followed by Enter, with the pause you set between them so
the menu has time to redraw — which is why multi-step options like Donate work.
The default is `1.0s`, comfortable on a busy machine; raise it if a step gets
missed because the game was slow to respond, or drop it to `0` for a
single-press button where it makes no difference. There is no pause after the
last press.

**Takes** is `(entries − 1) × pause`, because the pause falls between presses
and not after the last, and `/menu` counts as an entry when it is switched on.
**All of them** multiplies that by the number of instances the button applies
to, since running it everywhere works through them one after another.

Both are marked *estimated* for a reason: neither knows how long the game will
take to answer. Treat them as the floor, not the finish time.

Changing the default does not touch buttons you already saved — each keeps the
pause it was created with, shown in the **Pause** column. Edit one and save it
to bring it up to a second.

A button holds up to 30 presses, which covers a whole daily routine in one
press.

> Remember both the length and the pause multiply on **run everywhere**. A
> three-press button at 2 seconds across 24 instances is about a minute and a
> half; a twenty-press routine at the same pause is over ten minutes, and the
> panel sits waiting with its buttons disabled for the duration. Keep the pause
> as low as reliably works, and prefer running long routines on one instance at
> a time.

Buttons live in `/config/panel-buttons.json`, so they survive restarts,
rebuilds and updates. On first run the file is seeded from `QUICK_KEYS`
(default `5,6,9,11`); after that the editor is the only thing that changes it.

Names and numbers are both validated — a name has to be ordinary text, and the
presses have to be plain menu numbers, so nothing typed into the editor can
reach a command.

> **These type into whatever is on screen.** If an instance is sitting at a
> submenu or a prompt rather than the main menu, the keypress goes there
> instead. Options that open a submenu or ask a question (Donate, Activate
> miracle) will need the rest of the answers typed in the terminal — the button
> only sends the first keypress.

### Updating just the panel

Most panel changes do not need a rebuild. The panel is a single file, so it can
be swapped into the running container and reloaded — instances keep running and
nobody has to log in again.

From the Unraid terminal, with the new files downloaded into `build/`:

```bash
docker cp /mnt/user/appdata/ikabot/build/ika-panel ikabot:/usr/local/bin/ika-panel
docker cp /mnt/user/appdata/ikabot/build/ika       ikabot:/usr/local/bin/ika
docker exec ikabot chmod +x /usr/local/bin/ika-panel /usr/local/bin/ika
docker exec ikabot ika panel restart
```

`ika panel` on its own shows the address and whether it is running.

**What can be hot-swapped this way:** `ika`, `ika-panel`, `ika-modules`,
`ika-update`, `ika-panel-host`. The helper scripts run fresh on every command,
so copying them is enough — only the panel needs the reload.

**What still needs a rebuild and recreate:** `Dockerfile`, `requirements.txt`,
and `entrypoint.sh`, since those only take effect when the container is built
or started. The tmux settings live in the entrypoint, so those count too.

> A hot-swapped file lives only in the running container. Rebuild at some point
> or the next `docker rm`/`docker run` silently puts the old version back.

### Panel versions

The panel carries its own version, separate from ikabot's. It is shown in three
places so there is never any doubt which one is actually running:

- next to the title on the page itself, and in the browser tab
- `docker exec -it ikabot ika panel`
- the first line of `/config/.ikabot/logs/panel.log`

In the repo the file is named `docker/ika-panel_v1.0.0`, the same `_vX.Y.Z`
convention the modules use. **The suffix is stripped on install** — the running
file is always `/usr/local/bin/ika-panel` — so the build and the entrypoint
never need editing when the version changes.

To upgrade without downloading anything by hand:

```bash
docker exec -it ikabot ika panel upgrade
```

That finds the newest `ika-panel_v*` in the repo, checks it really is the panel
before replacing anything, installs it with the suffix stripped, and reloads.
It prints `panel 1.0.0 -> 1.1.0`, or says you are already current.

```bash
docker exec -it ikabot ika panel
```

reports the running version and the newest available, so a stale panel is
obvious rather than something you discover from a confusing error.

> GitHub names its own download `main.zip` and that cannot be changed, so the
> version lives in the file inside it rather than in the zip name.

### Serving it over HTTPS

Like the terminal, it is plain HTTP on a port. To reach it by name with a real
certificate, give it its own Tailscale port:

```bash
docker exec tailscale tailscale serve --bg --https=8443 7682
```

Then `https://tower.tailXXXXX.ts.net:8443`.

### Turning it off

Same rule as the terminal: **no password, no listener.** Remove `TTYD_PASS`
(and `PANEL_PASS`) and neither starts. To give the panel its own password, set
`PANEL_PASS` and `PANEL_USER`.

> The panel can restart instances and update ikabot, so treat its password the
> way you treat the terminal's. It only ever runs a fixed list of commands —
> anything not on that list is rejected rather than passed to a shell — but it
> is still a control surface for your bots.

---

## Part 19 — Giving this to someone else

Everything above is your own setup. If someone else wants the same thing on
**their** hardware, they do not need this guide — there is a one-file installer
for them.

### Building the release zip

From the repo folder on the server (or anywhere you have the repo):

```
python3 tools/build-docker-release.py
```

That writes `releases/ikabot-docker_v1.0.0.zip`. Inside it are `INSTALL.bat`,
`install.sh`, `README.txt`, the whole `docker/` folder, and `app/` — ikabot
itself, plus the modules and the config examples.

`app/` is not optional. The image deliberately does not contain ikabot: the
installer copies it to `<data folder>/app` on the host and mounts it at
`/app`, exactly as Part 5 does by hand, so that `ika update` can replace it
and the new version outlives the container. Without it the container builds,
starts, and then fails with `No module named ikabot` in every window. The
packager refuses to build a zip that is missing it.

The version number in the filename comes from `installer-docker/VERSION`. The
script refuses to build if that number disagrees with the copies printed inside
`install.sh`, `INSTALL.bat` and `README.txt`, so what they download and what
they see on screen can never drift apart.

### Publishing it

Attach the zip to a GitHub Release. Because the version is in the filename, the
release page shows people exactly what they are downloading.

### What they do

| They have | They do | Hand them |
|---|---|---|
| Windows or Mac | Unzip, double-click `INSTALL.bat` | `docs/WINDOWS_GUIDE.md` |
| Unraid, TrueNAS, Linux | Unzip, `./install.sh` | `README.txt` in the zip |
| A Steam Deck | Unzip, `./install.sh` | `docs/STEAMDECK_GUIDE.md` |

`docs/WINDOWS_GUIDE.md` assumes no knowledge of Docker, containers or ikabot
and starts at "download this". It is the one to send someone who has never
done any of this before.

Either way it asks three questions — where to keep data, how many accounts, and
a password for the web pages — then builds and starts the container and prints
the panel and terminal URLs. Nothing else is typed.

### What they get, and what they do not

They get their own container, on their own machine, with their own vault. None
of your accounts, sessions or hardware are involved: the zip contains code only.

The one difference on Windows and Mac is that Docker there cannot share the
host's network, so the installer publishes ports 7681 and 7682 instead. The
panel and terminal work; the per-instance web servers from Part 17 are not
reachable. On Unraid, TrueNAS and Linux the installer uses `--network=host` and
everything behaves exactly as it does for you.

### Bumping the version

Edit `installer-docker/VERSION`, then update the three strings the packager
checks:

| File | String |
|---|---|
| `install.sh` | `INSTALLER_VERSION="1.0.1"` |
| `INSTALL.bat` | `INSTALLER_VERSION=1.0.1` |
| `README.txt` | `installer v1.0.1` |

Rebuild, and a new zip appears alongside the old one.

---

## Appendix A — What the build files do

You copied these in Part 5; you do not need to create them. This is just so you
know what they are.

| File | Purpose |
|---|---|
| `Dockerfile` | The recipe. Python 3.12 + tmux + ikabot's libraries |
| `requirements.txt` | ikabot's Python libraries |
| `entrypoint.sh` | Starts the 24 tmux windows when the container boots |
| `ika` | The maintenance commands (`status`, `restart`, `attach`…) |
| `ika-modules` | Installs/updates modules, stripping `_vX.Y.Z` from filenames |
| `ika-web-attach` | What the optional web terminal runs (Part 16) |
| `ika-update` | Updates ikabot itself, with rollback |
| `ika-panel` | The web control panel (Part 18) |
| `docker-compose.yml` | Alternative start method, for HexOS later |

Two details worth knowing:

- **Python is pinned to 3.12 on purpose.** 3.14 changed how background
  processes start, which would undo the memory saving described in Part 0.
- **`ncurses-bin` is installed on purpose.** ikabot clears the screen by
  running the `clear` command; without it every screen redraw prints an error
  instead.

---

## Appendix B — Where everything lives

| On Windows | In Docker | On the server |
|---|---|---|
| `%APPDATA%\.ikabot\vault` | `/config/.ikabot/vault` | `/mnt/user/appdata/ikabot/config/.ikabot/vault` |
| `%APPDATA%\.ikabot\sessions` | `/config/.ikabot/sessions` | `…/config/.ikabot/sessions` |
| `%APPDATA%\.ikabot\logs` | `/config/.ikabot/logs` | `…/config/.ikabot/logs` |
| `…\modules` | `/config/modules` | `…/config/modules` |
| `ikariam 1`…`24\` | `/app` (one copy) | `/mnt/user/appdata/ikabot/app` |

All of it is reachable over the network at `\\TOWER\appdata\ikabot\` if you
want to edit a CSV from Windows.

---

> **On a Steam Deck?** See `docs/STEAMDECK_GUIDE.md` — same container,
> but SteamOS needs Docker switching on, the Deck has to be stopped from
> going to sleep, and a system update wipes anything installed with
> `pacman`.

---

*Written against ikabot 7.4.5 / mod v1.7.7, Unraid 6.12.3.*
