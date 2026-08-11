"""Переиспользуемые отрисовываемые виджеты интерфейса."""

from __future__ import annotations

import math
from datetime import datetime

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeyEvent,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSpacerItem,
    QWidget,
)

from .theme import ACCENT, ACCENT_BLUE, DANGER, SUCCESS, TEXT, TEXT_MUTED, WARNING


def _color(value: str, alpha: int | None = None) -> QColor:
    color = QColor(value)
    if alpha is not None:
        color.setAlpha(alpha)
    return color


class LogoGlyph(QWidget):
    """Минималистичная камера/объектив без зависимости от иконочного шрифта."""

    def __init__(self, size: int = 28, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, _color(ACCENT_BLUE))
        gradient.setColorAt(1.0, _color(ACCENT))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawRoundedRect(rect, rect.width() * 0.29, rect.height() * 0.29)

        center = rect.center()
        radius = rect.width() * 0.24
        painter.setBrush(_color("#091017", 230))
        painter.drawEllipse(center, radius, radius)
        painter.setBrush(_color("#D8FFF8", 225))
        painter.drawEllipse(
            QPointF(center.x() - radius * 0.25, center.y() - radius * 0.25),
            radius * 0.27,
            radius * 0.27,
        )


class ToolIconButton(QAbstractButton):
    """Кнопка заголовка с векторным глифом и мягким hover."""

    def __init__(
        self,
        kind: str,
        tooltip: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.kind = kind
        self.setToolTip(tooltip)
        self.setFixedSize(34, 34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._hover = 0.0
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(150)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._set_hover)

    def sizeHint(self) -> QSize:
        return QSize(34, 34)

    def _set_hover(self, value: object) -> None:
        self._hover = float(value)
        self.update()

    def _animate_to(self, end: float) -> None:
        self._animation.stop()
        self._animation.setStartValue(self._hover)
        self._animation.setEndValue(end)
        self._animation.start()

    def enterEvent(self, event: object) -> None:
        self._animate_to(1.0)
        super().enterEvent(event)  # type: ignore[arg-type]

    def leaveEvent(self, event: object) -> None:
        self._animate_to(0.0)
        super().leaveEvent(event)  # type: ignore[arg-type]

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        alpha = int(9 + 24 * self._hover)
        if self.isDown():
            alpha = 48
        background = _color(DANGER if self.kind == "close" else "#FFFFFF", alpha)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 9, 9)

        glyph = _color("#FFFFFF", int(150 + 90 * max(self._hover, 0.15)))
        if self.kind == "close" and self._hover > 0.01:
            glyph = _color("#FFABB1")
        pen = QPen(glyph, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        cx, cy = self.width() / 2, self.height() / 2

        if self.kind == "close":
            painter.drawLine(QPointF(cx - 4, cy - 4), QPointF(cx + 4, cy + 4))
            painter.drawLine(QPointF(cx + 4, cy - 4), QPointF(cx - 4, cy + 4))
        elif self.kind == "minimize":
            painter.drawLine(QPointF(cx - 5, cy + 2), QPointF(cx + 5, cy + 2))
        elif self.kind == "fullscreen":
            for sx, sy in ((-1, -1), (1, -1), (-1, 1), (1, 1)):
                x = cx + sx * 5
                y = cy + sy * 5
                painter.drawLine(QPointF(x, y), QPointF(x - sx * 3.5, y))
                painter.drawLine(QPointF(x, y), QPointF(x, y - sy * 3.5))
        elif self.kind == "settings":
            painter.drawEllipse(QPointF(cx, cy), 3.2, 3.2)
            painter.drawEllipse(QPointF(cx, cy), 6.0, 6.0)
            for index in range(8):
                angle = math.pi * index / 4
                start = QPointF(cx + math.cos(angle) * 6.8, cy + math.sin(angle) * 6.8)
                end = QPointF(cx + math.cos(angle) * 8.3, cy + math.sin(angle) * 8.3)
                painter.drawLine(start, end)
        elif self.kind == "back":
            painter.drawLine(QPointF(cx + 4, cy - 5), QPointF(cx - 2, cy))
            painter.drawLine(QPointF(cx - 2, cy), QPointF(cx + 4, cy + 5))


class SegmentedControl(QWidget):
    value_changed = Signal(str)

    def __init__(
        self,
        labels: tuple[str, ...],
        values: tuple[str, ...],
        value: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if len(labels) != len(values) or not labels:
            raise ValueError("labels и values должны быть непустыми и одинаковыми")
        self.labels = labels
        self.values = values
        self._index = values.index(value) if value in values else 0
        self._position = float(self._index)
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(210)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._set_position)
        self.setFixedHeight(32)
        self.setMinimumWidth(82 if len(labels) == 2 else 112)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self) -> QSize:
        widths = [self.fontMetrics().horizontalAdvance(label) + 24 for label in self.labels]
        return QSize(sum(widths), 32)

    def value(self) -> str:
        return self.values[self._index]

    def set_value(self, value: str, *, emit: bool = False, animate: bool = True) -> None:
        if value not in self.values:
            return
        index = self.values.index(value)
        changed = index != self._index
        self._index = index
        self._animation.stop()
        if animate:
            self._animation.setStartValue(self._position)
            self._animation.setEndValue(float(index))
            self._animation.start()
        else:
            self._position = float(index)
            self.update()
        if changed and emit:
            self.value_changed.emit(value)

    def _set_position(self, value: object) -> None:
        self._position = float(value)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            index = min(
                len(self.values) - 1,
                max(0, int(event.position().x() / max(1, self.width()) * len(self.values))),
            )
            self.set_value(self.values[index], emit=True)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            index = max(0, self._index - 1)
        elif event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            index = min(len(self.values) - 1, self._index + 1)
        elif event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            index = (self._index + 1) % len(self.values)
        else:
            super().keyPressEvent(event)
            return
        self.set_value(self.values[index], emit=True)
        event.accept()

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        outer = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(_color("#FFFFFF", 22), 1))
        painter.setBrush(_color("#05080D", 125))
        painter.drawRoundedRect(outer, 10, 10)

        segment_width = outer.width() / len(self.values)
        slider = QRectF(
            outer.left() + segment_width * self._position + 2.5,
            outer.top() + 2.5,
            segment_width - 5,
            outer.height() - 5,
        )
        gradient = QLinearGradient(slider.topLeft(), slider.bottomRight())
        gradient.setColorAt(0.0, _color("#313A48", 245))
        gradient.setColorAt(1.0, _color("#222A35", 245))
        painter.setPen(QPen(_color("#FFFFFF", 30), 1))
        painter.setBrush(gradient)
        painter.drawRoundedRect(slider, 8, 8)

        font = QFont(self.font())
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        for index, label in enumerate(self.labels):
            rect = QRectF(
                outer.left() + index * segment_width,
                outer.top(),
                segment_width,
                outer.height(),
            )
            distance = abs(self._position - index)
            alpha = int(238 - min(1.0, distance) * 105)
            painter.setPen(_color(TEXT, alpha))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

        if self.hasFocus():
            painter.setPen(QPen(_color(ACCENT, 120), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(outer.adjusted(1, 1, -1, -1), 9, 9)


class StatusPill(QWidget):
    _STATE = {
        "unconfigured": ("Не настроена", TEXT_MUTED),
        "connecting": ("Подключение…", ACCENT_BLUE),
        "online": ("В сети", SUCCESS),
        "reconnecting": ("Переподключение…", WARNING),
        "offline": ("Нет связи", DANGER),
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._state = "connecting"
        self._pulse = 0.0
        self.setFixedHeight(28)
        self._animation = QVariantAnimation(self)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.setDuration(1250)
        self._animation.setLoopCount(-1)
        self._animation.setEasingCurve(QEasingCurve.Type.InOutSine)
        self._animation.valueChanged.connect(self._set_pulse)
        self._animation.start()
        self.setToolTip("Состояние RTSP-соединения")

    def _set_pulse(self, value: object) -> None:
        self._pulse = float(value)
        if self._state != "online":
            self.update()

    def set_state(self, state: str) -> None:
        if state not in self._STATE:
            state = "offline"
        if state == self._state:
            return
        self._state = state
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        text = self._STATE[self._state][0]
        return QSize(self.fontMetrics().horizontalAdvance(text) + 38, 28)

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        text, color_name = self._STATE[self._state]
        color = _color(color_name)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 42), 1))
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 15))
        painter.drawRoundedRect(rect, 13.5, 13.5)

        dot_center = QPointF(14, rect.center().y())
        if self._state != "online":
            pulse_radius = 4.5 + 3.2 * self._pulse
            pulse_alpha = int(70 * (1.0 - self._pulse))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(color.red(), color.green(), color.blue(), pulse_alpha))
            painter.drawEllipse(dot_center, pulse_radius, pulse_radius)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(dot_center, 3.4, 3.4)

        font = QFont(self.font())
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(color.red(), color.green(), color.blue(), 235))
        painter.drawText(
            QRectF(25, 0, self.width() - 31, self.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            text,
        )


class VideoCanvas(QWidget):
    double_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(480, 360)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self._frame = None
        self._frame_opacity = 0.0
        self._overlay_opacity = 1.0
        self._state = "connecting"
        self._detail = "Подключение к камере…"
        self._corner_radius = 20.0

        self._frame_animation = QVariantAnimation(self)
        self._frame_animation.setDuration(360)
        self._frame_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._frame_animation.valueChanged.connect(self._set_frame_opacity)

        self._overlay_animation = QVariantAnimation(self)
        self._overlay_animation.setDuration(260)
        self._overlay_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._overlay_animation.valueChanged.connect(self._set_overlay_opacity)

    def set_frame(self, image: object) -> None:
        first_frame = self._frame is None
        self._frame = image
        if first_frame:
            self._frame_animation.stop()
            self._frame_animation.setStartValue(0.0)
            self._frame_animation.setEndValue(1.0)
            self._frame_animation.start()
        else:
            self._frame_opacity = 1.0
        self.update()

    def set_stream_state(self, state: str, detail: str) -> None:
        self._state = state
        self._detail = detail
        target = 0.0 if state == "online" else 1.0
        self._overlay_animation.stop()
        self._overlay_animation.setStartValue(self._overlay_opacity)
        self._overlay_animation.setEndValue(target)
        self._overlay_animation.start()
        self.update()

    def set_corner_radius(self, radius: float) -> None:
        self._corner_radius = max(0.0, radius)
        self.update()

    def _set_frame_opacity(self, value: object) -> None:
        self._frame_opacity = float(value)
        self.update()

    def _set_overlay_opacity(self, value: object) -> None:
        self._overlay_opacity = float(value)
        self.update()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def _draw_background(self, painter: QPainter) -> None:
        rect = QRectF(self.rect())
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, _color("#0A0E14"))
        gradient.setColorAt(0.5, _color("#0D1119"))
        gradient.setColorAt(1.0, _color("#070A0F"))
        painter.fillRect(rect, gradient)

        painter.setPen(QPen(_color("#FFFFFF", 7), 1))
        spacing = max(54, int(min(self.width(), self.height()) / 9))
        for x in range(-self.height(), self.width() + self.height(), spacing):
            painter.drawLine(x, 0, x - self.height(), self.height())

        glow = QLinearGradient(0, 0, self.width(), self.height())
        glow.setColorAt(0.0, _color(ACCENT_BLUE, 12))
        glow.setColorAt(0.55, _color("#000000", 0))
        glow.setColorAt(1.0, _color(ACCENT, 9))
        painter.fillRect(rect, glow)

    def _draw_placeholder(self, painter: QPainter) -> None:
        center = QPointF(self.width() / 2, self.height() / 2 - 16)
        painter.setPen(QPen(_color("#FFFFFF", 38), 1.6))
        painter.setBrush(_color("#FFFFFF", 6))
        painter.drawRoundedRect(
            QRectF(center.x() - 29, center.y() - 22, 58, 44),
            13,
            13,
        )
        painter.setBrush(_color(ACCENT_BLUE, 38))
        painter.drawEllipse(center, 12, 12)
        painter.setBrush(_color("#071019", 220))
        painter.drawEllipse(center, 6, 6)

    def _draw_frame(self, painter: QPainter) -> None:
        if self._frame is None:
            self._draw_placeholder(painter)
            return
        image = self._frame
        source_width = image.width()
        source_height = image.height()
        if source_width <= 0 or source_height <= 0:
            return
        scale = min(self.width() / source_width, self.height() / source_height)
        target_width = source_width * scale
        target_height = source_height * scale
        target = QRectF(
            (self.width() - target_width) / 2,
            (self.height() - target_height) / 2,
            target_width,
            target_height,
        )
        painter.save()
        painter.setOpacity(self._frame_opacity)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(target, image)
        painter.restore()

    def _draw_status_overlay(self, painter: QPainter) -> None:
        if self._overlay_opacity <= 0.01:
            return
        painter.save()
        painter.setOpacity(self._overlay_opacity)

        if self._frame is not None:
            painter.fillRect(self.rect(), _color("#04070B", 58))

        primary = {
            "unconfigured": "Камера не настроена",
            "connecting": "Подключение…",
            "reconnecting": "Восстанавливаем связь…",
            "offline": "Камера недоступна",
        }.get(self._state, "Подключение…")

        max_width = min(460.0, max(280.0, self.width() - 80.0))
        card = QRectF(
            (self.width() - max_width) / 2,
            self.height() / 2 + 28,
            max_width,
            82,
        )
        if self._frame is not None:
            card.moveCenter(QPointF(self.width() / 2, self.height() / 2))

        painter.setPen(QPen(_color("#FFFFFF", 22), 1))
        painter.setBrush(_color("#10161E", 224))
        painter.drawRoundedRect(card, 16, 16)

        title_font = QFont(self.font())
        title_font.setPixelSize(14)
        title_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(title_font)
        painter.setPen(_color(TEXT, 245))
        painter.drawText(
            card.adjusted(18, 12, -18, -43),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            primary,
        )

        detail_font = QFont(self.font())
        detail_font.setPixelSize(11)
        painter.setFont(detail_font)
        painter.setPen(_color(TEXT_MUTED, 235))
        painter.drawText(
            card.adjusted(18, 38, -18, -10),
            Qt.AlignmentFlag.AlignLeft
            | Qt.AlignmentFlag.AlignTop
            | Qt.TextFlag.TextWordWrap,
            self._detail,
        )
        painter.restore()

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._corner_radius > 0.0:
            # Верх примыкает к title bar, скругляются только нижние углы.
            # WindingFill обязателен: иначе пересечение двух подпутей по
            # правилу OddEvenFill вычитается, и клип оставляет лишь нижнюю
            # полосу высотой в радиус — из-за чего видео обрезается снизу.
            clip = QPainterPath()
            clip.setFillRule(Qt.FillRule.WindingFill)
            clip.addRoundedRect(QRectF(self.rect()), self._corner_radius, self._corner_radius)
            clip.addRect(
                QRectF(
                    0,
                    0,
                    self.width(),
                    max(0.0, self.height() - self._corner_radius),
                )
            )
            painter.setClipPath(clip)
        self._draw_background(painter)
        self._draw_frame(painter)
        self._draw_status_overlay(painter)


class TitleBar(QWidget):
    settings_clicked = Signal()
    fullscreen_clicked = Signal()
    minimize_clicked = Signal()
    close_clicked = Signal()
    quality_changed = Signal(str)
    transport_changed = Signal(str)

    _MONTHS = (
        "",
        "января",
        "февраля",
        "марта",
        "апреля",
        "мая",
        "июня",
        "июля",
        "августа",
        "сентября",
        "октября",
        "ноября",
        "декабря",
    )

    def __init__(
        self,
        camera_name: str,
        quality: str,
        transport: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("titleBar")
        self.setFixedHeight(56)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 10, 0)
        layout.setSpacing(8)

        self.logo = LogoGlyph(27, self)
        layout.addWidget(self.logo)
        self.title_label = QLabel(camera_name, self)
        self.title_label.setObjectName("appTitle")
        self.title_label.setMinimumWidth(74)
        self.title_label.setMaximumWidth(190)
        layout.addWidget(self.title_label)
        layout.addItem(QSpacerItem(6, 1, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum))

        self.clock_label = QLabel(self)
        self.clock_label.setObjectName("clockLabel")
        self.clock_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.clock_label)

        self.status = StatusPill(self)
        layout.addWidget(self.status)

        self.transport_control = SegmentedControl(
            ("TCP", "UDP"),
            ("tcp", "udp"),
            transport,
            self,
        )
        self.transport_control.setToolTip("Транспорт RTSP")
        self.transport_control.value_changed.connect(self.transport_changed)
        layout.addWidget(self.transport_control)

        self.quality_control = SegmentedControl(
            ("SD", "HD"),
            ("sd", "hd"),
            quality,
            self,
        )
        self.quality_control.setToolTip("Качество потока")
        self.quality_control.value_changed.connect(self.quality_changed)
        layout.addWidget(self.quality_control)

        self.settings_button = ToolIconButton("settings", "Настройки", self)
        self.fullscreen_button = ToolIconButton("fullscreen", "Полный экран · F11", self)
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

        self._clock_timer = QTimer(self)
        self._clock_timer.setInterval(1000)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start()
        self._clock_enabled = True
        self._compact = False
        self._update_clock()

    def set_camera_name(self, name: str) -> None:
        self.title_label.setText(name)
        self.title_label.setToolTip(name)

    def set_clock_enabled(self, enabled: bool) -> None:
        self._clock_enabled = enabled
        self.clock_label.setVisible(enabled and not self._compact)

    def set_compact(self, compact: bool) -> None:
        self._compact = compact
        self.clock_label.setVisible(self._clock_enabled and not compact)
        self.title_label.setVisible(not compact)

    def _update_clock(self) -> None:
        now = datetime.now()
        self.clock_label.setText(
            f"{now:%H:%M}  ·  {now.day} {self._MONTHS[now.month]}"
        )

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
            window = self.window()
            toggle = getattr(window, "toggle_maximized", None)
            if callable(toggle):
                toggle()
                event.accept()
                return
        super().mouseDoubleClickEvent(event)
