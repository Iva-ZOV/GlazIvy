"""Переиспользуемые отрисовываемые виджеты интерфейса."""

from __future__ import annotations

import math
from datetime import datetime

from PySide6.QtCore import (
    QEvent,
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
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSpacerItem,
    QToolTip,
    QWidget,
)

from ..resources import resource_path
from .theme import (
    BACKGROUND,
    BRONZE,
    DANGER,
    KHAKI,
    PRIMARY,
    PRIMARY_HOVER,
    PRIMARY_PRESSED,
    SUCCESS,
    SURFACE,
    TEXT,
    TEXT_MUTED,
    WARNING,
)


def _color(value: str, alpha: int | None = None) -> QColor:
    color = QColor(value)
    if alpha is not None:
        color.setAlpha(alpha)
    return color


def _blend_color(first: str, second: str, amount: float) -> QColor:
    """Смешивает два фирменных цвета без промежуточных CSS-артефактов."""

    start = QColor(first)
    end = QColor(second)
    t = min(1.0, max(0.0, amount))
    return QColor(
        round(start.red() + (end.red() - start.red()) * t),
        round(start.green() + (end.green() - start.green()) * t),
        round(start.blue() + (end.blue() - start.blue()) * t),
        round(start.alpha() + (end.alpha() - start.alpha()) * t),
    )


def _segment_path(
    rect: QRectF,
    radius: float,
    *,
    round_left: bool,
    round_right: bool,
) -> QPainterPath:
    """Прямоугольник с выборочными внешними углами для составных кнопок."""

    radius = min(radius, rect.width() / 2, rect.height() / 2)
    left_radius = radius if round_left else 0.0
    right_radius = radius if round_right else 0.0
    path = QPainterPath()
    path.moveTo(rect.left() + left_radius, rect.top())
    path.lineTo(rect.right() - right_radius, rect.top())
    if round_right:
        path.quadTo(rect.right(), rect.top(), rect.right(), rect.top() + radius)
    else:
        path.lineTo(rect.right(), rect.top())
    path.lineTo(rect.right(), rect.bottom() - right_radius)
    if round_right:
        path.quadTo(rect.right(), rect.bottom(), rect.right() - radius, rect.bottom())
    else:
        path.lineTo(rect.right(), rect.bottom())
    path.lineTo(rect.left() + left_radius, rect.bottom())
    if round_left:
        path.quadTo(rect.left(), rect.bottom(), rect.left(), rect.bottom() - radius)
    else:
        path.lineTo(rect.left(), rect.bottom())
    path.lineTo(rect.left(), rect.top() + left_radius)
    if round_left:
        path.quadTo(rect.left(), rect.top(), rect.left() + radius, rect.top())
    else:
        path.lineTo(rect.left(), rect.top())
    path.closeSubpath()
    return path


def _draw_header_action_icon(
    painter: QPainter,
    kind: str,
    rect: QRectF,
    color: QColor,
) -> None:
    """Рисует 18-px глифы поиска и добавления чистыми QPainter-примитивами."""

    painter.save()
    painter.setPen(
        QPen(
            color,
            1.65,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
    )
    painter.setBrush(Qt.BrushStyle.NoBrush)
    center = rect.center()
    if kind == "search":
        lens = QPointF(center.x() - 1.5, center.y() - 1.5)
        painter.drawEllipse(lens, 4.4, 4.4)
        painter.drawLine(
            QPointF(lens.x() + 3.2, lens.y() + 3.2),
            QPointF(center.x() + 6.1, center.y() + 6.1),
        )
    elif kind == "add":
        painter.drawRoundedRect(
            QRectF(center.x() - 6.5, center.y() - 6.5, 13.0, 13.0),
            1.4,
            1.4,
        )
        painter.drawLine(
            QPointF(center.x() - 3.2, center.y()),
            QPointF(center.x() + 3.2, center.y()),
        )
        painter.drawLine(
            QPointF(center.x(), center.y() - 3.2),
            QPointF(center.x(), center.y() + 3.2),
        )
    painter.restore()


def _draw_layout_icon(
    painter: QPainter,
    kind: str,
    rect: QRectF,
    color: QColor,
) -> None:
    """Рисует свободную раскладку или сетку в поле 18×18 px."""

    painter.save()
    painter.setPen(
        QPen(
            color,
            1.35,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.SquareCap,
            Qt.PenJoinStyle.MiterJoin,
        )
    )
    wash = QColor(color)
    wash.setAlpha(38)
    painter.setBrush(wash)
    cx, cy = rect.center().x(), rect.center().y()
    if kind == "free":
        painter.drawRect(QRectF(cx - 7.5, cy - 6.5, 9.0, 6.0))
        painter.drawRect(QRectF(cx - 1.0, cy - 2.0, 8.5, 6.0))
        painter.drawRect(QRectF(cx - 6.0, cy + 2.0, 7.5, 5.0))
    elif kind == "grid":
        for x in (cx - 6.5, cx + 1.0):
            for y in (cy - 6.5, cy + 1.0):
                painter.drawRect(QRectF(x, y, 5.5, 5.5))
    painter.restore()


_logo_source: QPixmap | None = None
_logo_cache: dict[tuple[int, int, int], QPixmap] = {}
_grain_source: QPixmap | None = None
_grain_cache: dict[int, QPixmap] = {}


def set_heading_capitalization(label: QLabel) -> None:
    """Делает фирменный заголовок капсом, не меняя исходную строку."""

    font = QFont(label.font())
    font.setCapitalization(QFont.Capitalization.AllUppercase)
    label.setFont(font)


def set_action_button_capitalization(button: QAbstractButton) -> None:
    """Отрисовывает рабочую кнопку капсом, сохраняя исходный текст для a11y."""

    font = QFont(button.font())
    font.setCapitalization(QFont.Capitalization.AllUppercase)
    button.setFont(font)


def _logo_pixmap(size: QSize, dpr: float) -> QPixmap | None:
    global _logo_source
    if _logo_source is None:
        # Пиксельная версия маскота — для логотипов в интерфейсе; качественный
        # мастер остаётся источником ярлыка и крупных размеров иконки.
        _logo_source = QPixmap(str(resource_path("assets", "app_logo_pixel.png")))
        if _logo_source.isNull():
            _logo_source = QPixmap(str(resource_path("assets", "app_icon_source.png")))
    if _logo_source.isNull():
        return None

    ratio_key = max(100, round(dpr * 100))
    pixel_width = max(1, round(size.width() * dpr))
    pixel_height = max(1, round(size.height() * dpr))
    key = (pixel_width, pixel_height, ratio_key)
    cached = _logo_cache.get(key)
    if cached is not None:
        return cached

    scaled = _logo_source.scaled(
        pixel_width,
        pixel_height,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    scaled.setDevicePixelRatio(dpr)
    _logo_cache[key] = scaled
    return scaled


def _grain_pixmap(dpr: float) -> QPixmap | None:
    global _grain_source
    if _grain_source is None:
        _grain_source = QPixmap(str(resource_path("assets", "textures", "grain.png")))
    if _grain_source.isNull():
        return None

    ratio_key = max(100, round(dpr * 100))
    cached = _grain_cache.get(ratio_key)
    if cached is not None:
        return cached

    scaled = _grain_source.scaled(
        max(1, round(_grain_source.width() * dpr)),
        max(1, round(_grain_source.height() * dpr)),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.FastTransformation,
    )
    scaled.setDevicePixelRatio(dpr)
    _grain_cache[ratio_key] = scaled
    return scaled


def draw_grain(
    painter: QPainter,
    rect: QRectF,
    dpr: float,
    *,
    opacity: float = 0.20,
) -> None:
    """Рисует лёгкое кэшированное зерно на уже окрашенной статичной поверхности."""

    texture = _grain_pixmap(dpr)
    if texture is None:
        return
    painter.save()
    painter.setOpacity(opacity)
    painter.drawTiledPixmap(rect, texture)
    painter.restore()


class GrainFrame(QFrame):
    """QSS-поверхность окна/диалога с процедурным зерном внутри скругления."""

    def paintEvent(self, event: QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        radius = 0.0 if self.property("flat") else 6.0
        if radius > 0.0:
            clip = QPainterPath()
            clip.setFillRule(Qt.FillRule.WindingFill)
            clip.addRoundedRect(rect, radius, radius)
            painter.setClipPath(clip)
        draw_grain(painter, rect, self.devicePixelRatioF(), opacity=0.18)


class LogoGlyph(QWidget):
    """Растровый логотип-котик с векторным fallback без внешнего шрифта."""

    def __init__(self, size: int = 36, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        pixmap = _logo_pixmap(self.size(), self.devicePixelRatioF())
        if pixmap is not None:
            logical_size = pixmap.deviceIndependentSize()
            painter.drawPixmap(
                QPointF(
                    (self.width() - logical_size.width()) / 2,
                    (self.height() - logical_size.height()) / 2,
                ),
                pixmap,
            )
            return

        # Fallback тоже остаётся без подложки: логотип всегда лежит прямо на
        # графите, даже если PNG временно недоступен.
        rect = QRectF(self.rect()).adjusted(3.0, 3.0, -3.0, -3.0)
        painter.setPen(QPen(_color(BRONZE), max(1.4, rect.width() * 0.07)))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        lens_radius = rect.width() * 0.18
        left = QPointF(rect.center().x() - lens_radius, rect.center().y() + 1.0)
        right = QPointF(rect.center().x() + lens_radius, rect.center().y() + 1.0)
        painter.drawEllipse(left, lens_radius, lens_radius)
        painter.drawEllipse(right, lens_radius, lens_radius)
        painter.drawLine(
            QPointF(left.x() + lens_radius, left.y()),
            QPointF(right.x() - lens_radius, right.y()),
        )


class ToolIconButton(QAbstractButton):
    """Кнопка заголовка с бронзовым глифом и красным опасным состоянием."""

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

    def set_kind(self, kind: str, tooltip: str | None = None) -> None:
        if kind != self.kind:
            self.kind = kind
            self.update()
        if tooltip is not None:
            self.setToolTip(tooltip)

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

        if self.kind == "close":
            background = _color(PRIMARY, int(8 + 50 * self._hover))
            if self.isDown():
                background = _color(PRIMARY_PRESSED, 230)
        else:
            background = _color(BRONZE, int(5 + 24 * self._hover))
            if self.isDown():
                background = _color(BRONZE, 42)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(2, 2, -2, -2), 3, 3)

        glyph = _color(BRONZE, int(185 + 65 * self._hover))
        if self.kind == "close" and self._hover > 0.01:
            glyph = _color(TEXT)
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
        elif self.kind == "windowed":
            painter.drawRoundedRect(QRectF(cx - 5.5, cy - 4.5, 11, 9), 1.2, 1.2)
            painter.drawLine(QPointF(cx - 5, cy - 1.5), QPointF(cx + 5, cy - 1.5))
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


class HeaderActionButton(QAbstractButton):
    """Одна половина составной кнопки поиска/добавления в оконной панели."""

    def __init__(
        self,
        text: str,
        kind: str,
        *,
        primary: bool,
        outer_side: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        if kind not in {"search", "add"}:
            raise ValueError("Неизвестная иконка кнопки панели")
        if outer_side not in {"left", "right"}:
            raise ValueError("outer_side должен быть left или right")
        self.kind = kind
        self.primary = primary
        self.outer_side = outer_side
        self._compact = False
        self._hover = 0.0
        self.setText(text)
        self.setFixedHeight(36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        font = QFont(self.font())
        font.setCapitalization(QFont.Capitalization.AllUppercase)
        self.setFont(font)
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(140)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._set_hover)

    def sizeHint(self) -> QSize:
        if self._compact:
            return QSize(42, 36)
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 53, 36)

    def set_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        self.setProperty("compact", compact)
        self.updateGeometry()
        self.update()

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
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        shape = _segment_path(
            rect,
            3.0,
            round_left=self.outer_side == "left",
            round_right=self.outer_side == "right",
        )

        if not self.isEnabled():
            if self.primary:
                background = _color(PRIMARY_PRESSED, 58)
                border = _color(BRONZE, 18)
            else:
                background = _color(BRONZE, 0)
                border = _color(BRONZE, 36)
            glyph = _color(BRONZE, 92)
        elif self.primary:
            background = (
                _color(PRIMARY_PRESSED, 244)
                if self.isDown()
                else _blend_color(PRIMARY, PRIMARY_HOVER, self._hover)
            )
            border = _color(TEXT, int(24 + 28 * self._hover))
            glyph = _color(TEXT, 250)
        else:
            background = _color(BRONZE, int(5 + 22 * self._hover))
            if self.isDown():
                background = _color(BRONZE, 38)
            border = _color(BRONZE, int(145 + 45 * self._hover))
            glyph = _blend_color(BRONZE, TEXT, self._hover)

        painter.setPen(QPen(border, 1))
        painter.setBrush(background)
        painter.drawPath(shape)

        font = QFont(self.font())
        font.setPixelSize(11 if self._compact else 12)
        font.setWeight(QFont.Weight.Bold if self.primary else QFont.Weight.DemiBold)
        font.setCapitalization(QFont.Capitalization.AllUppercase)
        painter.setFont(font)
        if self._compact:
            icon_rect = QRectF(
                (self.width() - 18) / 2,
                (self.height() - 18) / 2,
                18,
                18,
            )
        else:
            text_width = painter.fontMetrics().horizontalAdvance(self.text())
            content_width = 18 + 8 + text_width
            content_left = (self.width() - content_width) / 2
            icon_rect = QRectF(content_left, (self.height() - 18) / 2, 18, 18)
            painter.setPen(glyph)
            painter.drawText(
                QRectF(
                    content_left + 26,
                    0,
                    text_width + 2,
                    self.height(),
                ),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                self.text(),
            )
        _draw_header_action_icon(painter, self.kind, icon_rect, glyph)

        if self.hasFocus():
            painter.setPen(QPen(_color(KHAKI, 220), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(
                _segment_path(
                    rect.adjusted(2, 2, -2, -2),
                    2.0,
                    round_left=self.outer_side == "left",
                    round_right=self.outer_side == "right",
                )
            )


class SegmentedControl(QWidget):
    value_changed = Signal(str)

    def __init__(
        self,
        labels: tuple[str, ...],
        values: tuple[str, ...],
        value: str,
        parent: QWidget | None = None,
        *,
        icons: tuple[str | None, ...] | None = None,
        segment_tooltips: tuple[str, ...] | None = None,
    ) -> None:
        super().__init__(parent)
        if len(labels) != len(values) or not labels:
            raise ValueError("labels и values должны быть непустыми и одинаковыми")
        self.labels = labels
        self.values = values
        self.icons = icons or tuple(None for _ in labels)
        self.segment_tooltips = segment_tooltips
        if len(self.icons) != len(labels):
            raise ValueError("icons и labels должны быть одинаковой длины")
        if segment_tooltips is not None and len(segment_tooltips) != len(labels):
            raise ValueError("segment_tooltips и labels должны быть одинаковой длины")
        self._index = values.index(value) if value in values else 0
        self._position = float(self._index)
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(210)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(self._set_position)
        self.setFixedHeight(32)
        self.setMinimumWidth(self.sizeHint().width())
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        font = QFont(self.font())
        font.setCapitalization(QFont.Capitalization.AllUppercase)
        self.setFont(font)

    def sizeHint(self) -> QSize:
        if any(self.icons):
            return QSize(38 * len(self.labels), 32)
        widths = [self.fontMetrics().horizontalAdvance(label) + 24 for label in self.labels]
        return QSize(sum(widths), 32)

    def event(self, event: QEvent) -> bool:
        if (
            event.type() == QEvent.Type.ToolTip
            and self.segment_tooltips is not None
        ):
            position = event.pos()  # type: ignore[attr-defined]
            index = min(
                len(self.values) - 1,
                max(0, int(position.x() / max(1, self.width()) * len(self.values))),
            )
            QToolTip.showText(
                event.globalPos(),  # type: ignore[attr-defined]
                self.segment_tooltips[index],
                self,
            )
            return True
        return super().event(event)

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

    def mousePressEvent(self, event: QMouseEvent) -> None:
        # Нажатие обязаны принять сами: иначе оно всплывёт к родителю, а
        # заголовок доски на нём начинает перетаскивание окна и перехватывает
        # мышь — отпускание до нас уже не доходит, переключатель не срабатывает.
        if event.button() == Qt.MouseButton.LeftButton:
            event.accept()
            return
        super().mousePressEvent(event)

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
        painter.setPen(QPen(_color(BRONZE, 70), 1))
        painter.setBrush(_color(BACKGROUND, 185))
        painter.drawRoundedRect(outer, 3, 3)

        segment_width = outer.width() / len(self.values)
        slider = QRectF(
            outer.left() + segment_width * self._position + 2.5,
            outer.top() + 2.5,
            segment_width - 5,
            outer.height() - 5,
        )
        gradient = QLinearGradient(slider.topLeft(), slider.bottomRight())
        gradient.setColorAt(0.0, _color(PRIMARY, 238))
        gradient.setColorAt(1.0, _color(PRIMARY_PRESSED, 242))
        painter.setPen(QPen(_color(BRONZE, 82), 1))
        painter.setBrush(gradient)
        painter.drawRoundedRect(slider, 2, 2)

        font = QFont(self.font())
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        for index, (label, icon) in enumerate(zip(self.labels, self.icons)):
            rect = QRectF(
                outer.left() + index * segment_width,
                outer.top(),
                segment_width,
                outer.height(),
            )
            distance = abs(self._position - index)
            glyph = _color(TEXT, 248) if distance < 0.5 else _color(BRONZE, 215)
            if icon is not None:
                icon_rect = QRectF(0, 0, 18, 18)
                icon_rect.moveCenter(rect.center())
                _draw_layout_icon(painter, icon, icon_rect, glyph)
            else:
                painter.setPen(glyph)
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)

        if self.hasFocus():
            painter.setPen(QPen(_color(KHAKI, 205), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(outer.adjusted(1, 1, -1, -1), 2, 2)


class StatusPill(QWidget):
    _STATE = {
        "unconfigured": ("Не настроена", BRONZE),
        "connecting": ("Подключение…", WARNING),
        "online": ("В сети", SUCCESS),
        "reconnecting": ("Переподключение…", WARNING),
        "offline": ("Нет связи", DANGER),
    }
    _PULSING_STATES = {"connecting", "reconnecting"}

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
        if self._state in self._PULSING_STATES:
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
        painter.setPen(QPen(QColor(color.red(), color.green(), color.blue(), 82), 1))
        painter.setBrush(_color(BACKGROUND, 224))
        painter.drawRoundedRect(rect, 13.5, 13.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color.red(), color.green(), color.blue(), 25))
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 12.5, 12.5)

        dot_center = QPointF(14, rect.center().y())
        if self._state in self._PULSING_STATES:
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
        painter.setPen(_color(TEXT, 245))
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
        self._corner_radius = 4.0

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
        gradient.setColorAt(0.0, _color("#151610"))
        gradient.setColorAt(0.5, _color(BACKGROUND))
        gradient.setColorAt(1.0, _color("#0D0E0C"))
        painter.fillRect(rect, gradient)

        painter.setPen(QPen(_color(BRONZE, 11), 1))
        spacing = max(54, int(min(self.width(), self.height()) / 9))
        for x in range(-self.height(), self.width() + self.height(), spacing):
            painter.drawLine(x, 0, x - self.height(), self.height())

        glow = QLinearGradient(0, 0, self.width(), self.height())
        glow.setColorAt(0.0, _color(BRONZE, 12))
        glow.setColorAt(0.55, _color("#000000", 0))
        glow.setColorAt(1.0, _color(KHAKI, 10))
        painter.fillRect(rect, glow)

    def _draw_placeholder(self, painter: QPainter) -> None:
        center = QPointF(self.width() / 2, self.height() / 2 - 16)
        painter.setPen(QPen(_color(BRONZE, 88), 1.6))
        painter.setBrush(_color(BRONZE, 12))
        painter.drawRoundedRect(
            QRectF(center.x() - 29, center.y() - 22, 58, 44),
            3,
            3,
        )
        painter.setBrush(_color(KHAKI, 52))
        painter.drawEllipse(center, 12, 12)
        painter.setBrush(_color(BACKGROUND, 235))
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
            painter.fillRect(self.rect(), _color(BACKGROUND, 72))

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

        painter.setPen(QPen(_color(BRONZE, 76), 1))
        painter.setBrush(_color(SURFACE, 238))
        painter.drawRoundedRect(card, 3, 3)

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
            # WindingFill сохраняем обязательно: клип живого видео уже ломался
            # при неявном возврате к OddEvenFill.
            clip = QPainterPath()
            clip.setFillRule(Qt.FillRule.WindingFill)
            clip.addRoundedRect(QRectF(self.rect()), self._corner_radius, self._corner_radius)
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

        self.logo = LogoGlyph(35, self)
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
