"""Главное безрамочное окно с конечной доской камер."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRectF, QTimer, Qt
from PySide6.QtGui import (
    QColor,
    QCloseEvent,
    QKeySequence,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QResizeEvent,
    QShortcut,
)
from PySide6.QtWidgets import QApplication, QFrame, QMessageBox, QVBoxLayout, QWidget

from ..autostart import AutostartError, set_autostart
from ..config import AppConfig, CameraConfig, ConfigError, ConfigStore
from ..constants import APP_NAME
from ..resources import application_icon
from .camera_board import BoardTitleBar, CameraBoard
from .dialogs import BoardSettingsDialog, SettingsDialog


class MainWindow(QWidget):
    RESIZE_MARGIN = 11
    WINDOW_MARGIN = 18

    def __init__(
        self,
        store: ConfigStore,
        config: AppConfig,
        *,
        load_error: str = "",
    ) -> None:
        super().__init__()
        self.store = store
        self.config = config
        self._shutting_down = False
        self._config_dirty = False
        self._was_maximized_before_fullscreen = False

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(application_icon())
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinMaxButtonsHint
        )
        # Прозрачность нужна только вокруг скруглённого surface. Графические
        # эффекты на surface недопустимы: внутри него рисуется живое видео.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.setMinimumSize(760, 520)
        self._apply_initial_geometry()

        self.outer_layout = QVBoxLayout(self)
        self.outer_layout.setContentsMargins(
            self.WINDOW_MARGIN,
            self.WINDOW_MARGIN,
            self.WINDOW_MARGIN,
            self.WINDOW_MARGIN,
        )
        self.surface = QFrame(self)
        self.surface.setObjectName("windowSurface")
        self.outer_layout.addWidget(self.surface)

        surface_layout = QVBoxLayout(self.surface)
        surface_layout.setContentsMargins(0, 0, 0, 0)
        surface_layout.setSpacing(0)

        self.title_bar = BoardTitleBar(self.surface)
        surface_layout.addWidget(self.title_bar)
        self.board = CameraBoard(config.cameras, self.surface)
        surface_layout.addWidget(self.board, 1)
        self.title_bar.set_camera_count(self.board.camera_count())
        self.title_bar.set_compact(self.width() < 960)

        self.title_bar.add_camera_clicked.connect(self.add_camera)
        self.title_bar.settings_clicked.connect(self.open_board_settings)
        self.title_bar.fullscreen_clicked.connect(self.toggle_fullscreen)
        self.title_bar.minimize_clicked.connect(self.showMinimized)
        self.title_bar.close_clicked.connect(self.close)
        self.board.camera_count_changed.connect(self.title_bar.set_camera_count)
        self.board.layout_changed.connect(self._schedule_save)
        self.board.settings_requested.connect(self.open_camera_settings)
        self.board.quick_change_requested.connect(self._quick_camera_change)

        self.fullscreen_shortcut = QShortcut(QKeySequence("F11"), self)
        self.fullscreen_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self.fullscreen_shortcut.activated.connect(self.toggle_fullscreen)
        self.escape_shortcut = QShortcut(QKeySequence("Esc"), self)
        self.escape_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self.escape_shortcut.activated.connect(self._escape_pressed)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(350)
        self._save_timer.timeout.connect(self._persist_current_config)

        if load_error:
            QTimer.singleShot(
                0,
                lambda: QMessageBox.warning(
                    self,
                    APP_NAME,
                    "Сохранённую раскладку не удалось прочитать. "
                    "Открыта пустая доска.\n\n" + load_error,
                ),
            )

    def _apply_initial_geometry(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            self.resize(1180, 800)
            return
        available = screen.availableGeometry()
        width = min(1280, max(self.minimumWidth(), int(available.width() * 0.86)))
        height = min(860, max(self.minimumHeight(), int(available.height() * 0.86)))
        width = min(width, available.width())
        height = min(height, available.height())
        self.resize(width, height)
        self.move(
            available.center().x() - width // 2,
            available.center().y() - height // 2,
        )

    def _snapshot_config(
        self,
        *,
        replacements: dict[str, CameraConfig] | None = None,
        autostart: bool | None = None,
    ) -> AppConfig:
        return AppConfig(
            cameras=self.board.camera_configs(replacements),
            autostart=self.config.autostart if autostart is None else autostart,
        )

    def _persist_candidate(self, candidate: AppConfig, *, show_error: bool = True) -> bool:
        try:
            self.store.save(candidate)
        except ConfigError as exc:
            if show_error:
                QMessageBox.warning(self, APP_NAME, str(exc))
            return False
        self.config = candidate
        self._config_dirty = False
        self._save_timer.stop()
        return True

    def _persist_current_config(self, *, show_error: bool = True) -> bool:
        return self._persist_candidate(
            self._snapshot_config(),
            show_error=show_error,
        )

    def _schedule_save(self) -> None:
        if not self._shutting_down:
            self._config_dirty = True
            self._save_timer.start()

    def add_camera(self) -> None:
        self.board.create_camera()

    def _quick_camera_change(
        self,
        camera_id: str,
        field_name: str,
        value: str,
    ) -> None:
        tile = self.board.tile_for(camera_id)
        if tile is None or field_name not in {"quality", "transport"}:
            return
        candidate_camera = tile.config.updated(**{field_name: value})
        candidate = self._snapshot_config(
            replacements={camera_id: candidate_camera}
        )
        if not self._persist_candidate(candidate):
            tile.restore_quick_controls()
            return
        applied_camera = next(
            camera for camera in candidate.cameras if camera.camera_id == camera_id
        )
        tile.apply_config(applied_camera)

    def open_camera_settings(self, camera_id: str) -> None:
        tile = self.board.tile_for(camera_id)
        if tile is None:
            return
        dialog = SettingsDialog(tile.config, self)
        if dialog.exec() != SettingsDialog.DialogCode.Accepted:
            return

        if dialog.delete_requested:
            remaining: list[CameraConfig] = []
            for camera in self.board.camera_configs():
                if camera.camera_id == camera_id:
                    continue
                remaining.append(
                    camera.updated(
                        geometry=camera.geometry.updated(z=len(remaining))
                    )
                )
            candidate = AppConfig(
                cameras=tuple(remaining),
                autostart=self.config.autostart,
            )
            if self._persist_candidate(candidate):
                self.board.remove_camera(camera_id)
            return

        candidate_camera = dialog.result_config
        if candidate_camera is None:
            return
        candidate = self._snapshot_config(
            replacements={camera_id: candidate_camera}
        )
        if not self._persist_candidate(candidate):
            return
        applied_camera = next(
            camera for camera in candidate.cameras if camera.camera_id == camera_id
        )
        self.board.replace_camera_config(applied_camera)

    def open_board_settings(self) -> None:
        dialog = BoardSettingsDialog(self.config.autostart, self)
        if dialog.exec() != BoardSettingsDialog.DialogCode.Accepted:
            return
        enabled = dialog.result_autostart
        if enabled is None or enabled == self.config.autostart:
            return

        previous = self.config.autostart
        try:
            set_autostart(enabled)
            candidate = self._snapshot_config(autostart=enabled)
            if not self._persist_candidate(candidate):
                set_autostart(previous)
        except AutostartError as exc:
            try:
                set_autostart(previous)
            except AutostartError:
                pass
            QMessageBox.warning(self, APP_NAME, str(exc))

    def _escape_pressed(self) -> None:
        # В обычном режиме Esc намеренно ничего не делает и не закрывает окно.
        if self.isFullScreen():
            self.toggle_fullscreen()

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.title_bar.show()
            if self._was_maximized_before_fullscreen:
                self.showMaximized()
            else:
                self.showNormal()
        else:
            self._was_maximized_before_fullscreen = self.isMaximized()
            self.title_bar.hide()
            self.showFullScreen()
        QTimer.singleShot(0, self._sync_window_chrome)

    def toggle_maximized(self) -> None:
        if self.isFullScreen():
            return
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        QTimer.singleShot(0, self._sync_window_chrome)

    def _sync_window_chrome(self) -> None:
        flat = self.isMaximized() or self.isFullScreen()
        margin = 0 if flat else self.WINDOW_MARGIN
        self.outer_layout.setContentsMargins(margin, margin, margin, margin)
        self.surface.setProperty("flat", flat)
        self.surface.style().unpolish(self.surface)
        self.surface.style().polish(self.surface)
        self.board.set_corner_radius(0.0 if flat else 20.0)
        self.update()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            QTimer.singleShot(0, self._sync_window_chrome)

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        if not hasattr(self, "surface") or self.isMaximized() or self.isFullScreen():
            return

        # Тень рисуется родителем позади surface. Она не переносит вложенные
        # VideoCanvas и их кадры в offscreen-кэш.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        shadow_rect = QRectF(self.surface.geometry()).translated(0.0, 4.0)
        for spread, alpha in (
            (14.0, 5),
            (11.0, 7),
            (8.0, 9),
            (5.0, 12),
            (2.0, 16),
        ):
            painter.setBrush(QColor(0, 0, 0, alpha))
            rect = shadow_rect.adjusted(-spread, -spread, spread, spread)
            radius = 22.0 + spread
            painter.drawRoundedRect(rect, radius, radius)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "title_bar"):
            self.title_bar.set_compact(self.width() < 960)

    def _resize_edges(self, position: QPoint) -> Qt.Edge:
        if self.isMaximized() or self.isFullScreen():
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

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            edges = self._resize_edges(event.position().toPoint())
            handle = self.windowHandle()
            if edges and handle is not None:
                handle.startSystemResize(edges)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        edges = self._resize_edges(event.position().toPoint())
        if edges in (
            Qt.Edge.LeftEdge | Qt.Edge.TopEdge,
            Qt.Edge.RightEdge | Qt.Edge.BottomEdge,
        ):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif edges in (
            Qt.Edge.RightEdge | Qt.Edge.TopEdge,
            Qt.Edge.LeftEdge | Qt.Edge.BottomEdge,
        ):
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self.unsetCursor()
        super().leaveEvent(event)

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        needs_save = self._config_dirty or self._save_timer.isActive()
        self._save_timer.stop()
        if needs_save:
            self._persist_current_config(show_error=False)
        self._shutting_down = True
        self.board.shutdown()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.shutdown()
        event.accept()
