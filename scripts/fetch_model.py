"""Скачивает и проверяет официальный ONNX-артефакт YOLOX-tiny."""

from __future__ import annotations

import hashlib
import os
import sys
import urllib.request
from pathlib import Path


# YOLOX распространяется по Apache-2.0:
# https://github.com/Megvii-BaseDetection/YOLOX/blob/main/LICENSE
MODEL_VERSION = "0.1.1rc0"
MODEL_FILENAME = "yolox_tiny.onnx"
MODEL_URL = (
    "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/"
    f"{MODEL_VERSION}/{MODEL_FILENAME}"
)
MODEL_SHA256 = "427cc366d34e27ff7a03e2899b5e3671425c262ea2291f88bb942bc1cc70b0f7"
ROOT = Path(__file__).resolve().parent.parent
DESTINATION = ROOT / "assets" / "models" / MODEL_FILENAME


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_model() -> Path:
    """Загружает модель атомарно и не принимает файл с неверным хэшем."""

    print(f"Источник: {MODEL_URL}")
    print(f"Версия: {MODEL_VERSION}")
    print(f"Назначение: {DESTINATION}")

    if DESTINATION.is_file():
        actual = sha256(DESTINATION)
        if actual == MODEL_SHA256:
            print(f"SHA-256: {actual} (совпадает, загрузка не требуется)")
            return DESTINATION
        print(f"Существующий файл повреждён: SHA-256 {actual}")

    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    temporary = DESTINATION.with_suffix(DESTINATION.suffix + ".part")
    try:
        temporary.unlink(missing_ok=True)
        request = urllib.request.Request(
            MODEL_URL,
            headers={"User-Agent": "GlazIvy-model-fetcher/1"},
        )
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open(
            "wb"
        ) as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)

        actual = sha256(temporary)
        if actual != MODEL_SHA256:
            raise RuntimeError(
                "SHA-256 скачанной модели не совпал: "
                f"ожидался {MODEL_SHA256}, получен {actual}"
            )
        os.replace(temporary, DESTINATION)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    print(f"SHA-256: {MODEL_SHA256} (проверен)")
    return DESTINATION


def main() -> int:
    try:
        fetch_model()
    except Exception as exc:
        print(f"Не удалось получить модель: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
