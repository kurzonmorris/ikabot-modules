# Installing ikabot on Windows

Follow these steps in order. Everything you need is linked. It takes about
twenty minutes, most of which is waiting.

You need Windows 10 or 11, 64-bit, and about 4 GB of free disk space.

Steps 1 to 6 get it running. Step 10 is optional and adds access from your
phone, from anywhere.

---

## Step 1 — Install Docker Desktop

Download it here:

**https://www.docker.com/products/docker-desktop/**

Click **Download for Windows**, run the installer, accept the defaults.

- It will probably ask to **restart your PC**. Let it.
- After the restart, open **Docker Desktop** from the Start menu.
- The first time, it shows a sign-in page. **You do not need an account** —
  look for *Skip* or just close that panel.
- Wait until the bottom-left corner says **Engine running** with a green dot.

Leave Docker Desktop open. Nothing else works until it says *Engine running*.

---

## Step 2 — Download ikabot

Click this link and the file will download:

**[ikabot-docker_v1.0.25.zip](https://github.com/kurzonmorris/ikabot-modules/raw/main/releases/ikabot-docker_v1.0.25.zip)**

It is about 7 MB and lands in your **Downloads** folder.

> **Getting a newer one.** All the versions live in
> [the releases folder](https://github.com/kurzonmorris/ikabot-modules/tree/main/releases).
> Take the one with the highest number. Click it, then click the **Download raw
> file** button — the little downward arrow at the top right. Do **not**
> right-click and *Save link as* on the file's name: that saves the web page
> about the file instead of the file, and the zip will not open.

---

## Step 3 — Unzip it

1. Open your **Downloads** folder
2. **Right-click** `ikabot-docker_v1.0.25.zip` → **Properties**
3. If you see an **Unblock** tickbox at the bottom, tick it and press **OK**
4. **Right-click** the file again → **Extract All…** → **Extract**

A folder called `ikabot-docker_v1.0.25` opens. Inside it you should see:

```
INSTALL.bat        README.txt        app        docker        install.sh
```

If you do not see those, the download was not a real zip — go back to Step 2
and use the **Download raw file** button.

---

## Step 4 — Run the installer

**Double-click `INSTALL.bat`.**

Windows may show a blue box saying *"Windows protected your PC"*. Click
**More info**, then **Run anyway**. That warning appears for any downloaded
`.bat` file; it is not about this one in particular.

A black window opens and asks three things. Press **Enter** to accept the
suggestion in brackets, or type your own answer.

| It asks | What to do |
|---|---|
| `Where should ikabot keep its data?` | Press **Enter** |
| `How many accounts will you run?` | Type the number, press **Enter** |
| `Password:` | Type a password, press **Enter** |

> That password is for the ikabot web pages, not your game account. You will
> need it in a moment, so pick something you can remember. Nothing appears on
> screen as you type it — that is normal.

Then it builds. **The first time takes several minutes** and prints a wall of
text. That is meant to happen. Leave it alone until it says:

```
==================================================
  Done.

  Control panel : http://localhost:7682
  Terminal      : http://localhost:7681
```

Your browser opens the control panel by itself.

---

## Step 5 — Sign in

The browser asks for a username and password:

- **Username:** `ikabot`
- **Password:** the one you chose in Step 4

You should now see the ikabot control panel, with a menu down the left and a
box for every account you asked for.

> **Two version numbers, and that is normal.** The heading shows the control
> panel's own version; the line under it ends with the installer's. They are
> numbered separately and are usually a little apart — installer `v1.0.25`
> carries control panel `v1.0.23`, for instance. A lower number on the page
> than on the file you downloaded does **not** mean the install went wrong.
> The installer prints both numbers when it finishes, so you can check the
> page against that line rather than against the file name.

---

## Step 6 — Put your accounts in

Click **Accounts** in the left menu.

1. In the big box, type one account per line, like this:

   ```
   My first account, me@example.com, mygamepassword
   My second account, other@example.com, otherpassword
   ```

   The first part is any name you like — it is just a label so you can tell
   them apart.

2. Press **Read the lines**. Each line becomes a row. Check them.
3. In **Vault master password**, make up a password and type it twice. This is
   the password that protects your saved logins. **Write it down — it cannot
   be recovered.**
4. Press **Save all to the vault** and confirm.

Now click **Instances** in the left menu and press **Restart all**.

Each box is one account, and each will now log itself in.

---

## Step 7 — Using it

Everything is in the left-hand menu.

| Menu item | What it is for |
|---|---|
| **Instances** | Every account. Restart one, restart the crashed ones, see what each is doing |
| **Terminal** | The ikabot screens themselves, for anything the buttons do not cover |
| **Accounts** | Add more accounts later |
| **Buttons** | Make a button that presses a sequence of menu options for you, on one account or all of them |
| **Modules** | Keep the extra features up to date |
| **ikabot** | Update ikabot itself |
| **Files** | Read the CSV files the modules keep |

To drive an account by hand, click **Terminal** and use the ikabot menus there.
Press **Ctrl-B** then **W** to see the list of accounts and pick one.

Come back any time at **http://localhost:7682**. Bookmark it.

---

## Step 8 — Turning it off and on

It starts by itself whenever Docker Desktop is running, so normally there is
nothing to do. If you want to stop it:

- Open **Docker Desktop**, click **Containers** on the left
- Find **ikabot** and press the **stop** square, or the **play** triangle to
  start it again

Closing Docker Desktop stops everything. Your accounts and settings are kept.

> Your PC has to stay on and awake for ikabot to keep working. If it sleeps,
> ikabot sleeps with it.

---

## Step 9 — Keeping it up to date

In the control panel:

- **ikabot** in the menu → **Download & update ikabot**, then **Instances** →
  **Restart all**
- **Modules** in the menu → **Update all from GitHub**

You do not need to download the installer again for those.

---

## Step 10 — Reaching it from your phone, anywhere (optional)

So far the control panel only works on the PC it is installed on. This step
lets you open it from your phone, at home or away, without opening anything up
to the internet.

It uses **Tailscale**, which builds a small private network between your own
devices. Nothing becomes public; only devices signed in to your own account can
see each other. It is free for personal use.

> **Do not port-forward instead.** Forwarding port 7682 on your router puts the
> control panel — which can restart your bots and read your files — on the open
> internet for anyone to find. Tailscale is the safe way to do this, and it is
> easier.

### On the PC

1. Go to **https://tailscale.com/download** and install it
2. It appears in the system tray, bottom-right, near the clock
3. Click it → **Log in**, and sign in with Google, Microsoft or GitHub. That
   creates your private network. No payment, no configuration.
4. Click the tray icon again. It shows this PC and an address beginning
   **100.** — something like `100.122.72.17`

**Write that number down.** That is your PC's address on your private network,
and it does not change.

### On Android

1. Install **Tailscale** from the Play Store
2. Sign in with **the same account** you used on the PC
3. Turn the switch on. A key icon appears in the status bar

> **If the panel will not load but Tailscale says it is connected**, this is
> almost certainly Android's Private DNS setting. Go to **Settings → Network &
> internet → Private DNS** and set it to **Off** or **Automatic** — not a named
> provider. This catches a lot of people and looks exactly like Tailscale being
> broken.

### On iPhone or iPad

1. Install **Tailscale** from the App Store
2. Sign in with **the same account** you used on the PC
3. It asks to add a VPN configuration — allow it
4. Turn the switch on

### Using it

On the phone, open a browser and go to:

```
http://100.122.72.17:7682
```

using **your** number from the PC step. Sign in with `ikabot` and the password
from Step 4. Bookmark it.

That address works on mobile data, on someone else's wifi, anywhere — as long
as Tailscale is switched on at both ends and the PC is awake.

> **A shorter address.** Tailscale can also give the PC a name, so
> `http://my-pc:7682` works instead of the number. It is on by default for new
> accounts; if the name does not work, use the number.

### Turning it off

Switching Tailscale off on the phone stops the access. Nothing is left exposed
either way — there is no open port to close.

---

## One thing that does not work on Windows

ikabot can start a small web page for each account showing that account's town
(menu option 16). **On Windows those pages are not reachable**, and the
control panel's **Web servers** section will stay empty.

This is a Docker-on-Windows limitation, not a setting — Docker cannot share
your PC's network the way it can on Linux. Everything else works normally. If
you ever want that feature, it works on a Linux PC, a home server such as
Unraid or TrueNAS, or a Steam Deck.

---

## If something goes wrong

| What you see | What it means | What to do |
|---|---|---|
| `Docker Desktop is not installed` | Step 1 not done, or not finished | Install it, restart, open it, wait for *Engine running* |
| `Docker Desktop is installed but not running` | Docker is closed or still starting | Open Docker Desktop and wait for *Engine running*, then run `INSTALL.bat` again |
| `Cannot find the docker folder next to this file` | You ran `INSTALL.bat` from inside the zip instead of the extracted folder | Do Step 3 properly, then run it from the extracted folder |
| The zip will not open | You downloaded the web page, not the file | Step 2 — use the **Download raw file** button |
| `Windows protected your PC` | Normal for any downloaded `.bat` | **More info** → **Run anyway** |
| The browser says it cannot connect | The container is not running | Open Docker Desktop → **Containers** → start **ikabot** |
| It asks for a password you never set | It wants the web page password from Step 4 | Username is `ikabot` |
| An account box says **crashed** | That account stopped | Press **Restart** on that box |
| Updating ikabot ends in a wall of red Python text | One of ikabot's own commands is broken, and the update button cannot replace those | **ikabot** in the menu → **Refresh ika's own scripts**, then update again |
| Phone: Tailscale is on but the panel will not load | Android Private DNS is intercepting it | **Settings → Network & internet → Private DNS** → **Off** or **Automatic** |
| Phone: the PC shows as offline in Tailscale | The PC is asleep, off, or Tailscale is not running on it | Wake the PC and check the Tailscale tray icon says *Connected* |
| Phone: it asks for a password that is not accepted | Wrong account — the phone is on a different private network | Sign out on the phone and sign in with the same account as the PC |
| It works on the PC but not from the phone | Windows Firewall is blocking the ports on the Tailscale adapter | Allow **Docker Desktop** through Windows Firewall for **private** networks |

Nothing here can be broken by trying again. Running `INSTALL.bat` a second
time is safe: it keeps your accounts and settings and leaves an ikabot you have
already updated alone.

---

## Where your things are kept

In the folder you chose in Step 4 — by default `C:\Users\<you>\ikabot`:

| Folder | What is in it |
|---|---|
| `config` | Your saved logins, settings and logs. **Back this up. Do not share it** |
| `app` | ikabot itself |

To move ikabot to another PC, copy the `config` folder across after installing
there.
