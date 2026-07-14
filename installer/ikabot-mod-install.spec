# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for ikabot-mod-install — ONEFILE build
#
# Build command (run from the installer/ folder):
#   python -m PyInstaller --clean ikabot-mod-install.spec
#
# Or use build.bat which reads the version automatically and zips the output.
#
# Output: dist/ikabot-mod-install.exe   <- single self-contained file
#
# Everything (Python runtime, tkinter, bundled files) is packed inside the
# exe and unpacked to a temp folder at startup. The exe can be copied
# anywhere on its own — there is no _internal folder to lose.

a = Analysis(
    ['ikabot-mod-install.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('RELEASE_NOTES.txt',          '.'),
        ('open-all-instances.ps1',     '.'),
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
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,              # replace None with 'icon.ico' if you add an icon file
)
