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
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..config import CameraConfig
from ..onvif import DiscoveredCamera
from ..resources import application_icon
from .forms import CameraForm
from .theme import SUCCESS, TEXT_MUTED, WARNING
from .widgets import (
    GrainFrame,
    LogoGlyph,
    ToolIconButton,
    set_heading_capitalization,
)


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
        set_heading_capitalization(label)
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
        self.surface = GrainFrame(self)
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


class OnvifProgressDialog(FramelessDialog):
    """Неблокирующий индикатор фонового WS-Discovery/ONVIF-запроса."""

    def __init__(
        self,
        title: str,
        heading: str,
        detail: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            title,
            parent,
            preferred_width=620,
            preferred_height=430,
        )
        content = QWidget(self.surface)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(30, 24, 26, 28)
        layout.setSpacing(15)

        label = QLabel(heading, content)
        label.setObjectName("dialogTitle")
        set_heading_capitalization(label)
        label.setWordWrap(True)
        layout.addWidget(label)
        description = QLabel(detail, content)
        description.setObjectName("dialogSubtitle")
        description.setWordWrap(True)
        layout.addWidget(description)

        progress = QProgressBar(content)
        progress.setObjectName("discoveryProgress")
        progress.setRange(0, 0)
        progress.setTextVisible(False)
        layout.addWidget(progress)
        layout.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Отмена", content)
        cancel.setObjectName("secondaryButton")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)
        self.surface_layout.addWidget(content, 1)


class OnvifDiscoveryDialog(FramelessDialog):
    """Список найденных устройств с выбором камер для добавления."""

    def __init__(
        self,
        cameras: tuple[DiscoveredCamera, ...],
        already_added_ips: set[str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            "Найденные камеры",
            parent,
            preferred_width=760,
            preferred_height=650,
        )
        self._rows: list[tuple[QCheckBox, DiscoveredCamera]] = []

        content = QWidget(self.surface)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 16, 22, 26)
        layout.setSpacing(14)

        title = QLabel("Камеры в локальной сети", content)
        title.setObjectName("dialogTitle")
        set_heading_capitalization(title)
        layout.addWidget(title)
        subtitle = QLabel(
            "Выберите камеры, которые нужно разместить на доске. "
            "Потоки, полученные через ONVIF, подключатся автоматически.",
            content,
        )
        subtitle.setObjectName("dialogSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        scroll = QScrollArea(content)
        scroll.setWidgetResizable(True)
        rows_widget = QWidget(scroll)
        rows_layout = QVBoxLayout(rows_widget)
        rows_layout.setContentsMargins(2, 2, 8, 2)
        rows_layout.setSpacing(9)
        for camera in cameras:
            duplicate = camera.ip in already_added_ips
            row = QFrame(rows_widget)
            row.setObjectName("discoveryRow")
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(14, 10, 14, 10)
            row_layout.setSpacing(14)

            check = QCheckBox(camera.name.replace("&", "&&"), row)
            check.setChecked(not duplicate)
            check.setEnabled(not duplicate)
            check.setMinimumWidth(210)
            check.toggled.connect(self._sync_add_enabled)
            row_layout.addWidget(check, 1)

            address = QLabel(camera.ip, row)
            address.setObjectName("discoveryAddress")
            address.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            row_layout.addWidget(address)

            status = QLabel(row)
            if duplicate:
                status.setText("●  Уже добавлена")
                status.setStyleSheet(f"color: {TEXT_MUTED};")
            elif camera.ready:
                status.setText("●  Подключится сразу")
                status.setStyleSheet(f"color: {SUCCESS};")
            else:
                status.setText("●  Нужен пароль")
                status.setStyleSheet(f"color: {WARNING};")
            status.setMinimumWidth(170)
            row_layout.addWidget(status)
            rows_layout.addWidget(row)
            self._rows.append((check, camera))
        rows_layout.addStretch(1)
        scroll.setWidget(rows_widget)
        layout.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Отмена", content)
        cancel.setObjectName("secondaryButton")
        cancel.clicked.connect(self.reject)
        self.add_button = QPushButton("Добавить выбранные", content)
        self.add_button.setObjectName("primaryButton")
        self.add_button.setDefault(True)
        self.add_button.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(self.add_button)
        layout.addLayout(buttons)
        self.surface_layout.addWidget(content, 1)
        self._sync_add_enabled()

    def _sync_add_enabled(self) -> None:
        self.add_button.setEnabled(any(check.isChecked() for check, _ in self._rows))

    def selected_cameras(self) -> tuple[DiscoveredCamera, ...]:
        return tuple(camera for check, camera in self._rows if check.isChecked())


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
        set_heading_capitalization(title)
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
        set_heading_capitalization(title)
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
