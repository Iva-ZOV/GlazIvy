"""Собирает Windows-иконку из набора PNG-источников Ивы.

Схема (решение Ивы, итерация 4 дизайна):
- 256/128 — качественный арт `app_icon_source.png` (он же ярлык);
- 64/48   — пиксельный спрайт `app_icon_64.png`;
- 32/24/20 — пиксельный спрайт `app_icon_32.png`;
- 16      — пиксельный спрайт `app_icon_16.png`.
Спрайты нарезаны из «супер-пиксельного трио» (оригиналы в GlazIvy-docs,
assets/moodboards/). Интерфейсный логотип — отдельный `app_logo_pixel.png`.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

# размер в ICO → (файл-источник, )
SIZE_SOURCES: dict[int, str] = {
    256: "app_icon_source.png",
    128: "app_icon_source.png",
    64: "app_icon_64.png",
    48: "app_icon_64.png",
    32: "app_icon_32.png",
    24: "app_icon_32.png",
    20: "app_icon_32.png",
    16: "app_icon_16.png",
}


def _fit(image: Image.Image, size: int) -> Image.Image:
    """Вписывает в прозрачный квадрат size×size без обрезки."""

    source = image.convert("RGBA")
    scale = size / max(source.size)
    fitted = source.resize(
        (max(1, round(source.width * scale)), max(1, round(source.height * scale))),
        # Уменьшение пиксель-арта — усредняющим BOX, чтобы не звенел;
        # качественный арт крупных размеров — LANCZOS.
        Image.Resampling.LANCZOS if size >= 128 else Image.Resampling.BOX,
    )
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(
        fitted,
        ((size - fitted.width) // 2, (size - fitted.height) // 2),
    )
    return canvas


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ASSETS / "app_icon.ico")
    args = parser.parse_args()

    frames: dict[int, Image.Image] = {}
    for size, name in SIZE_SOURCES.items():
        path = ASSETS / name
        if not path.is_file():
            parser.error(f"PNG-источник не найден: {path}")
        with Image.open(path) as source:
            frames[size] = (
                source.convert("RGBA")
                if source.size == (size, size)
                else _fit(source, size)
            )

    sizes = sorted(frames, reverse=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frames[sizes[0]].save(
        args.output,
        format="ICO",
        sizes=[(size, size) for size in sizes],
        append_images=[frames[size] for size in sizes[1:]],
        bitmap_format="png",
    )
    print(f"Создана иконка: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
