"""Модальное окно журнала «Шухер»."""

from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPixmap,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..events import EventJournal, EventRecord
from ..resources import application_icon
from .dialogs import FramelessDialog
from .theme import (
    BACKGROUND,
    BRONZE,
    SURFACE_RAISED,
    TEXT,
    TEXT_MUTED,
    WARNING,
)
from .widgets import (
    _mascot_pixmap,
    frame_target_rect,
    normalized_bbox_to_widget_rect,
    set_action_button_capitalization,
    set_heading_capitalization,
)


_RUSSIAN_MONTHS = (
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


def _color(value: str, alpha: int | None = None) -> QColor:
    color = QColor(value)
    if alpha is not None:
        color.setAlpha(alpha)
    return color


def format_event_time(value: str, *, now: datetime | None = None) -> str:
    """Форматирует ISO-время по-русски относительно локального дня."""

    try:
        moment = datetime.fromisoformat(value).astimezone()
    except (TypeError, ValueError):
        return value
    current = (now or datetime.now().astimezone()).astimezone()
    clock = moment.strftime("%H:%M")
    if moment.date() == current.date():
        return f"сегодня {clock}"
    if moment.date() == (current - timedelta(days=1)).date():
        return f"вчера {clock}"
    year = f" {moment.year}" if moment.year != current.year else ""
    return f"{moment.day} {_RUSSIAN_MONTHS[moment.month - 1]}{year} · {clock}"


def _class_label(object_class: str) -> str:
    return "Человек" if object_class == "person" else "Машина"


def _class_color(object_class: str) -> QColor:
    return _color(WARNING if object_class == "person" else BRONZE)


class _ThumbnailCache:
    """Небольшой ленивый кэш: список никогда не открывает полный PNG."""

    def __init__(self, capacity: int = 64) -> None:
        self.capacity = max(1, int(capacity))
        self._items: OrderedDict[str, QPixmap | None] = OrderedDict()

    def get(self, path: Path) -> QPixmap | None:
        key = str(path)
        if key in self._items:
            pixmap = self._items.pop(key)
            self._items[key] = pixmap
            return pixmap
        pixmap = QPixmap()
        try:
            loaded = pixmap.loadFromData(path.read_bytes(), "PNG")
        except OSError:
            loaded = False
        value = pixmap if loaded and not pixmap.isNull() else None
        self._items[key] = value
        while len(self._items) > self.capacity:
            self._items.popitem(last=False)
        return value

    def clear(self) -> None:
        self._items.clear()


class EventRow(QWidget):
    """Полностью кликабельная строка журнала с ручной отрисовкой."""

    activated = Signal(int)

    HEIGHT = 116
    THUMB_WIDTH = 168
    THUMB_HEIGHT = 95

    def __init__(
        self,
        event: EventRecord,
        directory: Path,
        cache: _ThumbnailCache,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.record = event
        self.directory = directory
        self.cache = cache
        self._hovered = False
        self._pressed = False
        self.setFixedHeight(self.HEIGHT)
        self.setMinimumWidth(650)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(
            f"{_class_label(event.object_class)}, {event.camera_name}, "
            f"{format_event_time(event.time)}"
        )

    def enterEvent(self, event: object) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)  # type: ignore[arg-type]

    def leaveEvent(self, event: object) -> None:
        self._hovered = False
        self._pressed = False
        self.update()
        super().leaveEvent(event)  # type: ignore[arg-type]

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._pressed:
            self._pressed = False
            self.update()
            if self.rect().contains(event.position().toPoint()):
                self.activated.emit(self.record.id)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.activated.emit(self.record.id)
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        outer = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        background = _color(SURFACE_RAISED)
        if self._pressed:
            background = _color("#32342A")
        elif self._hovered:
            background = _color("#2E3027")
        border = _color(BRONZE, 98 if self._hovered or self.hasFocus() else 45)
        painter.setPen(QPen(border, 1))
        painter.setBrush(background)
        painter.drawRoundedRect(outer, 4, 4)

        if not self.record.viewed:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_class_color(self.record.object_class))
            painter.drawRoundedRect(QRectF(8, 45, 5, 26), 2.5, 2.5)

        thumb_rect = QRectF(23, 10, self.THUMB_WIDTH, self.THUMB_HEIGHT)
        painter.setPen(QPen(_color(BRONZE, 70), 1))
        painter.setBrush(_color(BACKGROUND))
        painter.drawRoundedRect(thumb_rect, 3, 3)
        thumbnail = self.cache.get(self.directory / self.record.thumb)
        if thumbnail is not None:
            painter.save()
            painter.setClipRect(thumb_rect.adjusted(1, 1, -1, -1))
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.drawPixmap(
                thumb_rect.adjusted(1, 1, -1, -1),
                thumbnail,
                QRectF(thumbnail.rect()),
            )
            painter.restore()
        else:
            missing_font = QFont(self.font())
            missing_font.setPixelSize(11)
            painter.setFont(missing_font)
            painter.setPen(_color(TEXT_MUTED, 170))
            painter.drawText(thumb_rect, Qt.AlignmentFlag.AlignCenter, "нет снимка")

        info_left = thumb_rect.right() + 19
        info_right = self.width() - 20
        available = max(1.0, info_right - info_left)

        time_font = QFont(self.font())
        time_font.setPixelSize(12)
        time_font.setWeight(QFont.Weight.Medium)
        painter.setFont(time_font)
        painter.setPen(_color(TEXT_MUTED))
        painter.drawText(
            QRectF(info_left, 14, available, 20),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            format_event_time(self.record.time),
        )

        if not self.record.viewed:
            pill_font = QFont(self.font())
            pill_font.setPixelSize(9)
            pill_font.setWeight(QFont.Weight.Bold)
            pill_font.setCapitalization(QFont.Capitalization.AllUppercase)
            painter.setFont(pill_font)
            pill_width = 57.0
            pill = QRectF(info_right - pill_width, 12, pill_width, 21)
            wash = _class_color(self.record.object_class)
            wash.setAlpha(32)
            painter.setPen(QPen(_class_color(self.record.object_class), 1))
            painter.setBrush(wash)
            painter.drawRoundedRect(pill, 10, 10)
            painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, "новое")

        camera_font = QFont(self.font())
        camera_font.setPixelSize(15)
        camera_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(camera_font)
        painter.setPen(_color(TEXT))
        camera_text = QFontMetricsF(camera_font).elidedText(
            self.record.camera_name,
            Qt.TextElideMode.ElideRight,
            max(1, round(available)),
        )
        painter.drawText(
            QRectF(info_left, 41, available, 25),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            camera_text,
        )

        class_font = QFont(self.font())
        class_font.setPixelSize(13)
        class_font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(class_font)
        painter.setPen(_class_color(self.record.object_class))
        label = _class_label(self.record.object_class)
        painter.drawText(
            QRectF(info_left, 76, available * 0.55, 22),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            label,
        )
        painter.setPen(_color(TEXT_MUTED))
        painter.drawText(
            QRectF(info_left, 76, available, 22),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"уверенность {self.record.confidence:.0%}",
        )


class EventImageCanvas(QWidget):
    """Крупный просмотр чистого PNG с рамками из записи события."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._event: EventRecord | None = None
        self._image: QImage | None = None
        self.setMinimumSize(520, 330)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_event(self, event: EventRecord, directory: Path) -> None:
        self._event = event
        image = QImage()
        try:
            loaded = image.loadFromData((directory / event.image).read_bytes(), "PNG")
        except OSError:
            loaded = False
        self._image = image if loaded and not image.isNull() else None
        self.update()

    def clear(self) -> None:
        self._event = None
        self._image = None
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), _color("#0D0E0C"))
        painter.setPen(QPen(_color(BRONZE, 65), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 4, 4)

        image = self._image
        current = self._event
        if image is None or current is None:
            muted = QFont(self.font())
            muted.setPixelSize(14)
            painter.setFont(muted)
            painter.setPen(_color(TEXT_MUTED))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Снимок недоступен")
            return

        target = frame_target_rect(
            self.width(),
            self.height(),
            image.width(),
            image.height(),
        )
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(target, image)
        painter.restore()

        label_font = QFont(self.font())
        label_font.setPixelSize(11)
        label_font.setWeight(QFont.Weight.DemiBold)
        label_metrics = QFontMetricsF(label_font)
        label_text = _class_label(current.object_class)
        frame_color = _class_color(current.object_class)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for bbox in current.bboxes:
            rect = normalized_bbox_to_widget_rect(
                bbox,
                self.width(),
                self.height(),
                image.width(),
                image.height(),
            )
            if rect.isEmpty():
                continue
            pen = QPen(frame_color, 1.7)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(rect)
            label_width = min(target.width(), label_metrics.horizontalAdvance(label_text) + 12)
            label_height = max(17.0, label_metrics.height() + 4)
            label_left = min(max(target.left(), rect.left()), target.right() - label_width)
            label_top = min(max(target.top(), rect.top()), target.bottom() - label_height)
            label_rect = QRectF(label_left, label_top, label_width, label_height)
            painter.fillRect(label_rect, _color(BACKGROUND, 228))
            painter.setFont(label_font)
            painter.setPen(frame_color)
            painter.drawText(
                label_rect.adjusted(6, 0, -3, 0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                label_text,
            )
        painter.restore()


class EmptyJournal(QWidget):
    """Пустое состояние с дозорным маскотом."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 24, 30, 34)
        layout.setSpacing(8)
        layout.addStretch(1)
        self.mascot = QLabel(self)
        self.mascot.setFixedSize(246, 246)
        self.mascot.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mascot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.mascot, 0, Qt.AlignmentFlag.AlignHCenter)
        title = QLabel("Всё тихо, происшествий нет", self)
        title.setObjectName("dialogTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        set_heading_capitalization(title)
        layout.addWidget(title)
        subtitle = QLabel(
            "Когда распознавание заметит человека или машину, "
            "снимок появится здесь.",
            self,
        )
        subtitle.setObjectName("helperText")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        layout.addStretch(1)
        self._sync_mascot()

    def _sync_mascot(self) -> None:
        pixmap = _mascot_pixmap(
            "watch",
            self.mascot.size(),
            self.devicePixelRatioF(),
        )
        if pixmap is None:
            self.mascot.hide()
        else:
            self.mascot.setPixmap(pixmap)
            self.mascot.show()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self._sync_mascot()


class ShuherDialog(FramelessDialog):
    """Живой журнал: новые события обновляют список внутри exec-цикла."""

    def __init__(
        self,
        journal: EventJournal,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            "ШУХЕР",
            parent,
            preferred_width=980,
            preferred_height=760,
        )
        self.journal = journal
        self._events_by_id: dict[int, EventRecord] = {}
        self._current_event_id: int | None = None
        self._thumbnail_cache = _ThumbnailCache()

        content = QWidget(self.surface)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(28, 14, 24, 24)
        content_layout.setSpacing(12)

        self.pages = QStackedWidget(content)
        content_layout.addWidget(self.pages, 1)
        self.surface_layout.addWidget(content, 1)

        self._build_list_page()
        self._build_viewer_page()
        self.pages.setCurrentWidget(self.list_page)

        self.journal.signals.journal_changed.connect(self._journal_changed)
        self.refresh(self.journal.records())

    def _build_list_page(self) -> None:
        self.list_page = QWidget(self.pages)
        layout = QVBoxLayout(self.list_page)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(12)

        intro = QHBoxLayout()
        copy = QVBoxLayout()
        copy.setSpacing(5)
        title = QLabel("Журнал сработок", self.list_page)
        title.setObjectName("dialogTitle")
        set_heading_capitalization(title)
        copy.addWidget(title)
        subtitle = QLabel(
            "Снимок сохраняется при первой сработке каждого эпизода. "
            "Новые события находятся сверху.",
            self.list_page,
        )
        subtitle.setObjectName("dialogSubtitle")
        subtitle.setWordWrap(True)
        copy.addWidget(subtitle)
        intro.addLayout(copy, 1)
        self.summary = QLabel(self.list_page)
        self.summary.setObjectName("helperText")
        self.summary.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        intro.addWidget(self.summary)
        layout.addLayout(intro)

        self.scroll = QScrollArea(self.list_page)
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.scroll, 1)

        buttons = QHBoxLayout()
        self.clear_button = QPushButton("Очистить журнал", self.list_page)
        self.clear_button.setObjectName("secondaryButton")
        set_action_button_capitalization(self.clear_button)
        self.clear_button.clicked.connect(self._confirm_clear)
        buttons.addWidget(self.clear_button)
        buttons.addStretch(1)
        close = QPushButton("Закрыть", self.list_page)
        close.setObjectName("secondaryButton")
        set_action_button_capitalization(close)
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.pages.addWidget(self.list_page)

    def _build_viewer_page(self) -> None:
        self.viewer_page = QWidget(self.pages)
        layout = QVBoxLayout(self.viewer_page)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(12)

        header = QHBoxLayout()
        back = QPushButton("Назад к журналу", self.viewer_page)
        back.setObjectName("secondaryButton")
        set_action_button_capitalization(back)
        back.clicked.connect(self.show_list)
        header.addWidget(back)
        header.addSpacing(8)
        copy = QVBoxLayout()
        copy.setSpacing(3)
        self.viewer_title = QLabel(self.viewer_page)
        self.viewer_title.setObjectName("dialogTitle")
        set_heading_capitalization(self.viewer_title)
        copy.addWidget(self.viewer_title)
        self.viewer_meta = QLabel(self.viewer_page)
        self.viewer_meta.setObjectName("dialogSubtitle")
        copy.addWidget(self.viewer_meta)
        header.addLayout(copy, 1)
        layout.addLayout(header)

        self.viewer = EventImageCanvas(self.viewer_page)
        layout.addWidget(self.viewer, 1)
        hint = QLabel(
            "Рамки восстановлены по координатам первого кадра эпизода; "
            "сам сохранённый PNG остаётся чистым.",
            self.viewer_page,
        )
        hint.setObjectName("helperText")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.pages.addWidget(self.viewer_page)

    @staticmethod
    def _event_word(count: int) -> str:
        if count % 10 == 1 and count % 100 != 11:
            return "событие"
        if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
            return "события"
        return "событий"

    def _journal_changed(self, payload: object) -> None:
        if isinstance(payload, tuple) and all(
            isinstance(item, EventRecord) for item in payload
        ):
            self.refresh(payload)
        else:
            self.refresh(self.journal.records())

    def refresh(self, events: tuple[EventRecord, ...]) -> None:
        self._events_by_id = {event.id: event for event in events}
        unread = sum(not event.viewed for event in events)
        summary = f"{len(events)} {self._event_word(len(events))}"
        if unread:
            summary += f" · новых {unread}"
        self.summary.setText(summary)
        self.clear_button.setEnabled(bool(events))

        previous = self.scroll.takeWidget()
        if previous is not None:
            previous.deleteLater()
        if not events:
            self._thumbnail_cache.clear()
            self.scroll.setWidget(EmptyJournal(self.scroll))
        else:
            rows = QWidget(self.scroll)
            rows_layout = QVBoxLayout(rows)
            rows_layout.setContentsMargins(2, 2, 8, 2)
            rows_layout.setSpacing(9)
            for event in events:
                row = EventRow(
                    event,
                    self.journal.directory,
                    self._thumbnail_cache,
                    rows,
                )
                row.activated.connect(self.open_event)
                rows_layout.addWidget(row)
            rows_layout.addStretch(1)
            self.scroll.setWidget(rows)

        if self._current_event_id is not None:
            current = self._events_by_id.get(self._current_event_id)
            if current is None:
                self.show_list()
            elif self.pages.currentWidget() is self.viewer_page:
                self._show_event(current)

    def open_event(self, event_id: int) -> None:
        event = self._events_by_id.get(int(event_id))
        if event is None:
            return
        self._current_event_id = event.id
        self._show_event(event)
        self.pages.setCurrentWidget(self.viewer_page)

    def _show_event(self, event: EventRecord) -> None:
        self.viewer_title.setText(
            f"{_class_label(event.object_class)} · {event.confidence:.0%}"
        )
        self.viewer_meta.setText(
            f"{format_event_time(event.time)} · {event.camera_name}"
        )
        self.viewer.set_event(event, self.journal.directory)

    def show_list(self) -> None:
        self.pages.setCurrentWidget(self.list_page)
        self._current_event_id = None
        self.viewer.clear()

    def _confirm_clear(self) -> None:
        message = QMessageBox(self)
        message.setWindowTitle("Очистить журнал")
        message.setWindowIcon(application_icon())
        message.setIcon(QMessageBox.Icon.Warning)
        message.setText("Удалить все события и снимки?")
        message.setInformativeText("Это действие нельзя отменить.")
        clear = message.addButton("Очистить", QMessageBox.ButtonRole.DestructiveRole)
        cancel = message.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        clear.setObjectName("dangerButton")
        cancel.setObjectName("secondaryButton")
        set_action_button_capitalization(clear)
        set_action_button_capitalization(cancel)
        message.setDefaultButton(cancel)
        message.exec()
        if message.clickedButton() is clear:
            self.show_list()
            self.journal.clear()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.refresh(self.journal.records())

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape and self.pages.currentWidget() is self.viewer_page:
            self.show_list()
            event.accept()
            return
        super().keyPressEvent(event)

    def done(self, result: int) -> None:
        try:
            self.journal.signals.journal_changed.disconnect(self._journal_changed)
        except (RuntimeError, TypeError):
            pass
        super().done(result)
