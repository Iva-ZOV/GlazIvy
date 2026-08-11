"""Детерминированно создаёт многоразмерную Windows-иконку приложения."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw


CANVAS = 1024


def _mix(start: tuple[int, int, int], end: tuple[int, int, int], amount: float) -> tuple[int, int, int, int]:
    return (
        round(start[0] + (end[0] - start[0]) * amount),
        round(start[1] + (end[1] - start[1]) * amount),
        round(start[2] + (end[2] - start[2]) * amount),
        255,
    )


def make_icon() -> Image.Image:
    image = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    gradient = Image.new("RGBA", image.size)
    gradient_draw = ImageDraw.Draw(gradient)
    for y in range(CANVAS):
        amount = y / (CANVAS - 1)
        gradient_draw.line(
            (0, y, CANVAS, y),
            fill=_mix((31, 41, 54), (7, 12, 18), amount),
        )

    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((28, 28, 996, 996), radius=232, fill=255)
    image.alpha_composite(Image.composite(gradient, Image.new("RGBA", image.size), mask))
    draw = ImageDraw.Draw(image, "RGBA")

    draw.rounded_rectangle(
        (28, 28, 996, 996),
        radius=232,
        outline=(255, 255, 255, 22),
        width=16,
    )
    draw.ellipse((186, 186, 838, 838), fill=(84, 214, 195, 8), outline=(84, 214, 195, 76), width=20)

    # Корпус камеры и выступ сверху.
    draw.rounded_rectangle(
        (242, 308, 782, 716),
        radius=128,
        fill=(10, 16, 24, 255),
        outline=(84, 214, 195, 235),
        width=28,
    )
    draw.polygon(((318, 308), (372, 236), (652, 236), (706, 308)), fill=(16, 25, 36, 255))
    draw.line(((318, 308), (372, 236), (652, 236), (706, 308)), fill=(91, 169, 255, 220), width=24, joint="curve")

    # Объектив с простым радиальным градиентом.
    lens = Image.new("RGBA", image.size, (0, 0, 0, 0))
    lens_draw = ImageDraw.Draw(lens, "RGBA")
    for radius in range(112, 0, -2):
        amount = 1.0 - radius / 112
        color = _mix((7, 18, 27), (70, 158, 190), amount * 0.72)
        lens_draw.ellipse(
            (512 - radius, 512 - radius, 512 + radius, 512 + radius),
            fill=color,
        )
    image.alpha_composite(lens)
    draw = ImageDraw.Draw(image, "RGBA")
    draw.ellipse((351, 351, 673, 673), outline=(145, 232, 221, 105), width=22)
    draw.ellipse((439, 437, 497, 495), fill=(232, 255, 255, 225))
    draw.ellipse((674, 368, 714, 408), fill=(85, 214, 151, 255))
    return image


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "assets" / "app_icon.ico",
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    image = make_icon()
    image.save(
        args.output,
        format="ICO",
        sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
        bitmap_format="png",
    )
    print(f"Создана иконка: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

