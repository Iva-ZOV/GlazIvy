"""Интерактивный редактор прямоугольной зоны распознавания."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..config import DetectZone
from .theme import BACKGROUND, BRONZE, SURFACE, TEXT, TEXT_MUTED
from .widgets import _mascot_pixmap, frame_target_rect


MINIMUM_ZONE_FRACTION = 0.05
_HANDLE_SIZE = 8.0
_HANDLE_HIT_MARGIN = 4.0
_EDGE_HIT_WIDTH = 7.0


def editor_point_to_normalized(
    point: QPointF,
    canvas_width: float,
    canvas_height: float,
    frame_width: int,
    frame_height: int,
) -> QPointF:
    """Переводит координату редактора в доли кадра с прижимом letterbox."""

    target = frame_target_rect(
        canvas_width,
        canvas_height,
        frame_width,
        frame_height,
    )
    if target.isEmpty():
        return QPointF()
    x = (point.x() - target.left()) / target.width()
    y = (point.y() - target.top()) / target.height()
    return QPointF(
        min(1.0, max(0.0, x)),
        min(1.0, max(0.0, y)),
    )


class DetectionZoneCanvas(QWidget):
    """Рисует snapshot камеры и редактирует зону исключительно в долях кадра."""

    def __init__(
        self,
        frame: QImage | None,
        zone: DetectZone | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._frame = QImage(frame) if frame is not None and not frame.isNull() else None
        self._zone = zone
        self._gesture: str | None = None
        self._gesture_start = QPointF()
        self._gesture_original: DetectZone | None = None
        self._preview_zone: DetectZone | None = None
        self._mouse_grabbed = False

        self.setObjectName("detectionZoneCanvas")
        self.setMinimumSize(480, 270)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def sizeHint(self) -> QSize:
        return QSize(880, 495)

    def zone(self) -> DetectZone | None:
        return self._zone

    def set_zone(self, zone: DetectZone | None) -> None:
        self.cancel_active_gesture()
        self._zone = zone
        self._preview_zone = None
        self.update()

    def _frame_size(self) -> tuple[int, int]:
        if self._frame is None:
            return (16, 9)
        return (self._frame.width(), self._frame.height())

    def frame_rect(self) -> QRectF:
        frame_width, frame_height = self._frame_size()
        return frame_target_rect(
            self.width(),
            self.height(),
            frame_width,
            frame_height,
        )

    def normalized_point(self, point: QPointF) -> QPointF:
        frame_width, frame_height = self._frame_size()
        return editor_point_to_normalized(
            point,
            self.width(),
            self.height(),
            frame_width,
            frame_height,
        )

    def _zone_rect(self, zone: DetectZone | None = None) -> QRectF:
        current = self._zone if zone is None else zone
        target = self.frame_rect()
        if current is None or target.isEmpty():
            return QRectF()
        x1, y1, x2, y2 = current
        return QRectF(
            target.left() + x1 * target.width(),
            target.top() + y1 * target.height(),
            (x2 - x1) * target.width(),
            (y2 - y1) * target.height(),
        )

    @staticmethod
    def _handle_centers(rect: QRectF) -> dict[str, QPointF]:
        center = rect.center()
        return {
            "nw": rect.topLeft(),
            "n": QPointF(center.x(), rect.top()),
            "ne": rect.topRight(),
            "e": QPointF(rect.right(), center.y()),
            "se": rect.bottomRight(),
            "s": QPointF(center.x(), rect.bottom()),
            "sw": rect.bottomLeft(),
            "w": QPointF(rect.left(), center.y()),
        }

    @staticmethod
    def _point_near(point: QPointF, center: QPointF, radius: float) -> bool:
        return abs(point.x() - center.x()) <= radius and abs(point.y() - center.y()) <= radius

    def _hit_test(self, point: QPointF) -> str:
        rect = self._zone_rect()
        if rect.isEmpty():
            return "new"
        centers = self._handle_centers(rect)
        corner_radius = _HANDLE_SIZE / 2.0 + _HANDLE_HIT_MARGIN
        # Углы проверяются до кромок, а всё — до прижима координаты к кадру.
        for name in ("nw", "ne", "se", "sw"):
            if self._point_near(point, centers[name], corner_radius):
                return f"resize:{name}"

        inside_x = rect.left() - corner_radius <= point.x() <= rect.right() + corner_radius
        inside_y = rect.top() - corner_radius <= point.y() <= rect.bottom() + corner_radius
        if inside_x and abs(point.y() - rect.top()) <= _EDGE_HIT_WIDTH:
            return "resize:n"
        if inside_x and abs(point.y() - rect.bottom()) <= _EDGE_HIT_WIDTH:
            return "resize:s"
        if inside_y and abs(point.x() - rect.left()) <= _EDGE_HIT_WIDTH:
            return "resize:w"
        if inside_y and abs(point.x() - rect.right()) <= _EDGE_HIT_WIDTH:
            return "resize:e"
        if rect.contains(point):
            return "move"
        return "new"

    @staticmethod
    def _cursor_for_gesture(gesture: str) -> Qt.CursorShape:
        if gesture.endswith(("nw", "se")):
            return Qt.CursorShape.SizeFDiagCursor
        if gesture.endswith(("ne", "sw")):
            return Qt.CursorShape.SizeBDiagCursor
        if gesture.endswith(("n", "s")):
            return Qt.CursorShape.SizeVerCursor
        if gesture.endswith(("e", "w")):
            return Qt.CursorShape.SizeHorCursor
        if gesture == "move":
            return Qt.CursorShape.SizeAllCursor
        return Qt.CursorShape.CrossCursor

    @staticmethod
    def _ordered_zone(first: QPointF, second: QPointF) -> DetectZone:
        return (
            min(first.x(), second.x()),
            min(first.y(), second.y()),
            max(first.x(), second.x()),
            max(first.y(), second.y()),
        )

    @staticmethod
    def _axis_from_anchor(
        anchor: float,
        moving: float,
        *,
        prefer_lower: bool,
    ) -> tuple[float, float]:
        """Нормализует переворот кромки и сохраняет минимальный размер."""

        moving = min(1.0, max(0.0, moving))
        if abs(moving - anchor) < MINIMUM_ZONE_FRACTION:
            lower_side = moving < anchor or (moving == anchor and prefer_lower)
            moving = (
                max(0.0, anchor - MINIMUM_ZONE_FRACTION)
                if lower_side
                else min(1.0, anchor + MINIMUM_ZONE_FRACTION)
            )
        return (min(anchor, moving), max(anchor, moving))

    def _moved_zone(self, current: QPointF) -> DetectZone:
        original = self._gesture_original
        if original is None:
            raise RuntimeError("Перенос начат без исходной зоны.")
        x1, y1, x2, y2 = original
        width = x2 - x1
        height = y2 - y1
        dx = current.x() - self._gesture_start.x()
        dy = current.y() - self._gesture_start.y()
        next_x1 = min(1.0 - width, max(0.0, x1 + dx))
        next_y1 = min(1.0 - height, max(0.0, y1 + dy))
        return (next_x1, next_y1, next_x1 + width, next_y1 + height)

    def _resized_zone(self, current: QPointF, handle: str) -> DetectZone:
        original = self._gesture_original
        if original is None:
            raise RuntimeError("Изменение размера начато без исходной зоны.")
        x1, y1, x2, y2 = original
        if "w" in handle:
            x1, x2 = self._axis_from_anchor(x2, current.x(), prefer_lower=True)
        elif "e" in handle:
            x1, x2 = self._axis_from_anchor(x1, current.x(), prefer_lower=False)
        if "n" in handle:
            y1, y2 = self._axis_from_anchor(y2, current.y(), prefer_lower=True)
        elif "s" in handle:
            y1, y2 = self._axis_from_anchor(y1, current.y(), prefer_lower=False)
        return (x1, y1, x2, y2)

    def _begin_mouse_grab(self) -> None:
        if self._mouse_grabbed:
            return
        self.grabMouse()
        self._mouse_grabbed = True

    def _end_mouse_grab(self) -> None:
        if not self._mouse_grabbed:
            return
        self.releaseMouse()
        self._mouse_grabbed = False

    def cancel_active_gesture(self) -> None:
        if self._gesture is not None:
            self._zone = self._gesture_original
        self._gesture = None
        self._gesture_original = None
        self._preview_zone = None
        self._end_mouse_grab()
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            point = event.position()
            self._gesture = self._hit_test(point)
            self._gesture_start = self.normalized_point(point)
            self._gesture_original = self._zone
            self._preview_zone = self._zone
            self.setCursor(self._cursor_for_gesture(self._gesture))
            self._begin_mouse_grab()
            self.setFocus(Qt.FocusReason.MouseFocusReason)
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        point = event.position()
        if self._gesture is None:
            self.setCursor(self._cursor_for_gesture(self._hit_test(point)))
            event.accept()
            return

        current = self.normalized_point(point)
        if self._gesture == "new":
            self._preview_zone = self._ordered_zone(self._gesture_start, current)
        elif self._gesture == "move":
            self._zone = self._moved_zone(current)
        else:
            self._zone = self._resized_zone(current, self._gesture.partition(":")[2])
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._gesture is not None:
            if self._gesture == "new" and self._preview_zone is not None:
                x1, y1, x2, y2 = self._preview_zone
                if (
                    x2 - x1 >= MINIMUM_ZONE_FRACTION
                    and y2 - y1 >= MINIMUM_ZONE_FRACTION
                ):
                    self._zone = self._preview_zone
                else:
                    self._zone = self._gesture_original
            self._gesture = None
            self._gesture_original = None
            self._preview_zone = None
            self._end_mouse_grab()
            self.setCursor(self._cursor_for_gesture(self._hit_test(event.position())))
            self.update()
        event.accept()

    def event(self, event: QEvent) -> bool:
        if event.type() in (QEvent.Type.WindowDeactivate, QEvent.Type.Hide):
            self.cancel_active_gesture()
        return super().event(event)

    def _draw_placeholder(self, painter: QPainter, target: QRectF) -> None:
        gradient = QLinearGradient(target.topLeft(), target.bottomRight())
        gradient.setColorAt(0.0, QColor("#191A16"))
        gradient.setColorAt(1.0, QColor(BACKGROUND))
        painter.fillRect(target, gradient)

        mascot_height = max(72, min(190, round(target.height() * 0.42)))
        mascot = _mascot_pixmap(
            "wrench",
            QSize(max(72, round(target.width() * 0.28)), mascot_height),
            self.devicePixelRatioF(),
        )
        text_top = target.center().y() + 42.0
        if mascot is not None:
            mascot_size = mascot.deviceIndependentSize()
            mascot_position = QPointF(
                target.center().x() - mascot_size.width() / 2.0,
                target.center().y() - mascot_size.height() / 2.0 - 62.0,
            )
            painter.drawPixmap(mascot_position, mascot)
            text_top = mascot_position.y() + mascot_size.height() + 8.0

        title_font = QFont(self.font())
        title_font.setPixelSize(14)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(QColor(TEXT))
        painter.drawText(
            QRectF(target.left() + 20, text_top, target.width() - 40, 24),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            "КАДРА ПОКА НЕТ",
        )
        hint_font = QFont(self.font())
        hint_font.setPixelSize(11)
        painter.setFont(hint_font)
        painter.setPen(QColor(TEXT_MUTED))
        painter.drawText(
            QRectF(target.left() + 20, text_top + 27, target.width() - 40, 24),
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
            "Нарисуйте зону прямо на этой заглушке",
        )

    def _draw_zone(self, painter: QPainter) -> None:
        zone = self._preview_zone if self._gesture == "new" else self._zone
        if zone is None:
            return
        target = self.frame_rect()
        rect = self._zone_rect(zone)
        if target.isEmpty() or rect.isEmpty():
            return

        shade = QColor(0, 0, 0, 100)
        painter.fillRect(
            QRectF(target.left(), target.top(), target.width(), rect.top() - target.top()),
            shade,
        )
        painter.fillRect(
            QRectF(target.left(), rect.bottom(), target.width(), target.bottom() - rect.bottom()),
            shade,
        )
        painter.fillRect(
            QRectF(target.left(), rect.top(), rect.left() - target.left(), rect.height()),
            shade,
        )
        painter.fillRect(
            QRectF(rect.right(), rect.top(), target.right() - rect.right(), rect.height()),
            shade,
        )

        outline = QPen(QColor(BRONZE), 1.5)
        outline.setCosmetic(True)
        outline.setStyle(Qt.PenStyle.DashLine)
        outline.setDashPattern([5.0, 4.0])
        painter.setPen(outline)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)

        x1, y1, x2, y2 = zone
        if x2 - x1 < MINIMUM_ZONE_FRACTION or y2 - y1 < MINIMUM_ZONE_FRACTION:
            return
        painter.setPen(QPen(QColor(BRONZE), 1.0))
        painter.setBrush(QColor("#E6D6AE"))
        for center in self._handle_centers(rect).values():
            painter.drawRect(
                QRectF(
                    center.x() - _HANDLE_SIZE / 2.0,
                    center.y() - _HANDLE_SIZE / 2.0,
                    _HANDLE_SIZE,
                    _HANDLE_SIZE,
                )
            )

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(SURFACE))
        target = self.frame_rect()
        if self._frame is None:
            self._draw_placeholder(painter, target)
        else:
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawImage(target, self._frame)
        self._draw_zone(painter)
