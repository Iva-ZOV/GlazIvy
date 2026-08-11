"""Растеризует мастер-SVG логотипа через QtSvg в прозрачный PNG."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRectF, QSize
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


ROOT = Path(__file__).resolve().parent.parent


def render_svg(source: Path, output: Path, size: int = 512) -> None:
    renderer = QSvgRenderer(str(source))
    if not renderer.isValid():
        raise RuntimeError(f"Некорректный SVG: {source}")

    image = QImage(QSize(size, size), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()

    output.parent.mkdir(parents=True, exist_ok=True)
    if not image.save(str(output), "PNG"):
        raise RuntimeError(f"Не удалось сохранить PNG: {output}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=ROOT / "assets" / "app_icon.svg",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "assets" / "app_icon_source.png",
    )
    parser.add_argument("--size", type=int, default=512)
    args = parser.parse_args()
    if args.size < 1:
        parser.error("Размер должен быть положительным")
    if not args.source.is_file():
        parser.error(f"SVG-источник не найден: {args.source}")
    render_svg(args.source, args.output, args.size)
    print(f"Создан PNG: {args.output} ({args.size}x{args.size})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
