"""Создаёт многоразмерную Windows-иконку из утверждённого PNG-источника."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parent.parent
ICON_SIZES = (16, 20, 24, 32, 48, 64, 128, 256)


def make_icon(source_path: Path) -> Image.Image:
    """Нормализует источник в прозрачный квадрат 256×256 без обрезки."""

    with Image.open(source_path) as source:
        source_rgba = source.convert("RGBA")
    fitted = ImageOps.contain(
        source_rgba,
        (ICON_SIZES[-1], ICON_SIZES[-1]),
        method=Image.Resampling.LANCZOS,
    )
    canvas = Image.new("RGBA", (ICON_SIZES[-1], ICON_SIZES[-1]), (0, 0, 0, 0))
    canvas.alpha_composite(
        fitted,
        (
            (canvas.width - fitted.width) // 2,
            (canvas.height - fitted.height) // 2,
        ),
    )
    return canvas


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT / "assets" / "app_icon_source.png",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "assets" / "app_icon.ico",
    )
    args = parser.parse_args()
    if not args.source.is_file():
        parser.error(f"PNG-источник не найден: {args.source}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    image = make_icon(args.source)
    image.save(
        args.output,
        format="ICO",
        sizes=[(size, size) for size in ICON_SIZES],
        bitmap_format="png",
    )
    print(f"Создана иконка: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
