# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


ROOT = Path(SPECPATH).resolve()
ICON = ROOT / "assets" / "app_icon.ico"
if not ICON.exists():
    raise SystemExit("Сначала выполните scripts/generate_icon.py или build.ps1")


a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ROOT / "assets"), "assets")],
    hiddenimports=["cv2", "numpy"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(ROOT / "packaging" / "runtime_hook.py")],
    excludes=["PyQt5", "PyQt6", "PySide2", "tkinter"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GlazIvy",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON),
    version=str(ROOT / "packaging" / "version_info.txt"),
    manifest=str(ROOT / "packaging" / "GlazIvy.manifest"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="GlazIvy",
)

