# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for ikabot-mod-install
#
# Build command (run from the ikabot-mod-install/ folder):
#   pyinstaller ikabot-mod-install.spec
#
# Output: dist/ikabot-mod-install.exe  (single-file, no console window)

a = Analysis(
    ['ikabot-mod-install.py'],
    pathex=[],
    binaries=[],
    datas=[],
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
    a.binaries,
    a.datas,
    [],
    name='ikabot-mod-install',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,           # keeps a progress window open so the user can see download/install steps
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,              # replace None with 'icon.ico' if you add an icon file
)
