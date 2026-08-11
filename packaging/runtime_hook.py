"""Переменные, которые должны быть заданы до импорта Qt/OpenCV в сборке."""

import os

os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")

