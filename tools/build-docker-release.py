#!/usr/bin/env python3
"""Package the Docker installer into a versioned zip in releases/.

The version comes from installer-docker/VERSION and is checked against the
copies inside the scripts, so a bumped version can never ship half-applied.
Run from anywhere:  python3 tools/build-docker-release.py
"""

import re
import signal
import sys
import zipfile
from pathlib import Path

# Piping this script into head/less otherwise ends in a BrokenPipeError
# traceback after the zip has already been written correctly.
signal.signal(signal.SIGPIPE, signal.SIG_DFL)

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "installer-docker"
DOCKER = ROOT / "docker"
OUT = ROOT / "releases"

# ikabot itself travels in the zip under app/. The image does not contain it:
# it is bind-mounted from the host so that `ika update` can replace it and the
# new version outlives the container. Without this the container builds and
# starts and then fails on "No module named ikabot" in every window.
APP_PARTS = ("ikabot", "modules", "config-examples")
SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache"}
SKIP_SUFFIX = (".pyc", ".pyo")


def fail(msg):
    print("error: %s" % msg, file=sys.stderr)
    raise SystemExit(1)


def main():
    version = (SRC / "VERSION").read_text().strip()
    if not re.match(r"^\d+\.\d+\.\d+$", version):
        fail("VERSION must look like 1.0.0, got %r" % version)

    # Every place the version is written must agree, or users see one number
    # in the filename and another on screen.
    checks = {
        "install.sh": 'INSTALLER_VERSION="%s"' % version,
        "INSTALL.bat": "INSTALLER_VERSION=%s" % version,
        "README.txt": "installer v%s" % version,
    }
    for name, needle in checks.items():
        text = (SRC / name).read_text(encoding="utf-8", errors="replace")
        if needle not in text:
            fail("%s does not contain %r — bump it to match VERSION" % (name, needle))

    panel = sorted(DOCKER.glob("ika-panel_v*"))
    if len(panel) != 1:
        fail("expected exactly one docker/ika-panel_v*, found %d" % len(panel))
    panel_version = panel[0].name.split("_v")[1]

    for part in APP_PARTS:
        if not (ROOT / part).is_dir():
            fail("%s/ is missing — the zip would install a container that "
                 "cannot start ikabot" % part)
    if not (ROOT / "ikabot" / "__main__.py").is_file():
        fail("ikabot/__main__.py is missing — `python3 -m ikabot` would fail")

    OUT.mkdir(exist_ok=True)
    target = OUT / ("ikabot-docker_v%s.zip" % version)

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
        for name in ("INSTALL.bat", "install.sh", "README.txt"):
            z.write(SRC / name, name)
        for f in sorted(DOCKER.iterdir()):
            if f.is_file() and f.name != "docker-compose.yml":
                z.write(f, "docker/" + f.name)
        z.write(DOCKER / "docker-compose.yml", "docker/docker-compose.yml")

        app_files = 0
        for part in APP_PARTS:
            base = ROOT / part
            for f in sorted(base.rglob("*")):
                if not f.is_file() or f.suffix in SKIP_SUFFIX:
                    continue
                if any(d in SKIP_DIRS for d in f.relative_to(ROOT).parts):
                    continue
                z.write(f, "app/" + str(f.relative_to(ROOT)))
                app_files += 1

    # Modes are carried from the source files by ZipFile.write, so the check
    # that matters is that they were executable in the repo to begin with.
    with zipfile.ZipFile(target) as z:
        for info in z.infolist():
            needs_exec = (info.filename == "install.sh"
                          or info.filename.startswith("docker/ika")
                          or info.filename == "docker/entrypoint.sh")
            if needs_exec and not (info.external_attr >> 16) & 0o100:
                fail("%s is not executable in the repo — chmod +x it and rebuild"
                     % info.filename)

    # The one file that proves the app payload arrived intact.
    with zipfile.ZipFile(target) as z:
        if "app/ikabot/__main__.py" not in z.namelist():
            fail("app/ikabot/__main__.py is not in the zip")

    size = target.stat().st_size / 1024.0
    print("built %s  (%.0f KB)" % (target.name, size))
    print("  installer version : %s" % version)
    print("  control panel     : v%s" % panel_version)
    print("  ikabot files      : %d under app/" % app_files)
    with zipfile.ZipFile(target) as z:
        print("  contents:")
        shown = [n for n in z.namelist() if not n.startswith("app/")]
        for n in shown:
            print("    " + n)
        print("    app/  (%d files: %s)" % (app_files, ", ".join(APP_PARTS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
