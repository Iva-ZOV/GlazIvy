"""Общие константы приложения."""

from __future__ import annotations

APP_NAME = "Глаз Ивы"
APP_SLUG = "GlazIvy"
APP_VERSION = "1.3.0"
ORGANIZATION_NAME = "GlazIvy"

DEFAULT_CAMERA_NAME = "Входная"
DEFAULT_NEW_CAMERA_NAME = "Новая камера"
DEFAULT_PORT = 554
DEFAULT_TRANSPORT = "tcp"
DEFAULT_QUALITY = "sd"
DEFAULT_STREAM_PATH = (
    "/user={user}&password={password}&channel=0&stream={stream}.sdp?real_stream"
)

QUALITY_TO_STREAM = {"sd": 1, "hd": 0}

# HD у этой камеры может отдавать первый кадр почти минуту. Таймаут открытия
# намеренно заметно больше read timeout: это два разных этапа соединения.
SD_OPEN_TIMEOUT_MS = 12_000
HD_OPEN_TIMEOUT_MS = 65_000
READ_TIMEOUT_MS = 7_000
SD_STARTUP_GRACE_SECONDS = 15.0
HD_STARTUP_GRACE_SECONDS = 65.0

RECONNECT_INITIAL_SECONDS = 1.0
RECONNECT_MAX_SECONDS = 6.0
