#!/usr/bin/env python3
"""Replace ikabot folders inside Ikariam profile directories with a desktop copy.

If --base-dir / --source are not passed, folder-picker dialogs appear instead.

Usage examples:
    python replace_ikabot_profiles.py
    python replace_ikabot_profiles.py --base-dir "C:/Users/You/Desktop/Ikariam Firefox" --source "C:/Users/You/Desktop/ikabot"
    python replace_ikabot_profiles.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
from pathlib import Path


def _handle_remove_readonly(func, path, exc_info):
    """Retry deletion after removing read-only flag (Windows-friendly)."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        raise exc_info[1]


def pick_folder(title: str) -> Path | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        chosen = filedialog.askdirectory(title=title)
        root.destroy()
        return Path(chosen) if chosen else None
    except Exception as exc:
        print(f"ERROR: Could not open folder picker: {exc}")
        return None


def find_ikabot_folder(profile_dir: Path) -> Path | None:
    for child in profile_dir.iterdir():
        if child.is_dir() and child.name.lower() == "ikabot":
            return child
    return None


def iter_profile_dirs(base_dir: Path):
    """Yield profile-like folders (e.g. Ikariam 1, Ikariam Primary, Ikariam Ambrosia)."""
    for child in sorted(base_dir.iterdir(), key=lambda path: path.name.lower()):
        if not child.is_dir():
            continue
        name = child.name.lower()
        if name.startswith("ikariam") or name.startswith("aikariam"):
            yield child


def replace_ikabot_in_profiles(base_dir: Path, source_ikabot: Path, dry_run: bool = False) -> int:
    if not source_ikabot.is_dir():
        print(f"ERROR: Source ikabot folder not found: {source_ikabot}")
        return 1

    if not base_dir.is_dir():
        print(f"ERROR: Base directory not found: {base_dir}")
        return 1

    changed = 0
    profiles = list(iter_profile_dirs(base_dir))

    if not profiles:
        print(f"WARNING: No Ikariam profile folders found in: {base_dir}")
        return 1

    for profile in profiles:
        target = find_ikabot_folder(profile)
        if target is None:
            target = profile / "ikabot"
            print(f"INFO: No existing ikabot folder in {profile.name}; will create {target.name}")

        if dry_run:
            print(f"DRY-RUN: Would replace {target}")
            changed += 1
            continue

        try:
            if target.exists():
                shutil.rmtree(target, onexc=_handle_remove_readonly)
                print(f"DELETED: {target}")

            shutil.copytree(source_ikabot, target)
            print(f"COPIED: {source_ikabot} -> {target}")
            changed += 1
        except PermissionError as exc:
            print(f"ERROR: Permission denied while updating {target}: {exc}")
            print("TIP: Close Firefox/ikabot processes and run this script again.")
        except Exception as exc:
            print(f"ERROR: Failed to update {target}: {exc}")

    print(f"\nDone. Updated {changed} profile(s).")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replace ikabot folders in Ikariam profiles.")
    parser.add_argument("--base-dir", type=Path, default=None,
        help="Folder containing Ikariam profile directories (prompted if omitted)")
    parser.add_argument("--source", type=Path, default=None,
        help="Source ikabot folder to copy from (prompted if omitted)")
    parser.add_argument("--dry-run", action="store_true", help="Show actions without changing files")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    base_dir = args.base_dir.expanduser() if args.base_dir else None
    source = args.source.expanduser() if args.source else None

    if base_dir is None:
        base_dir = pick_folder("Select the folder containing your Ikariam profile folders (Ikariam 1, Ikariam 2, ...)")
        if base_dir is None:
            print("Cancelled.")
            return 1

    if source is None:
        source = pick_folder("Select the source ikabot folder to copy from")
        if source is None:
            print("Cancelled.")
            return 1

    return replace_ikabot_in_profiles(base_dir, source, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
