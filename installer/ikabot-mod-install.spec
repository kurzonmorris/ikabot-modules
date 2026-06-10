# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for ikabot-mod-install
#
# Build command (run from the ikabot-mod-install/ folder):
#   pyinstaller ikabot-mod-install.spec
#
# Output: dist/ikabot-mod-install/
#           ikabot-mod-install.exe   <- the installer
#           _internal/               <- dependencies + bundled files
#               ikabot_manager.ahk
#               ikabot_update.ps1
#               RELEASE_NOTES.txt
#               ... (Python runtime, dlls, etc.)
#
# Distribute the entire dist/ikabot-mod-install/ folder, not just the exe.

a = Analysis(
    ['ikabot-mod-install.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('ikabot_manager.ahk', '.'),
        ('ikabot_update.ps1',  '.'),
        ('RELEASE_NOTES.txt',  '.'),
    ],
    hiddenimports=[
        'tkinter',
        'tkinter.filedialog',
        'tkinter.messagebox',
        'tkinter.simpledialog',
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
    name='ikabot-mod-install',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,              # replace None with 'icon.ico' if you add an icon file
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ikabot-mod-install',
)
