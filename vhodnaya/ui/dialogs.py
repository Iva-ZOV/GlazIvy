"""Безрамочные диалоги настроек доски и отдельных камер."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QCursor, QMouseEvent, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..config import CameraConfig
from ..resources import application_icon
from .forms import CameraForm
from .widgets import LogoGlyph, ToolIconButton


class DialogHeader(QWidget):
    close_clicked = Signal()

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(52)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 0, 10, 0)
        layout.setSpacing(9)
        layout.addWidget(LogoGlyph(25, self))
        label = QLabel(title, self)
        label.setObjectName("appTitle")
        layout.addWidget(label)
        layout.addStretch(1)
        close_button = ToolIconButton("close", "Закрыть", self)
        close_button.clicked.connect(self.close_clicked)
        layout.addWidget(close_button)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None:
                handle.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)


class FramelessDialog(QDialog):
    """Диалог с безопасным размером для 100/125/150% DPI."""

    def __init__(
        self,
        title: str,
        parent: QWidget | None = None,
        *,
        preferred_width: int = 720,
        preferred_height: int = 680,
    ) -> None:
        super().__init__(parent)
        self._preferred_width = preferred_width
        self._preferred_height = preferred_height
        self.setWindowTitle(title)
        self.setWindowIcon(application_icon())
        self.setWindowFlags(
            Qt.WindowType.Dialog
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowSystemMenuHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setModal(True)
        self.setMinimumSize(580, 430)

        self.outer_layout = QVBoxLayout(self)
        self.outer_layout.setContentsMargins(18, 18, 18, 18)
        self.surface = QFrame(self)
        self.surface.setObjectName("dialogSurface")
        self.outer_layout.addWidget(self.surface)

        shadow = QGraphicsDropShadowEffect(self.surface)
        shadow.setBlurRadius(44)
        shadow.setOffset(0, 12)
        shadow.setColor(QColor(0, 0, 0, 185))
        self.surface.setGraphicsEffect(shadow)

        self.surface_layout = QVBoxLayout(self.surface)
        self.surface_layout.setContentsMargins(0, 0, 0, 0)
        self.surface_layout.setSpacing(0)

        self.header = DialogHeader(title, self.surface)
        self.header.close_clicked.connect(self.reject)
        self.surface_layout.addWidget(self.header)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self.parentWidget() is not None and self.parentWidget().windowHandle() is not None:
            screen = self.parentWidget().windowHandle().screen()
        else:
            screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        width = min(self._preferred_width, max(580, available.width() - 28))
        height = min(self._preferred_height, max(430, available.height() - 28))
        self.resize(width, height)
        self.move(
            available.center().x() - self.width() // 2,
            available.center().y() - self.height() // 2,
        )


class SettingsDialog(FramelessDialog):
    """Настройки выбранной камеры; удаление не требует валидной формы."""

    def __init__(self, config: CameraConfig, parent: QWidget | None = None) -> None:
        super().__init__(
            "Настройки камеры",
            parent,
            preferred_width=720,
            preferred_height=720,
        )
        self.result_config: CameraConfig | None = None
        self.delete_requested = False

        content = QWidget(self.surface)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(28, 14, 20, 24)
        content_layout.setSpacing(15)

        title = QLabel("Параметры камеры", content)
        title.setObjectName("dialogTitle")
        content_layout.addWidget(title)
        subtitle = QLabel(
            "Изменения адреса, транспорта или потока применятся сразу после сохранения.",
            content,
        )
        subtitle.setObjectName("dialogSubtitle")
        subtitle.setWordWrap(True)
        content_layout.addWidget(subtitle)

        scroll = QScrollArea(content)
        scroll.setWidgetResizable(True)
        self.form = CameraForm(config, scroll)
        scroll.setWidget(self.form)
        content_layout.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        delete_button = QPushButton("Удалить камеру", content)
        delete_button.setObjectName("dangerButton")
        delete_button.clicked.connect(self._request_delete)
        buttons.addWidget(delete_button)
        buttons.addStretch(1)
        cancel = QPushButton("Отмена", content)
        cancel.setObjectName("secondaryButton")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Сохранить", content)
        save.setObjectName("primaryButton")
        save.setDefault(True)
        save.clicked.connect(self._accept_form)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        content_layout.addLayout(buttons)
        self.surface_layout.addWidget(content, 1)

    def _request_delete(self) -> None:
        self.delete_requested = True
        self.accept()

    def _accept_form(self) -> None:
        config = self.form.build_config()
        if config is None:
            return
        self.result_config = config
        self.accept()


class BoardSettingsDialog(FramelessDialog):
    """Небольшой экран общих настроек приложения."""

    def __init__(self, autostart: bool, parent: QWidget | None = None) -> None:
        super().__init__(
            "Настройки доски",
            parent,
            preferred_width=620,
            preferred_height=450,
        )
        self.result_autostart: bool | None = None

        content = QWidget(self.surface)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(30, 22, 26, 28)
        layout.setSpacing(14)

        title = QLabel("Доска камер", content)
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        subtitle = QLabel(
            "Положение, размер и порядок плиток сохраняются автоматически.",
            content,
        )
        subtitle.setObjectName("dialogSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.autostart_check = QCheckBox("Запускать при старте Windows", content)
        self.autostart_check.setChecked(autostart)
        layout.addSpacing(12)
        layout.addWidget(self.autostart_check)
        layout.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Отмена", content)
        cancel.setObjectName("secondaryButton")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Сохранить", content)
        save.setObjectName("primaryButton")
        save.setDefault(True)
        save.clicked.connect(self._accept_settings)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        self.surface_layout.addWidget(content, 1)

    def _accept_settings(self) -> None:
        self.result_autostart = self.autostart_check.isChecked()
        self.accept()
