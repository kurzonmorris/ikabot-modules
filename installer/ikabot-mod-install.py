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

INSTALLER_VERSION = "2.0.0"

GITHUB_API      = "https://api.github.com/repos/kurzonmorris/ikabot-modules/releases"
GITHUB_CONTENTS = "https://api.github.com/repos/kurzonmorris/ikabot-modules/contents"

# Regex patterns for release asset filenames — version numbers captured in groups.
# ikabot:    ikabot-v7.4.0--mod-v0.9.4.zip  (single or double dash before mod)
# installer: ikabot-mod-install_v1.3.0.zip
ASSET_IKABOT_RE    = re.compile(r'^ikabot-v([\d.]+)-+mod-v([\d.]+)\.zip$', re.IGNORECASE)
ASSET_INSTALLER_RE = re.compile(r'^ikabot-mod-install_v([\d.]+)\.zip$',    re.IGNORECASE)

DEFAULT_INSTALL   = Path.home() / "Desktop" / "ikabot"
DEFAULT_SHORTCUTS = Path.home() / "Desktop" / "ikabot shortcuts"

MODULES_TEMPLATE = "Ikabot Modules template"

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


def write_version(directory: Path, version: str, extra: dict | None = None) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    clean = version.lstrip("vV")
    data = {"version": clean}
    if extra:
        data.update(extra)
    (directory / "version.json").write_text(json.dumps(data, indent=2))
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


def read_version_key(directory: Path, key: str) -> str | None:
    """Read an extra key (e.g. ikabot_version) from a folder's version.json."""
    vj = directory / "version.json"
    if vj.exists():
        try:
            return json.loads(vj.read_text()).get(key)
        except Exception:
            pass
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


def split_module_name(name: str) -> tuple[str, str]:
    """'autoRecruitment_v1.2.py' -> ('autoRecruitment.py', '1.2').

    Files without a version suffix return (name, "").
    """
    p = Path(name)
    m = re.match(r"^(.*?)_v(.+)$", p.stem)
    if m:
        return m.group(1) + p.suffix, m.group(2)
    return name, ""


def fetch_module_listing() -> list[dict]:
    """List all module and config-example files available on GitHub.

    Returns [{name, url, base, ver}] sorted by base name.
    """
    items: list[dict] = []
    for folder in ("modules", "config-examples"):
        try:
            entries = fetch_repo_folder(folder)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                continue
            raise
        for it in entries:
            if it["type"] != "file":
                continue
            base, ver = split_module_name(it["name"])
            items.append({"name": it["name"], "url": it["download_url"],
                          "base": base, "ver": ver})
    items.sort(key=lambda d: d["base"].lower())
    return items


def installed_module_versions(template_dir: Path) -> dict[str, str]:
    """Map base filename -> installed version, read from the modules template folder."""
    versions: dict[str, str] = {}
    if template_dir.exists():
        for f in template_dir.iterdir():
            if f.is_file():
                base, ver = split_module_name(f.name)
                versions[base] = ver
    return versions


def download_module_file(item: dict, template_dir: Path, modules_dir: Path) -> None:
    """Download one module file to the template folder (versioned name kept),
    then copy it to the modules folder with the version suffix stripped.
    Old versions of the same file are removed from both folders first."""
    template_dir.mkdir(parents=True, exist_ok=True)
    modules_dir.mkdir(parents=True, exist_ok=True)

    req = urllib.request.Request(
        item["url"],
        headers={"User-Agent": f"ikabot-mod-install/{INSTALLER_VERSION}"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()

    # Remove any old versions of this module from the template folder
    for f in list(template_dir.iterdir()):
        if f.is_file() and split_module_name(f.name)[0] == item["base"]:
            f.unlink()
    (template_dir / item["name"]).write_bytes(data)

    # Remove stale versioned leftovers in the modules folder, then write clean copy
    for f in list(modules_dir.iterdir()):
        if f.is_file() and split_module_name(f.name)[0] == item["base"] and f.name != item["base"]:
            f.unlink()
    (modules_dir / item["base"]).write_bytes(data)


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


def _instance_lnks(folder: Path) -> list[Path]:
    """Numbered instance shortcuts only (e.g. '1 ikariam.lnk'), sorted by number.

    Ignores other shortcuts in the folder (installer, old manager tools, etc.)."""
    if not folder.exists():
        return []
    return sorted(
        [f for f in folder.glob("*.lnk") if f.name[0].isdigit()],
        key=lambda p: _leading_num(p.name),
    )


def maint_open_all(install_dir: Path) -> None:
    sc_dir = install_dir / "shortcuts"

    # Clean up the retired AHK manager shortcut if it is still around
    for stale in sc_dir.glob("ikabot_manager*.lnk") if sc_dir.exists() else []:
        try:
            stale.unlink()
            print(f"  Removed old shortcut: {stale.name}")
        except Exception:
            pass

    lnks = _instance_lnks(sc_dir)

    if not lnks:
        show_info(
            "Select the folder that contains your ikabot shortcut (.lnk) files.",
            "Open All — Select Folder",
        )
        picked = pick_folder("Select your ikabot shortcuts folder", initial=str(install_dir))
        if not picked:
            return
        lnks = _instance_lnks(picked)

    if not lnks:
        show_error("No numbered instance shortcuts (e.g. '1 ikariam.lnk')\n"
                   "were found in the selected folder.")
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
    """Open each instance inside its own PowerShell window (instead of the
    default cmd-style console host)."""
    ikabot_dir = install_dir / "ikabot"

    folders = sorted(
        [f for f in ikabot_dir.iterdir()
         if f.is_dir() and re.match(r"^ikariam \d+$", f.name)],
        key=lambda p: _leading_num(p.name),
    ) if ikabot_dir.exists() else []

    if not folders:
        show_error(
            "No ikariam instance folders were found in:\n"
            f"  {ikabot_dir}\n\n"
            "Run the installer first to set up your instances."
        )
        return

    launched = 0
    for folder in folders:
        exe = folder / "ikabot.exe"
        if not exe.exists():
            print(f"  Skipping {folder.name} — no ikabot.exe found")
            continue
        subprocess.Popen(
            ["powershell", "-NoExit", "-Command",
             f"$host.UI.RawUI.WindowTitle = '{folder.name}'; "
             f"Set-Location '{folder}'; & '.\\ikabot.exe'"],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        launched += 1
        time.sleep(0.15)

    show_info(
        f"{launched} ikabot instance(s) launched in PowerShell windows.",
        "Open All (PowerShell) — Done",
    )


def maint_close_all_ps() -> None:
    if not ask_yes_no("Close all running ikabot instances via PowerShell?", "Close All (PS)"):
        return

    # Kill ikabot.exe processes AND the PowerShell host windows they run in
    # (the -NoExit windows from 'Open all (PowerShell)' would otherwise stay open).
    ps_script = (
        "$closed = 0; "
        "$ik = Get-CimInstance Win32_Process -Filter \"Name='ikabot.exe'\" "
        "-ErrorAction SilentlyContinue; "
        "$parents = @(); "
        "foreach ($p in $ik) { "
        "  $par = Get-Process -Id $p.ParentProcessId -ErrorAction SilentlyContinue; "
        "  if ($par -and $par.Name -eq 'powershell') { $parents += $par } "
        "}; "
        "foreach ($p in $ik) { "
        "  Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue; $closed++ "
        "}; "
        "$parents | Stop-Process -Force -ErrorAction SilentlyContinue; "
        # Also close leftover PowerShell windows titled 'ikariam N' where
        # ikabot has already exited but the -NoExit window is still open.
        "Get-Process powershell -ErrorAction SilentlyContinue | "
        "Where-Object { $_.MainWindowTitle -match 'ikariam' -and $_.Id -ne $PID } | "
        "Stop-Process -Force -ErrorAction SilentlyContinue; "
        "exit ([int]($closed -eq 0))"
    )
    res = subprocess.run(
        ["powershell", "-WindowStyle", "Hidden", "-Command", ps_script],
        capture_output=True, text=True,
    )
    if res.returncode == 0:
        show_info("All ikabot instances and their PowerShell windows have been closed.",
                  "Close All (PS) — Done")
    else:
        show_info("No ikabot instances were running.\n"
                  "Any leftover 'ikariam' PowerShell windows were closed.",
                  "Close All (PS)")


def find_ikabot_asset(releases: list[dict]) -> tuple[str, str, str] | None:
    """Return (download_url, ikabot_ver, mod_ver) by matching the asset name directly."""
    for release in releases:
        for asset in release.get("assets", []):
            m = ASSET_IKABOT_RE.match(asset["name"])
            if m:
                return asset["browser_download_url"], m.group(1), m.group(2)
    return None


def maint_show_status(install_dir: Path) -> None:
    template_dir     = install_dir / "ikabot template"
    ikabot_dir       = install_dir / "ikabot"
    modules_dir      = install_dir / "modules"
    mod_template_dir = install_dir / MODULES_TEMPLATE

    mod_ver    = read_version(template_dir) or "not installed"
    ikabot_ver = read_version_key(template_dir, "ikabot_version") or "unknown"

    inst_count = 0
    if ikabot_dir.exists():
        inst_count = sum(
            1 for f in ikabot_dir.iterdir()
            if f.is_dir() and re.match(r"^ikariam \d+$", f.name)
        )

    py_count = 0
    modules_updated = "never"
    if modules_dir.exists():
        py_count = sum(
            1 for f in modules_dir.iterdir()
            if f.is_file() and f.suffix.lower() == ".py"
        )
        ts_file = modules_dir / "modules_updated.txt"
        if ts_file.exists():
            modules_updated = ts_file.read_text().strip()

    # Module list with versions, read from the modules template folder
    module_lines: list[str] = []
    installed = installed_module_versions(mod_template_dir)
    for base in sorted(installed, key=str.lower):
        ver = installed[base] or "no version"
        module_lines.append(f"    {base}  —  v{ver}" if installed[base]
                            else f"    {base}")
    modules_text = "\n".join(module_lines) if module_lines else \
        "    (none — use the Modules menu to download them)"

    show_info(
        "ikabot Status\n\n"
        f"  Install folder    : {install_dir}\n"
        f"  ikabot version    : {ikabot_ver}\n"
        f"  mod version       : {mod_ver}\n"
        f"  Installer version : v{INSTALLER_VERSION}\n"
        f"  Instances         : {inst_count}\n"
        f"  Modules installed : {py_count} .py file(s)  (last updated: {modules_updated})\n\n"
        "  Installed modules and extra files:\n"
        f"{modules_text}",
        "ikabot Status",
    )


# ── Maintenance: Update actions ───────────────────────────────────────────────

def _fetch_releases_or_error() -> list[dict] | None:
    try:
        return fetch_releases()
    except Exception as exc:
        show_error(
            f"Could not contact GitHub:\n\n{exc}\n\n"
            "Check your internet connection and try again."
        )
        return None


def maint_update_installer(install_dir: Path) -> None:
    installer_dir = install_dir / "ikabot installer"

    releases = _fetch_releases_or_error()
    if releases is None:
        return

    installer_asset = find_asset(releases, ASSET_INSTALLER_RE)
    if not installer_asset:
        show_info(
            "No installer release found on GitHub.\n\n"
            "The installer will appear here once a release is published.",
            "Update Installer",
        )
        return

    url, _, remote_ver = installer_asset

    if ver_tuple(remote_ver) == ver_tuple(INSTALLER_VERSION):
        if not ask_yes_no(
            f"The latest version on GitHub (v{remote_ver}) is the SAME\n"
            f"as the version you are running (v{INSTALLER_VERSION}).\n\n"
            "Are you sure you want to download it again?",
            "Same Version — Are You Sure?",
        ):
            return
    else:
        if not ask_yes_no(
            f"Running version : v{INSTALLER_VERSION}\n"
            f"Latest version  : v{remote_ver}\n\n"
            f"The installer will be downloaded to:\n  {installer_dir}\n\n"
            "Proceed?",
            "Update Installer",
        ):
            return

    try:
        installer_dir.mkdir(exist_ok=True)
        for item in installer_dir.iterdir():
            if item.name.startswith("version"):
                continue
            shutil.rmtree(item) if item.is_dir() else item.unlink()
        download_zip(url, installer_dir, f"installer v{remote_ver}")
        write_version(installer_dir, remote_ver)
    except Exception as exc:
        show_error(f"Failed to download installer:\n\n{exc}")
        return

    if ask_yes_no(
        f"Installer v{remote_ver} downloaded to:\n  {installer_dir}\n\n"
        "Create a shortcut to it in your shortcuts folder now?",
        "Create Installer Shortcut?",
    ):
        maint_update_installer_shortcut(install_dir)


def maint_update_ikabot(install_dir: Path) -> None:
    template_dir = install_dir / "ikabot template"
    ikabot_dir   = install_dir / "ikabot"

    releases = _fetch_releases_or_error()
    if releases is None:
        return

    ikabot_asset = find_ikabot_asset(releases)
    if not ikabot_asset:
        show_error(
            "No ikabot release found on GitHub.\n\n"
            "Expected an asset matching: ikabot-v{x.x.x}-mod-v{x.x.x}.zip\n\n"
            "Check that the release has been published and try again."
        )
        return

    url, ikabot_ver, mod_ver = ikabot_asset
    local_mod_ver = read_version(template_dir)

    if local_mod_ver and ver_tuple(mod_ver) == ver_tuple(local_mod_ver):
        if not ask_yes_no(
            f"The latest version on GitHub (mod v{mod_ver}) is the SAME\n"
            f"as the version currently installed (mod v{local_mod_ver}).\n\n"
            "Are you sure you want to download and install it again?",
            "Same Version — Are You Sure?",
        ):
            return
    else:
        if not ask_yes_no(
            f"Installed mod version : {local_mod_ver or 'not installed'}\n"
            f"Latest ikabot version : {ikabot_ver}\n"
            f"Latest mod version    : {mod_ver}\n\n"
            "This will download the latest ikabot and update your instances.\n\n"
            "Close all running instances before continuing.\n\n"
            "Proceed?",
            "Update Ikabot",
        ):
            return

    # Download into the template folder
    template_dir.mkdir(exist_ok=True)
    print(f"Downloading ikabot v{ikabot_ver} mod v{mod_ver} ...")
    for item in template_dir.iterdir():
        if item.name.startswith("version"):
            continue
        shutil.rmtree(item) if item.is_dir() else item.unlink()

    try:
        download_zip(url, template_dir, f"ikabot v{ikabot_ver} mod v{mod_ver}")
        write_version(template_dir, mod_ver, extra={"ikabot_version": ikabot_ver})
    except Exception as exc:
        show_error(f"Failed to download ikabot:\n\n{exc}")
        return

    # Instance count — same question as the installer
    ikabot_dir.mkdir(exist_ok=True)
    existing = sum(
        1 for f in ikabot_dir.iterdir()
        if f.is_dir() and re.match(r"^ikariam \d+$", f.name)
    )

    count = existing
    while True:
        raw = ask_count_or_skip(
            "Update Ikabot — Instance Count",
            "How many instances?\n\n"
            f"You currently have {existing} instance folder(s).\n"
            "Enter the total number of instances you want (max 100),\n"
            "or click Skip to keep the current number.",
            initial=str(existing) if existing > 0 else "",
        )
        if raw is None:
            print("Cancelled — ikabot downloaded but instances not updated.")
            return
        if raw == "SKIP" or raw.strip() == "" or raw.strip() == "0":
            count = existing
            break
        if not raw.strip().isdigit():
            show_error("Please enter a whole number, or click Skip.")
            continue
        count = int(raw.strip())
        if count < 1 or count > 100:
            show_error("Please enter a number between 1 and 100, or click Skip.")
            continue
        break

    if count == 0:
        show_info(
            f"ikabot v{ikabot_ver} mod v{mod_ver} downloaded.\n\n"
            "No instance folders exist yet — run the installer to create them.",
            "Update Ikabot — Done",
        )
        return

    print(f"Updating {count} instance folder(s) ...")
    sync_ikariam_folders(ikabot_dir, count, template_dir)

    show_info(
        f"Update complete!\n\n"
        f"  ikabot version   : {ikabot_ver}\n"
        f"  mod version      : {mod_ver}\n"
        f"  Instances updated: {count}",
        "Update Ikabot — Done",
    )


# ── Maintenance: Modules menu ─────────────────────────────────────────────────

def _modules_dialog(listing: list[dict], install_dir: Path):
    """Show module list with per-file Update buttons.

    Returns a listing item (update one), "ALL" (update all), or None (close).
    """
    mod_template_dir = install_dir / MODULES_TEMPLATE
    installed = installed_module_versions(mod_template_dir)

    result: list = [None]

    win = tk.Tk()
    win.withdraw()

    dlg = tk.Toplevel(win)
    dlg.title("ikabot Modules")
    dlg.resizable(False, False)
    dlg.attributes("-topmost", True)

    tk.Label(dlg, text="ikabot Modules\n\nModules and extra files — installed vs GitHub:",
             justify="left", padx=24, pady=10, wraplength=520).pack(fill="x")

    # Scrollable list
    outer = tk.Frame(dlg, padx=24)
    outer.pack(fill="both", expand=True)

    canvas = tk.Canvas(outer, width=560, height=min(360, 34 * max(len(listing), 1) + 10),
                       highlightthickness=0)
    scroll = tk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    rows = tk.Frame(canvas)

    rows.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=rows, anchor="nw")
    canvas.configure(yscrollcommand=scroll.set)

    canvas.pack(side="left", fill="both", expand=True)
    scroll.pack(side="right", fill="y")

    def _wheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    dlg.bind("<MouseWheel>", _wheel)

    for r, item in enumerate(listing):
        inst_ver = installed.get(item["base"], "")
        inst_txt = f"v{inst_ver}" if inst_ver else ("installed" if
                    (install_dir / "modules" / item["base"]).exists() else "not installed")
        gh_txt   = f"v{item['ver']}" if item["ver"] else "no version"

        tk.Label(rows, text=item["base"], anchor="w", width=32).grid(
            row=r, column=0, sticky="w", pady=2)
        tk.Label(rows, text=inst_txt, anchor="w", width=12, fg="#555555").grid(
            row=r, column=1, sticky="w", pady=2)
        tk.Label(rows, text=f"GitHub: {gh_txt}", anchor="w", width=16, fg="#006600").grid(
            row=r, column=2, sticky="w", pady=2)

        def _upd(it=item):
            result[0] = it
            win.destroy()
        tk.Button(rows, text="Update", width=8, command=_upd).grid(
            row=r, column=3, padx=(4, 0), pady=2)

    btns = tk.Frame(dlg, padx=24, pady=10)
    btns.pack()

    def _all():
        result[0] = "ALL"
        win.destroy()

    tk.Button(btns, text="Update all files", width=25, pady=4, command=_all).grid(
        row=0, column=0, padx=4)
    tk.Button(btns, text="Close", width=25, pady=4, command=win.destroy).grid(
        row=0, column=1, padx=4)

    dlg.protocol("WM_DELETE_WINDOW", win.destroy)
    dlg.lift()
    dlg.focus_force()
    win.mainloop()
    return result[0]


def maint_modules_menu(install_dir: Path) -> None:
    modules_dir      = install_dir / "modules"
    mod_template_dir = install_dir / MODULES_TEMPLATE

    show_info(
        "Connecting to GitHub to list the available modules.\n\n"
        "This may take a moment...",
        "ikabot Modules",
    )
    try:
        listing = fetch_module_listing()
    except Exception as exc:
        show_error(
            f"Could not fetch the module list from GitHub:\n\n{exc}\n\n"
            "Check your internet connection and try again."
        )
        return

    if not listing:
        show_info("No module files were found on GitHub.", "ikabot Modules")
        return

    while True:
        action = _modules_dialog(listing, install_dir)
        if action is None:
            return

        if action == "ALL":
            include_csv = ask_yes_no(
                "Do you want to download the .csv files too?\n\n"
                "WARNING: downloading bulkdistribution.csv will OVERWRITE\n"
                "your existing file and any settings saved in it will be LOST.\n\n"
                "  Yes = download everything including .csv files\n"
                "  No  = download everything EXCEPT .csv files",
                "Include CSV Files?",
            )
            count = 0
            errors: list[str] = []
            for item in listing:
                if not include_csv and item["base"].lower().endswith(".csv"):
                    print(f"  {item['base']}  (skipped — csv)")
                    continue
                try:
                    print(f"  {item['name']}")
                    download_module_file(item, mod_template_dir, modules_dir)
                    count += 1
                except Exception as exc:
                    errors.append(f"  {item['base']}: {exc}")
            write_modules_timestamp(modules_dir)
            msg = f"Update complete!\n\n  Files updated : {count}\n  Saved to      : {modules_dir}"
            if errors:
                msg += "\n\nSome files failed:\n" + "\n".join(errors)
            show_info(msg, "Modules Updated")
        else:
            try:
                print(f"  {action['name']}")
                download_module_file(action, mod_template_dir, modules_dir)
                write_modules_timestamp(modules_dir)
                ver_txt = f" v{action['ver']}" if action["ver"] else ""
                show_info(
                    f"{action['base']}{ver_txt} has been updated.",
                    "Module Updated",
                )
            except Exception as exc:
                show_error(f"Could not update {action['base']}:\n\n{exc}")


# ── Maintenance: installer shortcut helper ────────────────────────────────────

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
    for i, label in enumerate(("Update Installer", "Update Ikabot",
                               "Modules", "Status", "Exit")):
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
        elif choice == "Update Installer":
            maint_update_installer(install_dir)
        elif choice == "Update Ikabot":
            maint_update_ikabot(install_dir)
        elif choice == "Modules":
            maint_modules_menu(install_dir)
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
        "Edit the path below or leave it as the default (your Desktop).\n\n"
        "The folder will be created automatically if it does not exist.\n"
        "If you choose Program Files you may need to run as Administrator.",
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
    shortcuts_dir    = install_dir / "shortcuts"
    modules_dir      = install_dir / "modules"
    template_dir     = install_dir / "ikabot template"
    ikabot_dir       = install_dir / "ikabot"
    installer_dir    = install_dir / "ikabot installer"
    mod_template_dir = install_dir / MODULES_TEMPLATE

    for d in (shortcuts_dir, modules_dir, template_dir, ikabot_dir,
              installer_dir, mod_template_dir):
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
            write_version(template_dir, remote_mod_ver,
                          extra={"ikabot_version": remote_ikabot_ver})
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
        skip_names = {n.lower() for n in ask_csv_overwrite(modules_dir)}
        try:
            print("Downloading modules from repository ...")
            count = 0
            for item in fetch_module_listing():
                if item["base"].lower() in skip_names:
                    print(f"  {item['base']}  (skipped — keeping your existing file)")
                    continue
                print(f"  {item['name']}")
                download_module_file(item, mod_template_dir, modules_dir)
                count += 1
            write_modules_timestamp(modules_dir)
            print(f"Modules installed ({count} files).")
        except Exception as exc:
            show_error(
                f"Could not download modules:\n\n{exc}\n\n"
                "You can download them later from Maintenance → Modules."
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
