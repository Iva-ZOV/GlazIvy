"""Видимая доска камер и её оконная панель."""

from __future__ import annotations

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
from .widgets import LogoGlyph, ToolIconButton


class BoardTitleBar(QWidget):
    add_camera_clicked = Signal()
    find_cameras_clicked = Signal()
    settings_clicked = Signal()
    fullscreen_clicked = Signal()
    minimize_clicked = Signal()
    close_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setFixedHeight(60)
        self._compact = False
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
        self.camera_count.setVisible(not compact)
        self._sync_action_buttons()

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

    def __init__(
        self,
        cameras: tuple[CameraConfig, ...] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._tiles: dict[str, CameraTile] = {}
        self._z_order: list[str] = []
        self._corner_radius = 20.0
        self.setObjectName("cameraBoard")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(CameraTile.MINIMUM_WIDTH, CameraTile.MINIMUM_HEIGHT)

        for camera in sorted(cameras, key=lambda item: item.geometry.z):
            self.add_camera(camera, announce=False)
        self._raise_in_saved_order()

    def camera_count(self) -> int:
        return len(self._tiles)

    def tile_for(self, camera_id: str) -> CameraTile | None:
        return self._tiles.get(camera_id)

    def _connect_tile(self, tile: CameraTile) -> None:
        tile.raise_requested.connect(self.raise_tile)
        tile.layout_changed.connect(lambda _camera_id: self.layout_changed.emit())
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
        self._z_order.append(config.camera_id)
        tile.show()
        tile.raise_()
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
        if camera_id in self._z_order:
            self._z_order.remove(camera_id)
        tile.shutdown()
        tile.hide()
        tile.setParent(None)
        tile.deleteLater()
        self.camera_count_changed.emit(self.camera_count())
        self.layout_changed.emit()
        self.update()
        return True

    def raise_tile(self, camera_id: str) -> None:
        tile = self._tiles.get(camera_id)
        if tile is None:
            return
        changed = not self._z_order or self._z_order[-1] != camera_id
        if camera_id in self._z_order:
            self._z_order.remove(camera_id)
        self._z_order.append(camera_id)
        tile.raise_()
        if changed:
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
            result.append(
                tile.snapshot_config(
                    z_index,
                    replacements.get(camera_id),
                )
            )
        return tuple(result)

    def replace_camera_config(self, config: CameraConfig) -> bool:
        tile = self._tiles.get(config.camera_id)
        if tile is None:
            return False
        tile.apply_config(config)
        return True

    def set_corner_radius(self, radius: float) -> None:
        self._corner_radius = max(0.0, radius)
        self.update()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        changed = False
        for tile in self._tiles.values():
            changed = tile.constrain_to_parent() or changed
        if changed:
            self.layout_changed.emit()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        changed = False
        for tile in self._tiles.values():
            changed = tile.constrain_to_parent() or changed
        self._raise_in_saved_order()
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
