#!/usr/bin/env python3
"""ikabot-mod-install — Interactive installer and manager for ikabot multi-instance setup.

First screen: Install or Maintenance mode.

Install flow:
  1. Install location prompt
  2. Build folder structure
  3. Installer self-update check (downloads newer version if available)
  4. Download latest ikabot release into template folder
  5. Optional modules download (from GitHub repo — no release zip needed)
  6. Instance count prompt
  7. Sync ikariam folders from template
  8. Create numbered shortcuts (+ installer shortcut when frozen)
  9. Shortcut destination prompt (folder picker)
 10. Summary screen

Maintenance mode (loops until Exit):
  - Open all instances  (opens .lnk shortcuts from install dir)
  - Close all instances (taskkill ikabot.exe)
  - Download latest versions (ikabot, modules, installer)
  - Update (apply downloaded ikabot to instances, update installer shortcut)
  - Status
  - Exit
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
from datetime import datetime
from pathlib import Path
import urllib.request
import urllib.error

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

# ── Constants ─────────────────────────────────────────────────────────────────

INSTALLER_VERSION = "1.3.0"

GITHUB_API      = "https://api.github.com/repos/kurzonmorris/ikabot-modules/releases"
GITHUB_CONTENTS = "https://api.github.com/repos/kurzonmorris/ikabot-modules/contents"

# Regex patterns for release asset filenames — version numbers captured in groups.
# ikabot:    ikabot-v7.4.0--mod-v0.9.4.zip  (single or double dash before mod)
# installer: ikabot-mod-install_v1.3.0.zip
ASSET_IKABOT_RE    = re.compile(r'^ikabot-v([\d.]+)-+mod-v([\d.]+)\.zip$', re.IGNORECASE)
ASSET_INSTALLER_RE = re.compile(r'^ikabot-mod-install_v([\d.]+)\.zip$',    re.IGNORECASE)

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


def ask_count_or_skip(title: str, message: str, initial: str = "") -> str | None:
    """Show a dialog with a text entry plus OK, Skip, and Cancel buttons.

    Returns the entered text (may be empty), "SKIP" if Skip was clicked,
    or None if Cancel/close was clicked.
    """
    result: list[str | None] = [None]

    win = tk.Tk()
    win.withdraw()

    dlg = tk.Toplevel(win)
    dlg.title(title)
    dlg.resizable(False, False)
    dlg.attributes("-topmost", True)

    tk.Label(dlg, text=message, justify="left", padx=24, pady=12,
             wraplength=380).pack(fill="x")

    entry_var = tk.StringVar(value=initial)
    entry = tk.Entry(dlg, textvariable=entry_var, width=12, font=("", 11), justify="center")
    entry.pack(pady=(0, 8))
    entry.focus_set()
    entry.select_range(0, "end")

    frm = tk.Frame(dlg, padx=24, pady=8)
    frm.pack()

    def on_ok():
        result[0] = entry_var.get()
        win.destroy()

    def on_skip():
        result[0] = "SKIP"
        win.destroy()

    def on_cancel():
        win.destroy()

    tk.Button(frm, text="OK",     width=12, command=on_ok).grid(row=0, column=0, padx=4, pady=3)
    tk.Button(frm, text="Skip",   width=12, command=on_skip).grid(row=0, column=1, padx=4, pady=3)
    tk.Button(frm, text="Cancel", width=12, command=on_cancel).grid(row=0, column=2, padx=4, pady=3)

    dlg.bind("<Return>", lambda _: on_ok())
    dlg.bind("<Escape>", lambda _: on_cancel())
    dlg.protocol("WM_DELETE_WINDOW", on_cancel)
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


def fetch_repo_folder(folder_path: str) -> list[dict]:
    """Return Contents API listing for a repo folder."""
    req = urllib.request.Request(
        f"{GITHUB_CONTENTS}/{folder_path}",
        headers={
            "User-Agent": f"ikabot-mod-install/{INSTALLER_VERSION}",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def download_repo_files(folder_path: str, dest_dir: Path,
                        skip_names: set[str] | None = None) -> int:
    """Download every file in a repo folder to dest_dir. Returns count.

    Files whose name (case-insensitive) is in skip_names are not downloaded.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    items = fetch_repo_folder(folder_path)
    skip = {n.lower() for n in (skip_names or set())}
    count = 0
    for item in items:
        if item["type"] != "file":
            continue
        if item["name"].lower() in skip:
            print(f"  {item['name']}  (skipped — keeping your existing file)")
            continue
        print(f"  {item['name']}")
        req = urllib.request.Request(
            item["download_url"],
            headers={"User-Agent": f"ikabot-mod-install/{INSTALLER_VERSION}"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            (dest_dir / item["name"]).write_bytes(resp.read())
        count += 1
    return count


def ask_csv_overwrite(modules_dir: Path) -> set[str]:
    """If bulkdistribution.csv exists locally, ask before overwriting it.

    Returns the set of filenames to skip during download.
    """
    csv_file = modules_dir / "bulkdistribution.csv"
    if not csv_file.exists():
        return set()
    if ask_yes_no(
        "You already have a bulkdistribution.csv file:\n"
        f"  {csv_file}\n\n"
        "Downloading a new copy will OVERWRITE it and any\n"
        "distribution settings you have saved in it will be LOST.\n\n"
        "Do you want to download the new copy and overwrite yours?\n\n"
        "  Yes = overwrite with the latest version from GitHub\n"
        "  No  = keep your existing file",
        "Overwrite bulkdistribution.csv?",
    ):
        return set()
    return {"bulkdistribution.csv"}


def strip_version_suffixes(directory: Path) -> None:
    """Remove _vXXX suffix from every file in directory (CSV files left untouched)."""
    for f in list(directory.iterdir()):
        if not f.is_file() or f.suffix.lower() == ".csv":
            continue
        new_stem = re.sub(r"_v.+$", "", f.stem)
        new_name = new_stem + f.suffix
        if new_name != f.name:
            target = f.parent / new_name
            if target.exists():
                target.unlink()
            f.rename(target)


def write_modules_timestamp(modules_dir: Path) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    (modules_dir / "modules_updated.txt").write_text(ts)

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


def maint_open_all_ps(install_dir: Path) -> None:
    sc_dir = install_dir / "shortcuts"

    # Prefer ps1 already copied to the shortcuts folder; fall back to bundle
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    ps1 = next(
        (p for p in (
            sc_dir / "open-all-instances.ps1",
            bundle_root / "open-all-instances.ps1",
        ) if p.exists()),
        None,
    )

    if ps1:
        subprocess.Popen(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(ps1)])
        return

    # Inline fallback when ps1 is unavailable
    lnks = sorted(
        [f for f in sc_dir.glob("*.lnk") if f.name[0].isdigit()],
        key=lambda p: _leading_num(p.name),
    ) if sc_dir.exists() else []

    if not lnks:
        show_error("No numbered shortcuts found in:\n" + str(sc_dir))
        return

    cmds = "; ".join(
        f'Start-Process "{lnk}"; Start-Sleep -Milliseconds 150' for lnk in lnks
    )
    subprocess.Popen(
        ["powershell", "-ExecutionPolicy", "Bypass", "-NoExit", "-Command", cmds]
    )


def maint_close_all_ps() -> None:
    if not ask_yes_no("Close all running ikabot instances via PowerShell?", "Close All (PS)"):
        return

    res = subprocess.run(
        ["powershell", "-WindowStyle", "Hidden", "-Command",
         "$p = Get-Process -Name ikabot -ErrorAction SilentlyContinue; "
         "if ($p) { $p | Stop-Process -Force; exit 0 } else { exit 1 }"],
        capture_output=True, text=True,
    )
    if res.returncode == 0:
        show_info("All ikabot instances have been closed.", "Close All (PS) — Done")
    else:
        show_info("No ikabot instances were running.", "Close All (PS)")


def find_ikabot_asset(releases: list[dict]) -> tuple[str, str, str] | None:
    """Return (download_url, ikabot_ver, mod_ver) by matching the asset name directly."""
    for release in releases:
        for asset in release.get("assets", []):
            m = ASSET_IKABOT_RE.match(asset["name"])
            if m:
                return asset["browser_download_url"], m.group(1), m.group(2)
    return None


def maint_show_status(install_dir: Path) -> None:
    template_dir  = install_dir / "ikabot template"
    ikabot_dir    = install_dir / "ikabot"
    modules_dir   = install_dir / "modules"
    installer_dir = install_dir / "ikabot installer"

    mod_ver = read_version(template_dir) or "not installed"

    inst_count = 0
    if ikabot_dir.exists():
        inst_count = sum(
            1 for f in ikabot_dir.iterdir()
            if f.is_dir() and re.match(r"^ikariam \d+$", f.name)
        )

    module_count = 0
    modules_updated = "never"
    if modules_dir.exists():
        module_count = sum(
            1 for f in modules_dir.iterdir()
            if f.is_file() and f.name != "modules_updated.txt"
        )
        ts_file = modules_dir / "modules_updated.txt"
        if ts_file.exists():
            modules_updated = ts_file.read_text().strip()

    installer_ver = read_version(installer_dir) or "not installed"

    show_info(
        "ikabot Status\n\n"
        f"  Install folder     : {install_dir}\n"
        f"  ikabot mod version : {mod_ver}\n"
        f"  Instances          : {inst_count}\n"
        f"  Module files       : {module_count}  (last updated: {modules_updated})\n"
        f"  Installer (stored) : v{installer_ver}\n"
        f"  Installer (running): v{INSTALLER_VERSION}",
        "ikabot Status",
    )


# ── Maintenance: Download submenu ─────────────────────────────────────────────

def maint_download_ikabot(install_dir: Path, releases: list[dict]) -> None:
    template_dir = install_dir / "ikabot template"
    template_dir.mkdir(exist_ok=True)

    ikabot_asset = find_ikabot_asset(releases)
    if not ikabot_asset:
        show_error(
            "No ikabot release found on GitHub.\n\n"
            "Expected an asset matching: ikabot-v{x.x.x}-mod-v{x.x.x}.zip\n\n"
            "Check that the release has been published and try again."
        )
        return

    url, ikabot_ver, mod_ver = ikabot_asset
    local_mod_ver = read_version(template_dir) or "not installed"

    if not ask_yes_no(
        f"Installed mod version  : {local_mod_ver}\n"
        f"Latest ikabot version  : {ikabot_ver}\n"
        f"Latest mod version     : {mod_ver}\n\n"
        "This will replace the ikabot template folder with the latest version.\n\n"
        "Your instances will not be changed yet — use\n"
        "'Update → Update ikabot instances' afterwards to apply it.\n\n"
        "Proceed?",
        "Download Latest ikabot",
    ):
        return

    print(f"Downloading ikabot v{ikabot_ver} mod v{mod_ver} ...")
    for item in template_dir.iterdir():
        if item.name.startswith("version"):
            continue
        shutil.rmtree(item) if item.is_dir() else item.unlink()

    try:
        download_zip(url, template_dir, f"ikabot v{ikabot_ver} mod v{mod_ver}")
        write_version(template_dir, mod_ver)
        show_info(
            f"Download complete!\n\n"
            f"  ikabot version : {ikabot_ver}\n"
            f"  mod version    : {mod_ver}\n\n"
            "Use 'Update → Update ikabot instances' to apply this to all your instances.",
            "Download Complete",
        )
    except Exception as exc:
        show_error(f"Failed to download ikabot:\n\n{exc}")
        return

    if ask_yes_no(
        "Apply this update to all your ikabot instances now?\n\n"
        "This will wipe and re-populate all instance folders from the\n"
        "newly downloaded template.\n\n"
        "Close all running instances before continuing.\n\n"
        "Apply now?",
        "Apply Update Now?",
    ):
        maint_update_ikabot_instances(install_dir)


def maint_download_modules(install_dir: Path) -> None:
    modules_dir = install_dir / "modules"

    if not ask_yes_no(
        "This will download the latest modules and config examples\n"
        "directly from the GitHub repository into:\n"
        f"  {modules_dir}\n\n"
        "Files downloaded:\n"
        "  - All files from the modules folder\n"
        "  - All files from the config-examples folder\n\n"
        "Existing files will be replaced and version numbers\n"
        "will be removed from filenames so they load correctly in ikabot.\n\n"
        "Proceed?",
        "Download Latest Modules",
    ):
        return

    skip_names = ask_csv_overwrite(modules_dir)

    try:
        modules_dir.mkdir(exist_ok=True)
        print("Downloading modules ...")
        count = download_repo_files("modules", modules_dir, skip_names)
        print("Downloading config examples ...")
        count += download_repo_files("config-examples", modules_dir, skip_names)
        print("Removing version suffixes from filenames ...")
        strip_version_suffixes(modules_dir)
        write_modules_timestamp(modules_dir)
        show_info(
            f"Modules updated!\n\n"
            f"  Files downloaded : {count}\n"
            f"  Saved to         : {modules_dir}\n\n"
            "Version numbers have been removed from all filenames.\n"
            "The files are ready to use in ikabot.",
            "Download Complete",
        )
    except Exception as exc:
        show_error(f"Could not download modules:\n\n{exc}")


def maint_download_installer(install_dir: Path, releases: list[dict]) -> None:
    installer_dir = install_dir / "ikabot installer"
    installer_dir.mkdir(exist_ok=True)

    installer_asset = find_asset(releases, ASSET_INSTALLER_RE)
    if not installer_asset:
        show_info(
            "No installer release found on GitHub.\n\n"
            "The installer will appear here once a release is published.",
            "Download Latest Installer",
        )
        return

    url, _, remote_ver = installer_asset
    local_ver = read_version(installer_dir) or "not installed"

    if not ask_yes_no(
        f"Installed version  : {local_ver}\n"
        f"Latest version     : {remote_ver}\n\n"
        f"The installer will be downloaded to:\n  {installer_dir}\n\n"
        "Use 'Update → Update installer shortcut' afterwards\n"
        "to add a shortcut so you can launch it easily.\n\n"
        "Proceed?",
        "Download Latest Installer",
    ):
        return

    try:
        for item in installer_dir.iterdir():
            if item.name.startswith("version"):
                continue
            shutil.rmtree(item) if item.is_dir() else item.unlink()
        download_zip(url, installer_dir, f"installer v{remote_ver}")
        write_version(installer_dir, remote_ver)
        show_info(
            f"Installer v{remote_ver} downloaded to:\n  {installer_dir}\n\n"
            "Use 'Update → Update installer shortcut' to add it to your shortcuts folder.",
            "Download Complete",
        )
    except Exception as exc:
        show_error(f"Failed to download installer:\n\n{exc}")
        return

    if ask_yes_no(
        "Create a shortcut to the new installer now?\n\n"
        "This will add ikabot-mod-install.lnk to your shortcuts folder\n"
        "so you can launch the installer easily.\n\n"
        "Create shortcut now?",
        "Create Installer Shortcut?",
    ):
        maint_update_installer_shortcut(install_dir)


def maint_download_menu(install_dir: Path) -> None:
    show_info(
        "Connecting to GitHub to check for the latest versions.\n\n"
        "This may take a moment...",
        "Download Latest Versions",
    )
    try:
        releases = fetch_releases()
    except Exception as exc:
        show_error(
            f"Could not contact GitHub:\n\n{exc}\n\n"
            "Check your internet connection and try again."
        )
        return

    while True:
        choice = ask_choice(
            "Download Latest Versions",
            "Download Latest Versions\n\nWhat would you like to download?",
            ["ikabot", "Modules", "Installer", "Back"],
        )
        if choice is None or choice == "Back":
            return
        elif choice == "ikabot":
            maint_download_ikabot(install_dir, releases)
        elif choice == "Modules":
            maint_download_modules(install_dir)
        elif choice == "Installer":
            maint_download_installer(install_dir, releases)


# ── Maintenance: Update submenu ───────────────────────────────────────────────

def maint_update_ikabot_instances(install_dir: Path) -> None:
    template_dir = install_dir / "ikabot template"
    ikabot_dir   = install_dir / "ikabot"

    if not template_dir.exists() or not ikabot_dir.exists():
        show_error(
            f"Required folders not found inside:\n  {install_dir}\n\n"
            "Expected:\n  - ikabot template\n  - ikabot\n\n"
            "Make sure you selected the correct install folder."
        )
        return

    mod_ver      = read_version(template_dir) or "unknown"
    folder_count = sum(1 for f in ikabot_dir.iterdir()
                       if f.is_dir() and re.match(r"^ikariam \d+$", f.name))

    if folder_count == 0:
        show_info(
            "No ikariam instance folders were found.\n\n"
            "Run the installer first to set up your instances.",
            "Nothing to Update",
        )
        return

    if not ask_yes_no(
        f"Template mod version   : {mod_ver}\n"
        f"Instance folders       : {folder_count}\n\n"
        "This will wipe and re-populate all instance folders using\n"
        "the files currently in the ikabot template folder.\n\n"
        "This does NOT download anything — use\n"
        "'Download Latest Versions → ikabot' first if you want the latest version.\n\n"
        "Close all running instances before continuing.\n\n"
        "Proceed?",
        "Update ikabot Instances",
    ):
        return

    print(f"Updating {folder_count} instance folder(s) from template ...")
    sync_ikariam_folders(ikabot_dir, folder_count, template_dir)

    show_info(
        f"Update complete!\n\n"
        f"  mod version      : {mod_ver}\n"
        f"  Instances updated: {folder_count}\n\n"
        "All instances are now running the version in the template folder.",
        "Update Complete",
    )


def maint_update_installer_shortcut(install_dir: Path) -> None:
    installer_dir = install_dir / "ikabot installer"
    shortcuts_dir = install_dir / "shortcuts"

    if not installer_dir.exists():
        show_error(
            "No installer found.\n\n"
            "Use 'Download Latest Versions → Installer' first to download it."
        )
        return

    subdir = installer_dir / "ikabot-mod-install"
    exe = next(
        (p for p in (
            subdir / "ikabot-mod-install.exe",
            installer_dir / "ikabot-mod-install.exe",
        ) if p.exists()),
        None,
    )

    if not exe:
        show_error(
            "Could not find ikabot-mod-install.exe in:\n"
            f"  {installer_dir}\n\n"
            "Try downloading the installer again using\n"
            "'Download Latest Versions → Installer'."
        )
        return

    ver = read_version(installer_dir) or "?"
    shortcuts_dir.mkdir(exist_ok=True)

    try:
        lnk = shortcuts_dir / "ikabot-mod-install.lnk"
        create_shortcut(exe, lnk)
        show_info(
            f"Installer shortcut created!\n\n"
            f"  Version  : {ver}\n"
            f"  Shortcut : {lnk}\n\n"
            "The shortcut has been added to your shortcuts folder.\n"
            "Copy it to your Desktop if you need easy access to the installer.",
            "Shortcut Updated",
        )
    except Exception as exc:
        show_error(f"Could not create installer shortcut:\n\n{exc}")


def maint_update_menu(install_dir: Path) -> None:
    while True:
        choice = ask_choice(
            "Update",
            "Update\n\nWhat would you like to update?\n\n"
            "Note: these options apply files already downloaded\n"
            "to your install folder. Use 'Download Latest Versions'\n"
            "first to get the newest files from GitHub.",
            ["Update ikabot instances", "Update installer shortcut", "Back"],
        )
        if choice is None or choice == "Back":
            return
        elif choice == "Update ikabot instances":
            maint_update_ikabot_instances(install_dir)
        elif choice == "Update installer shortcut":
            maint_update_installer_shortcut(install_dir)


def _maintenance_menu() -> str | None:
    """Two-column maintenance menu: Standard (cmd) | PowerShell."""
    result: list[str | None] = [None]

    win = tk.Tk()
    win.withdraw()

    dlg = tk.Toplevel(win)
    dlg.title("ikabot Manager")
    dlg.resizable(False, False)
    dlg.attributes("-topmost", True)

    tk.Label(dlg, text="ikabot Manager\n\nSelect an action:",
             justify="left", padx=24, pady=12, wraplength=420).pack(fill="x")

    frm = tk.Frame(dlg, padx=24, pady=4)
    frm.pack()

    # Column headers
    tk.Label(frm, text="Standard", font=("", 9, "bold"), width=22).grid(
        row=0, column=0, padx=4, pady=(0, 2))
    tk.Label(frm, text="PowerShell", font=("", 9, "bold"), width=22).grid(
        row=0, column=1, padx=4, pady=(0, 2))

    # Two-column open / close rows
    pairs = [
        ("Open all instances",  "Open all (PowerShell)"),
        ("Close all instances", "Close all (PowerShell)"),
    ]
    for r, (left, right) in enumerate(pairs, start=1):
        for c, label in enumerate((left, right)):
            def _h(o=label):
                result[0] = o
                win.destroy()
            tk.Button(frm, text=label, width=22, pady=4, command=_h).grid(
                row=r, column=c, padx=4, pady=3)

    # Separator
    tk.Frame(frm, height=2, bg="#cccccc").grid(
        row=len(pairs) + 1, column=0, columnspan=2, sticky="ew", pady=8)

    # Full-width single buttons
    for i, label in enumerate(("Download latest versions", "Update", "Status", "Exit")):
        def _h(o=label):
            result[0] = o
            win.destroy()
        tk.Button(frm, text=label, width=47, pady=4, command=_h).grid(
            row=len(pairs) + 2 + i, column=0, columnspan=2, padx=4, pady=3)

    dlg.protocol("WM_DELETE_WINDOW", win.destroy)
    dlg.lift()
    dlg.focus_force()
    win.mainloop()
    return result[0]


def maintenance_mode(install_dir: Path) -> None:
    while True:
        choice = _maintenance_menu()
        if choice is None or choice == "Exit":
            return
        elif choice == "Open all instances":
            maint_open_all(install_dir)
        elif choice == "Open all (PowerShell)":
            maint_open_all_ps(install_dir)
        elif choice == "Close all instances":
            maint_close_all()
        elif choice == "Close all (PowerShell)":
            maint_close_all_ps()
        elif choice == "Download latest versions":
            maint_download_menu(install_dir)
        elif choice == "Update":
            maint_update_menu(install_dir)
        elif choice == "Status":
            maint_show_status(install_dir)

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
            + "\n\nThis is usually because ikabot is still running.\n"
            "Please close all ikabot instances and try again."
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
        default_dir = saved_install or last_install

        if default_dir:
            choice = ask_choice(
                "ikabot Manager — Install Folder",
                f"Use the last install folder?\n\n"
                f"  {default_dir}\n\n"
                "Click 'Use this folder' to continue, or 'Browse...' to select a different one.",
                ["Use this folder", "Browse..."],
            )
            if choice is None:
                return
            if choice == "Browse...":
                picked = pick_folder(
                    "Select your ikabot install folder",
                    initial=str(default_dir),
                )
                if not picked:
                    return
                install_dir = picked
            else:
                install_dir = default_dir
        else:
            show_info(
                "Select your ikabot install folder.\n\n"
                "This is the folder that contains the 'ikabot' and\n"
                "'ikabot template' subfolders.",
                "ikabot Manager — Select Install Folder",
            )
            picked = pick_folder(
                "Select your ikabot install folder",
                initial=str(DEFAULT_INSTALL),
            )
            if not picked:
                return
            install_dir = picked

        save_config({"install_dir": str(install_dir)})
        maintenance_mode(install_dir)
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
        "  2. Optionally downloads extra automation modules\n"
        "  3. Creates a separate folder for each instance\n"
        "  4. Creates shortcuts so you can launch them easily\n\n"
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
    installer_dir = install_dir / "ikabot installer"

    for d in (shortcuts_dir, modules_dir, template_dir, ikabot_dir, installer_dir):
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
        local_ver = read_version(installer_dir) or INSTALLER_VERSION
        if is_newer(remote_ver, local_ver):
            print(f"Downloading installer update v{remote_ver} ...")
            try:
                download_zip(url, installer_dir, f"ikabot-mod-install v{remote_ver}")
                write_version(installer_dir, remote_ver)
            except Exception as exc:
                print(f"Warning: could not download installer update: {exc}")
                # Non-fatal — continue with the current version

        update_ver = read_version(installer_dir)
        if update_ver and is_newer(update_ver, INSTALLER_VERSION):
            # The release zip may be a onedir bundle (ikabot-mod-install/ikabot-mod-install.exe)
            # or a flat archive (ikabot-mod-install.exe). Check both.
            subdir = installer_dir / "ikabot-mod-install"
            new_exe = next(
                (p for p in (
                    subdir / "ikabot-mod-install.exe",
                    installer_dir / "ikabot-mod-install.exe",
                ) if p.exists()),
                None,
            )
            new_py = next(
                (p for p in (
                    subdir / "ikabot-mod-install.py",
                    installer_dir / "ikabot-mod-install.py",
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
    ikabot_asset = find_ikabot_asset(releases)
    if not ikabot_asset:
        show_error(
            "The ikabot program files could not be found on GitHub.\n\n"
            "Expected an asset matching:  ikabot-v{x.x.x}-mod-v{x.x.x}.zip\n\n"
            "Please check that the release has been published and try again."
        )
        return

    url, remote_ikabot_ver, remote_mod_ver = ikabot_asset

    local_ikabot_ver = read_version(template_dir)
    if local_ikabot_ver is None or is_newer(remote_mod_ver, local_ikabot_ver):
        print(f"Downloading ikabot v{remote_ikabot_ver} mod v{remote_mod_ver} ...")
        for item in template_dir.iterdir():
            if item.name.startswith("version"):
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        try:
            download_zip(url, template_dir, f"ikabot v{remote_ikabot_ver} mod v{remote_mod_ver}")
            write_version(template_dir, remote_mod_ver)
            print(f"ikabot v{remote_ikabot_ver} mod v{remote_mod_ver} downloaded.")
        except Exception as exc:
            show_error(f"Failed to download ikabot:\n\n{exc}")
            return
    else:
        print(f"ikabot already up to date (mod v{local_ikabot_ver}).")

    mod_ver    = read_version(template_dir) or remote_mod_ver
    ikabot_ver = remote_ikabot_ver

    # ── 5b. Download ikabot modules (optional) ────────────────────────────────
    if ask_yes_no(
        "Step 2 of 4 — ikabot Modules\n\n"
        "Additional automation modules are available for ikabot.\n"
        "These extend what ikabot can do (recruitment, construction,\n"
        "resource transport, and more).\n\n"
        "Modules are downloaded directly from the GitHub repository\n"
        "and placed in your modules folder.\n\n"
        "Would you like to download and install them now?",
        "Step 2 of 4 — ikabot Modules",
    ):
        skip_names = ask_csv_overwrite(modules_dir)
        try:
            print("Downloading modules from repository ...")
            count = download_repo_files("modules", modules_dir, skip_names)
            print("Downloading config examples ...")
            count += download_repo_files("config-examples", modules_dir, skip_names)
            print("Removing version suffixes from filenames ...")
            strip_version_suffixes(modules_dir)
            write_modules_timestamp(modules_dir)
            print(f"Modules installed ({count} files).")
        except Exception as exc:
            show_error(
                f"Could not download modules:\n\n{exc}\n\n"
                "You can download them later from Maintenance → Download latest versions → Modules."
            )
    else:
        print("Modules skipped.")

    # ── 6. Instance count ─────────────────────────────────────────────────────
    existing = sum(
        1 for f in ikabot_dir.iterdir()
        if f.is_dir() and re.match(r"^ikariam \d+$", f.name)
    ) if ikabot_dir.exists() else 0

    existing_note = (
        f"\n\nYou already have {existing} instance folder(s) set up.\n"
        "Enter a higher number to add more, or the same number to refresh.\n"
        "Click Skip to leave your instances untouched."
        if existing > 0 else ""
    )

    count = existing  # default: keep existing count if user skips
    while True:
        raw = ask_count_or_skip(
            "Step 3 of 4 — Instance Count",
            "Step 3 of 4 — How many instances?\n\n"
            "Each instance is a separate copy of ikabot that can log into\n"
            "a different Ikariam account and run at the same time.\n\n"
            "Enter the total number of instances you want (max 100).\n"
            "Click Skip to keep your existing setup unchanged.\n"
            "Re-run the installer any time to add more without losing your setup."
            + existing_note,
            initial=str(existing) if existing > 0 else "",
        )
        if raw is None:
            print("Cancelled.")
            return
        if raw == "SKIP" or raw.strip() == "" or raw.strip() == "0":
            print(f"Instance count skipped — keeping {existing} existing folder(s).")
            count = existing
            break
        if not raw.strip().isdigit():
            show_error("Please enter a whole number, or click Skip.")
            continue
        count = int(raw.strip())
        if count < 1 or count > 100:
            show_error("Please enter a number between 1 and 100, or click Skip.")
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

    # Installer shortcut — points to the currently-running exe when frozen
    if getattr(sys, "frozen", False):
        try:
            create_shortcut(Path(sys.executable), shortcuts_dir / "ikabot-mod-install.lnk")
            print("  ikabot-mod-install.lnk")
        except Exception as exc:
            print(f"  Warning: could not create installer shortcut: {exc}")

    # Copy open-all-instances.ps1 into shortcuts folder if bundled
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    ps1_src = bundle_root / "open-all-instances.ps1"
    if ps1_src.exists():
        try:
            shutil.copy2(ps1_src, shortcuts_dir / "open-all-instances.ps1")
            print("  open-all-instances.ps1")
        except Exception as exc:
            print(f"  Warning: could not copy open-all-instances.ps1: {exc}")

    # ── 9. User shortcut destination ──────────────────────────────────────────
    show_info(
        "Step 4 of 4 — Shortcut Location\n\n"
        "Choose where to put your ikabot shortcuts.\n\n"
        "A folder browser will open — simply select Desktop\n"
        "(or any folder you prefer) and click OK.\n\n"
        "The installer will automatically create an\n"
        "'ikabot shortcuts' folder there for you.\n"
        "You do not need to create the folder yourself.\n\n"
        "Click Cancel to place the shortcuts on the Desktop by default.",
        "Step 4 of 4 — Shortcut Location",
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

    # ── Done ──────────────────────────────────────────────────────────────────
    save_config({"install_dir": str(install_dir)})
    show_info(
        "Installation complete!\n\n"
        f"  ikabot version   : {ikabot_ver}\n"
        f"  mod version      : {mod_ver}\n"
        f"  Installer version: {INSTALLER_VERSION}\n"
        f"  Instances set up : {count}\n"
        f"  Installed to     : {install_dir}\n"
        f"  Shortcuts saved  : {user_sc_dir}\n\n"
        "What to do next:\n"
        f"  1. Open '{user_sc_dir.name}' on your Desktop\n"
        "  2. Double-click a numbered shortcut to launch an instance\n"
        "  3. Log into your Ikariam account in the browser that opens\n"
        "  4. Use this installer's Maintenance mode to open, close,\n"
        "     or update all instances at once\n\n"
        "To add more instances later, simply re-run this installer\n"
        "and enter a higher number — existing instances are not affected.",
        "Installation Complete",
    )
    print("Installation complete.")


if __name__ == "__main__":
    main()
