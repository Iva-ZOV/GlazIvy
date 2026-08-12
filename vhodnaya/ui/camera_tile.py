"""Перетаскиваемая плитка камеры с независимым RTSP-потоком."""

from __future__ import annotations

import math

from PySide6.QtCore import (
    QEvent,
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    QTimer,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCursor,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPen,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..config import CameraConfig, CameraGeometry, ConfigError
from ..video import CameraReader
from .theme import BRONZE, SURFACE_RAISED
from .widgets import (
    LogoGlyph,
    StatusPill,
    ToolIconButton,
    VideoCanvas,
)


class CameraTile(QWidget):
    """Одна камера-стикер, ограниченная прямоугольником родительской доски."""

    raise_requested = Signal(str)
    layout_changed = Signal(str)
    settings_requested = Signal(str)
    hide_requested = Signal(str)
    expand_requested = Signal(str)

    MINIMUM_WIDTH = 180
    MINIMUM_HEIGHT = 120
    RESIZE_MARGIN = 8
    HEADER_HEIGHT = 54
    HEADER_IDLE_TIMEOUT_MS = 2800
    NAME_VISIBLE_MINIMUM_WIDTH = 300
    LOGO_VISIBLE_MINIMUM_WIDTH = 230
    FALLBACK_VIDEO_ASPECT = 16 / 9

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
        self._header_target_visible = False
        self._status_target_visible = True
        self._layout_interaction_enabled = True
        self._interaction_minimum_width = self.MINIMUM_WIDTH
        self._interaction_minimum_height = self.MINIMUM_HEIGHT
        self._expanded = False
        self._highlight_opacity = 0.0

        self.setObjectName("cameraTile")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(self.MINIMUM_WIDTH, self.MINIMUM_HEIGHT)
        self.setMouseTracking(True)

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(1, 1, 1, 1)
        self._root_layout.setSpacing(0)

        self.header = QWidget(self)
        self.header.setObjectName("cameraTileHeader")
        self.header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.header.setFixedHeight(self.HEADER_HEIGHT)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(12, 0, 8, 0)
        header_layout.setSpacing(7)

        self.logo = LogoGlyph(28, self.header)
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

        self.status = StatusPill(self)
        self.reconnect_button = ToolIconButton(
            "reconnect",
            "Переподключить",
            self.header,
        )
        header_layout.addWidget(self.reconnect_button)
        self.hide_button = ToolIconButton(
            "hide",
            "Скрыть с доски",
            self.header,
        )
        header_layout.addWidget(self.hide_button)
        self.settings_button = ToolIconButton(
            "settings",
            "Настройки камеры",
            self.header,
        )
        header_layout.addWidget(self.settings_button)

        self._header_effect = QGraphicsOpacityEffect(self.header)
        self._header_effect.setOpacity(0.0)
        self.header.setGraphicsEffect(self._header_effect)
        self._header_animation = QPropertyAnimation(
            self._header_effect,
            b"opacity",
            self,
        )
        self._header_animation.setDuration(180)
        self._header_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._header_animation.finished.connect(self._header_animation_finished)
        self.header.hide()

        self._header_idle_timer = QTimer(self)
        self._header_idle_timer.setSingleShot(True)
        self._header_idle_timer.setInterval(self.HEADER_IDLE_TIMEOUT_MS)
        self._header_idle_timer.timeout.connect(self._header_idle_timeout)

        self._highlight_animation = QVariantAnimation(self)
        self._highlight_animation.setDuration(150)
        self._highlight_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._highlight_animation.valueChanged.connect(self._set_highlight_opacity)

        # VideoCanvas занимает всю плитку, а шапка и индикаторы лежат поверх него.
        self.video = VideoCanvas(self)
        self.video.setMinimumSize(0, 0)
        self.video.unsetCursor()
        self.video.set_corner_radius(4.0)
        self._root_layout.addWidget(self.video, 1)

        self._status_effect = QGraphicsOpacityEffect(self.status)
        self._status_effect.setOpacity(1.0)
        self.status.setGraphicsEffect(self._status_effect)
        self._status_animation = QPropertyAnimation(
            self._status_effect,
            b"opacity",
            self,
        )
        self._status_animation.setDuration(180)
        self._status_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._status_animation.finished.connect(self._status_animation_finished)

        self._cleanup_timer = QTimer(self)
        self._cleanup_timer.setInterval(4000)
        self._cleanup_timer.timeout.connect(self._prune_readers)
        self._cleanup_timer.start()

        self.settings_button.clicked.connect(
            lambda: self.settings_requested.emit(self.config.camera_id)
        )
        self.hide_button.clicked.connect(
            lambda: self.hide_requested.emit(self.config.camera_id)
        )
        self.reconnect_button.clicked.connect(self.restart_stream)
        self.video.double_clicked.connect(self._video_double_clicked)

        self._drag_targets = {
            self.header,
            self.logo,
            self.name_label,
            self.status,
        }
        self._install_pointer_filters()
        self._sync_config_widgets()
        self._sync_header_content_visibility()
        self._position_overlays()
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
            config.source,
            config.stream_url_hd,
            config.stream_url_sd,
            config.onvif_endpoint,
            config.onvif_media_endpoint,
            config.onvif_username,
            config.onvif_password,
        )

    def _install_pointer_filters(self) -> None:
        for widget in (self, *self.findChildren(QWidget)):
            widget.setMouseTracking(True)
            widget.installEventFilter(self)

    def _event_position(self, event: QMouseEvent) -> QPoint:
        return self.mapFromGlobal(event.globalPosition().toPoint())

    def _pointer_inside(self) -> bool:
        return self.rect().contains(self.mapFromGlobal(QCursor.pos()))

    def _pointer_over_header(self) -> bool:
        position = self.header.mapFromGlobal(QCursor.pos())
        return self.header.isVisible() and self.header.rect().contains(position)

    def _set_status_visible(
        self,
        visible: bool,
        *,
        animate: bool = True,
    ) -> None:
        if visible == self._status_target_visible:
            return

        self._status_target_visible = visible
        target_opacity = 1.0 if visible else 0.0
        self._status_animation.stop()

        if visible:
            self.status.show()
            self.status.raise_()

        if not animate or abs(self._status_effect.opacity() - target_opacity) < 0.01:
            self._status_effect.setOpacity(target_opacity)
            self._status_animation_finished()
        else:
            self._status_animation.setStartValue(self._status_effect.opacity())
            self._status_animation.setEndValue(target_opacity)
            self._status_animation.start()

    def _status_animation_finished(self) -> None:
        if self._status_target_visible:
            self._status_effect.setOpacity(1.0)
            self.status.show()
            self.status.raise_()
            return
        self._status_effect.setOpacity(0.0)
        self.status.hide()

    def _register_header_activity(self) -> None:
        if self._shutting_down:
            return
        self._set_header_visible(True)
        self._header_idle_timer.start()

    def _header_idle_timeout(self) -> None:
        if (
            self._shutting_down
            or self._interaction is not None
            or self._pointer_over_header()
        ):
            return
        self._set_header_visible(False)

    def _set_header_visible(self, visible: bool, *, animate: bool = True) -> None:
        if not visible and self._interaction is not None:
            return
        self._set_highlight_visible(visible and not self._expanded, animate=animate)
        if visible == self._header_target_visible:
            if visible:
                self.header.raise_()
            return

        self._header_target_visible = visible
        self._header_animation.stop()
        self._set_status_visible(not visible, animate=animate)
        target_opacity = 1.0 if visible else 0.0
        if visible:
            self.header.setEnabled(True)
            self.header.show()
            self.header.raise_()
        elif self.header.isVisible():
            self.header.raise_()

        if not animate or abs(self._header_effect.opacity() - target_opacity) < 0.01:
            self._header_effect.setOpacity(target_opacity)
            self._header_animation_finished()
            return

        self._header_animation.setStartValue(self._header_effect.opacity())
        self._header_animation.setEndValue(target_opacity)
        self._header_animation.start()

    def _set_highlight_visible(self, visible: bool, *, animate: bool = True) -> None:
        target_opacity = 1.0 if visible else 0.0
        self._highlight_animation.stop()
        if not animate or abs(self._highlight_opacity - target_opacity) < 0.01:
            self._set_highlight_opacity(target_opacity)
            return
        self._highlight_animation.setStartValue(self._highlight_opacity)
        self._highlight_animation.setEndValue(target_opacity)
        self._highlight_animation.start()

    def _set_highlight_opacity(self, value: object) -> None:
        self._highlight_opacity = float(value)
        self.update()

    def _header_animation_finished(self) -> None:
        if self._header_target_visible:
            self._header_effect.setOpacity(1.0)
            self.header.show()
            self.header.raise_()
            return
        self._header_effect.setOpacity(0.0)
        self.header.setEnabled(False)
        self.header.hide()

    def _sync_header_hover(self) -> None:
        if self._shutting_down:
            return
        if self._pointer_inside():
            self._register_header_activity()
            return
        self._header_idle_timer.stop()
        self._set_header_visible(False)

    def _position_overlays(self) -> None:
        self.header.setGeometry(1, 1, max(0, self.width() - 2), self.HEADER_HEIGHT)
        self.status.adjustSize()
        self.status.move(12, 12)
        self.status.raise_()
        if self.header.isVisible():
            self.header.raise_()

    def _sync_header_content_visibility(self) -> None:
        self.name_label.setVisible(self.width() >= self.NAME_VISIBLE_MINIMUM_WIDTH)
        self.logo.setVisible(self.width() >= self.LOGO_VISIBLE_MINIMUM_WIDTH)

    def eventFilter(self, watched: object, event: QEvent) -> bool:
        event_type = event.type()
        if event_type in (
            QEvent.Type.Enter,
            QEvent.Type.MouseMove,
            QEvent.Type.MouseButtonPress,
        ):
            self._register_header_activity()
        elif event_type == QEvent.Type.Leave:
            QTimer.singleShot(0, self._sync_header_hover)

        if event_type == QEvent.Type.MouseButtonPress:
            mouse_event = event  # type: ignore[assignment]
            if mouse_event.button() == Qt.MouseButton.LeftButton:  # type: ignore[attr-defined]
                position = self._event_position(mouse_event)  # type: ignore[arg-type]
                self.raise_requested.emit(self.config.camera_id)
                edges = (
                    self._resize_edges(position)
                    if self._layout_interaction_enabled
                    else Qt.Edge(0)
                )
                if edges:
                    self._begin_interaction(
                        "resize",
                        mouse_event.globalPosition().toPoint(),  # type: ignore[attr-defined]
                        edges,
                    )
                    return True
                if self._layout_interaction_enabled and watched in self._drag_targets:
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
            if self._layout_interaction_enabled:
                self._update_resize_cursor(
                    self._resize_edges(self._event_position(mouse_event))  # type: ignore[arg-type]
                )
            else:
                self.unsetCursor()

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
        if not self._layout_interaction_enabled:
            return Qt.Edge(0)
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
        if not self._layout_interaction_enabled:
            return
        self._set_header_visible(True)
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
        edges = self._resize_edges_value
        horizontal = bool(edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge))
        vertical = bool(edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge))
        if not horizontal and not vertical:
            return

        aspect = self.video.frame_aspect() or self.FALLBACK_VIDEO_ASPECT
        if not math.isfinite(aspect) or aspect <= 0.0:
            aspect = self.FALLBACK_VIDEO_ASPECT

        requested_width = float(initial.width())
        requested_height = float(initial.height())
        if edges & Qt.Edge.LeftEdge:
            requested_width -= delta.x()
        elif edges & Qt.Edge.RightEdge:
            requested_width += delta.x()
        if edges & Qt.Edge.TopEdge:
            requested_height -= delta.y()
        elif edges & Qt.Edge.BottomEdge:
            requested_height += delta.y()

        if horizontal and not vertical:
            lead = "width"
        elif vertical and not horizontal:
            lead = "height"
        else:
            lead = "width" if abs(delta.x()) >= abs(delta.y()) else "height"

        initial_right = initial.x() + initial.width()
        initial_bottom = initial.y() + initial.height()
        if edges & Qt.Edge.LeftEdge:
            maximum_width = initial_right
        elif edges & Qt.Edge.RightEdge:
            maximum_width = board_width - initial.x()
        else:
            maximum_width = board_width
        if edges & Qt.Edge.TopEdge:
            maximum_height = initial_bottom
        elif edges & Qt.Edge.BottomEdge:
            maximum_height = board_height - initial.y()
        else:
            maximum_height = board_height

        size = self._aspect_constrained_size(
            requested_width,
            requested_height,
            aspect,
            self._interaction_minimum_width,
            self._interaction_minimum_height,
            maximum_width,
            maximum_height,
            lead=lead,
        )
        if size is None:
            # Для патологически маленькой доски одновременно выполнить
            # минимумы, границы и пропорцию невозможно: мягко останавливаемся.
            return
        width, height = size

        if edges & Qt.Edge.LeftEdge:
            x = initial_right - width
        elif edges & Qt.Edge.RightEdge:
            x = initial.x()
        else:
            center_x = initial.x() + initial.width() / 2.0
            x = round(center_x - width / 2.0)
            x = max(0, min(x, board_width - width))

        if edges & Qt.Edge.TopEdge:
            y = initial_bottom - height
        elif edges & Qt.Edge.BottomEdge:
            y = initial.y()
        else:
            center_y = initial.y() + initial.height() / 2.0
            y = round(center_y - height / 2.0)
            y = max(0, min(y, board_height - height))

        self.setGeometry(x, y, width, height)

    @staticmethod
    def _aspect_constrained_size(
        requested_width: float,
        requested_height: float,
        aspect: float,
        minimum_width: int,
        minimum_height: int,
        maximum_width: int,
        maximum_height: int,
        *,
        lead: str,
    ) -> tuple[int, int] | None:
        """Подбирает ближайший целочисленный размер с заданной пропорцией."""

        minimum_width = max(1, int(minimum_width))
        minimum_height = max(1, int(minimum_height))
        maximum_width = max(0, int(maximum_width))
        maximum_height = max(0, int(maximum_height))
        if maximum_width < minimum_width or maximum_height < minimum_height:
            return None

        minimum_scale = max(minimum_height, minimum_width / aspect)
        maximum_scale = min(maximum_height, maximum_width / aspect)

        requested_scale = (
            requested_width / aspect if lead == "width" else requested_height
        )
        if minimum_scale <= maximum_scale:
            target_scale = max(minimum_scale, min(requested_scale, maximum_scale))
        else:
            # Целочисленное округление иногда оставляет допустимую пару даже
            # при очень узком зазоре между непрерывными пределами.
            target_scale = (minimum_scale + maximum_scale) / 2.0

        height_seeds: set[int] = set()
        width_seeds: set[int] = set()
        for value in (target_scale, minimum_scale, maximum_scale):
            base = math.floor(value)
            height_seeds.update(range(base - 2, base + 4))
        for value in (
            target_scale * aspect,
            minimum_scale * aspect,
            maximum_scale * aspect,
        ):
            base = math.floor(value)
            width_seeds.update(range(base - 2, base + 4))

        candidates: set[tuple[int, int]] = set()
        for height in height_seeds:
            if not minimum_height <= height <= maximum_height:
                continue
            ideal_width = height * aspect
            for width in (
                math.floor(ideal_width),
                round(ideal_width),
                math.ceil(ideal_width),
            ):
                if minimum_width <= width <= maximum_width:
                    candidates.add((width, height))
        for width in width_seeds:
            if not minimum_width <= width <= maximum_width:
                continue
            ideal_height = width / aspect
            for height in (
                math.floor(ideal_height),
                round(ideal_height),
                math.ceil(ideal_height),
            ):
                if minimum_height <= height <= maximum_height:
                    candidates.add((width, height))

        if not candidates:
            return None

        if lead == "width":
            return min(
                candidates,
                key=lambda size: (
                    abs(size[0] - target_scale * aspect),
                    abs(size[0] / size[1] - aspect),
                    abs(size[1] - target_scale),
                ),
            )
        return min(
            candidates,
            key=lambda size: (
                abs(size[1] - target_scale),
                abs(size[0] / size[1] - aspect),
                abs(size[0] / aspect - target_scale),
            ),
        )

    def _finish_interaction(self) -> None:
        changed = self.geometry() != self._initial_geometry
        self._interaction = None
        self._resize_edges_value = Qt.Edge(0)
        self.releaseMouse()
        self.unsetCursor()
        QTimer.singleShot(0, self._sync_header_hover)
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

    def set_layout_interaction_enabled(self, enabled: bool) -> None:
        if enabled == self._layout_interaction_enabled:
            return
        self._layout_interaction_enabled = enabled
        if not enabled and self._interaction is not None:
            self._interaction = None
            self._resize_edges_value = Qt.Edge(0)
            self.releaseMouse()
        self.unsetCursor()
        if enabled:
            self.setMinimumSize(
                self._interaction_minimum_width,
                self._interaction_minimum_height,
            )
        else:
            self.setMinimumSize(0, 0)
        self.update()

    def set_layout_scale(self, scale_x: float, scale_y: float) -> None:
        """Масштабирует только экранные пределы, не меняя оконную геометрию."""

        self._interaction_minimum_width = max(
            1,
            round(self.MINIMUM_WIDTH * max(0.01, scale_x)),
        )
        self._interaction_minimum_height = max(
            1,
            round(self.MINIMUM_HEIGHT * max(0.01, scale_y)),
        )
        if self._layout_interaction_enabled:
            self.setMinimumSize(
                self._interaction_minimum_width,
                self._interaction_minimum_height,
            )

    def set_expanded(self, expanded: bool) -> None:
        if expanded == self._expanded:
            return
        self._expanded = expanded
        margin = 0 if expanded else 1
        self._root_layout.setContentsMargins(margin, margin, margin, margin)
        self.video.set_corner_radius(0.0 if expanded else 4.0)
        if expanded:
            self._set_highlight_visible(False)
        elif self._header_target_visible:
            self._set_highlight_visible(True)
        self._position_overlays()
        self.update()

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

    def apply_config(self, config: CameraConfig) -> None:
        previous = self.config
        reconnect = self._connection_signature(previous) != self._connection_signature(config)
        self.config = config
        self._sync_config_widgets()
        if reconnect or (config.is_configured() and self._current_reader is None):
            self.restart_stream()

    def _video_double_clicked(self) -> None:
        if not self.config.is_configured():
            self.settings_requested.emit(self.config.camera_id)
            return
        self.expand_requested.emit(self.config.camera_id)

    def _set_status_state(self, state: str) -> None:
        self.status.set_state(state)
        self.status.adjustSize()
        self._position_overlays()

    def restart_stream(self) -> None:
        if self._shutting_down:
            return
        if self._current_reader is not None:
            self._current_reader.stop()
            self._current_reader = None

        self._generation += 1
        generation = self._generation
        if not self.config.is_configured():
            self._set_status_state("unconfigured")
            detail = (
                "Откройте настройки по шестерёнке и введите логин и пароль ONVIF."
                if self.config.source == "onvif"
                else "Откройте настройки по шестерёнке и введите RTSP-данные."
            )
            self.video.set_stream_state(
                "unconfigured",
                detail,
            )
            return

        try:
            url = self.config.build_rtsp_url()
        except ConfigError as exc:
            self._set_status_state("offline")
            self.video.set_stream_state("offline", str(exc))
            return

        detail = (
            "HD-поток запускается — это может занять до 60 секунд"
            if self.config.quality == "hd"
            else "Открываем SD-поток"
        )
        self._set_status_state("connecting")
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
        self._set_status_state(state)
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

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "header"):
            self._sync_header_content_visibility()
            self._position_overlays()

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        border = QColor(BRONZE)
        border.setAlpha(82)
        painter.setPen(QPen(border, 1))
        painter.setBrush(QColor(SURFACE_RAISED))
        radius = 0.0 if self._expanded else 4.0
        painter.drawRoundedRect(rect, radius, radius)

        if self._highlight_opacity > 0.01 and not self._expanded:
            accent = QColor(BRONZE)
            accent.setAlpha(int(225 * self._highlight_opacity))
            painter.setPen(QPen(accent, 1.25))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(
                QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75),
                3.5,
                3.5,
            )

        # Ненавязчивый маркер правого нижнего угла подсказывает про ресайз.
        if self._layout_interaction_enabled:
            marker = QColor(BRONZE)
            marker.setAlpha(90)
            painter.setPen(QPen(marker, 1.2))
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
        self._header_idle_timer.stop()
        self._header_animation.stop()
        self._highlight_animation.stop()
        self._status_animation.stop()
        self._cleanup_timer.stop()
        if self._interaction is not None:
            self._interaction = None
            self.releaseMouse()
        for reader in self._readers.values():
            reader.stop()
        self._current_reader = None
