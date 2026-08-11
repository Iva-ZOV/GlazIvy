"""Перетаскиваемая плитка камеры с независимым RTSP-потоком."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPaintEvent, QPainter, QPen, QResizeEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from ..config import CameraConfig, CameraGeometry, ConfigError
from ..video import CameraReader
from .theme import SURFACE_RAISED
from .widgets import (
    LogoGlyph,
    SegmentedControl,
    StatusPill,
    ToolIconButton,
    VideoCanvas,
)


class CameraTile(QWidget):
    """Одна камера-стикер, ограниченная прямоугольником родительской доски."""

    raise_requested = Signal(str)
    layout_changed = Signal(str)
    settings_requested = Signal(str)
    quick_change_requested = Signal(str, str, str)

    MINIMUM_WIDTH = 520
    MINIMUM_HEIGHT = 320
    RESIZE_MARGIN = 8
    HEADER_HEIGHT = 54

    def __init__(self, config: CameraConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config
        self._generation = 0
        self._readers: dict[int, CameraReader] = {}
        self._current_reader: CameraReader | None = None
        self._shutting_down = False
        self._interaction: str | None = None
        self._resize_edges_value = Qt.Edge(0)
        self._press_global = QPoint()
        self._initial_geometry = QRect()
        self._drag_targets: set[QWidget] = set()

        self.setObjectName("cameraTile")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(self.MINIMUM_WIDTH, self.MINIMUM_HEIGHT)
        self.setMouseTracking(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(1, 1, 1, 1)
        root.setSpacing(0)

        self.header = QWidget(self)
        self.header.setObjectName("cameraTileHeader")
        self.header.setFixedHeight(self.HEADER_HEIGHT)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(12, 0, 8, 0)
        header_layout.setSpacing(7)

        self.logo = LogoGlyph(22, self.header)
        header_layout.addWidget(self.logo)
        self.name_label = QLabel(self.header)
        self.name_label.setObjectName("cameraTileName")
        self.name_label.setMinimumWidth(70)
        self.name_label.setMaximumWidth(190)
        self.name_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        header_layout.addWidget(self.name_label, 1)

        self.status = StatusPill(self.header)
        header_layout.addWidget(self.status)
        self.transport_control = SegmentedControl(
            ("TCP", "UDP"),
            ("tcp", "udp"),
            config.transport,
            self.header,
        )
        self.transport_control.setToolTip("Транспорт этой камеры")
        header_layout.addWidget(self.transport_control)
        self.quality_control = SegmentedControl(
            ("SD", "HD"),
            ("sd", "hd"),
            config.quality,
            self.header,
        )
        self.quality_control.setToolTip("Качество этой камеры")
        header_layout.addWidget(self.quality_control)
        self.settings_button = ToolIconButton(
            "settings",
            "Настройки камеры",
            self.header,
        )
        header_layout.addWidget(self.settings_button)
        root.addWidget(self.header)

        # VideoCanvas остаётся тем же рабочим виджетом. Меняется только его
        # минимальный размер, потому что теперь он находится внутри плитки.
        self.video = VideoCanvas(self)
        self.video.setMinimumSize(0, 0)
        self.video.unsetCursor()
        self.video.set_corner_radius(17.0)
        root.addWidget(self.video, 1)

        self.clock_label = QLabel(self.video)
        self.clock_label.setObjectName("tileClock")
        self.clock_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.clock_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.clock_label.setContentsMargins(9, 4, 9, 4)
        self.clock_label.raise_()

        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start()
        self._cleanup_timer = QTimer(self)
        self._cleanup_timer.setInterval(4000)
        self._cleanup_timer.timeout.connect(self._prune_readers)
        self._cleanup_timer.start()

        self.settings_button.clicked.connect(
            lambda: self.settings_requested.emit(self.config.camera_id)
        )
        self.quality_control.value_changed.connect(self._quality_changed)
        self.transport_control.value_changed.connect(self._transport_changed)
        self.video.double_clicked.connect(self._video_double_clicked)

        self._drag_targets = {
            self.header,
            self.logo,
            self.name_label,
            self.status,
        }
        self._install_pointer_filters()
        self._sync_config_widgets()
        QTimer.singleShot(0, self.restart_stream)

    @staticmethod
    def _connection_signature(config: CameraConfig) -> tuple[object, ...]:
        return (
            config.host,
            config.port,
            config.username,
            config.password,
            config.transport,
            config.quality,
            config.stream_path,
        )

    def _install_pointer_filters(self) -> None:
        for widget in (self, *self.findChildren(QWidget)):
            widget.setMouseTracking(True)
            widget.installEventFilter(self)

    def _event_position(self, event: QMouseEvent) -> QPoint:
        return self.mapFromGlobal(event.globalPosition().toPoint())

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        event_type = event.type()
        if event_type == QEvent.Type.MouseButtonPress:
            mouse_event = event  # type: ignore[assignment]
            if mouse_event.button() == Qt.MouseButton.LeftButton:  # type: ignore[attr-defined]
                position = self._event_position(mouse_event)  # type: ignore[arg-type]
                self.raise_requested.emit(self.config.camera_id)
                edges = self._resize_edges(position)
                if edges:
                    self._begin_interaction(
                        "resize",
                        mouse_event.globalPosition().toPoint(),  # type: ignore[attr-defined]
                        edges,
                    )
                    return True
                if watched in self._drag_targets:
                    self._begin_interaction(
                        "move",
                        mouse_event.globalPosition().toPoint(),  # type: ignore[attr-defined]
                    )
                    return True

        elif event_type == QEvent.Type.MouseMove:
            mouse_event = event  # type: ignore[assignment]
            if self._interaction is not None:
                self._continue_interaction(
                    mouse_event.globalPosition().toPoint()  # type: ignore[attr-defined]
                )
                return True
            self._update_resize_cursor(
                self._resize_edges(self._event_position(mouse_event))  # type: ignore[arg-type]
            )

        elif event_type == QEvent.Type.MouseButtonRelease:
            mouse_event = event  # type: ignore[assignment]
            if (
                self._interaction is not None
                and mouse_event.button() == Qt.MouseButton.LeftButton  # type: ignore[attr-defined]
            ):
                self._finish_interaction()
                return True

        elif event_type == QEvent.Type.MouseButtonDblClick:
            mouse_event = event  # type: ignore[assignment]
            if mouse_event.button() == Qt.MouseButton.LeftButton:  # type: ignore[attr-defined]
                self.raise_requested.emit(self.config.camera_id)

        return super().eventFilter(watched, event)

    def _resize_edges(self, position: QPoint) -> Qt.Edge:
        margin = self.RESIZE_MARGIN
        edges = Qt.Edge(0)
        if position.x() <= margin:
            edges |= Qt.Edge.LeftEdge
        elif position.x() >= self.width() - margin:
            edges |= Qt.Edge.RightEdge
        if position.y() <= margin:
            edges |= Qt.Edge.TopEdge
        elif position.y() >= self.height() - margin:
            edges |= Qt.Edge.BottomEdge
        return edges

    def _update_resize_cursor(self, edges: Qt.Edge) -> None:
        if edges in (
            Qt.Edge.LeftEdge | Qt.Edge.TopEdge,
            Qt.Edge.RightEdge | Qt.Edge.BottomEdge,
        ):
            cursor = Qt.CursorShape.SizeFDiagCursor
        elif edges in (
            Qt.Edge.RightEdge | Qt.Edge.TopEdge,
            Qt.Edge.LeftEdge | Qt.Edge.BottomEdge,
        ):
            cursor = Qt.CursorShape.SizeBDiagCursor
        elif edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge):
            cursor = Qt.CursorShape.SizeHorCursor
        elif edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge):
            cursor = Qt.CursorShape.SizeVerCursor
        else:
            self.unsetCursor()
            return
        self.setCursor(cursor)

    def _begin_interaction(
        self,
        mode: str,
        global_position: QPoint,
        edges: Qt.Edge = Qt.Edge(0),
    ) -> None:
        self._interaction = mode
        self._resize_edges_value = edges
        self._press_global = global_position
        self._initial_geometry = self.geometry()
        self.grabMouse()

    def _continue_interaction(self, global_position: QPoint) -> None:
        board = self.parentWidget()
        if board is None:
            return
        delta = global_position - self._press_global
        initial = self._initial_geometry
        board_width = max(1, board.width())
        board_height = max(1, board.height())

        if self._interaction == "move":
            x = max(0, min(initial.x() + delta.x(), board_width - initial.width()))
            y = max(0, min(initial.y() + delta.y(), board_height - initial.height()))
            self.move(x, y)
            return

        if self._interaction != "resize":
            return
        x = initial.x()
        y = initial.y()
        right = initial.x() + initial.width()
        bottom = initial.y() + initial.height()
        edges = self._resize_edges_value

        if edges & Qt.Edge.LeftEdge:
            x = max(0, min(initial.x() + delta.x(), right - self.MINIMUM_WIDTH))
        if edges & Qt.Edge.RightEdge:
            right = max(
                x + self.MINIMUM_WIDTH,
                min(initial.x() + initial.width() + delta.x(), board_width),
            )
        if edges & Qt.Edge.TopEdge:
            y = max(0, min(initial.y() + delta.y(), bottom - self.MINIMUM_HEIGHT))
        if edges & Qt.Edge.BottomEdge:
            bottom = max(
                y + self.MINIMUM_HEIGHT,
                min(initial.y() + initial.height() + delta.y(), board_height),
            )
        self.setGeometry(x, y, right - x, bottom - y)

    def _finish_interaction(self) -> None:
        changed = self.geometry() != self._initial_geometry
        self._interaction = None
        self._resize_edges_value = Qt.Edge(0)
        self.releaseMouse()
        self.unsetCursor()
        if changed:
            self.layout_changed.emit(self.config.camera_id)

    def constrain_to_parent(self) -> bool:
        board = self.parentWidget()
        if board is None or board.width() <= 0 or board.height() <= 0:
            return False
        before = self.geometry()
        width = min(max(self.MINIMUM_WIDTH, before.width()), board.width())
        height = min(max(self.MINIMUM_HEIGHT, before.height()), board.height())
        x = max(0, min(before.x(), board.width() - width))
        y = max(0, min(before.y(), board.height() - height))
        self.setGeometry(x, y, width, height)
        return self.geometry() != before

    def snapshot_config(
        self,
        z_index: int,
        config: CameraConfig | None = None,
    ) -> CameraConfig:
        source = config or self.config
        rect = self.geometry()
        return source.updated(
            geometry=CameraGeometry(
                x=rect.x(),
                y=rect.y(),
                width=rect.width(),
                height=rect.height(),
                z=z_index,
            )
        )

    def _sync_config_widgets(self) -> None:
        self.name_label.setText(self.config.camera_name)
        self.name_label.setToolTip(self.config.camera_name)
        self.quality_control.set_value(self.config.quality, animate=True)
        self.transport_control.set_value(self.config.transport, animate=True)
        self.clock_label.setVisible(self.config.show_clock)
        self._update_clock()

    def apply_config(self, config: CameraConfig) -> None:
        previous = self.config
        reconnect = self._connection_signature(previous) != self._connection_signature(config)
        self.config = config
        self._sync_config_widgets()
        if reconnect or (config.is_configured() and self._current_reader is None):
            self.restart_stream()

    def restore_quick_controls(self) -> None:
        self.quality_control.set_value(self.config.quality, animate=True)
        self.transport_control.set_value(self.config.transport, animate=True)

    def _quality_changed(self, quality: str) -> None:
        if quality != self.config.quality:
            self.quick_change_requested.emit(
                self.config.camera_id,
                "quality",
                quality,
            )

    def _transport_changed(self, transport: str) -> None:
        if transport != self.config.transport:
            self.quick_change_requested.emit(
                self.config.camera_id,
                "transport",
                transport,
            )

    def _video_double_clicked(self) -> None:
        if not self.config.is_configured():
            self.settings_requested.emit(self.config.camera_id)

    def restart_stream(self) -> None:
        if self._shutting_down:
            return
        if self._current_reader is not None:
            self._current_reader.stop()
            self._current_reader = None

        self._generation += 1
        generation = self._generation
        if not self.config.is_configured():
            self.status.set_state("unconfigured")
            self.video.set_stream_state(
                "unconfigured",
                "Откройте настройки по шестерёнке и введите RTSP-данные.",
            )
            return

        try:
            url = self.config.build_rtsp_url()
        except ConfigError as exc:
            self.status.set_state("offline")
            self.video.set_stream_state("offline", str(exc))
            return

        detail = (
            "HD-поток запускается — это может занять до 60 секунд"
            if self.config.quality == "hd"
            else "Открываем SD-поток"
        )
        self.status.set_state("connecting")
        self.video.set_stream_state("connecting", detail)

        reader = CameraReader(
            url=url,
            transport=self.config.transport,
            quality=self.config.quality,
            generation=generation,
        )
        reader.signals.frame_ready.connect(self._on_frame)
        reader.signals.state_changed.connect(self._on_state_changed)
        reader.signals.finished.connect(self._on_reader_finished)
        self._readers[generation] = reader
        self._current_reader = reader
        reader.start()

    def _on_frame(self, image: object, generation: int) -> None:
        if generation != self._generation or self._shutting_down:
            return
        self.video.set_frame(image)

    def _on_state_changed(self, state: str, detail: str, generation: int) -> None:
        if generation != self._generation or self._shutting_down:
            return
        self.status.set_state(state)
        self.video.set_stream_state(state, detail)

    def _on_reader_finished(self, generation: int) -> None:
        reader = self._readers.get(generation)
        if reader is not None and not reader.is_alive():
            self._readers.pop(generation, None)
        if generation == self._generation and reader is self._current_reader:
            self._current_reader = None

    def _prune_readers(self) -> None:
        for generation, reader in list(self._readers.items()):
            if generation != self._generation and not reader.is_alive():
                self._readers.pop(generation, None)

    def _update_clock(self) -> None:
        self.clock_label.setText(datetime.now().strftime("%H:%M:%S  ·  %d.%m.%Y"))
        self.clock_label.adjustSize()
        self._position_clock()

    def _position_clock(self) -> None:
        margin = 12
        self.clock_label.move(
            max(margin, self.video.width() - self.clock_label.width() - margin),
            margin,
        )
        self.clock_label.raise_()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "clock_label"):
            self._position_clock()

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(QColor(255, 255, 255, 38), 1))
        painter.setBrush(QColor(SURFACE_RAISED))
        painter.drawRoundedRect(rect, 18, 18)

        # Ненавязчивый маркер правого нижнего угла подсказывает про ресайз.
        painter.setPen(QPen(QColor(255, 255, 255, 55), 1.2))
        for offset in (7, 11):
            painter.drawLine(
                self.width() - offset,
                self.height() - 3,
                self.width() - 3,
                self.height() - offset,
            )

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self._generation += 1
        self._clock_timer.stop()
        self._cleanup_timer.stop()
        if self._interaction is not None:
            self._interaction = None
            self.releaseMouse()
        for reader in self._readers.values():
            reader.stop()
        self._current_reader = None
