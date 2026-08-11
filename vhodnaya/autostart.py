"""Автозапуск для текущего пользователя через HKCU."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .constants import APP_SLUG

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


class AutostartError(RuntimeError):
    pass


def _command() -> str:
    if getattr(sys, "frozen", False):
        parts = [sys.executable]
    else:
        main_file = Path(__file__).resolve().parent.parent / "main.py"
        parts = [sys.executable, str(main_file)]
    return subprocess.list2cmdline(parts)


def set_autostart(enabled: bool) -> None:
    """Создаёт/удаляет запись без запроса прав администратора."""

    if os.name != "nt":
        return
    try:
        import winreg

        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            RUN_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            if enabled:
                winreg.SetValueEx(key, APP_SLUG, 0, winreg.REG_SZ, _command())
            else:
                try:
                    winreg.DeleteValue(key, APP_SLUG)
                except FileNotFoundError:
                    pass
    except OSError as exc:
        raise AutostartError("Windows не разрешила изменить автозапуск.") from exc

