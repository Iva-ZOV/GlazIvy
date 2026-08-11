"""Цвета и глобальная таблица стилей."""

from __future__ import annotations

BACKGROUND = "#080B10"
SURFACE = "#11151C"
SURFACE_RAISED = "#171C25"
SURFACE_SOFT = "#1C222D"
TEXT = "#F4F7FB"
TEXT_MUTED = "#919AAA"
ACCENT = "#54D6C3"
ACCENT_BLUE = "#5BA9FF"
SUCCESS = "#55D697"
WARNING = "#F0B45A"
DANGER = "#FF6B75"


def stylesheet(font_family: str) -> str:
    return f"""
    * {{
        font-family: \"{font_family}\", \"Inter\", \"Segoe UI Variable Text\", \"Segoe UI\";
        color: {TEXT};
        outline: none;
    }}

    QWidget {{
        font-size: 14px;
    }}

    QFrame#windowSurface,
    QFrame#dialogSurface {{
        background-color: {SURFACE};
        border: 1px solid rgba(255, 255, 255, 18);
        border-radius: 22px;
    }}

    QFrame#windowSurface[flat="true"] {{
        border-radius: 0px;
        border: none;
    }}

    QWidget#titleBar {{
        background: transparent;
        border: none;
    }}

    QLabel#appTitle {{
        color: {TEXT};
        font-size: 14px;
        font-weight: 600;
    }}

    QLabel#clockLabel {{
        color: {TEXT_MUTED};
        font-size: 12px;
        font-weight: 500;
    }}

    QLabel#boardSubtitle,
    QLabel#cameraCount {{
        color: {TEXT_MUTED};
        font-size: 12px;
        font-weight: 500;
    }}

    QLabel#cameraTileName {{
        color: {TEXT};
        font-size: 13px;
        font-weight: 650;
    }}

    QLabel#tileClock {{
        color: rgba(244, 247, 251, 225);
        background-color: rgba(5, 8, 13, 155);
        border: 1px solid rgba(255, 255, 255, 24);
        border-radius: 9px;
        font-size: 11px;
        font-weight: 600;
    }}

    QLabel#dialogEyebrow {{
        color: {ACCENT};
        font-size: 11px;
        font-weight: 700;
    }}

    QLabel#dialogTitle {{
        color: {TEXT};
        font-size: 24px;
        font-weight: 650;
    }}

    QLabel#dialogSubtitle,
    QLabel#helperText {{
        color: {TEXT_MUTED};
        font-size: 13px;
    }}

    QLabel#errorText {{
        color: #FF8D96;
        background-color: rgba(255, 107, 117, 18);
        border: 1px solid rgba(255, 107, 117, 52);
        border-radius: 10px;
        padding: 9px 11px;
    }}

    QLabel#sectionTitle {{
        color: {TEXT};
        font-size: 15px;
        font-weight: 650;
        padding-top: 5px;
    }}

    QLineEdit,
    QSpinBox {{
        min-height: 40px;
        padding: 0 12px;
        color: {TEXT};
        background-color: {SURFACE_RAISED};
        border: 1px solid rgba(255, 255, 255, 20);
        border-radius: 11px;
        selection-background-color: rgba(84, 214, 195, 100);
    }}

    QLineEdit:hover,
    QSpinBox:hover {{
        border-color: rgba(255, 255, 255, 38);
    }}

    QLineEdit:focus,
    QSpinBox:focus {{
        background-color: #1A202A;
        border: 1px solid rgba(84, 214, 195, 145);
    }}

    QLineEdit[invalid="true"],
    QSpinBox[invalid="true"] {{
        border: 1px solid rgba(255, 107, 117, 180);
    }}

    QSpinBox::up-button,
    QSpinBox::down-button {{
        width: 18px;
        border: none;
        background: transparent;
    }}

    QLabel#fieldLabel {{
        color: #C6CDD8;
        font-size: 12px;
        font-weight: 550;
    }}

    QLabel#discoveryAddress {{
        color: {TEXT_MUTED};
        font-size: 13px;
        font-weight: 600;
    }}

    QFrame#discoveryRow {{
        background-color: {SURFACE_RAISED};
        border: 1px solid rgba(255, 255, 255, 18);
        border-radius: 12px;
    }}

    QCheckBox {{
        color: #CDD3DC;
        spacing: 9px;
        min-height: 27px;
    }}

    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border-radius: 6px;
        border: 1px solid rgba(255, 255, 255, 42);
        background: {SURFACE_RAISED};
    }}

    QCheckBox::indicator:hover {{
        border-color: rgba(84, 214, 195, 135);
    }}

    QCheckBox::indicator:checked {{
        background-color: {ACCENT};
        border-color: {ACCENT};
        image: url(none);
    }}

    QPushButton#primaryButton {{
        min-height: 42px;
        padding: 0 20px;
        color: #07110F;
        background-color: {ACCENT};
        border: none;
        border-radius: 12px;
        font-weight: 700;
    }}

    QPushButton#addCameraButton {{
        min-height: 36px;
        padding: 0 16px;
        color: #07110F;
        background-color: {ACCENT};
        border: none;
        border-radius: 11px;
        font-size: 13px;
        font-weight: 750;
    }}

    QPushButton#addCameraButton:hover {{
        background-color: #68E2CF;
    }}

    QPushButton#addCameraButton:pressed {{
        background-color: #45C7B4;
    }}

    QPushButton#primaryButton:hover {{
        background-color: #68E2CF;
    }}

    QPushButton#primaryButton:pressed {{
        background-color: #45C7B4;
    }}

    QPushButton#primaryButton:disabled,
    QPushButton#secondaryButton:disabled {{
        color: {TEXT_MUTED};
        background-color: rgba(255, 255, 255, 6);
        border: 1px solid rgba(255, 255, 255, 12);
    }}

    QPushButton#secondaryButton {{
        min-height: 40px;
        padding: 0 17px;
        color: #D9DEE7;
        background-color: rgba(255, 255, 255, 8);
        border: 1px solid rgba(255, 255, 255, 22);
        border-radius: 11px;
        font-weight: 600;
    }}

    QPushButton#secondaryButton:hover {{
        color: {TEXT};
        background-color: rgba(255, 255, 255, 15);
        border-color: rgba(255, 255, 255, 38);
    }}

    QPushButton#dangerButton {{
        min-height: 40px;
        padding: 0 17px;
        color: #FF9AA2;
        background-color: rgba(255, 107, 117, 10);
        border: 1px solid rgba(255, 107, 117, 48);
        border-radius: 11px;
        font-weight: 650;
    }}

    QPushButton#dangerButton:hover {{
        color: #FFD3D6;
        background-color: rgba(255, 107, 117, 22);
        border-color: rgba(255, 107, 117, 90);
    }}

    QPushButton#linkButton {{
        min-height: 32px;
        padding: 0 4px;
        color: {ACCENT};
        background: transparent;
        border: none;
        font-weight: 600;
        text-align: left;
    }}

    QPushButton#linkButton:hover {{
        color: #78E8D7;
    }}

    QScrollArea {{
        border: none;
        background: transparent;
    }}

    QScrollArea > QWidget > QWidget {{
        background: transparent;
    }}

    QScrollBar:vertical {{
        width: 8px;
        background: transparent;
        margin: 3px 0;
    }}

    QScrollBar::handle:vertical {{
        min-height: 32px;
        background: rgba(255, 255, 255, 34);
        border-radius: 4px;
    }}

    QScrollBar::handle:vertical:hover {{
        background: rgba(255, 255, 255, 55);
    }}

    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical,
    QScrollBar::add-page:vertical,
    QScrollBar::sub-page:vertical {{
        height: 0;
        background: transparent;
    }}

    QProgressBar#discoveryProgress {{
        min-height: 10px;
        max-height: 10px;
        background-color: {SURFACE_RAISED};
        border: 1px solid rgba(255, 255, 255, 20);
        border-radius: 5px;
    }}

    QProgressBar#discoveryProgress::chunk {{
        background-color: {ACCENT};
        border-radius: 4px;
    }}

    QToolTip {{
        color: {TEXT};
        background-color: #232A35;
        border: 1px solid rgba(255, 255, 255, 30);
        border-radius: 7px;
        padding: 6px 8px;
    }}
    """
