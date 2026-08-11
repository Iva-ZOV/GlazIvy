"""Канонная ретро-палитра и глобальная таблица стилей."""

from __future__ import annotations

BACKGROUND = "#11120F"
SURFACE = "#1B1C18"
SURFACE_RAISED = "#2A2C24"
SURFACE_SOFT = "#23241E"
TEXT = "#E6D6AE"
TEXT_MUTED = "#B89B6A"
BRONZE = "#B89B6A"
KHAKI = "#6E7A45"
PRIMARY = "#C0322B"
PRIMARY_HOVER = "#D4433B"
PRIMARY_PRESSED = "#8B1E1E"
SUCCESS = KHAKI
WARNING = "#D9A441"
DANGER = PRIMARY


def stylesheet(font_family: str, heading_font_family: str | None = None) -> str:
    heading = heading_font_family or font_family
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
        border: 1px solid rgba(184, 155, 106, 48);
        border-radius: 10px;
    }}

    QFrame#windowSurface[flat="true"] {{
        border-radius: 0px;
        border: none;
    }}

    QWidget#titleBar {{
        background: transparent;
        border: none;
    }}

    QWidget#cameraTileHeader {{
        background-color: rgba(17, 18, 15, 240);
        border: 1px solid rgba(184, 155, 106, 82);
        border-radius: 6px;
    }}

    QLabel#appTitle {{
        color: {TEXT};
        font-family: \"{heading}\", \"{font_family}\";
        font-size: 13px;
        font-weight: 700;
    }}

    QLabel#clockLabel {{
        color: {TEXT_MUTED};
        font-size: 12px;
        font-weight: 500;
    }}

    QLabel#cameraCount {{
        color: {TEXT_MUTED};
        font-size: 11px;
        font-weight: 500;
    }}

    QLabel#cameraTileName {{
        color: {TEXT};
        font-size: 13px;
        font-weight: 600;
    }}

    QLabel#dialogEyebrow {{
        color: {PRIMARY};
        font-family: \"{heading}\", \"{font_family}\";
        font-size: 11px;
        font-weight: 700;
    }}

    QLabel#dialogTitle {{
        color: {TEXT};
        font-family: \"{heading}\", \"{font_family}\";
        font-size: 22px;
        font-weight: 700;
    }}

    QLabel#dialogSubtitle,
    QLabel#helperText {{
        color: {TEXT_MUTED};
        font-size: 13px;
    }}

    QLabel#errorText {{
        color: #E7A29A;
        background-color: rgba(192, 50, 43, 24);
        border: 1px solid rgba(192, 50, 43, 92);
        border-radius: 6px;
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
        border: 1px solid rgba(184, 155, 106, 48);
        border-radius: 6px;
        selection-color: {TEXT};
        selection-background-color: rgba(110, 122, 69, 150);
    }}

    QLineEdit:hover,
    QSpinBox:hover {{
        border-color: rgba(184, 155, 106, 92);
    }}

    QLineEdit:focus,
    QSpinBox:focus {{
        background-color: #2E3027;
        border: 1px solid rgba(110, 122, 69, 220);
    }}

    QLineEdit[invalid="true"],
    QSpinBox[invalid="true"] {{
        border: 1px solid rgba(192, 50, 43, 225);
    }}

    QLineEdit:disabled,
    QSpinBox:disabled {{
        color: rgba(184, 155, 106, 115);
        background-color: rgba(42, 44, 36, 130);
        border-color: rgba(184, 155, 106, 24);
    }}

    QSpinBox::up-button,
    QSpinBox::down-button {{
        width: 18px;
        border: none;
        background: transparent;
    }}

    QLabel#fieldLabel {{
        color: #CFBB8C;
        font-size: 12px;
        font-weight: 600;
    }}

    QLabel#discoveryAddress {{
        color: {TEXT_MUTED};
        font-size: 13px;
        font-weight: 600;
    }}

    QFrame#discoveryRow {{
        background-color: {SURFACE_RAISED};
        border: 1px solid rgba(184, 155, 106, 42);
        border-radius: 6px;
    }}

    QCheckBox {{
        color: #D7C69E;
        spacing: 9px;
        min-height: 27px;
    }}

    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border-radius: 6px;
        border: 1px solid rgba(184, 155, 106, 88);
        background: {SURFACE_RAISED};
    }}

    QCheckBox::indicator:hover {{
        border-color: rgba(110, 122, 69, 220);
    }}

    QCheckBox::indicator:checked {{
        background-color: {KHAKI};
        border-color: #89965C;
        image: url(none);
    }}

    QCheckBox::indicator:disabled {{
        background-color: rgba(42, 44, 36, 110);
        border-color: rgba(184, 155, 106, 30);
    }}

    QPushButton#primaryButton,
    QPushButton#addCameraButton {{
        min-height: 42px;
        padding: 0 20px;
        color: {TEXT};
        background-color: {PRIMARY};
        border: 1px solid rgba(230, 214, 174, 22);
        border-radius: 6px;
        font-weight: 700;
    }}

    QPushButton#addCameraButton {{
        min-height: 36px;
        max-height: 36px;
        padding: 0 13px;
        font-size: 13px;
    }}

    QPushButton#findCameraButton {{
        min-height: 36px;
        max-height: 36px;
        padding: 0 13px;
        color: {BRONZE};
        background-color: transparent;
        border: 1px solid rgba(184, 155, 106, 145);
        border-radius: 6px;
        font-size: 13px;
        font-weight: 600;
    }}

    QPushButton#addCameraButton[compact="true"],
    QPushButton#findCameraButton[compact="true"] {{
        padding: 0 8px;
        font-size: 11px;
    }}

    QPushButton#primaryButton:hover,
    QPushButton#addCameraButton:hover {{
        background-color: {PRIMARY_HOVER};
        border-color: rgba(230, 214, 174, 50);
    }}

    QPushButton#findCameraButton:hover {{
        color: {TEXT};
        background-color: rgba(184, 155, 106, 18);
        border-color: rgba(230, 214, 174, 175);
    }}

    QPushButton#primaryButton:pressed,
    QPushButton#addCameraButton:pressed {{
        background-color: {PRIMARY_PRESSED};
        border-color: rgba(139, 30, 30, 230);
    }}

    QPushButton#findCameraButton:pressed {{
        color: {TEXT};
        background-color: rgba(184, 155, 106, 34);
        border-color: {BRONZE};
    }}

    QPushButton#primaryButton:disabled,
    QPushButton#addCameraButton:disabled {{
        color: rgba(184, 155, 106, 105);
        background-color: rgba(139, 30, 30, 58);
        border: 1px solid rgba(184, 155, 106, 18);
    }}

    QPushButton#findCameraButton:disabled {{
        color: rgba(184, 155, 106, 72);
        background-color: transparent;
        border: 1px solid rgba(184, 155, 106, 36);
    }}

    QPushButton#secondaryButton {{
        min-height: 40px;
        padding: 0 17px;
        color: {BRONZE};
        background-color: transparent;
        border: 1px solid rgba(184, 155, 106, 145);
        border-radius: 6px;
        font-weight: 600;
    }}

    QPushButton#secondaryButton:hover {{
        color: {TEXT};
        background-color: rgba(184, 155, 106, 18);
        border-color: rgba(230, 214, 174, 175);
    }}

    QPushButton#secondaryButton:pressed {{
        color: {TEXT};
        background-color: rgba(184, 155, 106, 34);
        border-color: {BRONZE};
    }}

    QPushButton#secondaryButton:disabled {{
        color: rgba(184, 155, 106, 72);
        background-color: transparent;
        border: 1px solid rgba(184, 155, 106, 36);
    }}

    QPushButton#dangerButton {{
        min-height: 40px;
        padding: 0 17px;
        color: #DD746D;
        background-color: transparent;
        border: 1px solid rgba(192, 50, 43, 180);
        border-radius: 6px;
        font-weight: 650;
    }}

    QPushButton#dangerButton:hover {{
        color: {TEXT};
        background-color: rgba(192, 50, 43, 42);
        border-color: {PRIMARY};
    }}

    QPushButton#dangerButton:pressed {{
        color: {TEXT};
        background-color: {PRIMARY_PRESSED};
        border-color: {PRIMARY_PRESSED};
    }}

    QPushButton#linkButton {{
        min-height: 32px;
        padding: 0 4px;
        color: {BRONZE};
        background: transparent;
        border: none;
        font-weight: 600;
        text-align: left;
    }}

    QPushButton#linkButton:hover {{
        color: {TEXT};
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
        background: rgba(184, 155, 106, 68);
        border-radius: 4px;
    }}

    QScrollBar::handle:vertical:hover {{
        background: rgba(184, 155, 106, 110);
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
        border: 1px solid rgba(184, 155, 106, 48);
        border-radius: 5px;
    }}

    QProgressBar#discoveryProgress::chunk {{
        background-color: {WARNING};
        border-radius: 4px;
    }}

    QToolTip {{
        color: {TEXT};
        background-color: {SURFACE_RAISED};
        border: 1px solid rgba(184, 155, 106, 90);
        border-radius: 6px;
        padding: 6px 8px;
    }}
    """
