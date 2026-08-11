"""Поиск ресурсов как из исходников, так и внутри сборки PyInstaller."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QFont, QFontDatabase, QIcon


_body_family = ""
_heading_family = ""


def project_root() -> Path:
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root)
    return Path(__file__).resolve().parent.parent


def resource_path(*parts: str) -> Path:
    return project_root().joinpath(*parts)


def application_icon() -> QIcon:
    """ICO используется в сборке, новый PNG — fallback из исходников."""

    ico = resource_path("assets", "app_icon.ico")
    if ico.exists():
        return QIcon(str(ico))
    source = resource_path("assets", "app_icon_source.png")
    if source.exists():
        return QIcon(str(source))
    return QIcon()


def install_application_fonts() -> str:
    """Регистрирует все вложенные TTF и возвращает семейство основного текста."""

    global _body_family, _heading_family

    font_dir = resource_path("assets", "fonts")
    loaded_families: list[str] = []
    if font_dir.exists():
        for font_file in sorted(font_dir.glob("*.ttf")):
            font_id = QFontDatabase.addApplicationFont(str(font_file))
            if font_id >= 0:
                loaded_families.extend(QFontDatabase.applicationFontFamilies(font_id))

    available = QFontDatabase.families()
    inter_family = next(
        (name for name in loaded_families if name.casefold() == "inter"),
        None,
    ) or next(
        (name for name in loaded_families if name.casefold().startswith("inter")),
        None,
    )
    unbounded_family = next(
        (name for name in loaded_families if name.casefold() == "unbounded"),
        None,
    ) or next(
        (name for name in loaded_families if name.casefold().startswith("unbounded")),
        None,
    )

    _body_family = inter_family or "Segoe UI Variable Text"
    if _body_family not in available:
        _body_family = "Segoe UI"
    _heading_family = unbounded_family or _body_family
    return _body_family


def body_family() -> str:
    """Возвращает выбранное семейство основного текста после установки TTF."""

    return _body_family or "Segoe UI"


def heading_family() -> str:
    """Возвращает Unbounded, а при недоступности — основной шрифт."""

    return _heading_family or body_family()


def default_font(family: str) -> QFont:
    font = QFont(family, 10)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font
