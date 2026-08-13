"""Единый фоновый детектор людей и машин на кадрах камер."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from .config import CameraConfig
from .constants import (
    DETECTION_INFERENCES_PER_SECOND,
    DETECTION_MODEL_FILENAME,
    DETECTION_ONNX_INTRA_OP_THREADS,
)
from .resources import resource_path


LOGGER = logging.getLogger(__name__)

PERSON_CLASS_ID = 0
VEHICLE_CLASS_IDS = (2, 3, 5, 7)  # car, motorcycle, bus, truck в COCO.
YOLOX_STRIDES = (8, 16, 32)
YOLOX_NMS_IOU_THRESHOLD = 0.45
STRICT_CONFIDENCE_THRESHOLD = 0.65
SENSITIVE_CONFIDENCE_THRESHOLD = 0.20


@dataclass(frozen=True, slots=True)
class Detection:
    """Одна рамка в долях исходного кадра, формат bbox — xyxy."""

    object_class: str
    confidence: float
    bbox: tuple[float, float, float, float]


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """Полная замена результата одной камеры для текущего кадра."""

    camera_id: str
    detections: tuple[Detection, ...]
    frame_size: tuple[int, int]
    timestamp: float
    frame: QImage


@dataclass(frozen=True, slots=True)
class LetterboxInfo:
    """Параметры масштабирования кадра во вход YOLOX."""

    original_width: int
    original_height: int
    input_width: int
    input_height: int
    scale: float
    pad_x: int
    pad_y: int


@dataclass(frozen=True, slots=True)
class CameraDetectionSettings:
    enabled: bool
    persons: bool
    vehicles: bool
    sensitivity: int

    @classmethod
    def from_config(cls, config: CameraConfig) -> "CameraDetectionSettings":
        return cls(
            enabled=config.detect_enabled,
            persons=config.detect_persons,
            vehicles=config.detect_vehicles,
            sensitivity=config.detect_sensitivity,
        )

    @property
    def active(self) -> bool:
        return self.enabled and (self.persons or self.vehicles)


@dataclass(frozen=True, slots=True)
class _CameraState:
    settings: CameraDetectionSettings
    generation: int


@dataclass(frozen=True, slots=True)
class _FrameItem:
    camera_id: str
    generation: int
    settings: CameraDetectionSettings
    image: QImage
    timestamp: float


class DetectionSignals(QObject):
    result_ready = Signal(object)
    status_changed = Signal(str, str, str)


def detection_model_path() -> Path:
    """Возвращает путь к модели и из исходников, и из PyInstaller-сборки."""

    return resource_path("assets", "models", DETECTION_MODEL_FILENAME)


def sensitivity_to_confidence(sensitivity: int) -> float:
    """Линейно переводит 0..100 в порог 0.65..0.20.

    Ноль намеренно самый строгий, а сто — самый чувствительный режим. Края
    собраны здесь, чтобы UI, тесты и движок не разошлись в трактовке шкалы.
    """

    if isinstance(sensitivity, bool) or not isinstance(sensitivity, int):
        raise ValueError("Чувствительность должна быть целым числом.")
    if not 0 <= sensitivity <= 100:
        raise ValueError("Чувствительность должна быть в диапазоне 0..100.")
    span = STRICT_CONFIDENCE_THRESHOLD - SENSITIVE_CONFIDENCE_THRESHOLD
    return STRICT_CONFIDENCE_THRESHOLD - span * (sensitivity / 100.0)


def preprocess_letterbox(
    image: np.ndarray,
    input_size: tuple[int, int],
) -> tuple[np.ndarray, LetterboxInfo]:
    """Масштабирует BGR-кадр без искажения и готовит NCHW float32."""

    if image.ndim != 3 or image.shape[2] != 3:
        raise ValueError("YOLOX ожидает BGR-изображение с тремя каналами.")
    original_height, original_width = image.shape[:2]
    input_height, input_width = input_size
    if min(original_width, original_height, input_width, input_height) <= 0:
        raise ValueError("Размеры кадра и входа модели должны быть положительными.")

    scale = min(input_width / original_width, input_height / original_height)
    resized_width = max(1, min(input_width, round(original_width * scale)))
    resized_height = max(1, min(input_height, round(original_height * scale)))
    resized = cv2.resize(
        image,
        (resized_width, resized_height),
        interpolation=cv2.INTER_LINEAR,
    )
    # Официальный ONNX-demo YOLOX кладёт кадр в левый верхний угол и заполняет
    # свободное поле значением 114; нормализация mean/std для этих весов не нужна.
    padded = np.full((input_height, input_width, 3), 114, dtype=np.uint8)
    pad_x = 0
    pad_y = 0
    padded[pad_y : pad_y + resized_height, pad_x : pad_x + resized_width] = resized
    blob = np.ascontiguousarray(padded.transpose(2, 0, 1), dtype=np.float32)[None]
    return blob, LetterboxInfo(
        original_width=original_width,
        original_height=original_height,
        input_width=input_width,
        input_height=input_height,
        scale=scale,
        pad_x=pad_x,
        pad_y=pad_y,
    )


@lru_cache(maxsize=8)
def _grid_and_strides(input_height: int, input_width: int) -> tuple[np.ndarray, np.ndarray]:
    grids: list[np.ndarray] = []
    expanded_strides: list[np.ndarray] = []
    for stride in YOLOX_STRIDES:
        grid_height = input_height // stride
        grid_width = input_width // stride
        y_grid, x_grid = np.meshgrid(
            np.arange(grid_height),
            np.arange(grid_width),
            indexing="ij",
        )
        grid = np.stack((x_grid, y_grid), axis=2).reshape(1, -1, 2)
        grids.append(grid)
        expanded_strides.append(
            np.full((1, grid.shape[1], 1), stride, dtype=np.float32)
        )
    return (
        np.concatenate(grids, axis=1).astype(np.float32),
        np.concatenate(expanded_strides, axis=1),
    )


def decode_yolox_output(
    output: np.ndarray,
    input_size: tuple[int, int],
) -> np.ndarray:
    """Декодирует сырые координаты официального YOLOX ONNX-export."""

    predictions = np.asarray(output, dtype=np.float32)
    if predictions.ndim == 2:
        predictions = predictions[None]
    if predictions.ndim != 3 or predictions.shape[2] < 6:
        raise ValueError("Модель вернула результат неожиданной формы.")

    input_height, input_width = input_size
    grids, strides = _grid_and_strides(input_height, input_width)
    if predictions.shape[1] != grids.shape[1]:
        raise ValueError("Число выходов YOLOX не соответствует размеру входа.")
    decoded = predictions.copy()
    decoded[..., :2] = (decoded[..., :2] + grids) * strides
    decoded[..., 2:4] = np.exp(np.clip(decoded[..., 2:4], -20.0, 20.0)) * strides
    return decoded[0]


def model_xyxy_to_normalized(
    box: Sequence[float],
    letterbox: LetterboxInfo,
) -> tuple[float, float, float, float] | None:
    """Возвращает bbox модели в долях исходного кадра с обрезкой по краям."""

    if len(box) != 4 or letterbox.scale <= 0.0:
        return None
    x1 = (float(box[0]) - letterbox.pad_x) / letterbox.scale
    y1 = (float(box[1]) - letterbox.pad_y) / letterbox.scale
    x2 = (float(box[2]) - letterbox.pad_x) / letterbox.scale
    y2 = (float(box[3]) - letterbox.pad_y) / letterbox.scale
    x1 = min(float(letterbox.original_width), max(0.0, x1))
    y1 = min(float(letterbox.original_height), max(0.0, y1))
    x2 = min(float(letterbox.original_width), max(0.0, x2))
    y2 = min(float(letterbox.original_height), max(0.0, y2))
    if x2 <= x1 or y2 <= y1:
        return None
    return (
        x1 / letterbox.original_width,
        y1 / letterbox.original_height,
        x2 / letterbox.original_width,
        y2 / letterbox.original_height,
    )


def non_max_suppression(
    boxes: np.ndarray,
    scores: np.ndarray,
    iou_threshold: float = YOLOX_NMS_IOU_THRESHOLD,
) -> list[int]:
    """Подавляет пересекающиеся рамки без учёта исходного COCO-класса."""

    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes.T
    areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    order = scores.argsort()[::-1]
    kept: list[int] = []
    while order.size:
        current = int(order[0])
        kept.append(current)
        if order.size == 1:
            break
        remaining = order[1:]
        intersection_width = np.maximum(
            0.0,
            np.minimum(x2[current], x2[remaining])
            - np.maximum(x1[current], x1[remaining]),
        )
        intersection_height = np.maximum(
            0.0,
            np.minimum(y2[current], y2[remaining])
            - np.maximum(y1[current], y1[remaining]),
        )
        intersection = intersection_width * intersection_height
        union = areas[current] + areas[remaining] - intersection
        iou = np.divide(
            intersection,
            union,
            out=np.zeros_like(intersection),
            where=union > 0.0,
        )
        order = remaining[iou <= iou_threshold]
    return kept


def filter_yolox_predictions(
    predictions: np.ndarray,
    letterbox: LetterboxInfo,
    *,
    confidence_threshold: float,
    persons: bool,
    vehicles: bool,
) -> tuple[Detection, ...]:
    """Фильтрует порог и NMS, объединяя транспорт в один пользовательский класс."""

    predictions = np.asarray(predictions, dtype=np.float32)
    if predictions.ndim != 2 or predictions.shape[1] <= max(VEHICLE_CLASS_IDS) + 5:
        raise ValueError("Модель вернула недостаточно COCO-классов.")
    centers = predictions[:, :2]
    sizes = predictions[:, 2:4]
    boxes = np.empty_like(predictions[:, :4])
    boxes[:, :2] = centers - sizes / 2.0
    boxes[:, 2:4] = centers + sizes / 2.0
    objectness = predictions[:, 4]

    groups: list[tuple[str, tuple[int, ...]]] = []
    if persons:
        groups.append(("person", (PERSON_CLASS_ID,)))
    if vehicles:
        groups.append(("vehicle", VEHICLE_CLASS_IDS))

    detections: list[Detection] = []
    for object_class, class_ids in groups:
        class_probabilities = predictions[:, np.asarray(class_ids) + 5]
        if class_probabilities.ndim == 1:
            class_probabilities = class_probabilities[:, None]
        scores = objectness * class_probabilities.max(axis=1)
        candidates = np.flatnonzero(scores >= confidence_threshold)
        if candidates.size == 0:
            continue
        candidate_boxes = boxes[candidates]
        candidate_scores = scores[candidates]
        # Для vehicle NMS намеренно общий: car/truck/bus/motorcycle не дают
        # две рамки на одном физическом автомобиле после агрегации класса.
        for local_index in non_max_suppression(candidate_boxes, candidate_scores):
            source_index = int(candidates[local_index])
            normalized = model_xyxy_to_normalized(boxes[source_index], letterbox)
            if normalized is None:
                continue
            detections.append(
                Detection(
                    object_class=object_class,
                    confidence=float(scores[source_index]),
                    bbox=normalized,
                )
            )
    detections.sort(key=lambda item: item.confidence, reverse=True)
    return tuple(detections)


def postprocess_yolox(
    output: np.ndarray,
    letterbox: LetterboxInfo,
    *,
    sensitivity: int,
    persons: bool,
    vehicles: bool,
) -> tuple[Detection, ...]:
    decoded = decode_yolox_output(
        output,
        (letterbox.input_height, letterbox.input_width),
    )
    return filter_yolox_predictions(
        decoded,
        letterbox,
        confidence_threshold=sensitivity_to_confidence(sensitivity),
        persons=persons,
        vehicles=vehicles,
    )


def qimage_to_bgr(image: QImage) -> np.ndarray:
    """Копирует QImage в BGR numpy-массив внутри потока детектора."""

    converted = image.convertToFormat(QImage.Format.Format_BGR888)
    width = converted.width()
    height = converted.height()
    if width <= 0 or height <= 0:
        raise ValueError("Получен пустой кадр камеры.")
    raw = np.frombuffer(
        converted.bits(),
        dtype=np.uint8,
        count=converted.sizeInBytes(),
    ).reshape(height, converted.bytesPerLine())
    return raw[:, : width * 3].reshape(height, width, 3).copy()


class YoloXDetector:
    """Ленивая ONNX Runtime-сессия YOLOX-tiny на CPU."""

    def __init__(
        self,
        model_path: Path | None = None,
        *,
        intra_op_num_threads: int = DETECTION_ONNX_INTRA_OP_THREADS,
    ) -> None:
        self.model_path = model_path or detection_model_path()
        self.intra_op_num_threads = max(1, int(intra_op_num_threads))
        self._session: object | None = None
        self._input_name = ""
        self._input_size = (416, 416)

    def _ensure_session(self) -> None:
        if self._session is not None:
            return
        if not self.model_path.is_file():
            raise FileNotFoundError(self.model_path)

        import onnxruntime as ort

        options = ort.SessionOptions()
        options.intra_op_num_threads = self.intra_op_num_threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        session = ort.InferenceSession(
            str(self.model_path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        model_input = session.get_inputs()[0]
        shape = model_input.shape
        if len(shape) == 4 and isinstance(shape[2], int) and isinstance(shape[3], int):
            self._input_size = (shape[2], shape[3])
        self._input_name = model_input.name
        self._session = session

    @property
    def input_size(self) -> tuple[int, int]:
        self._ensure_session()
        return self._input_size

    def infer_bgr(
        self,
        image: np.ndarray,
        *,
        sensitivity: int,
        persons: bool,
        vehicles: bool,
    ) -> tuple[Detection, ...]:
        self._ensure_session()
        blob, letterbox = preprocess_letterbox(image, self._input_size)
        session = self._session
        if session is None:
            raise RuntimeError("ONNX Runtime-сессия не создана.")
        output = session.run(None, {self._input_name: blob})[0]  # type: ignore[attr-defined]
        return postprocess_yolox(
            output,
            letterbox,
            sensitivity=sensitivity,
            persons=persons,
            vehicles=vehicles,
        )

    def infer_qimage(
        self,
        image: QImage,
        *,
        sensitivity: int,
        persons: bool,
        vehicles: bool,
    ) -> tuple[Detection, ...]:
        return self.infer_bgr(
            qimage_to_bgr(image),
            sensitivity=sensitivity,
            persons=persons,
            vehicles=vehicles,
        )


class DetectionEngine:
    """Один daemon-поток с почтовым ящиком на последний кадр каждой камеры."""

    def __init__(self, model_path: Path | None = None) -> None:
        self.model_path = model_path or detection_model_path()
        self.signals = DetectionSignals()
        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._states: dict[str, _CameraState] = {}
        self._mailboxes: dict[str, QImage] = {}
        self._last_started: dict[str, float] = {}
        self._thread: threading.Thread | None = None
        self._next_index = 0
        self._runtime_failed = False
        self._missing_reported = False

    def model_available(self) -> bool:
        return self.model_path.is_file()

    def configure_camera(self, config: CameraConfig) -> bool:
        """Применяет настройки сразу и возвращает доступность запуска."""

        settings = CameraDetectionSettings.from_config(config)
        model_available = self.model_available()
        with self._condition:
            previous = self._states.get(config.camera_id)
            generation = 1 if previous is None else previous.generation + 1
            self._states[config.camera_id] = _CameraState(settings, generation)
            if not settings.active or not model_available:
                self._mailboxes.pop(config.camera_id, None)
            if settings.active and model_available and not self._runtime_failed:
                self._ensure_thread_locked()
            self._condition.notify_all()

        if settings.active and not model_available:
            if not self._missing_reported:
                LOGGER.warning("Модель детектора не найдена: %s", self.model_path)
                self._missing_reported = True
            self.signals.status_changed.emit(
                config.camera_id,
                "model_missing",
                "Модель не найдена — запустите scripts\\fetch_model.py",
            )
            return False
        self.signals.status_changed.emit(config.camera_id, "ready", "")
        return settings.active and not self._runtime_failed

    def remove_camera(self, camera_id: str) -> None:
        with self._condition:
            self._states.pop(camera_id, None)
            self._mailboxes.pop(camera_id, None)
            self._last_started.pop(camera_id, None)
            self._condition.notify_all()

    def submit_frame(self, camera_id: str, image: QImage) -> None:
        """Заменяет непрочитанный кадр; очередь по камере никогда не растёт."""

        if self._stop_event.is_set() or image.isNull():
            return
        with self._condition:
            state = self._states.get(camera_id)
            if (
                state is None
                or not state.settings.active
                or self._runtime_failed
                or self._thread is None
            ):
                return
            # CameraReader уже создал независимый QImage; конструктор здесь
            # лишь дёшево удерживает implicit-shared копию до обработки.
            self._mailboxes[camera_id] = QImage(image)
            self._condition.notify()

    def _ensure_thread_locked(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        if self._stop_event.is_set():
            return
        self._thread = threading.Thread(
            target=self._run,
            name="object-detector",
            daemon=True,
        )
        self._thread.start()

    def _take_work(self) -> _FrameItem | None:
        interval = 1.0 / DETECTION_INFERENCES_PER_SECOND
        with self._condition:
            while not self._stop_event.is_set():
                if self._runtime_failed:
                    self._condition.wait()
                    continue
                active_ids = [
                    camera_id
                    for camera_id, state in self._states.items()
                    if state.settings.active
                ]
                if not active_ids or not self._mailboxes:
                    self._condition.wait()
                    continue

                now = time.monotonic()
                earliest_due: float | None = None
                count = len(active_ids)
                start = self._next_index % count
                for offset in range(count):
                    index = (start + offset) % count
                    camera_id = active_ids[index]
                    image = self._mailboxes.get(camera_id)
                    if image is None:
                        continue
                    due = self._last_started.get(camera_id, 0.0) + interval
                    if now < due:
                        earliest_due = due if earliest_due is None else min(earliest_due, due)
                        continue
                    state = self._states[camera_id]
                    self._mailboxes.pop(camera_id, None)
                    self._last_started[camera_id] = now
                    self._next_index = (index + 1) % count
                    return _FrameItem(
                        camera_id=camera_id,
                        generation=state.generation,
                        settings=state.settings,
                        image=image,
                        timestamp=now,
                    )

                timeout = None
                if earliest_due is not None:
                    timeout = max(0.001, earliest_due - now)
                self._condition.wait(timeout)
        return None

    def _run(self) -> None:
        detector = YoloXDetector(self.model_path)
        while not self._stop_event.is_set():
            work = self._take_work()
            if work is None:
                break
            try:
                detections = detector.infer_qimage(
                    work.image,
                    sensitivity=work.settings.sensitivity,
                    persons=work.settings.persons,
                    vehicles=work.settings.vehicles,
                )
            except Exception:
                LOGGER.exception("Детектор YOLOX остановлен из-за ошибки ONNX Runtime.")
                with self._condition:
                    self._runtime_failed = True
                    self._mailboxes.clear()
                    self._condition.notify_all()
                self.signals.status_changed.emit(
                    work.camera_id,
                    "runtime_error",
                    "Не удалось запустить модель распознавания.",
                )
                continue

            with self._condition:
                current = self._states.get(work.camera_id)
                if (
                    current is None
                    or current.generation != work.generation
                    or not current.settings.active
                ):
                    continue
            self.signals.result_ready.emit(
                DetectionResult(
                    camera_id=work.camera_id,
                    detections=detections,
                    frame_size=(work.image.width(), work.image.height()),
                    timestamp=work.timestamp,
                    frame=work.image,
                )
            )

    def is_alive(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def shutdown(self, timeout: float = 5.0) -> bool:
        self._stop_event.set()
        with self._condition:
            self._mailboxes.clear()
            self._condition.notify_all()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(max(0.0, timeout))
        return thread is None or not thread.is_alive()
