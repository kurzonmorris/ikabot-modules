#!/usr/bin/env python3
"""ikabot-mod-install — Interactive installer and manager for ikabot multi-instance setup.

First screen: Install or Maintenance mode.

Install flow:
  1. Install location prompt
  2. Build folder structure
  3. Installer self-update check (downloads newer version if available)
  4. Download latest ikabot release into template folder
  5. Install bundled ikabot Manager tool
  5c. Optional modules download
  6. Instance count prompt
  7. Sync ikariam folders from template
  8. Create numbered shortcuts
  9. Shortcut destination prompt (folder picker)
 10. Download open/close/update tools if available
 11. Summary screen

Maintenance mode (loops until Exit):
  - Open all instances  (opens .lnk shortcuts from install dir)
  - Close all instances (taskkill ikabot.exe)
  - Update ikabot       (download latest, wipe and repopulate all instance folders)
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
import urllib.request
import urllib.error

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

# ── Constants ─────────────────────────────────────────────────────────────────

INSTALLER_VERSION = "1.1.0"

GITHUB_API = "https://api.github.com/repos/kurzonmorris/ikabot-modules/releases"

# Regex patterns for release asset filenames — version numbers are captured in group 1.
# ikabot:    ikabot-v7.3.3-mod-v0.9.4.zip   (mod version used for comparison)
# installer: ikabot-mod-install_v1.1.0.zip
# tools:     open close update.zip  (exact, no version in name yet)
ASSET_IKABOT_RE    = re.compile(r'^ikabot-v[\d.]+-mod-v([\d.]+)\.zip$',       re.IGNORECASE)
ASSET_INSTALLER_RE = re.compile(r'^ikabot-mod-install_v([\d.]+)\.zip$',       re.IGNORECASE)
ASSET_MODULES_RE   = re.compile(r'^ikabot-modules-[\d]+-[\d]+-[\d]+_.*\.zip$', re.IGNORECASE)
ASSET_TOOLS_RE     = re.compile(r'^open.close.update.*\.zip$',                 re.IGNORECASE)

DEFAULT_INSTALL   = Path("C:/Program Files/ikabot")
DEFAULT_SHORTCUTS = Path.home() / "Desktop" / "ikabot shortcuts"

STATE_FILE  = Path(tempfile.gettempdir()) / "ikabot_install_state.json"
CONFIG_FILE = Path.home() / "AppData" / "Local" / "ikabot" / "installer_config.json"

# ── Tkinter helpers ───────────────────────────────────────────────────────────

def _root() -> tk.Tk:
    r = tk.Tk()
    r.withdraw()
    r.attributes("-topmost", True)
    return r


def ask_string(prompt: str, title: str = "ikabot Installer", initial: str = "") -> str | None:
    r = _root()
    val = simpledialog.askstring(title, prompt, parent=r, initialvalue=initial)
    r.destroy()
    return val.strip() if val and val.strip() else None


def ask_integer(prompt: str, title: str = "ikabot Installer",
                minval: int = 1, maxval: int = 100) -> int | None:
    r = _root()
    val = simpledialog.askinteger(title, prompt, parent=r, minvalue=minval, maxvalue=maxval)
    r.destroy()
    return val


def pick_folder(title: str, initial: str = "") -> Path | None:
    r = _root()
    chosen = filedialog.askdirectory(title=title, initialdir=initial or str(Path.home()))
    r.destroy()
    return Path(chosen) if chosen else None


def show_info(msg: str, title: str = "ikabot Installer") -> None:
    r = _root()
    messagebox.showinfo(title, msg, parent=r)
    r.destroy()


def show_error(msg: str, title: str = "ikabot Installer — Error") -> None:
    r = _root()
    messagebox.showerror(title, msg, parent=r)
    r.destroy()


def ask_yes_no(msg: str, title: str = "ikabot Installer") -> bool:
    r = _root()
    result = messagebox.askyesno(title, msg, parent=r)
    r.destroy()
    return result


def ask_ok_cancel(msg: str, title: str = "ikabot Installer") -> bool:
    r = _root()
    result = messagebox.askokcancel(title, msg, parent=r)
    r.destroy()
    return result


def ask_choice(title: str, message: str, options: list[str]) -> str | None:
    """Show a dialog with one button per option. Returns the chosen label or None if closed."""
    result: list[str | None] = [None]

    win = tk.Tk()
    win.withdraw()

    dlg = tk.Toplevel(win)
    dlg.title(title)
    dlg.resizable(False, False)
    dlg.attributes("-topmost", True)

    tk.Label(dlg, text=message, justify="left", padx=24, pady=12,
             wraplength=360).pack(fill="x")

    frm = tk.Frame(dlg, padx=24, pady=8)
    frm.pack()
    for opt in options:
        def handler(o=opt):
            result[0] = o
            win.destroy()
        tk.Button(frm, text=opt, width=30, pady=4, command=handler).pack(pady=3)

    dlg.protocol("WM_DELETE_WINDOW", win.destroy)
    dlg.lift()
    dlg.focus_force()
    win.mainloop()
    return result[0]

# ── Version helpers ───────────────────────────────────────────────────────────

def ver_tuple(v: str) -> tuple[int, ...]:
    v = v.strip().lstrip("vV")
    try:
        return tuple(int(x) for x in re.split(r"[.\-_]", v) if x.isdigit())
    except Exception:
        return (0,)


def is_newer(remote: str, local: str) -> bool:
    return ver_tuple(remote) > ver_tuple(local)


def write_version(directory: Path, version: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    clean = version.lstrip("vV")
    (directory / "version.json").write_text(json.dumps({"version": clean}, indent=2))
    for old in directory.glob("version_*"):
        old.unlink(missing_ok=True)
    (directory / f"version_{clean}").touch()


def read_version(directory: Path) -> str | None:
    vj = directory / "version.json"
    if vj.exists():
        try:
            return json.loads(vj.read_text()).get("version")
        except Exception:
            pass
    for f in directory.glob("version_*"):
        return f.name[len("version_"):]
    return None

# ── GitHub / download helpers ─────────────────────────────────────────────────

def fetch_releases() -> list[dict]:
    req = urllib.request.Request(
        GITHUB_API,
        headers={
            "User-Agent": f"ikabot-mod-install/{INSTALLER_VERSION}",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def find_asset(releases: list[dict], pattern: re.Pattern) -> tuple[str, str, str] | None:
    """Return (download_url, tag_name, version) for the newest release with a matching asset.

    GitHub returns releases newest-first, so the first match is always the latest.
    Version is extracted from the filename capture group; falls back to the release tag.
    """
    for release in releases:
        for asset in release.get("assets", []):
            m = pattern.match(asset["name"])
            if m:
                ver = m.group(1) if m.lastindex else release["tag_name"].lstrip("vV")
                return asset["browser_download_url"], release["tag_name"], ver
    return None


def download_zip(url: str, dest_dir: Path, label: str = "") -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest_dir / "_tmp_download.zip"
    print(f"  Downloading {label or url} ...")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"ikabot-mod-install/{INSTALLER_VERSION}"},
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        with open(tmp, "wb") as f:
            shutil.copyfileobj(response, f)
    print(f"  Extracting to {dest_dir} ...")
    try:
        with zipfile.ZipFile(tmp, "r") as zf:
            zf.extractall(dest_dir)
    finally:
        tmp.unlink(missing_ok=True)

# ── Shortcut helpers ──────────────────────────────────────────────────────────

def create_shortcut(target: Path, lnk_path: Path) -> None:
    ps = (
        f'$ws = New-Object -ComObject WScript.Shell; '
        f'$s = $ws.CreateShortcut("{lnk_path}"); '
        f'$s.TargetPath = "{target}"; '
        f'$s.WorkingDirectory = "{target.parent}"; '
        f'$s.Save()'
    )
    subprocess.run(["powershell", "-WindowStyle", "Hidden", "-Command", ps], check=True)


def shortcut_name(folder_name: str) -> str:
    """'ikariam 10' -> '10 ikariam.lnk'"""
    m = re.match(r"^(.*?)\s+(\d+)$", folder_name.strip())
    return f"{m.group(2)} {m.group(1).strip()}.lnk" if m else folder_name + ".lnk"

# ── State hand-off (self-update) ──────────────────────────────────────────────

def save_state(install_dir: Path) -> None:
    STATE_FILE.write_text(json.dumps({"install_dir": str(install_dir)}, indent=2))


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text())
            STATE_FILE.unlink(missing_ok=True)
            return data
        except Exception:
            pass
    return {}


def save_config(data: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2))


def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            pass
    return {}

# ── Maintenance helpers ───────────────────────────────────────────────────────

def _leading_num(name: str) -> int:
    m = re.search(r"\d+", name)
    return int(m.group()) if m else 0


def maint_open_all(install_dir: Path) -> None:
    sc_dir = install_dir / "shortcuts"
    lnks = sorted(sc_dir.glob("*.lnk"), key=lambda p: _leading_num(p.name)) if sc_dir.exists() else []

    if not lnks:
        show_info(
            "Select the folder that contains your ikabot shortcut (.lnk) files.",
            "Open All — Select Folder",
        )
        picked = pick_folder("Select your ikabot shortcuts folder", initial=str(install_dir))
        if not picked:
            return
        lnks = sorted(picked.glob("*.lnk"), key=lambda p: _leading_num(p.name))

    if not lnks:
        show_error("No shortcut (.lnk) files were found in the selected folder.")
        return

    for lnk in lnks:
        os.startfile(str(lnk))
        time.sleep(0.15)

    show_info(f"{len(lnks)} ikabot instance(s) launched.", "Open All — Done")


def maint_close_all() -> None:
    if not ask_yes_no("Close all running ikabot instances?", "Close All"):
        return

    res = subprocess.run(
        ["taskkill", "/F", "/IM", "ikabot.exe"],
        capture_output=True, text=True,
    )
    if res.returncode == 0:
        show_info("All ikabot instances have been closed.", "Close All — Done")
    elif res.returncode == 128:
        show_info("No ikabot instances were running.", "Close All")
    else:
        show_error(
            f"taskkill exited with code {res.returncode}.\n"
            "Some instances may not have closed cleanly.\n\n"
            + (res.stderr or res.stdout or "")
        )


def maint_update_ikabot(install_dir: Path) -> None:
    template_dir = install_dir / "ikabot template"
    ikabot_dir   = install_dir / "ikabot"

    if not template_dir.exists() or not ikabot_dir.exists():
        show_error(
            f"Required folders not found inside:\n  {install_dir}\n\n"
            "Expected:\n  - ikabot template\n  - ikabot\n\n"
            "Make sure you selected the correct install folder."
        )
        return

    local_ver    = read_version(template_dir) or "not installed"
    folder_count = sum(1 for f in ikabot_dir.iterdir()
                       if f.is_dir() and re.match(r"^ikariam \d+$", f.name))

    if not ask_yes_no(
        f"Current installed version:  {local_ver}\n"
        f"ikabot instance folders:    {folder_count}\n\n"
        "This will:\n"
        "  - Download the latest ikabot release from GitHub\n"
        "  - Replace the ikabot template folder\n"
        f"  - Wipe and re-populate all {folder_count} instance folder(s)\n\n"
        "Close all running instances before continuing.\n\n"
        "Proceed?",
        "Update ikabot",
    ):
        return

    print("Contacting GitHub ...")
    try:
        releases = fetch_releases()
    except Exception as exc:
        show_error(f"Could not contact GitHub:\n\n{exc}")
        return

    ikabot_asset = find_asset(releases, ASSET_IKABOT_RE)
    if not ikabot_asset:
        show_error(
            "No ikabot release asset found on GitHub.\n\n"
            "Expected: ikabot-v{x.x.x}-mod-v{x.x.x}.zip"
        )
        return

    url, _, remote_ver = ikabot_asset
    print(f"Downloading ikabot mod v{remote_ver} ...")
    for item in template_dir.iterdir():
        if item.name.startswith("version"):
            continue
        shutil.rmtree(item) if item.is_dir() else item.unlink()

    try:
        download_zip(url, template_dir, f"ikabot mod v{remote_ver}")
        write_version(template_dir, remote_ver)
    except Exception as exc:
        show_error(f"Failed to download ikabot:\n\n{exc}")
        return

    print(f"Updating {folder_count} instance folder(s) ...")
    sync_ikariam_folders(ikabot_dir, folder_count, template_dir)

    show_info(
        f"Update complete!\n\n"
        f"  ikabot mod version : {remote_ver}\n"
        f"  Instances updated  : {folder_count}\n\n"
        "All instances are now running the latest version.",
        "Update Complete",
    )


def maintenance_mode(install_dir: Path) -> None:
    while True:
        choice = ask_choice(
            "ikabot Manager",
            "ikabot Manager\n\nSelect an action:",
            ["Open all instances", "Close all instances", "Update ikabot", "Exit"],
        )
        if choice is None or choice == "Exit":
            return
        elif choice == "Open all instances":
            maint_open_all(install_dir)
        elif choice == "Close all instances":
            maint_close_all()
        elif choice == "Update ikabot":
            maint_update_ikabot(install_dir)

# ── ikariam folder management ─────────────────────────────────────────────────

def sync_ikariam_folders(ikabot_dir: Path, count: int, template_dir: Path) -> None:
    """Ensure ikariam 1..count exist, wipe their contents, copy template into each."""
    errors: list[str] = []
    for i in range(1, count + 1):
        folder = ikabot_dir / f"ikariam {i}"
        try:
            if folder.exists():
                for item in list(folder.iterdir()):
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
            else:
                folder.mkdir(parents=True)

            for item in template_dir.iterdir():
                if item.name.startswith("version"):
                    continue
                dest = folder / item.name
                if item.is_dir():
                    shutil.copytree(item, dest)
                else:
                    shutil.copy2(item, dest)

            print(f"  ikariam {i} ready")
        except Exception as exc:
            errors.append(f"  ikariam {i}: {exc}")
            print(f"  ERROR on ikariam {i}: {exc}")

    if errors:
        show_error(
            "Some instance folders could not be set up:\n\n"
            + "\n".join(errors)
            + "\n\nClose any running ikabot instances and re-run the installer."
        )

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    state = load_state()
    saved_install = Path(state["install_dir"]) if "install_dir" in state else None
    config        = load_config()
    last_install  = Path(config["install_dir"]) if "install_dir" in config else None

    # ── Mode selection ────────────────────────────────────────────────────────
    mode = ask_choice(
        f"ikabot  v{INSTALLER_VERSION}",
        f"ikabot Installer  v{INSTALLER_VERSION}\n\nWhat would you like to do?",
        ["Install / Update ikabot", "Maintenance"],
    )
    if mode is None:
        return

    if mode == "Maintenance":
        default_dir = saved_install or last_install or DEFAULT_INSTALL
        show_info(
            "Select your ikabot install folder.\n\n"
            "This is the folder that contains the 'ikabot' and\n"
            "'ikabot template' subfolders.",
            "ikabot Manager — Select Install Folder",
        )
        picked = pick_folder(
            "Select your ikabot install folder",
            initial=str(default_dir),
        )
        if not picked:
            return
        maintenance_mode(picked)
        return

    # ── Install mode ──────────────────────────────────────────────────────────
    # ── Welcome ───────────────────────────────────────────────────────────────
    show_info(
        f"Welcome to the ikabot Installer  (v{INSTALLER_VERSION})\n\n"
        "ikabot is a browser automation tool for Ikariam.\n"
        "This installer will set up one or more independent\n"
        "ikabot instances — each one can be logged into a\n"
        "different Ikariam account and run at the same time.\n\n"
        "What this installer does:\n"
        "  1. Downloads the latest version of ikabot\n"
        "  2. Downloads the ikabot Manager tool\n"
        "  3. Optionally downloads extra automation modules\n"
        "  4. Creates a separate folder for each instance\n"
        "  5. Creates shortcuts so you can launch them easily\n\n"
        "You will be asked a few questions. Click OK to begin.",
        "ikabot Installer",
    )

    # ── 1. Install location ───────────────────────────────────────────────────
    default_path = str(saved_install or DEFAULT_INSTALL)
    location_str = ask_string(
        "Step 1 of 4 — Install Location\n\n"
        "Where would you like to install ikabot?\n"
        "Edit the path below or leave it as the default.\n\n"
        "The folder will be created automatically if it does not exist.\n"
        "Installing to Program Files may require running as Administrator.",
        title="Step 1 of 4 — Install Location",
        initial=default_path,
    )
    if not location_str:
        print("Cancelled.")
        return

    install_dir = Path(location_str)
    if not ask_ok_cancel(
        f"ikabot will be installed to:\n\n"
        f"  {install_dir}\n\n"
        "If this folder already exists, its contents will be updated,\n"
        "not replaced. Existing instance folders will be kept.\n\n"
        "Continue?"
    ):
        print("Cancelled.")
        return

    # ── 2. Create folder structure ────────────────────────────────────────────
    shortcuts_dir = install_dir / "shortcuts"
    modules_dir   = install_dir / "modules"
    template_dir  = install_dir / "ikabot template"
    ikabot_dir    = install_dir / "ikabot"
    update_dir    = install_dir / "update"

    for d in (shortcuts_dir, modules_dir, template_dir, ikabot_dir, update_dir):
        d.mkdir(parents=True, exist_ok=True)

    print("Folder structure created.")

    # ── 3. Fetch GitHub releases ──────────────────────────────────────────────
    print("Contacting GitHub ...")
    try:
        releases = fetch_releases()
    except Exception as exc:
        show_error(
            "Could not contact GitHub to check for the latest version.\n\n"
            f"Detail: {exc}\n\n"
            "Check your internet connection and try again."
        )
        return

    # ── 4. Installer self-update ──────────────────────────────────────────────
    installer_asset = find_asset(releases, ASSET_INSTALLER_RE)
    if installer_asset:
        url, tag, remote_ver = installer_asset
        local_ver = read_version(update_dir) or INSTALLER_VERSION
        if is_newer(remote_ver, local_ver):
            print(f"Downloading installer update v{remote_ver} ...")
            try:
                download_zip(url, update_dir, f"ikabot-mod-install v{remote_ver}")
                write_version(update_dir, remote_ver)
            except Exception as exc:
                show_error(f"Failed to download the installer update:\n\n{exc}")
                return

        update_ver = read_version(update_dir)
        if update_ver and is_newer(update_ver, INSTALLER_VERSION):
            # The release zip may be a onedir bundle (ikabot-mod-install/ikabot-mod-install.exe)
            # or a flat archive (ikabot-mod-install.exe). Check both.
            subdir = update_dir / "ikabot-mod-install"
            new_exe = next(
                (p for p in (
                    subdir / "ikabot-mod-install.exe",
                    update_dir / "ikabot-mod-install.exe",
                ) if p.exists()),
                None,
            )
            new_py = next(
                (p for p in (
                    subdir / "ikabot-mod-install.py",
                    update_dir / "ikabot-mod-install.py",
                ) if p.exists()),
                None,
            )
            launcher = new_exe or new_py

            if launcher:
                show_info(
                    f"A newer version of this installer (v{update_ver}) has been\n"
                    "downloaded and will now launch automatically.\n\n"
                    "Your installation folder has been saved — the new installer\n"
                    "will continue from where this one left off.\n\n"
                    "This window will close.",
                    "Installer updated — relaunching",
                )
                save_state(install_dir)
                if launcher.suffix == ".py":
                    subprocess.Popen([sys.executable, str(launcher)],
                                     cwd=str(launcher.parent))
                else:
                    subprocess.Popen([str(launcher)],
                                     cwd=str(launcher.parent))
                sys.exit(0)
    else:
        print("Note: no ikabot-mod-install release asset found on GitHub — skipping self-update check.")

    # ── 5. Download ikabot ────────────────────────────────────────────────────
    ikabot_asset = find_asset(releases, ASSET_IKABOT_RE)
    if not ikabot_asset:
        show_error(
            "The ikabot program files could not be found on GitHub.\n\n"
            "Expected an asset matching:  ikabot-v{x.x.x}-mod-v{x.x.x}.zip\n\n"
            "Please check that the release has been published and try again."
        )
        return

    url, tag, remote_ver = ikabot_asset
    local_ikabot_ver = read_version(template_dir)
    if local_ikabot_ver is None or is_newer(remote_ver, local_ikabot_ver):
        print(f"Downloading ikabot mod v{remote_ver} ...")
        for item in template_dir.iterdir():
            if item.name.startswith("version"):
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        try:
            download_zip(url, template_dir, f"ikabot mod v{remote_ver}")
            write_version(template_dir, remote_ver)
            print(f"ikabot mod v{remote_ver} downloaded.")
        except Exception as exc:
            show_error(f"Failed to download ikabot:\n\n{exc}")
            return
    else:
        print(f"ikabot already up to date (mod v{local_ikabot_ver}).")

    ikabot_ver = read_version(template_dir) or remote_ver

    # ── 5b. Install ikabot manager ────────────────────────────────────────────
    # The manager is bundled with the installer. Copy it to its own folder
    # inside the install directory so it lives alongside the other tools.
    manager_dir = install_dir / "ikabot manager"
    manager_dir.mkdir(exist_ok=True)

    # When frozen by PyInstaller, bundled files land in sys._MEIPASS.
    # When running as a plain script, they sit next to this file.
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    bundled_manager = bundle_root / "ikabot_manager.ahk"
    bundled_ps      = bundle_root / "ikabot_update.ps1"

    if bundled_manager.exists():
        try:
            shutil.copy2(bundled_manager, manager_dir / "ikabot_manager.ahk")
            if bundled_ps.exists():
                shutil.copy2(bundled_ps, manager_dir / "ikabot_update.ps1")
            print("ikabot manager installed.")
        except Exception as exc:
            print(f"Warning: could not copy ikabot manager: {exc}")
    else:
        print("Note: ikabot_manager.ahk not found in installer bundle — skipping.")

    # ── 5c. Download ikabot modules (optional) ────────────────────────────────
    modules_asset = find_asset(releases, ASSET_MODULES_RE)
    if modules_asset:
        url, tag, _ = modules_asset
        if ask_yes_no(
            "Step 4 of 4 — ikabot Modules\n\n"
            "Additional automation modules are available for ikabot.\n"
            "These extend what ikabot can do (recruitment, construction,\n"
            "resource transport, and more).\n\n"
            "Would you like to download and install them now?\n"
            "(They will be placed in the modules folder.)"
        ):
            try:
                download_zip(url, modules_dir, "ikabot modules")
                # Rename files: strip _vXXX suffix from stem, leave CSV untouched
                for f in list(modules_dir.iterdir()):
                    if not f.is_file() or f.suffix.lower() == ".csv":
                        continue
                    new_stem = re.sub(r"_v.+$", "", f.stem)
                    new_name = new_stem + f.suffix
                    if new_name != f.name:
                        target = f.parent / new_name
                        if target.exists():
                            target.unlink()
                        f.rename(target)
                        print(f"  {f.name} -> {new_name}")
                print("Modules installed.")
            except Exception as exc:
                show_error(f"Could not download modules:\n\n{exc}")
        else:
            print("Modules skipped.")
    else:
        print("Note: no modules release found on GitHub — skipping.")

    # ── 6. Instance count ─────────────────────────────────────────────────────
    existing = sum(
        1 for f in ikabot_dir.iterdir()
        if f.is_dir() and re.match(r"^ikariam \d+$", f.name)
    ) if ikabot_dir.exists() else 0

    existing_note = (
        f"\n\nYou already have {existing} instance folder(s) set up.\n"
        "Enter a higher number to add more, the same number to refresh,\n"
        "or enter 0 (or leave blank) to skip this step and keep what you have."
        if existing > 0 else ""
    )

    count = existing  # default: keep existing count if user skips
    while True:
        raw = ask_string(
            "Step 2 of 4 — How many instances?\n\n"
            "Each instance is a separate copy of ikabot that can log into\n"
            "a different Ikariam account and run at the same time.\n\n"
            "Enter the total number of instances you want (max 100).\n"
            "Enter 0 or leave blank to skip and keep your existing setup.\n"
            "Re-run the installer any time to add more without losing your setup."
            + existing_note,
            title="Step 2 of 4 — Instance Count",
            initial=str(existing) if existing > 0 else "",
        )
        if raw is None:
            print("Cancelled.")
            return
        raw = raw.strip()
        if raw == "" or raw == "0":
            print(f"Instance count skipped — keeping {existing} existing folder(s).")
            count = existing
            break
        if not raw.isdigit():
            show_error("Please enter a whole number, or 0 to skip.")
            continue
        count = int(raw)
        if count < 1 or count > 100:
            show_error("Please enter a number between 1 and 100, or 0 to skip.")
            continue
        if count > 20:
            if not ask_yes_no(
                f"You requested {count} instances.\n\n"
                "Running this many at once will use significant memory and CPU.\n\n"
                f"Are you sure you want to set up all {count}?"
            ):
                continue
        break

    if count == 0:
        print("No instances to set up — skipping folder sync and shortcuts.")

    # ── 7. Sync ikariam folders ───────────────────────────────────────────────
    if count > 0:
        print(f"Setting up {count} ikabot instance(s) ...")
        sync_ikariam_folders(ikabot_dir, count, template_dir)
    else:
        print("Skipping instance folder sync.")

    # ── 8. Create internal shortcuts ──────────────────────────────────────────
    sc_errors: list[str] = []
    if count > 0:
        print("Creating shortcuts ...")
        for i in range(1, count + 1):
            exe = ikabot_dir / f"ikariam {i}" / "ikabot.exe"
            if exe.exists():
                lnk_name = shortcut_name(f"ikariam {i}")
                try:
                    create_shortcut(exe, shortcuts_dir / lnk_name)
                    print(f"  {lnk_name}")
                except Exception as exc:
                    sc_errors.append(lnk_name)
                    print(f"  ERROR creating {lnk_name}: {exc}")

        if sc_errors:
            show_error(
                "Some shortcuts could not be created:\n\n"
                + "\n".join(sc_errors)
                + "\n\nThe instance folders are still set up correctly.\n"
                "You can create the shortcuts manually later."
            )

    # ikabot manager shortcut — goes into shortcuts_dir so step 9 copies it automatically
    for ahk in manager_dir.glob("*.ahk"):
        try:
            create_shortcut(ahk, shortcuts_dir / (ahk.stem + ".lnk"))
            print(f"  {ahk.stem}.lnk")
        except Exception as exc:
            print(f"  Warning: could not create manager shortcut: {exc}")
    for exe_file in manager_dir.glob("*.exe"):
        try:
            create_shortcut(exe_file, shortcuts_dir / (exe_file.stem + ".lnk"))
            print(f"  {exe_file.stem}.lnk")
        except Exception as exc:
            print(f"  Warning: could not create manager shortcut: {exc}")

    # ── 9. User shortcut destination ──────────────────────────────────────────
    show_info(
        "Step 3 of 4 — Shortcut Location\n\n"
        "Choose where to put your ikabot shortcuts.\n\n"
        "A folder browser will open — simply select Desktop\n"
        "(or any folder you prefer) and click OK.\n\n"
        "The installer will automatically create an\n"
        "'ikabot shortcuts' folder there for you.\n"
        "You do not need to create the folder yourself.\n\n"
        "Click Cancel to place the shortcuts on the Desktop by default.",
        "Step 3 of 4 — Shortcut Location",
    )
    sc_parent = pick_folder(
        "Select where to create the 'ikabot shortcuts' folder  (e.g. your Desktop)",
        initial=str(Path.home() / "Desktop"),
    )
    user_sc_dir = (sc_parent / "ikabot shortcuts") if sc_parent else DEFAULT_SHORTCUTS
    user_sc_dir.mkdir(parents=True, exist_ok=True)

    copy_errors: list[str] = []
    for lnk in shortcuts_dir.glob("*.lnk"):
        try:
            shutil.copy2(lnk, user_sc_dir / lnk.name)
        except Exception as exc:
            copy_errors.append(f"  {lnk.name}: {exc}")

    if copy_errors:
        show_error(
            "Some shortcuts could not be copied to your chosen location:\n\n"
            + "\n".join(copy_errors)
        )
    else:
        print(f"Shortcuts saved to {user_sc_dir}")

    # ── 10. Open/close/update tools ───────────────────────────────────────────
    tools_asset = find_asset(releases, ASSET_TOOLS_RE)
    if tools_asset:
        url, tag, remote_ver = tools_asset
        tools_dir = update_dir / "tools"
        local_tools_ver = read_version(tools_dir)
        if local_tools_ver is None or is_newer(remote_ver, local_tools_ver):
            tools_dir.mkdir(exist_ok=True)
            print(f"Downloading tools v{remote_ver} ...")
            try:
                download_zip(url, tools_dir, f"tools v{remote_ver}")
                write_version(tools_dir, remote_ver)
                for sc_dest in (shortcuts_dir, user_sc_dir):
                    for ahk in tools_dir.glob("*.ahk"):
                        create_shortcut(ahk, sc_dest / (ahk.stem + ".lnk"))
                    for exe_file in tools_dir.glob("*.exe"):
                        create_shortcut(exe_file, sc_dest / (exe_file.stem + ".lnk"))
                print("Tools installed.")
            except Exception as exc:
                print(f"Warning: could not download tools: {exc}")
        else:
            print(f"Tools already up to date (v{local_tools_ver}).")
    else:
        print("Note: no open/close/update tools release found on GitHub — skipping.")

    # ── Done ──────────────────────────────────────────────────────────────────
    save_config({"install_dir": str(install_dir)})
    show_info(
        "Installation complete!\n\n"
        f"  ikabot version   : {ikabot_ver}\n"
        f"  Installer version: {INSTALLER_VERSION}\n"
        f"  Instances set up : {count}\n"
        f"  Installed to     : {install_dir}\n"
        f"  Shortcuts saved  : {user_sc_dir}\n\n"
        "What to do next:\n"
        f"  1. Open '{user_sc_dir.name}' on your Desktop\n"
        "  2. Use 'ikabot Manager' to open, close or update all instances\n"
        "  3. Or double-click a numbered shortcut to launch one instance\n"
        "  4. Log into your Ikariam account in the browser that opens\n\n"
        "To add more instances later, simply re-run this installer\n"
        "and enter a higher number — existing instances are not affected.",
        "Installation Complete",
    )
    print("Installation complete.")


if __name__ == "__main__":
    main()
