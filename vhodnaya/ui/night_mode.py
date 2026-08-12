"""Ночной интервал и лёгкие чёрные оверлеи поверх окон приложения."""

from __future__ import annotations

import weakref
from collections.abc import Callable
from datetime import datetime, time

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QRectF,
    QTimer,
    Qt,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QColor, QPaintEvent, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget

from ..config import AppConfig


NIGHT_OVERLAY_ALPHA = 0.8
NIGHT_CHECK_INTERVAL_MS = 20_000
NIGHT_TRANSITION_DURATION_MS = 1_500
SURFACE_CORNER_RADIUS = 6.0


def _hhmm_to_seconds(value: str) -> int:
    """Преобразует уже проверенное HH:MM в секунды от начала суток."""

    hour, minute = value.split(":", 1)
    return (int(hour) * 60 + int(minute)) * 60


def is_night_time(current: time, start: str, end: str) -> bool:
    """Проверяет полуинтервал [start, end); равные границы задают пустоту."""

    current_seconds = current.hour * 3600 + current.minute * 60 + current.second
    start_seconds = _hhmm_to_seconds(start)
    end_seconds = _hhmm_to_seconds(end)
    if start_seconds == end_seconds:
        return False
    if start_seconds < end_seconds:
        return start_seconds <= current_seconds < end_seconds
    return current_seconds >= start_seconds or current_seconds < end_seconds


class NightOverlay(QWidget):
    """Неинтерактивный слой размером ровно с surface родительского окна."""

    def __init__(
        self,
        window: QWidget,
        surface: QWidget,
        *,
        corner_radius: float = SURFACE_CORNER_RADIUS,
    ) -> None:
        super().__init__(window)
        self._surface = surface
        self._corner_radius = max(0.0, float(corner_radius))
        self._alpha = 0.0
        # raise_() сам порождает события у окна и surface, а те снова входят
        # в eventFilter — без замка получается бесконечная рекурсия.
        self._raising = False
        self.setObjectName("nightModeOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.hide()
        window.installEventFilter(self)
        surface.installEventFilter(self)
        self.sync_geometry()

    @property
    def alpha(self) -> float:
        return self._alpha

    def set_alpha(self, alpha: float) -> None:
        value = max(0.0, min(NIGHT_OVERLAY_ALPHA, float(alpha)))
        changed = abs(value - self._alpha) >= 0.001
        self._alpha = value
        if value <= 0.0:
            self.hide()
            return
        if self.isHidden():
            self.show()
        self.raise_overlay()
        if changed:
            self.update()

    def sync_geometry(self) -> None:
        self.setGeometry(self._surface.geometry())
        self.raise_overlay()

    def raise_overlay(self) -> None:
        if self._raising:
            return
        self._raising = True
        try:
            self.raise_()
        except RuntimeError:
            # C++-объект уже удалён (окно закрывается) — фильтры отработают
            # вхолостую и умрут вместе с ним.
            pass
        finally:
            self._raising = False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        # Фильтры переживают удаление C++-части оверлея: при закрытии окна
        # события ещё приходят, а обращаться к себе уже нельзя.
        surface = getattr(self, "_surface", None)
        if surface is None:
            return False
        try:
            event_type = event.type()
        except RuntimeError:
            return False
        if watched is self._surface and event_type in (
            QEvent.Type.Move,
            QEvent.Type.Resize,
            QEvent.Type.Show,
        ):
            self.sync_geometry()
        elif watched is self._surface and event_type in (
            QEvent.Type.DynamicPropertyChange,
            QEvent.Type.ChildAdded,
        ):
            self.update()
            QTimer.singleShot(0, self.raise_overlay)
        elif watched is self.parentWidget() and event_type in (
            QEvent.Type.Resize,
            QEvent.Type.Show,
            QEvent.Type.ChildAdded,
        ):
            QTimer.singleShot(0, self.sync_geometry)
        return False

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        if self._alpha <= 0.0:
            return
        color = QColor(0, 0, 0, round(self._alpha * 255))
        painter = QPainter(self)
        if bool(self._surface.property("flat")) or self._corner_radius <= 0.0:
            painter.fillRect(self.rect(), color)
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.WindingFill)
        path.addRoundedRect(
            QRectF(self.rect()).adjusted(0.0, 0.0, -0.5, -0.5),
            self._corner_radius,
            self._corner_radius,
        )
        painter.fillPath(path, color)


class NightModeController(QObject):
    """Хранит общую alpha, проверяет часы и синхронизирует оверлеи окон."""

    alpha_changed = Signal(float)

    def __init__(
        self,
        config: AppConfig,
        parent: QObject | None = None,
        *,
        now_provider: Callable[[], time] | None = None,
    ) -> None:
        super().__init__(parent)
        config.validate()
        self._now_provider = now_provider or (lambda: datetime.now().time())
        self._enabled = config.night_mode
        self._start = config.night_start
        self._end = config.night_end
        self._alpha = 0.0
        self._overlays: dict[int, NightOverlay] = {}

        self._animation = QVariantAnimation(self)
        self._animation.setDuration(NIGHT_TRANSITION_DURATION_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._animation.valueChanged.connect(self._animation_value_changed)

        self._timer = QTimer(self)
        self._timer.setInterval(NIGHT_CHECK_INTERVAL_MS)
        self._timer.setTimerType(Qt.TimerType.CoarseTimer)
        self._timer.timeout.connect(self.reevaluate)

        # Первый кадр сразу получает правильную яркость: вспышки при запуске нет.
        self._initialized = False
        self.reevaluate(animate=False)
        self._timer.start()

    @property
    def alpha(self) -> float:
        return self._alpha

    def register_window(
        self,
        window: QWidget,
        surface: QWidget,
        *,
        corner_radius: float = SURFACE_CORNER_RADIUS,
    ) -> NightOverlay:
        key = id(window)
        current = self._overlays.get(key)
        if current is not None:
            return current
        overlay = NightOverlay(
            window,
            surface,
            corner_radius=corner_radius,
        )
        self._overlays[key] = overlay
        controller_ref = weakref.ref(self)

        def forget_overlay(
            _object: QObject | None = None,
            *,
            overlay_key: int = key,
        ) -> None:
            controller = controller_ref()
            if controller is not None:
                controller._overlays.pop(overlay_key, None)

        window.destroyed.connect(forget_overlay)
        overlay.set_alpha(self._alpha)
        return overlay

    def overlay_for(self, window: QWidget) -> NightOverlay | None:
        return self._overlays.get(id(window))

    def unregister_window(self, window: QWidget) -> None:
        overlay = self._overlays.pop(id(window), None)
        if overlay is not None:
            overlay.hide()
            overlay.deleteLater()

    def raise_overlay(self, window: QWidget) -> None:
        overlay = self.overlay_for(window)
        if overlay is not None:
            overlay.sync_geometry()

    def raise_all_overlays(self) -> None:
        for overlay in tuple(self._overlays.values()):
            overlay.sync_geometry()

    def apply_config(self, config: AppConfig) -> None:
        config.validate()
        changed = (
            config.night_mode != self._enabled
            or config.night_start != self._start
            or config.night_end != self._end
        )
        self._enabled = config.night_mode
        self._start = config.night_start
        self._end = config.night_end
        if changed:
            self.reevaluate(animate=True)

    def reevaluate(self, *, animate: bool = True) -> None:
        active = self._enabled and is_night_time(
            self._now_provider(),
            self._start,
            self._end,
        )
        self._set_target_alpha(
            NIGHT_OVERLAY_ALPHA if active else 0.0,
            animate=animate,
        )

    def _set_target_alpha(self, target: float, *, animate: bool) -> None:
        target = max(0.0, min(NIGHT_OVERLAY_ALPHA, float(target)))
        self._animation.stop()
        if not animate or abs(target - self._alpha) < 0.001:
            self._set_alpha(target)
            return
        self._animation.setStartValue(self._alpha)
        self._animation.setEndValue(target)
        self._animation.start()

    def _animation_value_changed(self, value: object) -> None:
        self._set_alpha(float(value))

    def _set_alpha(self, alpha: float) -> None:
        value = max(0.0, min(NIGHT_OVERLAY_ALPHA, float(alpha)))
        if value == self._alpha and self._initialized:
            return
        self._alpha = value
        self._initialized = True
        for overlay in tuple(self._overlays.values()):
            overlay.set_alpha(value)
        self.alpha_changed.emit(value)

    def shutdown(self) -> None:
        self._timer.stop()
        self._animation.stop()


def controller_from_parent(parent: QWidget | None) -> NightModeController | None:
    """Находит контроллер главного окна через цепочку родительских диалогов."""

    current = parent
    while current is not None:
        controller = getattr(current, "night_mode_controller", None)
        if isinstance(controller, NightModeController):
            return controller
        current = current.parentWidget()
    return None
