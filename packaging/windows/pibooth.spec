# PyInstaller specification for the pibooth application (one-dir bundle).
#
# Build from the repository root with pibooth installed in the environment:
#   pyinstaller packaging/windows/pibooth.spec
#
# The resulting bundle is written to dist/pibooth/ and is wrapped into a
# Windows installer by packaging/windows/installer.iss.

import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

HERE = os.path.abspath(SPECPATH)  # noqa: F821 (SPECPATH is injected by PyInstaller)
# Let the analysis resolve pibooth from the source tree as well, so the spec
# also works when pibooth is installed in editable mode
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

datas = []
# Embedded fonts and picture assets (looked up relative to the package __file__)
datas += collect_data_files("pibooth")
# pygame_menu ships fonts/sounds/images used by the settings menu
datas += collect_data_files("pygame_menu")

hiddenimports = []
# gpiozero loads its pin factories (including the mock fallback used on
# non-Raspberry Pi machines) through entry points, invisible to static analysis
hiddenimports += collect_submodules("gpiozero")

a = Analysis(
    [os.path.join(HERE, "pibooth-launcher.py")],
    pathex=[REPO_ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="pibooth",
    icon=os.path.join(HERE, "pibooth.ico"),
    debug=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="pibooth",
)
