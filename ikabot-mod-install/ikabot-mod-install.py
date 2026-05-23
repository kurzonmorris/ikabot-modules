#!/usr/bin/env python3
"""ikabot-mod-install — Interactive installer for ikabot multi-instance setup.

Flow:
  1. Ask install location  (default C:/Program Files/ikabot/)
  2. Build folder structure
  3. Download latest ikabot-mod-install.zip → update/  (if newer on GitHub)
  4. Self-update check: if update/ version > this version, launch new exe and exit
  5. Download latest ikabot.zip            → ikabot template/  (if newer)
  6. Ask instance count (warn >20, hard cap 100)
  7. Sync ikariam folders, populate from template
  8. Create numbered shortcuts internally
  9. Ask where user wants shortcuts, copy there
 10. Download latest open close update.zip → update/tools/  (if newer)
     Create shortcuts to tools in both shortcut locations
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
import urllib.request
import urllib.error

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

# ── Constants ─────────────────────────────────────────────────────────────────

INSTALLER_VERSION = "1.1.0"

GITHUB_API = "https://api.github.com/repos/kurzonmorris/ikabot-modules/releases"

ASSET_INSTALLER = "ikabot-mod-install.zip"
ASSET_IKABOT    = "ikabot.zip"
ASSET_TOOLS     = "open close update.zip"

DEFAULT_INSTALL  = Path("C:/Program Files/ikabot")
DEFAULT_SHORTCUTS = Path.home() / "Desktop" / "ikabot shortcuts"

# Temp file used to hand install location to a newly launched updated installer
STATE_FILE = Path(tempfile.gettempdir()) / "ikabot_install_state.json"

# ── PyInstaller helpers ───────────────────────────────────────────────────────

def exe_dir() -> Path:
    """Directory containing the running exe or script."""
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent

# ── Tkinter helpers ───────────────────────────────────────────────────────────

def _root() -> tk.Tk:
    r = tk.Tk()
    r.withdraw()
    r.attributes("-topmost", True)
    return r


def ask_string(prompt: str, title: str = "ikabot-mod-install", initial: str = "") -> str | None:
    r = _root()
    val = simpledialog.askstring(title, prompt, parent=r, initialvalue=initial)
    r.destroy()
    return val.strip() if val and val.strip() else None


def ask_integer(prompt: str, title: str = "ikabot-mod-install",
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


def show_info(msg: str, title: str = "ikabot-mod-install") -> None:
    r = _root()
    messagebox.showinfo(title, msg, parent=r)
    r.destroy()


def show_error(msg: str, title: str = "Error") -> None:
    r = _root()
    messagebox.showerror(title, msg, parent=r)
    r.destroy()


def ask_yes_no(msg: str, title: str = "ikabot-mod-install") -> bool:
    r = _root()
    result = messagebox.askyesno(title, msg, parent=r)
    r.destroy()
    return result


def ask_ok_cancel(msg: str, title: str = "ikabot-mod-install") -> bool:
    r = _root()
    result = messagebox.askokcancel(title, msg, parent=r)
    r.destroy()
    return result

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


def find_asset(releases: list[dict], asset_name: str) -> tuple[str, str] | None:
    """Return (download_url, tag_name) for the first release containing asset_name."""
    name_lower = asset_name.lower()
    for release in releases:
        for asset in release.get("assets", []):
            if asset["name"].lower() == name_lower:
                return asset["browser_download_url"], release["tag_name"]
    return None


def download_zip(url: str, dest_dir: Path, label: str = "") -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest_dir / "_tmp_download.zip"
    print(f"  Downloading {label or url} …")
    urllib.request.urlretrieve(url, tmp)
    print(f"  Extracting to {dest_dir} …")
    with zipfile.ZipFile(tmp, "r") as zf:
        zf.extractall(dest_dir)
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
    """'ikariam 10' → '10 ikariam.lnk'"""
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

# ── ikariam folder management ─────────────────────────────────────────────────

def sync_ikariam_folders(ikabot_dir: Path, count: int, template_dir: Path) -> None:
    """Ensure ikariam 1..count exist, wipe their contents, copy template into each."""
    for i in range(1, count + 1):
        folder = ikabot_dir / f"ikariam {i}"
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

# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    state = load_state()
    saved_install = Path(state["install_dir"]) if "install_dir" in state else None

    # ── 1. Install location ──────────────────────────────────────────────────
    default_path = str(saved_install or DEFAULT_INSTALL)
    location_str = ask_string(
        "Installation folder\n(Edit the path below or leave as default):",
        title="Install Location",
        initial=default_path,
    )
    if not location_str:
        print("Cancelled.")
        return

    install_dir = Path(location_str)
    if not ask_ok_cancel(f"Install to:\n\n  {install_dir}\n\nProceed?"):
        print("Cancelled.")
        return

    # ── 2. Create folder structure ───────────────────────────────────────────
    shortcuts_dir = install_dir / "shortcuts"
    modules_dir   = install_dir / "modules"
    template_dir  = install_dir / "ikabot template"
    ikabot_dir    = install_dir / "ikabot"
    update_dir    = install_dir / "update"

    for d in (shortcuts_dir, modules_dir, template_dir, ikabot_dir, update_dir):
        d.mkdir(parents=True, exist_ok=True)

    print("Folder structure created.")

    # ── 3. Fetch GitHub releases ─────────────────────────────────────────────
    print("Contacting GitHub …")
    try:
        releases = fetch_releases()
    except Exception as exc:
        show_error(f"Could not reach GitHub:\n{exc}\n\nCheck your internet connection and try again.")
        return

    # ── 4. Installer self-update ─────────────────────────────────────────────
    installer_asset = find_asset(releases, ASSET_INSTALLER)
    if installer_asset:
        url, tag = installer_asset
        local_ver = read_version(update_dir) or INSTALLER_VERSION
        if is_newer(tag, local_ver):
            print(f"Downloading installer update {tag} …")
            try:
                download_zip(url, update_dir, ASSET_INSTALLER)
                write_version(update_dir, tag)
            except Exception as exc:
                show_error(f"Failed to download installer update:\n{exc}")
                return

        update_ver = read_version(update_dir)
        if update_ver and is_newer(update_ver, INSTALLER_VERSION):
            new_exe = update_dir / "ikabot-mod-install.exe"
            new_py  = update_dir / "ikabot-mod-install.py"
            launcher = new_exe if new_exe.exists() else (new_py if new_py.exists() else None)

            if launcher:
                show_info(
                    f"A newer installer (v{update_ver}) is available in the update folder.\n\n"
                    f"Press OK to launch it and close this version.",
                    "Update required",
                )
                save_state(install_dir)
                if launcher.suffix == ".py":
                    subprocess.Popen([sys.executable, str(launcher)])
                else:
                    subprocess.Popen([str(launcher)])
                sys.exit(0)
    else:
        print(f"Note: {ASSET_INSTALLER} not yet on GitHub releases — skipping self-update check.")

    # ── 5. Download ikabot template ──────────────────────────────────────────
    ikabot_asset = find_asset(releases, ASSET_IKABOT)
    if not ikabot_asset:
        show_error(
            f"{ASSET_IKABOT} was not found in any GitHub release.\n"
            f"Please publish the release asset and re-run."
        )
        return

    url, tag = ikabot_asset
    local_ikabot_ver = read_version(template_dir)
    if local_ikabot_ver is None or is_newer(tag, local_ikabot_ver):
        print(f"Downloading ikabot {tag} …")
        for item in template_dir.iterdir():
            if item.name.startswith("version"):
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        try:
            download_zip(url, template_dir, ASSET_IKABOT)
            write_version(template_dir, tag)
            print("ikabot template updated.")
        except Exception as exc:
            show_error(f"Failed to download ikabot:\n{exc}")
            return
    else:
        print(f"ikabot template already up to date (v{local_ikabot_ver}).")

    # ── 6. Instance count ────────────────────────────────────────────────────
    while True:
        count = ask_integer(
            "How many Ikariam bot instances do you want?\n"
            "(Max 100 per run — re-run the installer to add more)",
            minval=1,
            maxval=100,
        )
        if count is None:
            print("Cancelled.")
            return
        if count > 20:
            if not ask_yes_no(
                f"You requested {count} instances.\n"
                f"That is a large number — are you sure?"
            ):
                continue
        break

    # ── 7. Sync ikariam folders ──────────────────────────────────────────────
    print(f"Setting up {count} ikariam instance(s) …")
    sync_ikariam_folders(ikabot_dir, count, template_dir)

    # ── 8. Create internal shortcuts ─────────────────────────────────────────
    print("Creating shortcuts …")
    for i in range(1, count + 1):
        exe = ikabot_dir / f"ikariam {i}" / "ikabot.exe"
        if exe.exists():
            lnk_name = shortcut_name(f"ikariam {i}")
            create_shortcut(exe, shortcuts_dir / lnk_name)
            print(f"  {lnk_name}")

    # ── 9. User shortcut destination ─────────────────────────────────────────
    sc_str = ask_string(
        "Where should shortcuts be saved?\n(Edit path or leave as default):",
        title="Shortcut Location",
        initial=str(DEFAULT_SHORTCUTS),
    )
    user_sc_dir = Path(sc_str) if sc_str else DEFAULT_SHORTCUTS
    user_sc_dir.mkdir(parents=True, exist_ok=True)

    for lnk in shortcuts_dir.glob("*.lnk"):
        shutil.copy2(lnk, user_sc_dir / lnk.name)
    print(f"Shortcuts copied to {user_sc_dir}")

    # ── 10. Open/close/update tools ──────────────────────────────────────────
    tools_asset = find_asset(releases, ASSET_TOOLS)
    if tools_asset:
        url, tag = tools_asset
        tools_dir = update_dir / "tools"
        local_tools_ver = read_version(tools_dir)
        if local_tools_ver is None or is_newer(tag, local_tools_ver):
            tools_dir.mkdir(exist_ok=True)
            print(f"Downloading tools {tag} …")
            try:
                download_zip(url, tools_dir, ASSET_TOOLS)
                write_version(tools_dir, tag)

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
        print(f"Note: {ASSET_TOOLS} not yet on GitHub releases — skipping.")

    # ── Done ─────────────────────────────────────────────────────────────────
    show_info(
        f"Installation complete!\n\n"
        f"  Instances : {count}\n"
        f"  Installed : {install_dir}\n"
        f"  Shortcuts : {user_sc_dir}",
        "Done",
    )
    print("Installation complete.")


if __name__ == "__main__":
    main()
