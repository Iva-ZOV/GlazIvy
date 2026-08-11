"""Фоновый RTSP-захват OpenCV с адаптивным запуском и переподключением."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

import cv2
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from .constants import (
    HD_OPEN_TIMEOUT_MS,
    HD_STARTUP_GRACE_SECONDS,
    READ_TIMEOUT_MS,
    RECONNECT_INITIAL_SECONDS,
    RECONNECT_MAX_SECONDS,
    SD_OPEN_TIMEOUT_MS,
    SD_STARTUP_GRACE_SECONDS,
)

# OPENCV_FFMPEG_CAPTURE_OPTIONS — глобальная переменная процесса. Замок не
# позволяет двум переключающимся потокам прочитать чужой transport при open().
_CAPTURE_OPEN_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class StreamProfile:
    quality: str
    open_timeout_ms: int
    read_timeout_ms: int
    startup_grace_seconds: float

    @classmethod
    def for_quality(cls, quality: str) -> "StreamProfile":
        if quality == "hd":
            return cls(
                quality="hd",
                open_timeout_ms=HD_OPEN_TIMEOUT_MS,
                read_timeout_ms=READ_TIMEOUT_MS,
                startup_grace_seconds=HD_STARTUP_GRACE_SECONDS,
            )
        return cls(
            quality="sd",
            open_timeout_ms=SD_OPEN_TIMEOUT_MS,
            read_timeout_ms=READ_TIMEOUT_MS,
            startup_grace_seconds=SD_STARTUP_GRACE_SECONDS,
        )


class CameraSignals(QObject):
    frame_ready = Signal(QImage, int)
    state_changed = Signal(str, str, int)
    finished = Signal(int)


class CameraReader:
    """Daemon-поток: UI никогда не ждёт зависший сетевой open()/read()."""

    def __init__(
        self,
        *,
        url: str,
        transport: str,
        quality: str,
        generation: int,
    ) -> None:
        self.url = url
        self.transport = transport
        self.profile = StreamProfile.for_quality(quality)
        self.generation = generation
        self.signals = CameraSignals()
        self._stop_event = threading.Event()
        self._reconnect_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"camera-{generation}",
            daemon=True,
        )
        self._last_state: tuple[str, str] | None = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._reconnect_event.set()

    def force_reconnect(self) -> None:
        self._reconnect_event.set()

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def _emit_state(self, state: str, detail: str) -> None:
        current = (state, detail)
        if current == self._last_state:
            return
        self._last_state = current
        try:
            self.signals.state_changed.emit(state, detail, self.generation)
        except RuntimeError:
            self._stop_event.set()

    def _sleep_interruptibly(self, seconds: float) -> None:
        self._stop_event.wait(seconds)

    def _open_capture(self) -> cv2.VideoCapture:
        profile = self.profile
        transport = "udp" if self.transport == "udp" else "tcp"
        ffmpeg_options = (
            f"rtsp_transport;{transport}"
            f"|timeout;{profile.read_timeout_ms * 1000}"
            "|max_delay;500000"
        )

        params: list[int] = []
        open_timeout_prop = getattr(cv2, "CAP_PROP_OPEN_TIMEOUT_MSEC", None)
        read_timeout_prop = getattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC", None)
        if open_timeout_prop is not None:
            params.extend([open_timeout_prop, profile.open_timeout_ms])
        if read_timeout_prop is not None:
            params.extend([read_timeout_prop, profile.read_timeout_ms])

        with _CAPTURE_OPEN_LOCK:
            # Если этот reader устарел, пока ждал другой долгий HD-open, не
            # запускаем вслед за ним ещё одно ненужное соединение.
            if self._stop_event.is_set():
                return cv2.VideoCapture()
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = ffmpeg_options
            capture = cv2.VideoCapture()
            if params:
                capture.open(self.url, cv2.CAP_FFMPEG, params)
            else:
                capture.open(self.url, cv2.CAP_FFMPEG)

        if capture.isOpened():
            # Не все FFmpeg-сборки принимают это значение, поэтому результат
            # set() намеренно не считается ошибкой.
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 2)
        return capture

    @staticmethod
    def _to_qimage(frame: object) -> QImage:
        height, width = frame.shape[:2]  # type: ignore[attr-defined]
        stride = int(frame.strides[0])  # type: ignore[attr-defined]
        image = QImage(
            frame.data,  # type: ignore[attr-defined]
            width,
            height,
            stride,
            QImage.Format.Format_BGR888,
        )
        # OpenCV переиспользует буфер следующей итерацией; Qt нужна своя копия.
        return image.copy()

    def _run(self) -> None:
        session_started = time.monotonic()
        startup_deadline = session_started + self.profile.startup_grace_seconds
        has_received_frame = False
        reconnect_delay = RECONNECT_INITIAL_SECONDS

        quality_name = self.profile.quality.upper()
        self._emit_state(
            "connecting",
            (
                "HD-поток запускается — это может занять до 60 секунд"
                if self.profile.quality == "hd"
                else "Открываем SD-поток"
            ),
        )

        try:
            while not self._stop_event.is_set():
                self._reconnect_event.clear()
                capture: cv2.VideoCapture | None = None
                try:
                    capture = self._open_capture()
                    if self._stop_event.is_set():
                        break
                    if not capture.isOpened():
                        now = time.monotonic()
                        if not has_received_frame and now < startup_deadline:
                            self._emit_state(
                                "connecting",
                                f"Ожидаем первый кадр {quality_name}…",
                            )
                        else:
                            self._emit_state(
                                "reconnecting",
                                "Камера не ответила — пробуем снова",
                            )
                        self._sleep_interruptibly(reconnect_delay)
                        reconnect_delay = min(
                            reconnect_delay * 1.6,
                            RECONNECT_MAX_SECONDS,
                        )
                        continue

                    while (
                        not self._stop_event.is_set()
                        and not self._reconnect_event.is_set()
                    ):
                        ok, frame = capture.read()
                        now = time.monotonic()
                        if ok and frame is not None and getattr(frame, "size", 0):
                            image = self._to_qimage(frame)
                            try:
                                self.signals.frame_ready.emit(image, self.generation)
                            except RuntimeError:
                                self._stop_event.set()
                                break
                            if not has_received_frame:
                                has_received_frame = True
                                reconnect_delay = RECONNECT_INITIAL_SECONDS
                                self._emit_state("online", f"{quality_name} · {self.transport.upper()}")
                            elif self._last_state and self._last_state[0] != "online":
                                reconnect_delay = RECONNECT_INITIAL_SECONDS
                                self._emit_state("online", f"{quality_name} · {self.transport.upper()}")
                            continue

                        if not has_received_frame and now < startup_deadline:
                            # Некоторые камеры открывают RTSP-сессию раньше,
                            # чем декодер получает первый ключевой кадр.
                            self._emit_state(
                                "connecting",
                                f"Ожидаем первый кадр {quality_name}…",
                            )
                            if capture.isOpened():
                                self._sleep_interruptibly(0.2)
                                continue
                        break

                except Exception:
                    # В статус не передаём exception: сообщения FFmpeg иногда
                    # содержат URL и, следовательно, пароль.
                    if not self._stop_event.is_set():
                        now = time.monotonic()
                        if not has_received_frame and now < startup_deadline:
                            self._emit_state("connecting", f"Подключаем {quality_name}-поток…")
                        else:
                            self._emit_state("reconnecting", "Связь прервана — восстанавливаем")
                finally:
                    if capture is not None:
                        capture.release()

                if self._stop_event.is_set():
                    break

                now = time.monotonic()
                if not has_received_frame and now < startup_deadline:
                    self._emit_state("connecting", f"Подключаем {quality_name}-поток…")
                else:
                    self._emit_state("reconnecting", "Переподключение… последний кадр сохранён")
                self._sleep_interruptibly(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.6, RECONNECT_MAX_SECONDS)
        finally:
            try:
                self.signals.finished.emit(self.generation)
            except RuntimeError:
                pass
