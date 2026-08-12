"""Безрамочные диалоги настроек доски и отдельных камер."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, QTime, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QShowEvent,
)
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
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ..config import CameraConfig, DEFAULT_NIGHT_END, DEFAULT_NIGHT_START
from ..onvif import DiscoveredCamera
from ..resources import application_icon
from .forms import CameraForm
from .night_mode import NightModeController, controller_from_parent
from .theme import BRONZE, SUCCESS, TEXT_MUTED, WARNING
from .widgets import (
    GrainFrame,
    LogoGlyph,
    ToolIconButton,
    _mascot_pixmap,
    set_action_button_capitalization,
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
        layout.addWidget(LogoGlyph(30, self))
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

        self.night_mode_controller: NightModeController | None = (
            controller_from_parent(parent)
        )
        # Временно поднятые parent-диалоги не должны оказаться поверх уже
        # действующего затемнения главного окна.
        if self.night_mode_controller is not None:
            self.night_mode_controller.raise_all_overlays()
        self._night_overlay = (
            self.night_mode_controller.register_window(self, self.surface)
            if self.night_mode_controller is not None
            else None
        )

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if self.night_mode_controller is not None:
            controller = self.night_mode_controller
            QTimer.singleShot(0, lambda: controller.raise_overlay(self))
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

    def done(self, result: int) -> None:
        super().done(result)
        if self.night_mode_controller is not None:
            self.night_mode_controller.unregister_window(self)
            self._night_overlay = None


def _dialog_mascot(
    kind: str,
    max_size: QSize,
    parent: QWidget,
) -> QLabel:
    label = QLabel(parent)
    label.setFixedSize(max_size)
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    pixmap = _mascot_pixmap(kind, max_size, parent.devicePixelRatioF())
    if pixmap is None:
        label.hide()
    else:
        label.setPixmap(pixmap)
    return label


class OnvifProgressDialog(FramelessDialog):
    """Неблокирующий индикатор фонового WS-Discovery/ONVIF-запроса."""

    _SCAN_INTERVAL_MS = 425
    _SCAN_FRAME_SIZE = QSize(118, 160)
    _SCAN_SEQUENCE = (
        "scan_1",
        "scan_2",
        "scan_3",
        "scan_4",
        "scan_3",
        "scan_2",
    )

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
            preferred_height=520,
        )
        content = QWidget(self.surface)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(30, 18, 26, 24)
        layout.setSpacing(13)

        self._scan_frame_index = 0
        self._scan_label = QLabel(content)
        self._scan_label.setObjectName("scanMascot")
        self._scan_label.setFixedSize(self._SCAN_FRAME_SIZE)
        self._scan_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(
            self._scan_label,
            0,
            Qt.AlignmentFlag.AlignHCenter,
        )

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
        set_action_button_capitalization(cancel)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)
        self.surface_layout.addWidget(content, 1)

        self._scan_timer = QTimer(self)
        self._scan_timer.setInterval(self._SCAN_INTERVAL_MS)
        self._scan_timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._scan_timer.timeout.connect(self._advance_scan_frame)
        self._show_scan_frame()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._scan_frame_index = 0
        self._show_scan_frame()
        self._scan_timer.start()

    def done(self, result: int) -> None:
        self._scan_timer.stop()
        super().done(result)

    def _advance_scan_frame(self) -> None:
        self._scan_frame_index = (
            self._scan_frame_index + 1
        ) % len(self._SCAN_SEQUENCE)
        self._show_scan_frame()

    def _show_scan_frame(self) -> None:
        frame = self._SCAN_SEQUENCE[self._scan_frame_index]
        pixmap = _mascot_pixmap(
            frame,
            self._SCAN_FRAME_SIZE,
            self._scan_label.devicePixelRatioF(),
        )
        if pixmap is None:
            self._scan_label.clear()
            return
        self._scan_label.setPixmap(pixmap)


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
        set_action_button_capitalization(cancel)
        cancel.clicked.connect(self.reject)
        self.add_button = QPushButton("Добавить выбранные", content)
        self.add_button.setObjectName("primaryButton")
        set_action_button_capitalization(self.add_button)
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


class _CameraListGrip(QWidget):
    drag_pressed = Signal(QPoint)
    drag_moved = Signal(QPoint)
    drag_released = Signal(QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pressed = False
        self.setFixedSize(24, 36)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("Перетащить камеру")
        self.setAccessibleName("Изменить порядок камеры")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.grabMouse()
            self.drag_pressed.emit(event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._pressed:
            self.drag_moved.emit(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._pressed and event.button() == Qt.MouseButton.LeftButton:
            self._pressed = False
            self.releaseMouse()
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.drag_released.emit(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(BRONZE)
        color.setAlpha(175)
        painter.setPen(QPen(color, 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        for x in (9, 15):
            for y in (12, 18, 24):
                painter.drawPoint(x, y)


class CameraListDialog(FramelessDialog):
    """Полный список камер с мгновенным управлением видимостью на доске."""

    toggle_requested = Signal(str, bool)
    settings_requested = Signal(str)
    order_changed = Signal(tuple)

    def __init__(
        self,
        cameras: tuple[CameraConfig, ...],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            "Список камер",
            parent,
            preferred_width=720,
            preferred_height=560,
        )
        self._cameras: tuple[CameraConfig, ...] = ()
        self._checks: dict[str, QCheckBox] = {}
        self._row_widgets: list[tuple[str, QFrame]] = []
        self._rows_widget: QWidget | None = None
        self._drop_indicator: QFrame | None = None
        self._drag_camera_id: str | None = None
        self._drag_origin = QPoint()
        self._drag_target_index: int | None = None
        self._dragging = False

        content = QWidget(self.surface)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 16, 22, 26)
        layout.setSpacing(14)

        intro = QHBoxLayout()
        intro.setSpacing(16)
        intro_copy = QVBoxLayout()
        intro_copy.setSpacing(8)

        title = QLabel("Все камеры", content)
        title.setObjectName("dialogTitle")
        set_heading_capitalization(title)
        intro_copy.addWidget(title)

        subtitle = QLabel(
            "Перетаскивайте строки, чтобы менять порядок камер. Галочка "
            "управляет показом на доске; скрытая камера остаётся в списке.",
            content,
        )
        subtitle.setObjectName("dialogSubtitle")
        subtitle.setWordWrap(True)
        intro_copy.addWidget(subtitle)
        intro_copy.addStretch(1)
        intro.addLayout(intro_copy, 1)
        self.mascot = _dialog_mascot("list", QSize(104, 124), content)
        intro.addWidget(
            self.mascot,
            0,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
        )
        layout.addLayout(intro)

        self.scroll = QScrollArea(content)
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.scroll, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        done = QPushButton("Готово", content)
        done.setObjectName("secondaryButton")
        set_action_button_capitalization(done)
        done.setDefault(True)
        done.clicked.connect(self.accept)
        buttons.addWidget(done)
        layout.addLayout(buttons)

        self.surface_layout.addWidget(content, 1)
        self.refresh(cameras)

    def refresh(self, cameras: tuple[CameraConfig, ...]) -> None:
        self._cameras = tuple(cameras)
        self._cancel_drag()
        self._populate_rows()

    def _populate_rows(self) -> None:
        old_rows = self.scroll.takeWidget()
        if old_rows is not None:
            old_rows.deleteLater()

        rows_widget = QWidget(self.scroll)
        rows_layout = QVBoxLayout(rows_widget)
        rows_layout.setContentsMargins(2, 2, 8, 2)
        rows_layout.setSpacing(9)
        self._checks = {}
        self._row_widgets = []
        self._rows_widget = rows_widget

        if not self._cameras:
            empty = QLabel("Пока нет камер", rows_widget)
            empty.setObjectName("discoveryAddress")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            rows_layout.addWidget(empty, 1)
        else:
            for camera in self._cameras:
                row = QFrame(rows_widget)
                row.setObjectName("discoveryRow")
                row_layout = QHBoxLayout(row)
                row_layout.setContentsMargins(14, 10, 14, 10)
                row_layout.setSpacing(14)

                grip = _CameraListGrip(row)
                grip.drag_pressed.connect(
                    lambda position, camera_id=camera.camera_id: (
                        self._drag_pressed(camera_id, position)
                    )
                )
                grip.drag_moved.connect(
                    lambda position, camera_id=camera.camera_id: (
                        self._drag_moved(camera_id, position)
                    )
                )
                grip.drag_released.connect(
                    lambda position, camera_id=camera.camera_id: (
                        self._drag_released(camera_id, position)
                    )
                )
                row_layout.addWidget(grip)

                check = QCheckBox(camera.camera_name.replace("&", "&&"), row)
                check.setChecked(camera.on_board)
                check.setMinimumWidth(210)
                check.toggled.connect(
                    lambda checked, camera_id=camera.camera_id: (
                        self.toggle_requested.emit(camera_id, checked)
                    )
                )
                row_layout.addWidget(check, 1)
                self._checks[camera.camera_id] = check

                address = QLabel(
                    camera.host.strip() if camera.is_configured() else "не настроена",
                    row,
                )
                address.setObjectName("discoveryAddress")
                address.setTextInteractionFlags(
                    Qt.TextInteractionFlag.TextSelectableByMouse
                )
                row_layout.addWidget(address)

                settings = ToolIconButton(
                    "settings",
                    "Настройки камеры",
                    row,
                )
                settings.clicked.connect(
                    lambda _checked=False, camera_id=camera.camera_id: (
                        self.settings_requested.emit(camera_id)
                    )
                )
                row_layout.addWidget(settings)
                rows_layout.addWidget(row)
                self._row_widgets.append((camera.camera_id, row))
            rows_layout.addStretch(1)

        self._drop_indicator = QFrame(rows_widget)
        self._drop_indicator.setStyleSheet(
            f"background-color: {BRONZE}; border: none;"
        )
        self._drop_indicator.setFixedHeight(3)
        self._drop_indicator.hide()
        self.scroll.setWidget(rows_widget)

    def _cancel_drag(self) -> None:
        self._drag_camera_id = None
        self._drag_target_index = None
        self._dragging = False
        if self._drop_indicator is not None:
            self._drop_indicator.hide()

    def _drag_pressed(self, camera_id: str, position: QPoint) -> None:
        if not any(row_id == camera_id for row_id, _ in self._row_widgets):
            return
        self._drag_camera_id = camera_id
        self._drag_origin = position
        self._drag_target_index = None
        self._dragging = False

    def _drag_moved(self, camera_id: str, position: QPoint) -> None:
        if camera_id != self._drag_camera_id:
            return
        if not self._dragging:
            distance = (position - self._drag_origin).manhattanLength()
            if distance < QApplication.startDragDistance():
                return
            self._dragging = True
        self._update_drop_target(position)

    def _drag_released(self, camera_id: str, position: QPoint) -> None:
        if camera_id != self._drag_camera_id:
            return
        was_dragging = self._dragging
        if was_dragging:
            self._update_drop_target(position)
        target_index = self._drag_target_index
        self._cancel_drag()
        if not was_dragging or target_index is None:
            return

        cameras = list(self._cameras)
        source_index = next(
            (
                index
                for index, camera in enumerate(cameras)
                if camera.camera_id == camera_id
            ),
            None,
        )
        if source_index is None:
            return
        camera = cameras.pop(source_index)
        target_index = max(0, min(target_index, len(cameras)))
        cameras.insert(target_index, camera)
        reordered = tuple(cameras)
        if reordered == self._cameras:
            return
        self._cameras = reordered
        self._populate_rows()
        self.order_changed.emit(
            tuple(camera.camera_id for camera in self._cameras)
        )

    def _update_drop_target(self, global_position: QPoint) -> None:
        rows_widget = self._rows_widget
        indicator = self._drop_indicator
        camera_id = self._drag_camera_id
        if rows_widget is None or indicator is None or camera_id is None:
            return

        viewport = self.scroll.viewport()
        viewport_position = viewport.mapFromGlobal(global_position)
        scroll_bar = self.scroll.verticalScrollBar()
        edge = 28
        if viewport_position.y() < edge:
            scroll_bar.setValue(scroll_bar.value() - 18)
        elif viewport_position.y() > viewport.height() - edge:
            scroll_bar.setValue(scroll_bar.value() + 18)

        position = rows_widget.mapFromGlobal(global_position)
        other_rows = [
            row
            for row_id, row in self._row_widgets
            if row_id != camera_id
        ]
        target_index = sum(
            row.geometry().center().y() < position.y()
            for row in other_rows
        )
        self._drag_target_index = target_index
        if not other_rows:
            indicator.hide()
            return

        if target_index == 0:
            line_y = other_rows[0].geometry().top() - 5
        elif target_index == len(other_rows):
            line_y = other_rows[-1].geometry().bottom() + 5
        else:
            line_y = (
                other_rows[target_index - 1].geometry().bottom()
                + other_rows[target_index].geometry().top()
            ) // 2
        indicator.setGeometry(
            10,
            max(1, line_y - 1),
            max(1, rows_widget.width() - 28),
            3,
        )
        indicator.raise_()
        indicator.show()

    def set_camera_on_board(self, camera_id: str, on_board: bool) -> None:
        check = self._checks.get(camera_id)
        if check is None or check.isChecked() == on_board:
            return
        check.blockSignals(True)
        try:
            check.setChecked(on_board)
        finally:
            check.blockSignals(False)


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

        intro = QHBoxLayout()
        intro.setSpacing(16)
        intro_copy = QVBoxLayout()
        intro_copy.setSpacing(8)

        title = QLabel("Параметры камеры", content)
        title.setObjectName("dialogTitle")
        set_heading_capitalization(title)
        intro_copy.addWidget(title)
        subtitle = QLabel(
            "Изменения адреса, транспорта или потока применятся сразу после сохранения.",
            content,
        )
        subtitle.setObjectName("dialogSubtitle")
        subtitle.setWordWrap(True)
        intro_copy.addWidget(subtitle)
        intro_copy.addStretch(1)
        intro.addLayout(intro_copy, 1)
        self.mascot = _dialog_mascot("wrench", QSize(104, 124), content)
        intro.addWidget(
            self.mascot,
            0,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
        )
        content_layout.addLayout(intro)

        scroll = QScrollArea(content)
        scroll.setWidgetResizable(True)
        self.form = CameraForm(config, scroll)
        scroll.setWidget(self.form)
        content_layout.addWidget(scroll, 1)

        buttons = QHBoxLayout()
        delete_button = QPushButton("Удалить камеру", content)
        delete_button.setObjectName("dangerButton")
        set_action_button_capitalization(delete_button)
        delete_button.clicked.connect(self._request_delete)
        buttons.addWidget(delete_button)
        buttons.addStretch(1)
        cancel = QPushButton("Отмена", content)
        cancel.setObjectName("secondaryButton")
        set_action_button_capitalization(cancel)
        cancel.clicked.connect(self.reject)
        save = QPushButton("Сохранить", content)
        save.setObjectName("primaryButton")
        set_action_button_capitalization(save)
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

    def __init__(
        self,
        autostart: bool,
        parent: QWidget | None = None,
        *,
        night_mode: bool = False,
        night_start: str = DEFAULT_NIGHT_START,
        night_end: str = DEFAULT_NIGHT_END,
    ) -> None:
        super().__init__(
            "Настройки доски",
            parent,
            preferred_width=620,
            preferred_height=500,
        )
        self.result_autostart: bool | None = None
        self.result_night_mode: bool | None = None
        self.result_night_start: str | None = None
        self.result_night_end: str | None = None

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

        night_row = QFrame(content)
        night_row.setObjectName("settingsRow")
        night_layout = QHBoxLayout(night_row)
        night_layout.setContentsMargins(14, 9, 12, 9)
        night_layout.setSpacing(9)
        self.night_mode_check = QCheckBox("Ночник", night_row)
        self.night_mode_check.setChecked(night_mode)
        self.night_mode_check.setToolTip(
            "В выбранные часы всё приложение плавно затемняется до 20% яркости."
        )
        night_layout.addWidget(self.night_mode_check)
        night_layout.addStretch(1)

        start_label = QLabel("с", night_row)
        start_label.setObjectName("fieldLabel")
        night_layout.addWidget(start_label)
        self.night_start_edit = QTimeEdit(night_row)
        self.night_start_edit.setDisplayFormat("HH:mm")
        start_time = QTime.fromString(night_start, "HH:mm")
        self.night_start_edit.setTime(
            start_time if start_time.isValid() else QTime(22, 0)
        )
        self.night_start_edit.setFixedWidth(88)
        self.night_start_edit.setAccessibleName("Начало ночного режима")
        night_layout.addWidget(self.night_start_edit)

        end_label = QLabel("до", night_row)
        end_label.setObjectName("fieldLabel")
        night_layout.addWidget(end_label)
        self.night_end_edit = QTimeEdit(night_row)
        self.night_end_edit.setDisplayFormat("HH:mm")
        end_time = QTime.fromString(night_end, "HH:mm")
        self.night_end_edit.setTime(
            end_time if end_time.isValid() else QTime(6, 0)
        )
        self.night_end_edit.setFixedWidth(88)
        self.night_end_edit.setAccessibleName("Окончание ночного режима")
        night_layout.addWidget(self.night_end_edit)
        layout.addWidget(night_row)
        self.night_mode_check.toggled.connect(self._sync_night_fields)
        self._sync_night_fields(night_mode)
        layout.addStretch(1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Отмена", content)
        cancel.setObjectName("secondaryButton")
        set_action_button_capitalization(cancel)
        cancel.clicked.connect(self.reject)
        save = QPushButton("Сохранить", content)
        save.setObjectName("primaryButton")
        set_action_button_capitalization(save)
        save.setDefault(True)
        save.clicked.connect(self._accept_settings)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        self.surface_layout.addWidget(content, 1)

    def _sync_night_fields(self, enabled: bool) -> None:
        self.night_start_edit.setEnabled(enabled)
        self.night_end_edit.setEnabled(enabled)

    def _accept_settings(self) -> None:
        self.result_autostart = self.autostart_check.isChecked()
        self.result_night_mode = self.night_mode_check.isChecked()
        self.result_night_start = self.night_start_edit.time().toString("HH:mm")
        self.result_night_end = self.night_end_edit.time().toString("HH:mm")
        self.accept()
