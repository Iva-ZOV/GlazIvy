"""Фоновый журнал эпизодов распознавания со снимками в AppData."""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter

from .constants import APP_SLUG
from .detection import Detection, DetectionResult


LOGGER = logging.getLogger(__name__)

EVENTS_VERSION = 1
EVENT_COOLDOWN_SECONDS = 30.0
EVENT_LIMIT = 300
EVENT_QUEUE_LIMIT = 16
EVENT_SHUTDOWN_TIMEOUT_SECONDS = 3.0
THUMBNAIL_SIZE = QSize(336, 190)

_EVENT_IMAGE_RE = re.compile(r"event_\d{8}_\d{6}_(\d+)\.png\Z")
_THUMB_IMAGE_RE = re.compile(r"thumb_\d{8}_\d{6}_(\d+)\.png\Z")


@dataclass(frozen=True, slots=True)
class EventRecord:
    """Одна неизменяемая запись журнала; событие всегда про один класс."""

    id: int
    time: str
    camera_id: str
    camera_name: str
    object_class: str
    confidence: float
    bboxes: tuple[tuple[float, float, float, float], ...]
    image: str
    thumb: str
    viewed: bool

    def to_payload(self) -> dict[str, object]:
        return {
            "id": self.id,
            "time": self.time,
            "camera_id": self.camera_id,
            "camera_name": self.camera_name,
            "object_class": self.object_class,
            "confidence": self.confidence,
            "bboxes": [list(bbox) for bbox in self.bboxes],
            "image": self.image,
            "thumb": self.thumb,
            "viewed": self.viewed,
        }

    @classmethod
    def from_payload(cls, payload: object) -> "EventRecord":
        if not isinstance(payload, dict):
            raise ValueError("Запись события должна быть объектом.")

        event_id = payload.get("id")
        if isinstance(event_id, bool) or not isinstance(event_id, int) or event_id < 1:
            raise ValueError("У события неверный id.")

        timestamp = payload.get("time")
        if not isinstance(timestamp, str):
            raise ValueError("У события неверное время.")
        parsed_time = datetime.fromisoformat(timestamp)
        if parsed_time.tzinfo is None or parsed_time.utcoffset() is None:
            raise ValueError("Время события должно содержать UTC-офсет.")

        camera_id = payload.get("camera_id")
        camera_name = payload.get("camera_name")
        if not isinstance(camera_id, str) or not camera_id.strip():
            raise ValueError("У события отсутствует id камеры.")
        if not isinstance(camera_name, str) or not camera_name.strip():
            raise ValueError("У события отсутствует имя камеры.")

        object_class = payload.get("object_class")
        if object_class not in {"person", "vehicle"}:
            raise ValueError("У события неизвестный класс объекта.")

        confidence_raw = payload.get("confidence")
        if (
            isinstance(confidence_raw, bool)
            or not isinstance(confidence_raw, (int, float))
        ):
            raise ValueError("У события неверная уверенность.")
        confidence = float(confidence_raw)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError("Уверенность события должна быть в диапазоне 0..1.")

        bboxes_raw = payload.get("bboxes")
        if not isinstance(bboxes_raw, list) or not bboxes_raw:
            raise ValueError("У события отсутствуют рамки.")
        bboxes: list[tuple[float, float, float, float]] = []
        for raw_bbox in bboxes_raw:
            if not isinstance(raw_bbox, list) or len(raw_bbox) != 4:
                raise ValueError("Рамка события должна содержать четыре координаты.")
            if any(
                isinstance(value, bool) or not isinstance(value, (int, float))
                for value in raw_bbox
            ):
                raise ValueError("Координаты рамки должны быть числами.")
            bbox = tuple(float(value) for value in raw_bbox)
            if any(not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in bbox):
                raise ValueError("Координаты рамки должны быть в диапазоне 0..1.")
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                raise ValueError("У рамки события неверный порядок координат.")
            bboxes.append(bbox)  # type: ignore[arg-type]

        image = payload.get("image")
        thumb = payload.get("thumb")
        if not isinstance(image, str) or _EVENT_IMAGE_RE.fullmatch(image) is None:
            raise ValueError("У события неверное имя снимка.")
        if not isinstance(thumb, str) or _THUMB_IMAGE_RE.fullmatch(thumb) is None:
            raise ValueError("У события неверное имя миниатюры.")
        image_id = int(_EVENT_IMAGE_RE.fullmatch(image).group(1))  # type: ignore[union-attr]
        thumb_id = int(_THUMB_IMAGE_RE.fullmatch(thumb).group(1))  # type: ignore[union-attr]
        if image_id != event_id or thumb_id != event_id:
            raise ValueError("Имена снимков не соответствуют id события.")

        viewed = payload.get("viewed")
        if not isinstance(viewed, bool):
            raise ValueError("Поле viewed должно быть логическим.")

        return cls(
            id=event_id,
            time=timestamp,
            camera_id=camera_id,
            camera_name=camera_name,
            object_class=object_class,
            confidence=confidence,
            bboxes=tuple(bboxes),
            image=image,
            thumb=thumb,
            viewed=viewed,
        )


class EventJournalSignals(QObject):
    journal_changed = Signal(object)
    unread_changed = Signal(int)


@dataclass(frozen=True, slots=True)
class _IngestCommand:
    result: DetectionResult
    camera_name: str
    instant_utc: datetime


@dataclass(frozen=True, slots=True)
class _MarkViewedCommand:
    event_ids: frozenset[int] | None
    done: threading.Event | None = None


@dataclass(frozen=True, slots=True)
class _ClearCommand:
    done: threading.Event | None = None


@dataclass(frozen=True, slots=True)
class _ResetCameraCommand:
    camera_id: str
    done: threading.Event | None = None


@dataclass(frozen=True, slots=True)
class _BarrierCommand:
    done: threading.Event


@dataclass(frozen=True, slots=True)
class _StopCommand:
    pass


_Command = (
    _IngestCommand
    | _MarkViewedCommand
    | _ClearCommand
    | _ResetCameraCommand
    | _BarrierCommand
    | _StopCommand
)


class _CommandBuffer:
    """Ограниченная очередь: управляющая команда может вытеснить ingest."""

    def __init__(self, capacity: int) -> None:
        self.capacity = max(1, int(capacity))
        self._items: deque[_Command] = deque()
        self._condition = threading.Condition()

    def put_ingest(self, command: _IngestCommand) -> bool:
        with self._condition:
            if len(self._items) >= self.capacity:
                return False
            self._items.append(command)
            self._condition.notify()
            return True

    def put_control(
        self,
        command: _Command,
        *,
        evict_ingest: bool,
        timeout: float | None,
    ) -> tuple[bool, int]:
        deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
        dropped = 0
        with self._condition:
            while len(self._items) >= self.capacity:
                if evict_ingest:
                    for index, queued in enumerate(self._items):
                        if isinstance(queued, _IngestCommand):
                            del self._items[index]
                            dropped += 1
                            break
                    else:
                        evict_ingest = False
                    if dropped:
                        break
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0.0:
                        return False, dropped
                    self._condition.wait(remaining)
                else:
                    self._condition.wait()
            self._items.append(command)
            self._condition.notify()
            return True, dropped

    def get(self) -> _Command:
        with self._condition:
            while not self._items:
                self._condition.wait()
            command = self._items.popleft()
            self._condition.notify_all()
            return command


class EventJournal:
    """Один writer-поток владеет журналом, кулдаунами и файлами снимков."""

    def __init__(
        self,
        directory: Path | None = None,
        *,
        event_limit: int = EVENT_LIMIT,
        cooldown_seconds: float = EVENT_COOLDOWN_SECONDS,
        queue_limit: int = EVENT_QUEUE_LIMIT,
    ) -> None:
        self.directory = directory or self.default_directory()
        self.path = self.directory / "events.json"
        self.event_limit = max(1, int(event_limit))
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self.signals = EventJournalSignals()

        self._commands = _CommandBuffer(queue_limit)
        self._state_lock = threading.Lock()
        self._accepting_lock = threading.Lock()
        self._published_events: tuple[EventRecord, ...] = ()
        self._published_unread = 0
        self._events: list[EventRecord] = []  # Внутри writer: старые → новые.
        self._next_id = 1
        self._last_seen: dict[tuple[str, str], float] = {}
        self._accepting = True
        self._storage_blocked = False
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name="event-journal-writer",
            daemon=True,
        )
        self._thread.start()

    @staticmethod
    def default_directory() -> Path:
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / APP_SLUG / "events"
        return Path.home() / "AppData" / "Roaming" / APP_SLUG / "events"

    def records(self) -> tuple[EventRecord, ...]:
        """Возвращает опубликованный snapshot, новые события находятся сверху."""

        with self._state_lock:
            return self._published_events

    def unread_count(self) -> int:
        with self._state_lock:
            return self._published_unread

    def wait_until_ready(self, timeout: float = 3.0) -> bool:
        return self._ready.wait(max(0.0, timeout))

    def ingest(self, result: DetectionResult, camera_name: str) -> bool:
        """Неблокирующе принимает результат; при полной очереди дропает его."""

        if not isinstance(result, DetectionResult) or result.frame.isNull():
            return False
        if not any(
            detection.object_class in {"person", "vehicle"}
            for detection in result.detections
        ):
            return True
        with self._accepting_lock:
            if not self._accepting:
                return False
        safe_result = replace(result, frame=QImage(result.frame))
        command = _IngestCommand(
            safe_result,
            camera_name.strip() or "Камера",
            datetime.now(timezone.utc),
        )
        if self._commands.put_ingest(command):
            return True
        LOGGER.warning(
            "Очередь журнала заполнена: сработка камеры %s пропущена.",
            result.camera_id,
        )
        return False

    def mark_viewed(
        self,
        event_ids: Sequence[int] | None = None,
        *,
        wait: bool = False,
        timeout: float = 1.0,
    ) -> bool:
        done = threading.Event() if wait else None
        ids = None if event_ids is None else frozenset(int(value) for value in event_ids)
        if not self._enqueue_control(_MarkViewedCommand(ids, done)):
            return False
        return done.wait(max(0.0, timeout)) if done is not None else True

    def clear(self, *, wait: bool = False, timeout: float = 1.0) -> bool:
        done = threading.Event() if wait else None
        if not self._enqueue_control(_ClearCommand(done)):
            return False
        return done.wait(max(0.0, timeout)) if done is not None else True

    def reset_camera(
        self,
        camera_id: str,
        *,
        wait: bool = False,
        timeout: float = 1.0,
    ) -> bool:
        done = threading.Event() if wait else None
        if not self._enqueue_control(_ResetCameraCommand(camera_id, done)):
            return False
        return done.wait(max(0.0, timeout)) if done is not None else True

    def flush(self, timeout: float = 3.0) -> bool:
        done = threading.Event()
        placed, _ = self._commands.put_control(
            _BarrierCommand(done),
            evict_ingest=False,
            timeout=max(0.0, timeout),
        )
        return placed and done.wait(max(0.0, timeout))

    def _enqueue_control(self, command: _Command) -> bool:
        with self._accepting_lock:
            if not self._accepting:
                return False
        placed, dropped = self._commands.put_control(
            command,
            evict_ingest=True,
            timeout=0.75,
        )
        if dropped:
            LOGGER.warning(
                "Управляющая команда журнала вытеснила %d ожидающую сработку.",
                dropped,
            )
        if not placed:
            LOGGER.error("Не удалось поставить управляющую команду в очередь журнала.")
        return placed

    def _run(self) -> None:
        try:
            self._load_at_startup()
        except Exception:
            LOGGER.exception("Не удалось инициализировать журнал сработок.")
            self._events = []
            self._next_id = 1
        finally:
            self._publish()
            self._ready.set()

        while True:
            command = self._commands.get()
            if isinstance(command, _StopCommand):
                break
            done = getattr(command, "done", None)
            try:
                if isinstance(command, _IngestCommand):
                    self._handle_ingest(command)
                elif isinstance(command, _MarkViewedCommand):
                    self._handle_mark_viewed(command.event_ids)
                elif isinstance(command, _ClearCommand):
                    self._handle_clear()
                elif isinstance(command, _ResetCameraCommand):
                    self._handle_reset_camera(command.camera_id)
            except Exception:
                LOGGER.exception("Команда журнала завершилась с ошибкой; поток продолжает работу.")
            finally:
                if isinstance(done, threading.Event):
                    done.set()

    def _load_at_startup(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self._events = []
            self._next_id = 1
            self._write_journal(self._events, self._next_id)
            self._cleanup_unreferenced_assets(self._events)
            return

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            events, next_id = self._decode_journal(payload)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            LOGGER.warning("events.json повреждён: %s", exc)
            if not self._backup_corrupt_journal():
                self._storage_blocked = True
            self._events = []
            self._next_id = max(1, self._largest_asset_id() + 1)
            if not self._storage_blocked:
                try:
                    self._write_journal(self._events, self._next_id)
                except OSError:
                    self._storage_blocked = True
                    LOGGER.exception(
                        "Не удалось создать новый events.json после восстановления."
                    )
            # В запуске восстановления снимки намеренно сохраняются целиком.
            return

        normalized = [
            event
            for event in events
            if (self.directory / event.image).is_file()
            and (self.directory / event.thumb).is_file()
        ]
        if len(normalized) > self.event_limit:
            normalized = normalized[-self.event_limit :]
        changed = normalized != events
        self._events = normalized
        # Снимки могли остаться после сбоя между записью PNG и JSON. Даже до
        # их штатной чистки id больше не переиспользуется в этом запуске.
        self._next_id = max(next_id, self._largest_asset_id() + 1)
        saved = True
        if changed:
            try:
                self._write_journal(self._events, self._next_id)
            except OSError:
                saved = False
                LOGGER.exception("Не удалось сохранить очищенный журнал при старте.")
        if saved:
            self._cleanup_unreferenced_assets(self._events)

    @staticmethod
    def _decode_journal(payload: object) -> tuple[list[EventRecord], int]:
        if not isinstance(payload, dict) or payload.get("version") != EVENTS_VERSION:
            raise ValueError("Версия журнала не поддерживается.")
        next_id = payload.get("next_id")
        if isinstance(next_id, bool) or not isinstance(next_id, int) or next_id < 1:
            raise ValueError("Поле next_id повреждено.")
        raw_events = payload.get("events")
        if not isinstance(raw_events, list):
            raise ValueError("Поле events должно быть списком.")
        events = [EventRecord.from_payload(item) for item in raw_events]
        ids = [event.id for event in events]
        if len(ids) != len(set(ids)):
            raise ValueError("В журнале повторяются id событий.")
        events.sort(key=lambda event: event.id)
        if ids and next_id <= max(ids):
            raise ValueError("Поле next_id не больше существующих id.")
        return events, next_id

    def _backup_corrupt_journal(self) -> bool:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        candidate = self.directory / f"events.json.bak-{stamp}"
        suffix = 2
        while candidate.exists():
            candidate = self.directory / f"events.json.bak-{stamp}-{suffix}"
            suffix += 1
        try:
            os.replace(self.path, candidate)
        except OSError:
            LOGGER.exception("Не удалось сохранить резервную копию битого events.json.")
            return False
        return True

    def _largest_asset_id(self) -> int:
        largest = 0
        for pattern, matcher in (
            ("event_*.png", _EVENT_IMAGE_RE),
            ("thumb_*.png", _THUMB_IMAGE_RE),
        ):
            for path in self.directory.glob(pattern):
                match = matcher.fullmatch(path.name)
                if match is not None:
                    largest = max(largest, int(match.group(1)))
        return largest

    def _write_journal(
        self,
        events: Sequence[EventRecord],
        next_id: int,
    ) -> None:
        if self._storage_blocked:
            raise OSError("Хранилище журнала заблокировано после ошибки резервирования.")
        payload = {
            "version": EVENTS_VERSION,
            "next_id": next_id,
            "events": [event.to_payload() for event in events],
        }
        temporary = self.path.with_suffix(".json.tmp")
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _handle_ingest(self, command: _IngestCommand) -> None:
        grouped: dict[str, list[Detection]] = {"person": [], "vehicle": []}
        for detection in command.result.detections:
            if detection.object_class in grouped:
                grouped[detection.object_class].append(detection)

        changed = False
        for object_class in ("person", "vehicle"):
            detections = grouped[object_class]
            if not detections:
                continue
            key = (command.result.camera_id, object_class)
            last_seen = self._last_seen.get(key)
            elapsed = (
                math.inf
                if last_seen is None
                else command.result.timestamp - last_seen
            )
            self._last_seen[key] = command.result.timestamp
            if elapsed < self.cooldown_seconds:
                continue
            try:
                changed = self._create_event(
                    command.result,
                    command.camera_name,
                    object_class,
                    detections,
                    command.instant_utc,
                ) or changed
            except Exception:
                LOGGER.exception(
                    "Событие %s камеры %s пропущено из-за ошибки записи.",
                    object_class,
                    command.result.camera_id,
                )
        if changed:
            self._publish()

    def _create_event(
        self,
        result: DetectionResult,
        camera_name: str,
        object_class: str,
        detections: Sequence[Detection],
        instant_utc: datetime,
    ) -> bool:
        if result.frame.isNull() or not detections:
            return False
        event_id = self._next_id
        self._next_id += 1
        file_stamp = instant_utc.strftime("%Y%m%d_%H%M%S")
        image_name = f"event_{file_stamp}_{event_id}.png"
        thumb_name = f"thumb_{file_stamp}_{event_id}.png"
        image_path = self.directory / image_name
        thumb_path = self.directory / thumb_name

        image_bytes = _encode_png(result.frame)
        thumb_bytes = _encode_png(_thumbnail_image(result.frame))
        try:
            image_path.write_bytes(image_bytes)
            thumb_path.write_bytes(thumb_bytes)
        except OSError:
            _unlink_quietly(image_path)
            _unlink_quietly(thumb_path)
            raise

        record = EventRecord(
            id=event_id,
            time=instant_utc.astimezone().isoformat(timespec="seconds"),
            camera_id=result.camera_id,
            camera_name=camera_name,
            object_class=object_class,
            confidence=max(float(item.confidence) for item in detections),
            bboxes=tuple(tuple(float(value) for value in item.bbox) for item in detections),
            image=image_name,
            thumb=thumb_name,
            viewed=False,
        )
        candidate = [*self._events, record]
        excess = max(0, len(candidate) - self.event_limit)
        removed = candidate[:excess]
        candidate = candidate[excess:]
        try:
            self._write_journal(candidate, self._next_id)
        except OSError:
            _unlink_quietly(image_path)
            _unlink_quietly(thumb_path)
            raise

        self._events = candidate
        for old_event in removed:
            self._delete_event_assets(old_event)
        return True

    def _handle_mark_viewed(self, event_ids: frozenset[int] | None) -> None:
        candidate = [
            replace(event, viewed=True)
            if not event.viewed and (event_ids is None or event.id in event_ids)
            else event
            for event in self._events
        ]
        if candidate == self._events:
            return
        try:
            self._write_journal(candidate, self._next_id)
        except OSError:
            LOGGER.exception("Не удалось записать отметки просмотра журнала.")
            return
        self._events = candidate
        self._publish()

    def _handle_clear(self) -> None:
        if not self._events and not any(self.directory.glob("event_*.png")) and not any(
            self.directory.glob("thumb_*.png")
        ):
            return
        try:
            self._write_journal([], self._next_id)
        except OSError:
            LOGGER.exception("Не удалось очистить журнал.")
            return
        self._events = []
        self._delete_all_assets()
        self._publish()

    def _handle_reset_camera(self, camera_id: str) -> None:
        for key in [key for key in self._last_seen if key[0] == camera_id]:
            self._last_seen.pop(key, None)

    def _cleanup_unreferenced_assets(self, events: Sequence[EventRecord]) -> None:
        referenced = {
            name
            for event in events
            for name in (event.image, event.thumb)
        }
        for pattern in ("event_*.png", "thumb_*.png"):
            for path in self.directory.glob(pattern):
                if path.name not in referenced:
                    _unlink_quietly(path)
        _unlink_quietly(self.path.with_suffix(".json.tmp"))

    def _delete_all_assets(self) -> None:
        for pattern in ("event_*.png", "thumb_*.png"):
            for path in self.directory.glob(pattern):
                _unlink_quietly(path)

    def _delete_event_assets(self, event: EventRecord) -> None:
        _unlink_quietly(self.directory / event.image)
        _unlink_quietly(self.directory / event.thumb)

    def _publish(self) -> None:
        snapshot = tuple(reversed(self._events))
        unread = sum(not event.viewed for event in snapshot)
        with self._state_lock:
            self._published_events = snapshot
            self._published_unread = unread
        try:
            self.signals.journal_changed.emit(snapshot)
            self.signals.unread_changed.emit(unread)
        except RuntimeError:
            pass

    def shutdown(self, timeout: float = EVENT_SHUTDOWN_TIMEOUT_SECONDS) -> bool:
        with self._accepting_lock:
            if not self._accepting:
                thread = self._thread
                return not thread.is_alive()
            self._accepting = False
        deadline = time.monotonic() + max(0.0, timeout)
        placed, _ = self._commands.put_control(
            _StopCommand(),
            evict_ingest=False,
            timeout=max(0.0, deadline - time.monotonic()),
        )
        if not placed:
            LOGGER.error("Журнал не успел поставить команду остановки за %.1f с.", timeout)
            return False
        if self._thread is not threading.current_thread():
            self._thread.join(max(0.0, deadline - time.monotonic()))
        alive = self._thread.is_alive()
        if alive:
            LOGGER.error("Журнал не успел осушить очередь за %.1f с.", timeout)
        return not alive


def _encode_png(image: QImage) -> bytes:
    """Кодирует QImage в память; путь никогда не передаётся Qt/OpenCV."""

    if image.isNull():
        raise ValueError("Нельзя сохранить пустой кадр события.")
    payload = QByteArray()
    buffer = QBuffer(payload)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise OSError("Не удалось открыть буфер PNG.")
    try:
        if not image.save(buffer, "PNG"):
            raise OSError("Qt не смог закодировать PNG.")
    finally:
        buffer.close()
    return bytes(payload)


def _thumbnail_image(image: QImage) -> QImage:
    """Строит фиксированную 2x-миниатюру с letterbox без искажения."""

    canvas = QImage(
        THUMBNAIL_SIZE,
        QImage.Format.Format_RGB32,
    )
    canvas.fill(QColor("#11120F"))
    scaled = image.scaled(
        THUMBNAIL_SIZE,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    left = (THUMBNAIL_SIZE.width() - scaled.width()) // 2
    top = (THUMBNAIL_SIZE.height() - scaled.height()) // 2
    painter = QPainter(canvas)
    try:
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(left, top, scaled)
    finally:
        painter.end()
    return canvas


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        LOGGER.warning("Не удалось удалить файл журнала: %s", path.name)
