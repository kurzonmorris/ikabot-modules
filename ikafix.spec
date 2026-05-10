# -*- mode: python ; coding: utf-8 -*-
#
# Build command (run from the ikabot-modules directory):
#   Windows:  python -m PyInstaller ikafix.spec
#   Linux:    pyinstaller ikafix.spec

from PyInstaller.utils.hooks import collect_all, collect_data_files

# Collect every file inside the ikabot package (submodules, locale, etc.)
datas, binaries, hiddenimports = collect_all('ikabot')

# Also pull in the locale data files explicitly
datas += collect_data_files('ikabot', includes=['locale/**/*'])

a = Analysis(
    ['ikabot/__main__.py'],
    pathex=['.'],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports + [
        # Ensure flask is bundled (required by webServer.py)
        'flask',
        'flask.templating',
        # cryptography for the credential vault
        'cryptography',
        'cryptography.hazmat.primitives.ciphers.aead',
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
    a.binaries,
    a.datas,
    [],
    name='ikafix',
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
