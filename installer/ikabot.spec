# -*- mode: python ; coding: utf-8 -*-
#
# Build command (run from the ikabot-modules directory):
#   Windows:  python -m PyInstaller installer\ikabot.spec --clean
#   Linux:    python -m PyInstaller installer/ikabot.spec --clean
#
# Output: dist/ikabot/ikabot.exe  +  dist/ikabot/_internal/
# To distribute: copy the entire dist/ikabot/ folder.

import os
from PyInstaller.utils.hooks import collect_all, collect_data_files

# SPECPATH is the installer/ directory — step up one level to the repo root
ROOT = os.path.abspath(os.path.join(SPECPATH, '..'))

# Collect every file inside the ikabot package (submodules, locale, etc.)
datas, binaries, hiddenimports = collect_all('ikabot')

# Also pull in the locale data files explicitly
datas += collect_data_files('ikabot', includes=['locale/**/*'])

# The local pure-Python decaptcha weights (6.6 MB). Explicit rather than
# relying on collect_all's heuristics: if this is silently missing the solver
# falls back to the API with no visible error, which is very hard to diagnose.
datas += collect_data_files('ikabot', includes=['assets/*.bin'])

a = Analysis(
    [os.path.join(ROOT, 'ikabot', '__main__.py')],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + [
        # Ensure flask is bundled (required by webServer.py)
        'flask',
        'flask.templating',
        # cryptography for the credential vault
        'cryptography',
        'cryptography.hazmat.primitives.ciphers.aead',
        # dotenv used at startup in command_line.py
        'dotenv',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    name='ikabot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ikabot',
)
