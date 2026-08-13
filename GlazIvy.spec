# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_dynamic_libs


ROOT = Path(SPECPATH).resolve()
ICON = ROOT / "assets" / "app_icon.ico"
MODEL = ROOT / "assets" / "models" / "yolox_tiny.onnx"
if not ICON.exists():
    raise SystemExit("Сначала выполните scripts/generate_icon.py или build.ps1")
if not MODEL.exists():
    raise SystemExit("Сначала выполните scripts/fetch_model.py")

# ONNX Runtime содержит нативные DLL/PYD, которые должны попасть в onedir
# вместе с Python-модулями. Модель включается ниже как часть assets.
ORT_BINARIES = collect_dynamic_libs("onnxruntime")


a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=ORT_BINARIES,
    datas=[(str(ROOT / "assets"), "assets")],
    # ``av`` импортируется лениво из AudioReader, поэтому Analysis сам его не
    # увидит. Стандартный hook-av собирает cython-модули и Windows av.libs.
    hiddenimports=["cv2", "numpy", "av", "onnxruntime"],
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
