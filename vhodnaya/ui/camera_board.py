"""Видимая доска камер и её оконная панель."""

from __future__ import annotations

import math
import time

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QResizeEvent,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from ..config import CameraConfig, CameraGeometry
from ..constants import APP_NAME
from .camera_tile import CameraTile
from .theme import ACCENT, ACCENT_BLUE, TEXT, TEXT_MUTED
from .widgets import LogoGlyph, SegmentedControl, ToolIconButton


class BoardTitleBar(QWidget):
    add_camera_clicked = Signal()
    find_cameras_clicked = Signal()
    settings_clicked = Signal()
    fullscreen_clicked = Signal()
    minimize_clicked = Signal()
    close_clicked = Signal()
    layout_mode_changed = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        layout_mode: str = "free",
    ) -> None:
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setFixedHeight(60)
        self._compact = False
        self._fullscreen = False
        self._discovery_busy = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 10, 0)
        layout.setSpacing(9)
        layout.addWidget(LogoGlyph(28, self))

        title = QLabel(APP_NAME, self)
        title.setObjectName("appTitle")
        layout.addWidget(title)
        self.subtitle = QLabel("Доска камер", self)
        self.subtitle.setObjectName("boardSubtitle")
        layout.addWidget(self.subtitle)
        layout.addStretch(1)

        self.camera_count = QLabel(self)
        self.camera_count.setObjectName("cameraCount")
        layout.addWidget(self.camera_count)

        self.layout_control = SegmentedControl(
            ("Свободно", "Сетка"),
            ("free", "grid"),
            layout_mode,
            self,
        )
        self.layout_control.setToolTip("Режим раскладки камер")
        self.layout_control.value_changed.connect(self.layout_mode_changed)
        layout.addWidget(self.layout_control)

        self.find_button = QPushButton("Найти камеры", self)
        self.find_button.setObjectName("secondaryButton")
        self.find_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.find_button.clicked.connect(self.find_cameras_clicked)
        layout.addWidget(self.find_button)

        self.add_button = QPushButton("＋  Добавить камеру", self)
        self.add_button.setObjectName("addCameraButton")
        self.add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.add_button.clicked.connect(self.add_camera_clicked)
        layout.addWidget(self.add_button)

        self.settings_button = ToolIconButton(
            "settings",
            "Настройки доски",
            self,
        )
        self.fullscreen_button = ToolIconButton(
            "fullscreen",
            "Полный экран · F11",
            self,
        )
        self.minimize_button = ToolIconButton("minimize", "Свернуть", self)
        self.close_button = ToolIconButton("close", "Закрыть", self)
        for button in (
            self.settings_button,
            self.fullscreen_button,
            self.minimize_button,
            self.close_button,
        ):
            layout.addWidget(button)

        self.settings_button.clicked.connect(self.settings_clicked)
        self.fullscreen_button.clicked.connect(self.fullscreen_clicked)
        self.minimize_button.clicked.connect(self.minimize_clicked)
        self.close_button.clicked.connect(self.close_clicked)
        self.set_camera_count(0)

    @staticmethod
    def _camera_word(count: int) -> str:
        if count % 10 == 1 and count % 100 != 11:
            return "камера"
        if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
            return "камеры"
        return "камер"

    def set_camera_count(self, count: int) -> None:
        self.camera_count.setText(f"{count} {self._camera_word(count)}")

    def set_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        self.subtitle.setVisible(not compact)
        self.camera_count.setVisible(self._fullscreen or not compact)
        self._sync_action_buttons()

    def set_layout_mode(self, mode: str, *, animate: bool = True) -> None:
        self.layout_control.set_value(mode, animate=animate)

    def set_fullscreen_mode(self, fullscreen: bool) -> None:
        if fullscreen == self._fullscreen:
            return
        self._fullscreen = fullscreen
        self.camera_count.setVisible(fullscreen or not self._compact)
        self.fullscreen_button.set_kind(
            "windowed" if fullscreen else "fullscreen",
            "Оконный режим · F11" if fullscreen else "Полный экран · F11",
        )

    def set_floating(self, floating: bool) -> None:
        self.setProperty("floating", floating)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_discovery_busy(self, busy: bool) -> None:
        self._discovery_busy = busy
        self.find_button.setEnabled(not busy)
        self._sync_action_buttons()

    def _sync_action_buttons(self) -> None:
        if self._discovery_busy:
            self.find_button.setText("Ищем…")
        else:
            self.find_button.setText("Найти камеры")
        self.add_button.setText("＋  Камеру" if self._compact else "＋  Добавить камеру")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window()
            handle = window.windowHandle()
            if handle is not None and not window.isFullScreen():
                handle.startSystemMove()
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            toggle = getattr(self.window(), "toggle_maximized", None)
            if callable(toggle):
                toggle()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)


class CameraBoard(QWidget):
    """Конечная доска без прокрутки, зума и виртуального холста."""

    layout_changed = Signal()
    camera_count_changed = Signal(int)
    settings_requested = Signal(str)
    quick_change_requested = Signal(str, str, str)

    GRID_SPACING = 10

    def __init__(
        self,
        cameras: tuple[CameraConfig, ...] = (),
        parent: QWidget | None = None,
        *,
        layout_mode: str = "free",
    ) -> None:
        super().__init__(parent)
        if layout_mode not in {"free", "grid"}:
            raise ValueError("Неизвестный режим раскладки.")
        self._tiles: dict[str, CameraTile] = {}
        self._tile_order: list[str] = []
        self._z_order: list[str] = []
        self._free_geometries: dict[str, CameraGeometry] = {}
        self._layout_mode = layout_mode
        self._expanded_camera_id: str | None = None
        self._last_raise_snapshot: tuple[str, tuple[str, ...], float] | None = None
        self._corner_radius = 20.0
        self.setObjectName("cameraBoard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(CameraTile.MINIMUM_WIDTH, CameraTile.MINIMUM_HEIGHT)

        for camera in sorted(cameras, key=lambda item: item.geometry.z):
            self.add_camera(camera, announce=False)
        self._raise_in_saved_order()
        self._sync_tile_states()

    def camera_count(self) -> int:
        return len(self._tiles)

    def tile_for(self, camera_id: str) -> CameraTile | None:
        return self._tiles.get(camera_id)

    def layout_mode(self) -> str:
        return self._layout_mode

    def has_expanded_camera(self) -> bool:
        return self._expanded_camera_id is not None

    @staticmethod
    def _geometry_from_tile(tile: CameraTile, z: int) -> CameraGeometry:
        rect = tile.geometry()
        return CameraGeometry(
            x=rect.x(),
            y=rect.y(),
            width=rect.width(),
            height=rect.height(),
            z=z,
        )

    def _remember_free_geometry(self, camera_id: str) -> None:
        tile = self._tiles.get(camera_id)
        if tile is None:
            return
        previous = self._free_geometries.get(camera_id, tile.config.geometry)
        self._free_geometries[camera_id] = self._geometry_from_tile(
            tile,
            previous.z,
        )

    def _capture_free_layout(self) -> None:
        if self._layout_mode != "free" or self._expanded_camera_id is not None:
            return
        for camera_id in self._tile_order:
            self._remember_free_geometry(camera_id)

    def _tile_layout_changed(self, camera_id: str) -> None:
        if self._layout_mode != "free" or self._expanded_camera_id is not None:
            return
        self._remember_free_geometry(camera_id)
        self.layout_changed.emit()

    def _connect_tile(self, tile: CameraTile) -> None:
        tile.raise_requested.connect(self.raise_tile)
        tile.layout_changed.connect(self._tile_layout_changed)
        tile.settings_requested.connect(
            lambda camera_id: self.settings_requested.emit(camera_id)
        )
        tile.quick_change_requested.connect(
            lambda camera_id, field_name, value: self.quick_change_requested.emit(
                camera_id,
                field_name,
                value,
            )
        )
        tile.expand_requested.connect(self.toggle_camera_expanded)

    def add_camera(self, config: CameraConfig, *, announce: bool = True) -> CameraTile:
        if config.camera_id in self._tiles:
            raise ValueError("Камера с таким идентификатором уже есть на доске.")
        tile = CameraTile(config, self)
        geometry = config.geometry
        tile.setGeometry(
            geometry.x,
            geometry.y,
            geometry.width,
            geometry.height,
        )
        self._connect_tile(tile)
        self._tiles[config.camera_id] = tile
        self._tile_order.append(config.camera_id)
        self._z_order.append(config.camera_id)
        self._free_geometries[config.camera_id] = config.geometry
        tile.show()
        tile.raise_()
        if self.isVisible():
            self._apply_current_layout()
        else:
            self._sync_tile_states()
        self.update()
        if announce:
            self.camera_count_changed.emit(self.camera_count())
            self.layout_changed.emit()
        return tile

    def create_camera(self, base_config: CameraConfig | None = None) -> CameraConfig:
        board_width = max(self.width(), CameraTile.MINIMUM_WIDTH + 48)
        board_height = max(self.height(), CameraTile.MINIMUM_HEIGHT + 48)
        width = max(
            CameraTile.MINIMUM_WIDTH,
            min(620, board_width - 48),
        )
        height = max(
            CameraTile.MINIMUM_HEIGHT,
            min(420, board_height - 48),
        )
        cascade = len(self._tiles) % 7
        x = min(max(0, board_width - width), 24 + cascade * 30)
        y = min(max(0, board_height - height), 24 + cascade * 26)
        config = (base_config or CameraConfig()).updated(
            geometry=CameraGeometry(
                x=x,
                y=y,
                width=width,
                height=height,
                z=len(self._z_order),
            )
        )
        self.add_camera(config)
        return config

    def remove_camera(self, camera_id: str) -> bool:
        tile = self._tiles.pop(camera_id, None)
        if tile is None:
            return False
        if camera_id in self._tile_order:
            self._tile_order.remove(camera_id)
        if camera_id in self._z_order:
            self._z_order.remove(camera_id)
        self._free_geometries.pop(camera_id, None)
        if self._expanded_camera_id == camera_id:
            self._expanded_camera_id = None
        tile.shutdown()
        tile.hide()
        tile.setParent(None)
        tile.deleteLater()
        self._apply_current_layout()
        self.camera_count_changed.emit(self.camera_count())
        self.layout_changed.emit()
        self.update()
        return True

    def raise_tile(self, camera_id: str) -> None:
        tile = self._tiles.get(camera_id)
        if tile is None:
            return
        if self._expanded_camera_id is not None:
            tile.raise_()
            return
        changed = not self._z_order or self._z_order[-1] != camera_id
        previous_order = tuple(self._z_order)
        if camera_id in self._z_order:
            self._z_order.remove(camera_id)
        self._z_order.append(camera_id)
        tile.raise_()
        if changed:
            self._last_raise_snapshot = (
                camera_id,
                previous_order,
                time.monotonic(),
            )
            self.layout_changed.emit()

    def _raise_in_saved_order(self) -> None:
        for camera_id in self._z_order:
            tile = self._tiles.get(camera_id)
            if tile is not None:
                tile.raise_()

    def camera_configs(
        self,
        replacements: dict[str, CameraConfig] | None = None,
    ) -> tuple[CameraConfig, ...]:
        replacements = replacements or {}
        result: list[CameraConfig] = []
        for z_index, camera_id in enumerate(self._z_order):
            tile = self._tiles.get(camera_id)
            if tile is None:
                continue
            source = replacements.get(camera_id, tile.config)
            geometry = self._free_geometries.get(camera_id, source.geometry)
            result.append(source.updated(geometry=geometry.updated(z=z_index)))
        return tuple(result)

    def replace_camera_config(self, config: CameraConfig) -> bool:
        tile = self._tiles.get(config.camera_id)
        if tile is None:
            return False
        self._free_geometries[config.camera_id] = config.geometry
        tile.apply_config(config)
        if self._layout_mode == "free" and self._expanded_camera_id is None:
            tile.setGeometry(
                config.geometry.x,
                config.geometry.y,
                config.geometry.width,
                config.geometry.height,
            )
            if tile.constrain_to_parent():
                self._remember_free_geometry(config.camera_id)
        return True

    def set_layout_mode(self, mode: str) -> bool:
        if mode not in {"free", "grid"}:
            return False
        if mode == self._layout_mode:
            return True
        self._capture_free_layout()
        self._layout_mode = mode
        self._last_raise_snapshot = None
        self._apply_current_layout()
        return True

    def toggle_camera_expanded(self, camera_id: str) -> None:
        tile = self._tiles.get(camera_id)
        if tile is None or not tile.config.is_configured():
            return
        if self._expanded_camera_id == camera_id:
            self.collapse_expanded_camera()
            return

        self._capture_free_layout()
        recent_raise = self._last_raise_snapshot
        if (
            recent_raise is not None
            and recent_raise[0] == camera_id
            and time.monotonic() - recent_raise[2] <= 1.0
        ):
            self._z_order = list(recent_raise[1])
        self._last_raise_snapshot = None
        self._expanded_camera_id = camera_id
        self._apply_current_layout()

    def collapse_expanded_camera(self) -> bool:
        if self._expanded_camera_id is None:
            return False
        self._expanded_camera_id = None
        self._apply_current_layout()
        return True

    def _sync_tile_states(self) -> None:
        interaction_enabled = (
            self._layout_mode == "free" and self._expanded_camera_id is None
        )
        for camera_id, tile in self._tiles.items():
            tile.set_layout_interaction_enabled(interaction_enabled)
            tile.set_expanded(camera_id == self._expanded_camera_id)

    def _apply_free_layout(self) -> bool:
        changed = False
        for camera_id in self._tile_order:
            tile = self._tiles.get(camera_id)
            geometry = self._free_geometries.get(camera_id)
            if tile is None or geometry is None:
                continue
            tile.show()
            tile.setGeometry(
                geometry.x,
                geometry.y,
                geometry.width,
                geometry.height,
            )
            if tile.constrain_to_parent():
                self._remember_free_geometry(camera_id)
                changed = True
        self._raise_in_saved_order()
        return changed

    def _apply_grid_layout(self) -> None:
        camera_ids = [
            camera_id for camera_id in self._tile_order if camera_id in self._tiles
        ]
        count = len(camera_ids)
        if count == 0:
            return
        columns = math.ceil(math.sqrt(count))
        rows = math.ceil(count / columns)
        spacing = self.GRID_SPACING
        available_width = max(1, self.width() - spacing * (columns + 1))
        available_height = max(1, self.height() - spacing * (rows + 1))
        tile_width = max(1, available_width // columns)
        tile_height = max(1, available_height // rows)
        grid_width = tile_width * columns + spacing * (columns - 1)
        grid_height = tile_height * rows + spacing * (rows - 1)
        start_x = max(0, (self.width() - grid_width) // 2)
        start_y = max(0, (self.height() - grid_height) // 2)

        for index, camera_id in enumerate(camera_ids):
            tile = self._tiles[camera_id]
            row, column = divmod(index, columns)
            tile.show()
            tile.setGeometry(
                start_x + column * (tile_width + spacing),
                start_y + row * (tile_height + spacing),
                tile_width,
                tile_height,
            )
        self._raise_in_saved_order()

    def _apply_current_layout(self) -> bool:
        self._sync_tile_states()
        if self._expanded_camera_id is not None:
            for camera_id, tile in self._tiles.items():
                tile.setVisible(camera_id == self._expanded_camera_id)
            expanded = self._tiles.get(self._expanded_camera_id)
            if expanded is not None:
                expanded.setGeometry(self.rect())
                expanded.raise_()
            return False
        if self._layout_mode == "grid":
            self._apply_grid_layout()
            return False
        return self._apply_free_layout()

    def set_corner_radius(self, radius: float) -> None:
        self._corner_radius = max(0.0, radius)
        self.update()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        changed = self._apply_current_layout()
        if changed:
            self.layout_changed.emit()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        changed = self._apply_current_layout()
        if changed:
            self.layout_changed.emit()

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        if self._corner_radius > 0.0:
            clip = QPainterPath()
            clip.setFillRule(Qt.FillRule.WindingFill)
            clip.addRoundedRect(rect, self._corner_radius, self._corner_radius)
            clip.addRect(
                QRectF(
                    0,
                    0,
                    self.width(),
                    max(0.0, self.height() - self._corner_radius),
                )
            )
            painter.setClipPath(clip)

        background = QLinearGradient(rect.topLeft(), rect.bottomRight())
        background.setColorAt(0.0, QColor("#0A0E14"))
        background.setColorAt(0.52, QColor("#0D1119"))
        background.setColorAt(1.0, QColor("#070A0F"))
        painter.fillRect(rect, background)

        painter.setPen(QPen(QColor(255, 255, 255, 7), 1))
        spacing = max(58, int(min(self.width(), self.height()) / 9))
        for x in range(-self.height(), self.width() + self.height(), spacing):
            painter.drawLine(x, 0, x - self.height(), self.height())

        glow = QLinearGradient(0, 0, self.width(), self.height())
        accent_blue = QColor(ACCENT_BLUE)
        accent_blue.setAlpha(13)
        accent = QColor(ACCENT)
        accent.setAlpha(9)
        transparent = QColor(0, 0, 0, 0)
        glow.setColorAt(0.0, accent_blue)
        glow.setColorAt(0.55, transparent)
        glow.setColorAt(1.0, accent)
        painter.fillRect(rect, glow)

        if self._tiles:
            return
        title_font = QFont(self.font())
        title_font.setPixelSize(23)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor(TEXT))
        center_y = self.height() / 2 - 24
        painter.drawText(
            QRectF(30, center_y - 24, self.width() - 60, 42),
            Qt.AlignmentFlag.AlignCenter,
            "Пока нет камер",
        )
        helper_font = QFont(self.font())
        helper_font.setPixelSize(13)
        painter.setFont(helper_font)
        painter.setPen(QColor(TEXT_MUTED))
        painter.drawText(
            QRectF(30, center_y + 18, self.width() - 60, 54),
            Qt.AlignmentFlag.AlignHCenter
            | Qt.AlignmentFlag.AlignTop
            | Qt.TextFlag.TextWordWrap,
            "Нажмите «Найти камеры» или добавьте камеру вручную.",
        )

    def shutdown(self) -> None:
        for tile in self._tiles.values():
            tile.shutdown()
