#!/usr/bin/env python3
"""Interactive setup script for an ikabot multi-instance folder structure.

Creates on the Desktop:
  [name]/
    shortcuts/         <- .lnk files, numbered prefix (e.g. "1 ikariam.lnk")
    modules/
    ikabot template/   <- master copy of ikabot.exe + _internal
    ikabot/
      ikariam 1/       <- copy of template
      ikariam 2/
      ...
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog


# ---------------------------------------------------------------------------
# Tkinter helpers
# ---------------------------------------------------------------------------

def _root() -> tk.Tk:
    r = tk.Tk()
    r.withdraw()
    r.attributes("-topmost", True)
    return r


def ask_string(prompt: str, title: str = "Input") -> str | None:
    r = _root()
    val = simpledialog.askstring(title, prompt, parent=r)
    r.destroy()
    return val.strip() if val else None


def ask_integer(prompt: str, title: str = "Input", minval: int = 1) -> int | None:
    r = _root()
    val = simpledialog.askinteger(title, prompt, parent=r, minvalue=minval)
    r.destroy()
    return val


def pick_folder(title: str) -> Path | None:
    r = _root()
    chosen = filedialog.askdirectory(title=title, parent=r)
    r.destroy()
    return Path(chosen) if chosen else None


def show_info(msg: str, title: str = "Info") -> None:
    r = _root()
    messagebox.showinfo(title, msg, parent=r)
    r.destroy()


def ask_yes_no(msg: str, title: str = "Confirm") -> bool:
    r = _root()
    result = messagebox.askyesno(title, msg, parent=r)
    r.destroy()
    return result


# ---------------------------------------------------------------------------
# Windows shortcut creation via PowerShell
# ---------------------------------------------------------------------------

def create_shortcut(target: Path, lnk_path: Path) -> None:
    ps = (
        f'$ws = New-Object -ComObject WScript.Shell; '
        f'$s = $ws.CreateShortcut("{lnk_path}"); '
        f'$s.TargetPath = "{target}"; '
        f'$s.WorkingDirectory = "{target.parent}"; '
        f'$s.Save()'
    )
    subprocess.run(
        ["powershell", "-WindowStyle", "Hidden", "-Command", ps],
        check=True,
    )


# ---------------------------------------------------------------------------
# Shortcut filename: "ikariam 10" -> "10 ikariam.lnk"
# ---------------------------------------------------------------------------

def shortcut_name(folder_name: str) -> str:
    m = re.match(r"^(.*?)\s+(\d+)$", folder_name.strip())
    if m:
        return f"{m.group(2)} {m.group(1).strip()}.lnk"
    return folder_name + ".lnk"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    desktop = Path.home() / "Desktop"

    # 1. Root folder name
    name = ask_string("Enter a name for the new folder on your Desktop:")
    if not name:
        print("Cancelled.")
        return

    root_dir = desktop / name
    if root_dir.exists():
        if not ask_yes_no(f'"{root_dir}" already exists.\nContinue and add to it?'):
            print("Cancelled.")
            return

    # 2. Locate ikabot.exe + _internal
    while True:
        src = pick_folder("Select the folder that contains ikabot.exe and the _internal folder")
        if src is None:
            print("Cancelled.")
            return

        missing = []
        if not (src / "ikabot.exe").is_file():
            missing.append("ikabot.exe")
        if not (src / "_internal").is_dir():
            missing.append("_internal folder")

        if not missing:
            break

        show_info(
            f"The following were not found in the selected folder:\n\n"
            + "\n".join(f"  • {m}" for m in missing)
            + "\n\nPlease select the correct folder.",
            "Missing files",
        )

    # 3. Instance count
    count = ask_integer("How many Ikariam instances do you want?", "Instance count")
    if count is None:
        print("Cancelled.")
        return

    # 4. Build folder structure
    shortcuts_dir = root_dir / "shortcuts"
    modules_dir   = root_dir / "modules"
    template_dir  = root_dir / "ikabot template"
    ikabot_dir    = root_dir / "ikabot"

    for d in (shortcuts_dir, modules_dir, template_dir, ikabot_dir):
        d.mkdir(parents=True, exist_ok=True)

    # 5. Populate template
    print("Copying to ikabot template…")
    shutil.copy2(src / "ikabot.exe", template_dir / "ikabot.exe")
    dest_internal = template_dir / "_internal"
    if dest_internal.exists():
        shutil.rmtree(dest_internal)
    shutil.copytree(src / "_internal", dest_internal)
    print("Template ready.")

    # 6. Create ikariam folders and copy template into each
    ikariam_folders: list[Path] = []
    for i in range(1, count + 1):
        folder = ikabot_dir / f"ikariam {i}"
        folder.mkdir(exist_ok=True)
        ikariam_folders.append(folder)

        print(f"  Setting up ikariam {i}…")
        shutil.copy2(template_dir / "ikabot.exe", folder / "ikabot.exe")
        dest = folder / "_internal"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(template_dir / "_internal", dest)

    # 7. Create shortcuts
    print("Creating shortcuts…")
    for folder in ikariam_folders:
        lnk_name = shortcut_name(folder.name)
        lnk_path = shortcuts_dir / lnk_name
        create_shortcut(folder / "ikabot.exe", lnk_path)
        print(f"  {lnk_name}")

    show_info(
        f"Setup complete!\n\n"
        f"{count} instance(s) created in:\n  {ikabot_dir}\n\n"
        f"Shortcuts saved to:\n  {shortcuts_dir}",
        "Done",
    )
    print("Done.")


if __name__ == "__main__":
    main()
