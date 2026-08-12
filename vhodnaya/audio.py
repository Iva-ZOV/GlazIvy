"""Отдельный RTSP-аудиопоток и единственный Qt-аудиовыход приложения."""

from __future__ import annotations

import importlib
import threading
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtMultimedia import (
    QAudio,
    QAudioFormat,
    QAudioSink,
    QMediaDevices,
    QtAudio,
)

from .config import AppConfig, CameraConfig
from .constants import (
    READ_TIMEOUT_MS,
    RECONNECT_INITIAL_SECONDS,
    RECONNECT_MAX_SECONDS,
    SD_OPEN_TIMEOUT_MS,
)


@dataclass(frozen=True, slots=True)
class AudioPcmFormat:
    """Формат PCM между PyAV и QAudioSink."""

    sample_rate: int = 48_000
    channels: int = 2
    bytes_per_sample: int = 2

    @property
    def frame_bytes(self) -> int:
        return self.channels * self.bytes_per_sample

    def bytes_for_seconds(self, seconds: float) -> int:
        return max(
            self.frame_bytes,
            round(self.sample_rate * self.frame_bytes * max(0.0, seconds)),
        )


class PcmBuffer:
    """Ограниченная FIFO-очередь: при переполнении отбрасывает старый PCM."""

    def __init__(self, max_bytes: int, *, frame_bytes: int = 1) -> None:
        self.frame_bytes = max(1, int(frame_bytes))
        aligned_maximum = int(max_bytes) - int(max_bytes) % self.frame_bytes
        self.max_bytes = max(self.frame_bytes, aligned_maximum)
        self._data = bytearray()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def write(self, payload: bytes | bytearray | memoryview) -> int:
        """Добавляет целые PCM-фреймы и возвращает число отброшенных байтов."""

        data = bytes(payload)
        usable = len(data) - len(data) % self.frame_bytes
        if usable <= 0:
            return 0
        data = data[:usable]

        with self._lock:
            before = len(self._data)
            if len(data) >= self.max_bytes:
                self._data = bytearray(data[-self.max_bytes :])
                return before + len(data) - self.max_bytes

            self._data.extend(data)
            overflow = len(self._data) - self.max_bytes
            if overflow <= 0:
                return 0
            drop = (
                (overflow + self.frame_bytes - 1) // self.frame_bytes
            ) * self.frame_bytes
            del self._data[:drop]
            return drop

    def read(self, maximum_bytes: int) -> bytes:
        requested = max(0, int(maximum_bytes))
        requested -= requested % self.frame_bytes
        if requested <= 0:
            return b""
        with self._lock:
            size = min(requested, len(self._data))
            size -= size % self.frame_bytes
            if size <= 0:
                return b""
            result = bytes(self._data[:size])
            del self._data[:size]
            return result


class AudioReaderSignals(QObject):
    state_changed = Signal(str, int)
    track_missing = Signal(int)
    backend_unavailable = Signal(int)
    finished = Signal(int)


class AudioReader:
    """Daemon-поток PyAV; модуль ``av`` импортируется только внутри треда."""

    def __init__(
        self,
        *,
        config: CameraConfig,
        generation: int,
        buffer: PcmBuffer,
        pcm_format: AudioPcmFormat,
    ) -> None:
        self.config = config
        self.generation = generation
        self.buffer = buffer
        self.pcm_format = pcm_format
        self.signals = AudioReaderSignals()
        self._stop_event = threading.Event()
        self._reconnect_event = threading.Event()
        self._wake_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"audio-{generation}",
            daemon=True,
        )
        self._last_state: str | None = None

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()

    def force_reconnect(self) -> None:
        self._reconnect_event.set()
        self._wake_event.set()

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    @staticmethod
    def _load_av() -> Any:
        # Не переносить import av на уровень модуля: приложение обязано
        # запускаться с выключенным звуком даже при сломанной установке PyAV.
        return importlib.import_module("av")

    def demuxer_options(self) -> dict[str, str]:
        transport = "udp" if self.config.transport == "udp" else "tcp"
        return {
            "rtsp_transport": transport,
            "stimeout": str(SD_OPEN_TIMEOUT_MS * 1000),
            "timeout": str(READ_TIMEOUT_MS * 1000),
            # Критично для трафика: вторая RTSP-сессия не запрашивает видео.
            "allowed_media_types": "audio",
        }

    def _open_container(self, av_module: Any) -> Any:
        url = self.config.build_rtsp_url()
        return av_module.open(
            url,
            mode="r",
            options=self.demuxer_options(),
            timeout=(SD_OPEN_TIMEOUT_MS / 1000.0, READ_TIMEOUT_MS / 1000.0),
        )

    def _emit_state(self, state: str) -> None:
        if state == self._last_state:
            return
        self._last_state = state
        try:
            self.signals.state_changed.emit(state, self.generation)
        except RuntimeError:
            self._stop_event.set()

    def _emit_terminal(self, signal: Signal) -> None:
        try:
            signal.emit(self.generation)
        except RuntimeError:
            self._stop_event.set()

    def _pcm_bytes(self, frame: Any) -> bytes:
        samples = max(0, int(getattr(frame, "samples", 0)))
        expected = samples * self.pcm_format.frame_bytes
        planes = getattr(frame, "planes", ())
        if expected <= 0 or not planes:
            return b""
        return bytes(planes[0])[:expected]

    @staticmethod
    def _resampled_frames(result: Any) -> tuple[Any, ...]:
        if result is None:
            return ()
        if isinstance(result, (list, tuple)):
            return tuple(result)
        return (result,)

    def _run(self) -> None:
        try:
            try:
                av_module = self._load_av()
                # FFmpeg иногда включает RTSP URL в диагностический вывод.
                # Своих исключений наружу мы также никогда не передаём.
                try:
                    av_module.logging.set_level(None)
                except Exception:
                    pass
            except Exception:
                self._emit_terminal(self.signals.backend_unavailable)
                return

            reconnect_delay = RECONNECT_INITIAL_SECONDS
            first_attempt = True
            while not self._stop_event.is_set():
                self._reconnect_event.clear()
                self._emit_state("connecting" if first_attempt else "reconnecting")
                first_attempt = False
                container: Any | None = None
                received_pcm = False
                try:
                    container = self._open_container(av_module)
                    if self._stop_event.is_set():
                        break

                    audio_streams = tuple(getattr(container.streams, "audio", ()))
                    if not audio_streams:
                        # Только успешный RTSP-open без audio stream считается
                        # подтверждённым отсутствием дорожки.
                        self._emit_terminal(self.signals.track_missing)
                        return

                    audio_stream = audio_streams[0]
                    resampler = av_module.AudioResampler(
                        format="s16",
                        layout="stereo",
                        rate=self.pcm_format.sample_rate,
                    )
                    for packet in container.demux(audio_stream):
                        if (
                            self._stop_event.is_set()
                            or self._reconnect_event.is_set()
                        ):
                            break
                        for decoded in packet.decode():
                            for frame in self._resampled_frames(
                                resampler.resample(decoded)
                            ):
                                payload = self._pcm_bytes(frame)
                                if not payload:
                                    continue
                                self.buffer.write(payload)
                                if not received_pcm:
                                    received_pcm = True
                                    reconnect_delay = RECONNECT_INITIAL_SECONDS
                                    self._emit_state("playing")
                except Exception:
                    # Ошибки сети/демультиплексора/декодера не меняют audio_on.
                    if not self._stop_event.is_set():
                        self._emit_state("reconnecting")
                finally:
                    if container is not None:
                        try:
                            container.close()
                        except Exception:
                            pass

                self._wake_event.clear()
                if self._stop_event.is_set():
                    break
                if self._reconnect_event.is_set():
                    reconnect_delay = RECONNECT_INITIAL_SECONDS
                    continue

                self._emit_state("reconnecting")
                self._wake_event.wait(reconnect_delay)
                reconnect_delay = min(
                    reconnect_delay * 1.6,
                    RECONNECT_MAX_SECONDS,
                )
        finally:
            try:
                self.signals.finished.emit(self.generation)
            except RuntimeError:
                pass


def audio_connection_signature(config: CameraConfig) -> tuple[object, ...]:
    """Поля, изменение которых требует новой аудио-RTSP-сессии."""

    return (
        config.host,
        config.port,
        config.username,
        config.password,
        config.transport,
        config.quality,
        config.stream_path,
        config.source,
        config.stream_url_hd,
        config.stream_url_sd,
    )


class AudioController(QObject):
    """Владеет единственными AudioReader/QAudioSink на всё приложение."""

    track_missing = Signal(str)
    state_changed = Signal(str, str)
    backend_available_changed = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.pcm_format = AudioPcmFormat()
        self.buffer = self._new_buffer()
        self._generation = 0
        self._readers: dict[int, AudioReader] = {}
        self._current_reader: AudioReader | None = None
        self._active_camera_id: str | None = None
        self._active_signature: tuple[object, ...] | None = None
        self._desired_config = AppConfig()
        self._reader_state = "off"
        self._sink: QAudioSink | None = None
        self._sink_device: Any | None = None
        self._creating_sink = False
        self._backend_available = True
        self._missing_signatures: dict[str, tuple[object, ...]] = {}
        self._shutting_down = False

        # QMediaDevices и все QAudioSink ниже создаются и используются только
        # в потоке QObject-контроллера — MainWindow/GUI thread.
        self._media_devices = QMediaDevices(self)
        self._media_devices.audioOutputsChanged.connect(
            self._audio_outputs_changed
        )

        self._drain_timer = QTimer(self)
        self._drain_timer.setInterval(20)
        self._drain_timer.timeout.connect(self._drain_pcm)

        self._sink_retry_timer = QTimer(self)
        self._sink_retry_timer.setInterval(2000)
        self._sink_retry_timer.timeout.connect(self._retry_sink)

        self._cleanup_timer = QTimer(self)
        self._cleanup_timer.setInterval(3000)
        self._cleanup_timer.timeout.connect(self._prune_readers)
        self._cleanup_timer.start()

    def _new_buffer(self) -> PcmBuffer:
        return PcmBuffer(
            self.pcm_format.bytes_for_seconds(0.75),
            frame_bytes=self.pcm_format.frame_bytes,
        )

    @property
    def backend_available(self) -> bool:
        return self._backend_available

    def sync_config(self, config: AppConfig, *, force_restart: bool = False) -> None:
        if self._shutting_down:
            return
        self._desired_config = config
        by_id = {camera.camera_id: camera for camera in config.cameras}
        for camera_id, signature in tuple(self._missing_signatures.items()):
            camera = by_id.get(camera_id)
            if camera is None or audio_connection_signature(camera) != signature:
                self._missing_signatures.pop(camera_id, None)

        selected = next((camera for camera in config.cameras if camera.audio_on), None)
        if (
            selected is None
            or not selected.on_board
            or not selected.is_configured()
        ):
            self._stop_session()
            return

        signature = audio_connection_signature(selected)
        if self._missing_signatures.get(selected.camera_id) == signature:
            self._stop_session()
            self.state_changed.emit(selected.camera_id, "no_track")
            return
        if not self._backend_available:
            self._stop_session()
            self.state_changed.emit(selected.camera_id, "backend_unavailable")
            return

        if (
            not force_restart
            and selected.camera_id == self._active_camera_id
            and signature == self._active_signature
            and self._current_reader is not None
        ):
            self._set_sink_volume(selected.volume)
            return
        self._start_session(selected)

    def restart_camera(self, camera_id: str) -> None:
        selected = next(
            (
                camera
                for camera in self._desired_config.cameras
                if camera.camera_id == camera_id and camera.audio_on
            ),
            None,
        )
        if selected is None or not selected.on_board or not selected.is_configured():
            return
        self._missing_signatures.pop(camera_id, None)
        self._start_session(selected)

    def _start_session(self, camera: CameraConfig) -> None:
        self._stop_session()
        if self._shutting_down:
            return
        self._generation += 1
        generation = self._generation
        self._active_camera_id = camera.camera_id
        self._active_signature = audio_connection_signature(camera)
        self._reader_state = "connecting"
        # Останавливающийся reader сохраняет ссылку на прежнюю очередь и уже
        # не может подмешать PCM старой камеры в новый активный звук.
        self.buffer = self._new_buffer()
        self._create_sink(camera.volume)

        reader = AudioReader(
            config=camera,
            generation=generation,
            buffer=self.buffer,
            pcm_format=self.pcm_format,
        )
        reader.signals.state_changed.connect(self._reader_state_changed)
        reader.signals.track_missing.connect(self._reader_track_missing)
        reader.signals.backend_unavailable.connect(
            self._reader_backend_unavailable
        )
        reader.signals.finished.connect(self._reader_finished)
        self._readers[generation] = reader
        self._current_reader = reader
        self._drain_timer.start()
        self.state_changed.emit(camera.camera_id, "connecting")
        reader.start()

    def _stop_session(self) -> None:
        self._generation += 1
        reader = self._current_reader
        if reader is not None:
            reader.stop()
        self._current_reader = None
        self._active_camera_id = None
        self._active_signature = None
        self._reader_state = "off"
        self._drain_timer.stop()
        self._sink_retry_timer.stop()
        self.buffer.clear()
        self._destroy_sink()

    def _qt_audio_format(self) -> QAudioFormat:
        audio_format = QAudioFormat()
        audio_format.setSampleRate(self.pcm_format.sample_rate)
        audio_format.setChannelCount(self.pcm_format.channels)
        audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        return audio_format

    def _create_sink(self, volume: int) -> bool:
        if self._creating_sink:
            return False
        self._creating_sink = True
        try:
            self._destroy_sink()
            if self._active_camera_id is None:
                return False
            try:
                device = QMediaDevices.defaultAudioOutput()
                audio_format = self._qt_audio_format()
                if device.isNull() or not device.isFormatSupported(audio_format):
                    raise RuntimeError("default audio output unavailable")
                sink = QAudioSink(device, audio_format, self)
                sink.setBufferSize(audio_format.bytesForDuration(250_000))
                sink.stateChanged.connect(
                    lambda state, current=sink: self._sink_state_changed(
                        current,
                        state,
                    )
                )
                self._sink = sink
                self._sink_device = sink.start()
                if self._sink_device is None:
                    raise RuntimeError("audio sink did not return an IO device")
                self._set_sink_volume(volume)
            except Exception:
                self._destroy_sink()
                self._sink_retry_timer.start()
                self.state_changed.emit(self._active_camera_id, "device_error")
                return False
            self._sink_retry_timer.stop()
            if self._reader_state == "playing":
                self.state_changed.emit(self._active_camera_id, "playing")
            return True
        finally:
            self._creating_sink = False

    def _destroy_sink(self) -> None:
        sink = self._sink
        self._sink = None
        self._sink_device = None
        if sink is None:
            return
        try:
            sink.stop()
        except RuntimeError:
            pass
        try:
            sink.deleteLater()
        except RuntimeError:
            pass

    def _set_sink_volume(self, volume: int) -> None:
        sink = self._sink
        if sink is None:
            return
        logarithmic = min(1.0, max(0.0, int(volume) / 100.0))
        linear = QtAudio.convertVolume(
            logarithmic,
            QtAudio.VolumeScale.LogarithmicVolumeScale,
            QtAudio.VolumeScale.LinearVolumeScale,
        )
        try:
            sink.setVolume(float(linear))
        except RuntimeError:
            if self._creating_sink:
                raise
            self._create_sink(volume)

    def _drain_pcm(self) -> None:
        sink = self._sink
        device = self._sink_device
        if sink is None or device is None:
            return
        try:
            available = max(0, int(sink.bytesFree()))
            payload = self.buffer.read(min(available, 32_768))
            if not payload:
                return
            written = int(device.write(payload))
            if written < 0:
                self._create_sink(self._active_volume())
        except (AttributeError, RuntimeError, TypeError, ValueError):
            self._create_sink(self._active_volume())

    def _active_volume(self) -> int:
        active_id = self._active_camera_id
        camera = next(
            (
                item
                for item in self._desired_config.cameras
                if item.camera_id == active_id
            ),
            None,
        )
        return camera.volume if camera is not None else 80

    def _reader_state_changed(self, state: str, generation: int) -> None:
        if generation != self._generation or self._shutting_down:
            return
        camera_id = self._active_camera_id
        if camera_id is None:
            return
        self._reader_state = state
        if state == "playing" and self._sink is None:
            self.state_changed.emit(camera_id, "device_error")
        else:
            self.state_changed.emit(camera_id, state)

    def _reader_track_missing(self, generation: int) -> None:
        if generation != self._generation or self._shutting_down:
            return
        camera_id = self._active_camera_id
        signature = self._active_signature
        if camera_id is None or signature is None:
            return
        self._missing_signatures[camera_id] = signature
        self._stop_session()
        self.state_changed.emit(camera_id, "no_track")
        self.track_missing.emit(camera_id)

    def _reader_backend_unavailable(self, generation: int) -> None:
        if generation != self._generation or self._shutting_down:
            return
        camera_id = self._active_camera_id
        if camera_id is None:
            return
        self._backend_available = False
        self._stop_session()
        self.backend_available_changed.emit(False)
        self.state_changed.emit(camera_id, "backend_unavailable")

    def _reader_finished(self, generation: int) -> None:
        reader = self._readers.get(generation)
        if reader is not None and not reader.is_alive():
            self._readers.pop(generation, None)
        if generation == self._generation and reader is self._current_reader:
            self._current_reader = None

    def _prune_readers(self) -> None:
        for generation, reader in tuple(self._readers.items()):
            if reader is not self._current_reader and not reader.is_alive():
                self._readers.pop(generation, None)

    def _sink_state_changed(self, sink: QAudioSink, state: Any) -> None:
        if (
            sink is not self._sink
            or self._shutting_down
            or self._creating_sink
        ):
            return
        if state != QAudio.State.StoppedState:
            return
        self._create_sink(self._active_volume())

    def _audio_outputs_changed(self) -> None:
        if self._active_camera_id is None or self._shutting_down:
            return
        self.buffer.clear()
        self._create_sink(self._active_volume())

    def _retry_sink(self) -> None:
        if self._active_camera_id is None or self._shutting_down:
            self._sink_retry_timer.stop()
            return
        self._create_sink(self._active_volume())

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self._cleanup_timer.stop()
        self._stop_session()
