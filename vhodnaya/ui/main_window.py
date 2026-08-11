"""Главное безрамочное окно с конечной доской камер."""

from __future__ import annotations

import ipaddress
import re
import threading
from collections.abc import Callable
from dataclasses import replace
from urllib.parse import urlsplit

from PySide6.QtCore import (
    QEvent,
    QEasingCurve,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRectF,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QCloseEvent,
    QCursor,
    QKeySequence,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QResizeEvent,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from ..autostart import AutostartError, set_autostart
from ..config import AppConfig, CameraConfig, ConfigError, ConfigStore
from ..constants import APP_NAME
from ..onvif import DiscoveredCamera, discover_cameras, resolve_onvif_camera
from ..resources import application_icon
from .camera_board import BoardTitleBar, CameraBoard
from .dialogs import (
    BoardSettingsDialog,
    OnvifDiscoveryDialog,
    OnvifProgressDialog,
    SettingsDialog,
)
from .widgets import ToolIconButton


class _OnvifTaskSignals(QObject):
    succeeded = Signal(object, object)
    failed = Signal(object)


class _OnvifTask:
    """Daemon-поток с Qt-сигналами; сетевые запросы не блокируют интерфейс."""

    def __init__(self, target: Callable[[threading.Event], object]) -> None:
        self.signals = _OnvifTaskSignals()
        self.stop_event = threading.Event()
        self._target = target
        self._thread = threading.Thread(
            target=self._run,
            name="onvif-ui-task",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def cancel(self) -> None:
        self.stop_event.set()

    def _run(self) -> None:
        try:
            result = self._target(self.stop_event)
        except Exception:
            # Исключение может включать URL с паролем — наружу его не передаём.
            try:
                self.signals.failed.emit(self)
            except RuntimeError:
                pass
            return
        try:
            self.signals.succeeded.emit(self, result)
        except RuntimeError:
            pass


class MainWindow(QWidget):
    RESIZE_MARGIN = 11
    WINDOW_MARGIN = 18
    FULLSCREEN_OVERLAY_WIDTH = 150
    FULLSCREEN_OVERLAY_HEIGHT = 54
    FULLSCREEN_OVERLAY_MARGIN = 10

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
        self._onvif_task: _OnvifTask | None = None
        self._onvif_progress: OnvifProgressDialog | None = None
        self._onvif_cancelled = False
        self._onvif_success_callback: Callable[[object], None] | None = None
        self._onvif_failure_callback: Callable[[], None] | None = None
        self._fullscreen_overlay_target_visible = False
        self._fullscreen_cursor_hidden = False
        self._application_event_filter_installed = False

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
        self.title_bar.set_compact(self.width() < 1080)

        self.fullscreen_overlay = QWidget(self)
        self.fullscreen_overlay.setObjectName("fullscreenOverlay")
        self.fullscreen_overlay.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.fullscreen_overlay.setFixedHeight(self.FULLSCREEN_OVERLAY_HEIGHT)
        self.fullscreen_overlay.setMouseTracking(True)
        fullscreen_layout = QHBoxLayout(self.fullscreen_overlay)
        fullscreen_layout.setContentsMargins(12, 10, 12, 10)
        fullscreen_layout.setSpacing(6)
        fullscreen_layout.addStretch(1)
        self.fullscreen_minimize_button = ToolIconButton(
            "minimize",
            "Свернуть",
            self.fullscreen_overlay,
        )
        self.fullscreen_windowed_button = ToolIconButton(
            "windowed",
            "Оконный режим",
            self.fullscreen_overlay,
        )
        self.fullscreen_close_button = ToolIconButton(
            "close",
            "Закрыть",
            self.fullscreen_overlay,
        )
        for button in (
            self.fullscreen_minimize_button,
            self.fullscreen_windowed_button,
            self.fullscreen_close_button,
        ):
            fullscreen_layout.addWidget(button)

        self._fullscreen_overlay_effect = QGraphicsOpacityEffect(
            self.fullscreen_overlay
        )
        self._fullscreen_overlay_effect.setOpacity(0.0)
        self.fullscreen_overlay.setGraphicsEffect(self._fullscreen_overlay_effect)
        self._fullscreen_overlay_animation = QPropertyAnimation(
            self._fullscreen_overlay_effect,
            b"opacity",
            self,
        )
        self._fullscreen_overlay_animation.setDuration(180)
        self._fullscreen_overlay_animation.setEasingCurve(
            QEasingCurve.Type.OutCubic
        )
        self._fullscreen_overlay_animation.finished.connect(
            self._fullscreen_overlay_animation_finished
        )
        self.fullscreen_overlay.hide()

        self.title_bar.add_camera_clicked.connect(self.add_camera)
        self.title_bar.find_cameras_clicked.connect(self.find_cameras)
        self.title_bar.settings_clicked.connect(self.open_board_settings)
        self.title_bar.fullscreen_clicked.connect(self.toggle_fullscreen)
        self.title_bar.minimize_clicked.connect(self.showMinimized)
        self.title_bar.close_clicked.connect(self.close)
        self.fullscreen_minimize_button.clicked.connect(
            self._minimize_from_fullscreen
        )
        self.fullscreen_windowed_button.clicked.connect(self.toggle_fullscreen)
        self.fullscreen_close_button.clicked.connect(self.close)
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

        self._fullscreen_idle_timer = QTimer(self)
        self._fullscreen_idle_timer.setSingleShot(True)
        self._fullscreen_idle_timer.setInterval(2800)
        self._fullscreen_idle_timer.timeout.connect(self._fullscreen_idle_timeout)

        for widget in (self, *self.findChildren(QWidget)):
            widget.setMouseTracking(True)
        application = QApplication.instance()
        if application is not None:
            application.installEventFilter(self)
            self._application_event_filter_installed = True
        self._position_fullscreen_overlay()

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

    @staticmethod
    def _canonical_ip(value: str) -> str:
        value = value.strip().strip("[]").split("%", 1)[0]
        try:
            return ipaddress.ip_address(value).compressed
        except ValueError:
            return value.lower()

    def _camera_ips(self) -> set[str]:
        result: set[str] = set()
        for camera in self.board.camera_configs():
            if camera.host.strip():
                result.add(self._canonical_ip(camera.host))
            if camera.onvif_endpoint:
                try:
                    hostname = urlsplit(camera.onvif_endpoint).hostname
                except ValueError:
                    hostname = None
                if hostname:
                    result.add(self._canonical_ip(hostname))
        return result

    def _name_discovered_cameras(
        self,
        cameras: tuple[DiscoveredCamera, ...],
    ) -> tuple[DiscoveredCamera, ...]:
        used_numbers: set[int] = set()
        used_names = {
            camera.camera_name.casefold() for camera in self.board.camera_configs()
        }
        for name in used_names:
            match = re.fullmatch(r"камера\s+(\d+)", name, re.IGNORECASE)
            if match:
                used_numbers.add(int(match.group(1)))

        result: list[DiscoveredCamera] = []
        next_number = 1
        for camera in cameras:
            name = " ".join(camera.name.split())
            if not name:
                while (
                    next_number in used_numbers
                    or f"камера {next_number}" in used_names
                ):
                    next_number += 1
                name = f"Камера {next_number}"
                used_numbers.add(next_number)
                used_names.add(name.casefold())
                next_number += 1
            result.append(replace(camera, name=name))
        return tuple(result)

    def _begin_onvif_task(
        self,
        target: Callable[[threading.Event], object],
        progress: OnvifProgressDialog,
        on_success: Callable[[object], None],
        on_failure: Callable[[], None],
    ) -> bool:
        if self._onvif_task is not None:
            QMessageBox.information(
                self,
                APP_NAME,
                "Предыдущий ONVIF-запрос ещё завершается. Попробуйте через несколько секунд.",
            )
            progress.deleteLater()
            return False

        task = _OnvifTask(target)
        self._onvif_task = task
        self._onvif_progress = progress
        self._onvif_cancelled = False
        self._onvif_success_callback = on_success
        self._onvif_failure_callback = on_failure
        self.title_bar.set_discovery_busy(True)
        task.signals.succeeded.connect(self._on_onvif_task_succeeded)
        task.signals.failed.connect(self._on_onvif_task_failed)
        progress.rejected.connect(lambda current=task: self._cancel_onvif_task(current))
        progress.show()
        task.start()
        return True

    def _cancel_onvif_task(self, task: _OnvifTask) -> None:
        if task is not self._onvif_task:
            return
        self._onvif_cancelled = True
        task.cancel()

    def _on_onvif_task_succeeded(self, task: object, result: object) -> None:
        if not isinstance(task, _OnvifTask):
            return
        callback = self._onvif_success_callback
        if callback is None:
            return
        self._finish_onvif_task(task, callback, result=result)

    def _on_onvif_task_failed(self, task: object) -> None:
        if not isinstance(task, _OnvifTask):
            return
        callback = self._onvif_failure_callback
        if callback is None:
            return
        self._finish_onvif_task(task, callback)

    def _finish_onvif_task(
        self,
        task: _OnvifTask,
        callback: Callable[..., None],
        *,
        result: object | None = None,
    ) -> None:
        if task is not self._onvif_task:
            return
        cancelled = self._onvif_cancelled
        progress = self._onvif_progress
        self._onvif_task = None
        self._onvif_progress = None
        self._onvif_cancelled = False
        self._onvif_success_callback = None
        self._onvif_failure_callback = None
        self.title_bar.set_discovery_busy(False)
        if progress is not None:
            if progress.isVisible():
                progress.accept()
            progress.deleteLater()
        if cancelled or self._shutting_down:
            return
        if result is None:
            callback()
        else:
            callback(result)

    def find_cameras(self) -> None:
        progress = OnvifProgressDialog(
            "Поиск камер",
            "Ищем ONVIF-камеры…",
            "Отправляем WS-Discovery Probe по сетевым интерфейсам, затем "
            "запрашиваем профили и RTSP-адреса. Это может занять несколько секунд.",
            self,
        )
        self._begin_onvif_task(
            lambda stop_event: discover_cameras(
                timeout=4.0,
                stop_event=stop_event,
            ),
            progress,
            self._show_discovered_cameras,
            self._show_discovery_error,
        )

    def _show_discovery_error(self) -> None:
        QMessageBox.warning(
            self,
            APP_NAME,
            "Не удалось выполнить поиск камер. Проверьте сетевое подключение "
            "и разрешение брандмауэра для UDP 3702.",
        )

    def _show_discovered_cameras(self, payload: object) -> None:
        cameras = tuple(
            camera
            for camera in (payload if isinstance(payload, tuple) else ())
            if isinstance(camera, DiscoveredCamera)
        )
        if not cameras:
            QMessageBox.information(
                self,
                APP_NAME,
                "ONVIF-камеры не найдены. Убедитесь, что камера находится в той же "
                "сети и ONVIF включён. Брандмауэр также может блокировать UDP 3702.",
            )
            return

        cameras = self._name_discovered_cameras(cameras)
        known_ips = self._camera_ips()
        dialog = OnvifDiscoveryDialog(cameras, known_ips, self)
        if dialog.exec() != OnvifDiscoveryDialog.DialogCode.Accepted:
            return

        added_ips = set(known_ips)
        added = False
        for camera in dialog.selected_cameras():
            ip = self._canonical_ip(camera.ip)
            if ip in added_ips:
                continue
            config = CameraConfig(
                camera_name=camera.name,
                host=camera.ip,
                source="onvif",
                stream_url_hd=camera.stream_url_hd,
                stream_url_sd=camera.stream_url_sd,
                onvif_endpoint=camera.endpoint,
                onvif_media_endpoint=camera.media_endpoint,
                onvif_username=camera.username,
                onvif_password=camera.password,
            )
            self.board.create_camera(config)
            added_ips.add(ip)
            added = True
        if added:
            self._persist_current_config()

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
        needs_onvif_refresh = (
            candidate_camera.source == "onvif"
            and not candidate_camera.is_configured()
        )
        candidate = self._snapshot_config(
            replacements={camera_id: candidate_camera}
        )
        if not self._persist_candidate(candidate):
            return
        applied_camera = next(
            camera for camera in candidate.cameras if camera.camera_id == camera_id
        )
        self.board.replace_camera_config(applied_camera)
        if needs_onvif_refresh:
            self._refresh_onvif_camera(applied_camera)

    def _refresh_onvif_camera(self, camera: CameraConfig) -> None:
        progress = OnvifProgressDialog(
            "Подключение камеры",
            "Запрашиваем поток через ONVIF…",
            "Проверяем введённую учётную запись и выполняем "
            "GetProfiles → GetStreamUri. RTSP-адрес будет взят из ответа камеры.",
            self,
        )
        self._begin_onvif_task(
            lambda stop_event: resolve_onvif_camera(
                camera.onvif_endpoint,
                ip=camera.host,
                credentials=(camera.onvif_username, camera.onvif_password),
                stop_event=stop_event,
            ),
            progress,
            lambda result: self._apply_resolved_onvif_camera(camera, result),
            self._show_onvif_credentials_error,
        )

    def _apply_resolved_onvif_camera(
        self,
        requested: CameraConfig,
        payload: object,
    ) -> None:
        if not isinstance(payload, DiscoveredCamera) or not payload.ready:
            self._show_onvif_credentials_error()
            return
        tile = self.board.tile_for(requested.camera_id)
        if tile is None:
            return
        current = tile.config
        if (
            current.onvif_endpoint != requested.onvif_endpoint
            or current.onvif_username != requested.onvif_username
            or current.onvif_password != requested.onvif_password
        ):
            return
        resolved = current.updated(
            stream_url_hd=payload.stream_url_hd,
            stream_url_sd=payload.stream_url_sd,
            onvif_media_endpoint=payload.media_endpoint,
        )
        candidate = self._snapshot_config(
            replacements={current.camera_id: resolved}
        )
        if not self._persist_candidate(candidate):
            return
        applied_camera = next(
            item for item in candidate.cameras if item.camera_id == current.camera_id
        )
        self.board.replace_camera_config(applied_camera)

    def _show_onvif_credentials_error(self) -> None:
        QMessageBox.warning(
            self,
            APP_NAME,
            "Не удалось получить RTSP-поток через ONVIF. Проверьте логин и пароль, "
            "а также что ONVIF включён в настройках камеры.",
        )

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

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            self.isFullScreen()
            and not self.isMinimized()
            and self.isActiveWindow()
            and event.type() in (QEvent.Type.Enter, QEvent.Type.MouseMove)
            and isinstance(watched, QWidget)
            and watched.window() is self
        ):
            self._register_fullscreen_activity()
        return super().eventFilter(watched, event)

    def _position_fullscreen_overlay(self) -> None:
        self.fullscreen_overlay.setGeometry(
            max(
                self.FULLSCREEN_OVERLAY_MARGIN,
                self.width()
                - self.FULLSCREEN_OVERLAY_WIDTH
                - self.FULLSCREEN_OVERLAY_MARGIN,
            ),
            self.FULLSCREEN_OVERLAY_MARGIN,
            self.FULLSCREEN_OVERLAY_WIDTH,
            self.FULLSCREEN_OVERLAY_HEIGHT,
        )
        if self.fullscreen_overlay.isVisible():
            self.fullscreen_overlay.raise_()

    def _set_fullscreen_cursor_hidden(self, hidden: bool) -> None:
        if hidden == self._fullscreen_cursor_hidden:
            return
        self._fullscreen_cursor_hidden = hidden
        if hidden:
            QApplication.setOverrideCursor(QCursor(Qt.CursorShape.BlankCursor))
        else:
            QApplication.restoreOverrideCursor()

    def _set_fullscreen_overlay_visible(
        self,
        visible: bool,
        *,
        animate: bool = True,
    ) -> None:
        if visible and (not self.isFullScreen() or self.isMinimized()):
            return
        if visible == self._fullscreen_overlay_target_visible:
            if visible:
                self.fullscreen_overlay.show()
                self.fullscreen_overlay.raise_()
            return

        self._fullscreen_overlay_target_visible = visible
        self._fullscreen_overlay_animation.stop()
        target_opacity = 1.0 if visible else 0.0
        if visible:
            self._position_fullscreen_overlay()
            self.fullscreen_overlay.show()
            self.fullscreen_overlay.raise_()

        if (
            not animate
            or abs(self._fullscreen_overlay_effect.opacity() - target_opacity) < 0.01
        ):
            self._fullscreen_overlay_effect.setOpacity(target_opacity)
            self._fullscreen_overlay_animation_finished()
            return

        self._fullscreen_overlay_animation.setStartValue(
            self._fullscreen_overlay_effect.opacity()
        )
        self._fullscreen_overlay_animation.setEndValue(target_opacity)
        self._fullscreen_overlay_animation.start()

    def _fullscreen_overlay_animation_finished(self) -> None:
        if self._fullscreen_overlay_target_visible:
            self._fullscreen_overlay_effect.setOpacity(1.0)
            self.fullscreen_overlay.show()
            self.fullscreen_overlay.raise_()
            return
        self._fullscreen_overlay_effect.setOpacity(0.0)
        self.fullscreen_overlay.hide()

    def _register_fullscreen_activity(self) -> None:
        if not self.isFullScreen() or self.isMinimized():
            return
        self._set_fullscreen_cursor_hidden(False)
        self._set_fullscreen_overlay_visible(True)
        self._fullscreen_idle_timer.start()

    def _fullscreen_idle_timeout(self) -> None:
        if not self.isFullScreen() or self.isMinimized():
            self._deactivate_fullscreen_ui()
            return
        if not self.isActiveWindow():
            self._set_fullscreen_cursor_hidden(False)
            return
        if (
            self.fullscreen_overlay.underMouse()
            or QApplication.mouseButtons() != Qt.MouseButton.NoButton
        ):
            self._set_fullscreen_cursor_hidden(False)
            self._fullscreen_idle_timer.start()
            return
        self._set_fullscreen_cursor_hidden(True)
        self._set_fullscreen_overlay_visible(False)

    def _activate_fullscreen_ui(self) -> None:
        if not self.isFullScreen() or self.isMinimized():
            return
        self.title_bar.hide()
        self._position_fullscreen_overlay()
        self._register_fullscreen_activity()

    def _deactivate_fullscreen_ui(self) -> None:
        self._fullscreen_idle_timer.stop()
        self._fullscreen_overlay_animation.stop()
        self._fullscreen_overlay_target_visible = False
        self._fullscreen_overlay_effect.setOpacity(0.0)
        self.fullscreen_overlay.hide()
        self._set_fullscreen_cursor_hidden(False)

    def _minimize_from_fullscreen(self) -> None:
        self._fullscreen_idle_timer.stop()
        self._set_fullscreen_cursor_hidden(False)
        self.showMinimized()

    def _escape_pressed(self) -> None:
        # В обычном режиме Esc намеренно ничего не делает и не закрывает окно.
        if self.isFullScreen():
            self.toggle_fullscreen()

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self._deactivate_fullscreen_ui()
            self.title_bar.show()
            if self._was_maximized_before_fullscreen:
                self.showMaximized()
            else:
                self.showNormal()
        else:
            self._was_maximized_before_fullscreen = self.isMaximized()
            self.title_bar.hide()
            self.showFullScreen()
            QTimer.singleShot(0, self._activate_fullscreen_ui)
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
        if self.isFullScreen():
            self.title_bar.hide()
            self._position_fullscreen_overlay()
            if self.isMinimized() or not self.isActiveWindow():
                self._fullscreen_idle_timer.stop()
                self._set_fullscreen_cursor_hidden(False)
            else:
                self._register_fullscreen_activity()
        else:
            self._deactivate_fullscreen_ui()
            self.title_bar.show()
        self.update()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in (
            QEvent.Type.ActivationChange,
            QEvent.Type.WindowStateChange,
        ):
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
            self.title_bar.set_compact(self.width() < 1080)
        if hasattr(self, "fullscreen_overlay"):
            self._position_fullscreen_overlay()

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
        self._deactivate_fullscreen_ui()
        if self._application_event_filter_installed:
            application = QApplication.instance()
            if application is not None:
                application.removeEventFilter(self)
            self._application_event_filter_installed = False
        needs_save = self._config_dirty or self._save_timer.isActive()
        self._save_timer.stop()
        if needs_save:
            self._persist_current_config(show_error=False)
        if self._onvif_task is not None:
            self._onvif_task.cancel()
        self._shutting_down = True
        self.board.shutdown()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.shutdown()
        event.accept()
