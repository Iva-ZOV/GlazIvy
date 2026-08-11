"""Загрузка, проверка и безопасная запись конфигурации доски камер."""

from __future__ import annotations

import base64
import binascii
import json
import os
import shutil
import string
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from .constants import (
    APP_SLUG,
    DEFAULT_NEW_CAMERA_NAME,
    DEFAULT_PORT,
    DEFAULT_QUALITY,
    DEFAULT_STREAM_PATH,
    DEFAULT_TRANSPORT,
    QUALITY_TO_STREAM,
)

CONFIG_VERSION = 2


class ConfigError(ValueError):
    """Ошибка чтения или проверки конфигурации."""


@dataclass(frozen=True, slots=True)
class CameraGeometry:
    """Абсолютная геометрия плитки внутри видимой доски."""

    x: int = 24
    y: int = 24
    width: int = 560
    height: int = 360
    z: int = 0

    def updated(self, **changes: Any) -> "CameraGeometry":
        return replace(self, **changes)

    def validate(self) -> None:
        values = (self.x, self.y, self.width, self.height, self.z)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise ConfigError("Геометрия камеры должна содержать целые числа.")
        if self.x < 0 or self.y < 0:
            raise ConfigError("Координаты камеры не могут быть отрицательными.")
        if self.width < 1 or self.height < 1:
            raise ConfigError("Размер камеры должен быть положительным.")
        if self.z < 0:
            raise ConfigError("Z-порядок камеры не может быть отрицательным.")


@dataclass(frozen=True, slots=True)
class CameraConfig:
    """Настройки одной камеры и её положение на доске."""

    camera_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    camera_name: str = DEFAULT_NEW_CAMERA_NAME
    host: str = ""
    port: int = DEFAULT_PORT
    username: str = ""
    password: str = ""
    transport: str = DEFAULT_TRANSPORT
    quality: str = DEFAULT_QUALITY
    stream_path: str = DEFAULT_STREAM_PATH
    show_clock: bool = True
    geometry: CameraGeometry = field(default_factory=CameraGeometry)
    source: str = "template"
    stream_url_hd: str = ""
    stream_url_sd: str = ""
    onvif_endpoint: str = ""
    onvif_media_endpoint: str = ""
    onvif_username: str = ""
    onvif_password: str = ""

    def updated(self, **changes: Any) -> "CameraConfig":
        return replace(self, **changes)

    def is_configured(self) -> bool:
        """Полностью пустые реквизиты означают намеренно ненастроенную плитку."""

        if self.source == "onvif":
            return bool(self._onvif_stream_url(self.quality))
        return bool(self.host.strip() or self.username or self.password)

    @staticmethod
    def _validate_host(host: str) -> None:
        if not host:
            raise ConfigError("Укажите IP-адрес или имя камеры.")
        if "://" in host or "/" in host or any(char.isspace() for char in host):
            raise ConfigError("В поле адреса нужен только IP или имя без rtsp:// и пути.")

    @staticmethod
    def _validate_endpoint(endpoint: str, field_name: str) -> None:
        try:
            parsed = urlsplit(endpoint)
        except ValueError as exc:
            raise ConfigError(f"{field_name} камеры имеет неверный формат.") from exc
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise ConfigError(f"{field_name} камеры имеет неверный формат.")

    @staticmethod
    def _validate_stream_url(url: str) -> None:
        try:
            parsed = urlsplit(url)
        except ValueError as exc:
            raise ConfigError("Камера вернула некорректный RTSP-URL.") from exc
        if parsed.scheme.lower() not in {"rtsp", "rtsps"} or not parsed.hostname:
            raise ConfigError("Камера вернула некорректный RTSP-URL.")

    def _onvif_stream_url(self, quality: str) -> str:
        preferred = self.stream_url_hd if quality == "hd" else self.stream_url_sd
        fallback = self.stream_url_sd if quality == "hd" else self.stream_url_hd
        return preferred.strip() or fallback.strip()

    def validate(self, *, require_connection: bool = False) -> None:
        if not self.camera_id.strip():
            raise ConfigError("У камеры отсутствует внутренний идентификатор.")
        if not self.camera_name.strip():
            raise ConfigError("Укажите имя камеры.")
        if self.transport not in {"tcp", "udp"}:
            raise ConfigError("Транспорт должен быть TCP или UDP.")
        if self.quality not in QUALITY_TO_STREAM:
            raise ConfigError("Качество должно быть SD или HD.")
        if self.source not in {"template", "onvif"}:
            raise ConfigError("Неизвестный источник потока камеры.")
        if isinstance(self.port, bool) or not isinstance(self.port, int):
            raise ConfigError("Порт должен быть целым числом.")
        if not 1 <= self.port <= 65535:
            raise ConfigError("Порт должен быть в диапазоне от 1 до 65535.")
        if not isinstance(self.show_clock, bool):
            raise ConfigError("Параметр часов камеры должен быть логическим.")
        self.geometry.validate()

        if self.source == "onvif":
            host = self.host.strip()
            self._validate_host(host)
            endpoint = self.onvif_endpoint.strip()
            if not endpoint:
                raise ConfigError("У камеры отсутствует ONVIF endpoint.")
            self._validate_endpoint(endpoint, "ONVIF endpoint")
            media_endpoint = self.onvif_media_endpoint.strip()
            if media_endpoint:
                self._validate_endpoint(media_endpoint, "ONVIF media endpoint")
            for stream_url in (self.stream_url_hd.strip(), self.stream_url_sd.strip()):
                if stream_url:
                    self._validate_stream_url(stream_url)
            if require_connection and not self._onvif_stream_url(self.quality):
                raise ConfigError(
                    "Не удалось получить RTSP-поток. Укажите логин и пароль ONVIF."
                )
            return

        stream_path = self.stream_path.strip()
        if not stream_path:
            raise ConfigError("Укажите путь RTSP-потока.")
        if not stream_path.startswith("/"):
            raise ConfigError("Путь RTSP должен начинаться с символа /.")

        allowed_fields = {"user", "password", "stream"}
        try:
            fields = {
                field_name
                for _, field_name, _, _ in string.Formatter().parse(stream_path)
                if field_name
            }
        except ValueError as exc:
            raise ConfigError("В шаблоне пути есть незакрытая фигурная скобка.") from exc
        unknown_fields = fields - allowed_fields
        if unknown_fields:
            names = ", ".join(sorted(unknown_fields))
            raise ConfigError(f"Неизвестные подстановки в пути: {names}.")
        if "stream" not in fields:
            raise ConfigError("Добавьте {stream} в путь, чтобы работало переключение SD/HD.")

        if not require_connection and not self.is_configured():
            return

        host = self.host.strip()
        self._validate_host(host)
        if not self.username:
            raise ConfigError("Укажите логин RTSP.")
        if not self.password:
            raise ConfigError("Укажите пароль RTSP.")

    def build_rtsp_url(self, quality: str | None = None) -> str:
        """Собирает URL только в памяти; он никогда не пишется в журнал."""

        self.validate(require_connection=True)
        selected_quality = quality or self.quality
        if selected_quality not in QUALITY_TO_STREAM:
            raise ConfigError("Неизвестное качество потока.")

        if self.source == "onvif":
            return self._onvif_stream_url(selected_quality)

        encoded_user = quote(self.username, safe="")
        encoded_password = quote(self.password, safe="")
        path = self.stream_path.strip().format(
            user=encoded_user,
            password=encoded_password,
            stream=QUALITY_TO_STREAM[selected_quality],
        )

        host = self.host.strip()
        # Квадратные скобки обязательны для IPv6 в URL.
        if ":" in host and not (host.startswith("[") and host.endswith("]")):
            host = f"[{host}]"
        return f"rtsp://{encoded_user}:{encoded_password}@{host}:{int(self.port)}{path}"


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Настройки приложения и упорядоченный список камер доски."""

    cameras: tuple[CameraConfig, ...] = field(default_factory=tuple)
    autostart: bool = False

    def updated(self, **changes: Any) -> "AppConfig":
        return replace(self, **changes)

    def validate(self) -> None:
        if not isinstance(self.autostart, bool):
            raise ConfigError("Параметр автозапуска должен быть логическим.")
        seen_ids: set[str] = set()
        for camera in self.cameras:
            if not isinstance(camera, CameraConfig):
                raise ConfigError("Список камер содержит значение неверного типа.")
            camera.validate()
            if camera.camera_id in seen_ids:
                raise ConfigError("В config.json повторяется идентификатор камеры.")
            seen_ids.add(camera.camera_id)


def _encode_password(password: str) -> str:
    return base64.b64encode(password.encode("utf-8")).decode("ascii")


def _decode_password(value: str) -> str:
    try:
        return base64.b64decode(value.encode("ascii"), validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise ConfigError("Поле password_b64 в конфигурации повреждено.") from exc


def _decode_secret(payload: dict[str, Any], encoded_key: str, plain_key: str) -> str:
    """Читает новые URL из base64 и допускает ранний plaintext-вариант поля."""

    if encoded_key in payload:
        value = payload.get(encoded_key, "")
        if not isinstance(value, str):
            raise ConfigError(f"Поле {encoded_key} должно быть строкой.")
        try:
            return base64.b64decode(value.encode("ascii"), validate=True).decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
            raise ConfigError(f"Поле {encoded_key} в конфигурации повреждено.") from exc
    value = payload.get(plain_key, "")
    if not isinstance(value, str):
        raise ConfigError(f"Поле {plain_key} должно быть строкой.")
    return value


def _read_bool(payload: dict[str, Any], key: str, default: bool) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"Поле {key} должно быть логическим.")
    return value


def _read_int(payload: dict[str, Any], key: str, default: int) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool):
        raise ConfigError(f"Поле {key} должно быть целым числом.")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"Поле {key} должно быть целым числом.") from exc


class ConfigStore:
    """Хранилище `%APPDATA%\\Vhodnaya\\config.json`."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or self.default_path()

    @staticmethod
    def default_path() -> Path:
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / APP_SLUG / "config.json"
        # Fallback полезен для запуска исходников вне Windows.
        return Path.home() / "AppData" / "Roaming" / APP_SLUG / "config.json"

    def exists(self) -> bool:
        return self.path.is_file()

    def _load_camera(self, payload: object, index: int) -> CameraConfig:
        if not isinstance(payload, dict):
            raise ConfigError(f"Камера №{index + 1} должна быть объектом.")
        geometry_payload = payload.get("geometry", {})
        if not isinstance(geometry_payload, dict):
            raise ConfigError(f"Геометрия камеры №{index + 1} должна быть объектом.")

        camera = CameraConfig(
            camera_id=str(payload.get("id") or uuid.uuid4().hex),
            camera_name=str(payload.get("camera_name", DEFAULT_NEW_CAMERA_NAME)),
            host=str(payload.get("host", "")),
            port=_read_int(payload, "port", DEFAULT_PORT),
            username=str(payload.get("username", "")),
            password=_decode_password(str(payload.get("password_b64", ""))),
            transport=str(payload.get("transport", DEFAULT_TRANSPORT)).lower(),
            quality=str(payload.get("quality", DEFAULT_QUALITY)).lower(),
            stream_path=str(payload.get("stream_path", DEFAULT_STREAM_PATH)),
            source=str(payload.get("source", "template")).lower(),
            stream_url_hd=_decode_secret(
                payload,
                "stream_url_hd_b64",
                "stream_url_hd",
            ),
            stream_url_sd=_decode_secret(
                payload,
                "stream_url_sd_b64",
                "stream_url_sd",
            ),
            onvif_endpoint=str(payload.get("onvif_endpoint", "")),
            onvif_media_endpoint=str(payload.get("onvif_media_endpoint", "")),
            onvif_username=str(payload.get("onvif_username", "")),
            onvif_password=_decode_secret(
                payload,
                "onvif_password_b64",
                "onvif_password",
            ),
            show_clock=_read_bool(payload, "show_clock", True),
            geometry=CameraGeometry(
                x=_read_int(geometry_payload, "x", 24),
                y=_read_int(geometry_payload, "y", 24),
                width=_read_int(geometry_payload, "width", 560),
                height=_read_int(geometry_payload, "height", 360),
                z=_read_int(geometry_payload, "z", index),
            ),
        )
        try:
            camera.validate()
        except ConfigError as exc:
            raise ConfigError(f"Камера №{index + 1}: {exc}") from exc
        return camera

    def _backup_legacy_and_reset(self, *, autostart: bool = False) -> AppConfig:
        """Сохраняет одиночный конфиг без переноса секретов на новую доску."""

        backup_path = self.path.with_name(f"{self.path.name}.bak")
        try:
            shutil.copy2(self.path, backup_path)
        except OSError as exc:
            raise ConfigError("Не удалось создать резервную копию config.json.bak.") from exc
        empty = AppConfig(autostart=autostart)
        self.save(empty)
        return empty

    def load(self) -> AppConfig:
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ConfigError("Не удалось прочитать config.json.") from exc
        if not isinstance(payload, dict):
            raise ConfigError("Корень config.json должен быть объектом.")

        version = payload.get("config_version")
        if version != CONFIG_VERSION:
            legacy_fields = {"camera_name", "host", "username", "password_b64"}
            if version in (None, 1) and (
                not payload or bool(legacy_fields.intersection(payload))
            ):
                legacy_autostart = payload.get("autostart", False)
                return self._backup_legacy_and_reset(
                    autostart=(
                        legacy_autostart
                        if isinstance(legacy_autostart, bool)
                        else False
                    )
                )
            raise ConfigError("Версия config.json не поддерживается.")

        camera_payloads = payload.get("cameras")
        if not isinstance(camera_payloads, list):
            raise ConfigError("Поле cameras должно быть списком.")
        cameras = tuple(
            self._load_camera(camera_payload, index)
            for index, camera_payload in enumerate(camera_payloads)
        )
        config = AppConfig(
            cameras=cameras,
            autostart=_read_bool(payload, "autostart", False),
        )
        config.validate()
        return config

    @staticmethod
    def _camera_payload(camera: CameraConfig) -> dict[str, Any]:
        geometry = camera.geometry
        return {
            "id": camera.camera_id,
            "camera_name": camera.camera_name,
            "host": camera.host,
            "port": camera.port,
            "username": camera.username,
            "password_b64": _encode_password(camera.password),
            "transport": camera.transport,
            "quality": camera.quality,
            "stream_path": camera.stream_path,
            "source": camera.source,
            "stream_url_hd_b64": _encode_password(camera.stream_url_hd),
            "stream_url_sd_b64": _encode_password(camera.stream_url_sd),
            "onvif_endpoint": camera.onvif_endpoint,
            "onvif_media_endpoint": camera.onvif_media_endpoint,
            "onvif_username": camera.onvif_username,
            "onvif_password_b64": _encode_password(camera.onvif_password),
            "show_clock": camera.show_clock,
            "geometry": {
                "x": geometry.x,
                "y": geometry.y,
                "width": geometry.width,
                "height": geometry.height,
                "z": geometry.z,
            },
        }

    def save(self, config: AppConfig) -> None:
        config.validate()
        data = {
            "config_version": CONFIG_VERSION,
            "autostart": config.autostart,
            "cameras": [self._camera_payload(camera) for camera in config.cameras],
        }

        temporary_path = self.path.with_suffix(".json.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary_path, self.path)
        except OSError as exc:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise ConfigError("Не удалось сохранить настройки в AppData.") from exc
