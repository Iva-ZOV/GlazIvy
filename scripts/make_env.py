"""Собирает локальный .env с параметрами камер из рабочего конфига приложения.

Личные данные (адреса, учётки, готовые RTSP-URL) держим в ОДНОМ файле `.env`
в корне проекта: он в .gitignore и никогда не попадает в репозиторий. Брифы и
скрипты ссылаются на имена переменных, а не на значения.

Запуск:  .\\.venv\\Scripts\\python.exe scripts\\make_env.py
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"


def config_path() -> Path:
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "GlazIvy" / "config.json"


def decode(value: str) -> str:
    if not value:
        return ""
    try:
        return base64.b64decode(value.encode("ascii"), validate=True).decode("utf-8")
    except Exception:
        return ""


def slug(name: str, index: int) -> str:
    letters = []
    for char in name:
        if char.isascii() and (char.isalnum() or char == "_"):
            letters.append(char.upper())
        elif char.isspace() or char == "-":
            letters.append("_")
    return "".join(letters).strip("_") or f"CAM{index + 1}"


def build_lines(cameras: list[dict]) -> list[str]:
    counts: dict[str, int] = {}
    for index, camera in enumerate(cameras):
        counts[slug(camera.get("camera_name", ""), index)] = (
            counts.get(slug(camera.get("camera_name", ""), index), 0) + 1
        )
    # Одинаковые модели дают одинаковый слаг — повторы нумеруем, иначе
    # переменные молча затирают друг друга.
    duplicated = {name for name, count in counts.items() if count > 1}
    used: dict[str, int] = {}

    lines = [
        "# Личные параметры камер для локальных прогонов и тестов.",
        "# Файл в .gitignore и НИКОГДА не коммитится.",
        "# Пересобрать: .\\.venv\\Scripts\\python.exe scripts\\make_env.py",
        "",
    ]
    for index, camera in enumerate(cameras):
        host = camera.get("host", "")
        if not host:
            continue
        key = slug(camera.get("camera_name", ""), index)
        if key in duplicated:
            used[key] = used.get(key, 0) + 1
            key = f"{key}_{used[key]}"

        lines.append(f"# {camera.get('camera_name', '')} ({camera.get('source', '')})")
        lines.append(f"GLAZIVY_{key}_HOST={host}")
        lines.append(f"GLAZIVY_{key}_PORT={camera.get('port', 554)}")
        for env_suffix, value in (
            ("USER", camera.get("username", "")),
            ("PASSWORD", decode(camera.get("password_b64", ""))),
            ("ONVIF", camera.get("onvif_endpoint", "")),
            ("ONVIF_USER", camera.get("onvif_username", "")),
            ("ONVIF_PASSWORD", decode(camera.get("onvif_password_b64", ""))),
            ("RTSP_SD", decode(camera.get("stream_url_sd_b64", ""))),
            ("RTSP_HD", decode(camera.get("stream_url_hd_b64", ""))),
        ):
            if value:
                lines.append(f"GLAZIVY_{key}_{env_suffix}={value}")
        lines.append("")
    return lines


def main() -> int:
    source = config_path()
    if not source.is_file():
        print(f"Не найден конфиг приложения: {source}")
        return 1
    payload = json.loads(source.read_text(encoding="utf-8"))
    lines = build_lines(payload.get("cameras", []))
    ENV_PATH.write_text("\n".join(lines), encoding="utf-8")
    written = sum(1 for line in lines if line and not line.startswith("#"))
    print(f"{ENV_PATH.name}: {written} переменных (значения не печатаем)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
