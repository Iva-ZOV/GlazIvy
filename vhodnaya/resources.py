"""Поиск ресурсов как из исходников, так и внутри сборки PyInstaller."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase, QIcon


def project_root() -> Path:
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root)
    return Path(__file__).resolve().parent.parent


def resource_path(*parts: str) -> Path:
    return project_root().joinpath(*parts)


def application_icon() -> QIcon:
    """ICO используется в сборке, SVG — удобный fallback из исходников."""

    ico = resource_path("assets", "app_icon.ico")
    if ico.exists():
        return QIcon(str(ico))
    return QIcon(str(resource_path("assets", "app_icon.svg")))


def install_application_fonts() -> str:
    """Подключает вложенный Inter, если TTF добавлены, иначе системный аналог."""

    font_dir = resource_path("assets", "fonts")
    loaded_families: list[str] = []
    if font_dir.exists():
        for font_file in sorted(font_dir.glob("*.ttf")):
            font_id = QFontDatabase.addApplicationFont(str(font_file))
            if font_id >= 0:
                loaded_families.extend(QFontDatabase.applicationFontFamilies(font_id))

    inter_family = next(
        (name for name in loaded_families if name.casefold().startswith("inter")),
        None,
    )
    family = inter_family or "Segoe UI Variable Text"
    if family not in QFontDatabase.families():
        family = "Segoe UI"
    return family


def default_font(family: str) -> QFont:
    font = QFont(family, 10)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font
