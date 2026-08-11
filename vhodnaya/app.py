"""Инициализация Qt, DPI и пустой либо сохранённой доски камер."""

from __future__ import annotations

import ctypes
import os
import sys

# Эти параметры должны быть установлены до создания QApplication.
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

from PySide6.QtCore import QCoreApplication, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from .config import AppConfig, ConfigError, ConfigStore  # noqa: E402
from .constants import APP_NAME, APP_VERSION, ORGANIZATION_NAME  # noqa: E402
from .resources import (  # noqa: E402
    application_icon,
    default_font,
    install_application_fonts,
)
from .ui.main_window import MainWindow  # noqa: E402
from .ui.theme import stylesheet  # noqa: E402


def _set_windows_app_id() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(  # type: ignore[attr-defined]
            "GlazIvy.Desktop.1"
        )
    except (AttributeError, OSError):
        pass


def run() -> int:
    _set_windows_app_id()
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    QCoreApplication.setOrganizationName(ORGANIZATION_NAME)
    QCoreApplication.setApplicationName(APP_NAME)
    QCoreApplication.setApplicationVersion(APP_VERSION)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    app.setWindowIcon(application_icon())
    font_family = install_application_fonts()
    app.setFont(default_font(font_family))
    app.setStyle("Fusion")
    app.setStyleSheet(stylesheet(font_family))

    store = ConfigStore()
    config = AppConfig()
    load_error = ""
    try:
        config = store.load()
    except FileNotFoundError:
        pass
    except ConfigError as exc:
        load_error = str(exc)

    window = MainWindow(store, config, load_error=load_error)
    app.aboutToQuit.connect(window.shutdown)
    window.show()
    return app.exec()
