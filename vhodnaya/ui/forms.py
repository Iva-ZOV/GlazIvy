"""Форма параметров камеры для диалога настроек плитки."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..config import CameraConfig, ConfigError
from .widgets import SegmentedControl


class FieldBlock(QWidget):
    def __init__(
        self,
        label: str,
        field: QWidget,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        caption = QLabel(label, self)
        caption.setObjectName("fieldLabel")
        layout.addWidget(caption)
        layout.addWidget(field)


class CameraForm(QWidget):
    """Редактирует CameraConfig, не сохраняя данные самостоятельно."""

    def __init__(self, config: CameraConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._base_config = config
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

        root = QVBoxLayout(self)
        root.setContentsMargins(2, 2, 10, 6)
        root.setSpacing(16)

        camera_section = QLabel("Камера", self)
        camera_section.setObjectName("sectionTitle")
        root.addWidget(camera_section)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(13)
        grid.setColumnStretch(0, 4)
        grid.setColumnStretch(1, 2)
        root.addLayout(grid)

        self.name_edit = QLineEdit(config.camera_name, self)
        self.name_edit.setPlaceholderText("Например, Входная дверь")
        grid.addWidget(FieldBlock("Имя камеры", self.name_edit, self), 0, 0, 1, 2)

        self.host_edit = QLineEdit(config.host, self)
        self.host_edit.setPlaceholderText("192.168.1.10")
        self.host_edit.setClearButtonEnabled(True)
        self.port_spin = QSpinBox(self)
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(config.port)
        grid.addWidget(FieldBlock("IP-адрес или имя", self.host_edit, self), 1, 0)
        grid.addWidget(FieldBlock("RTSP-порт", self.port_spin, self), 1, 1)

        self.username_edit = QLineEdit(config.username, self)
        self.username_edit.setPlaceholderText("Логин RTSP")
        self.username_edit.setClearButtonEnabled(True)
        self.password_edit = QLineEdit(config.password, self)
        self.password_edit.setPlaceholderText("Пароль RTSP")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        grid.addWidget(FieldBlock("Логин", self.username_edit, self), 2, 0)
        grid.addWidget(FieldBlock("Пароль", self.password_edit, self), 2, 1)

        self.show_password_check = QCheckBox("Показать пароль", self)
        self.show_password_check.toggled.connect(self._toggle_password)
        grid.addWidget(self.show_password_check, 3, 1)

        self.path_edit = QLineEdit(config.stream_path, self)
        self.path_edit.setPlaceholderText(
            "/user={user}&password={password}&channel=0&stream={stream}.sdp?real_stream"
        )
        self.path_edit.setClearButtonEnabled(True)
        grid.addWidget(FieldBlock("Шаблон пути потока", self.path_edit, self), 4, 0, 1, 2)

        path_help = QLabel(
            "Оставьте {user}, {password} и {stream} как подстановки. "
            "Для камеры Xiongmai подходит значение по умолчанию.",
            self,
        )
        path_help.setObjectName("helperText")
        path_help.setWordWrap(True)
        grid.addWidget(path_help, 5, 0, 1, 2)

        connection_section = QLabel("Подключение", self)
        connection_section.setObjectName("sectionTitle")
        root.addWidget(connection_section)

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(26)
        root.addLayout(controls)

        transport_box = QVBoxLayout()
        transport_box.setSpacing(7)
        transport_label = QLabel("Транспорт", self)
        transport_label.setObjectName("fieldLabel")
        self.transport_control = SegmentedControl(
            ("TCP", "UDP"),
            ("tcp", "udp"),
            config.transport,
            self,
        )
        transport_box.addWidget(transport_label)
        transport_box.addWidget(self.transport_control)
        controls.addLayout(transport_box)

        quality_box = QVBoxLayout()
        quality_box.setSpacing(7)
        quality_label = QLabel("Качество по умолчанию", self)
        quality_label.setObjectName("fieldLabel")
        self.quality_control = SegmentedControl(
            ("SD", "HD"),
            ("sd", "hd"),
            config.quality,
            self,
        )
        quality_box.addWidget(quality_label)
        quality_box.addWidget(self.quality_control)
        controls.addLayout(quality_box)
        controls.addStretch(1)

        timeout_help = QLabel(
            "HD может подключаться до 60 секунд. В это время приложение продолжит "
            "показывать последний кадр и не отметит запуск как обрыв.",
            self,
        )
        timeout_help.setObjectName("helperText")
        timeout_help.setWordWrap(True)
        root.addWidget(timeout_help)

        options_section = QLabel("Плитка", self)
        options_section.setObjectName("sectionTitle")
        root.addWidget(options_section)

        self.clock_check = QCheckBox("Показывать часы и дату поверх видео", self)
        self.clock_check.setChecked(config.show_clock)
        root.addWidget(self.clock_check)

        self.error_label = QLabel(self)
        self.error_label.setObjectName("errorText")
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        root.addWidget(self.error_label)

        root.addStretch(1)

    def _toggle_password(self, visible: bool) -> None:
        self.password_edit.setEchoMode(
            QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password
        )

    @staticmethod
    def _set_invalid(widget: QWidget, invalid: bool) -> None:
        widget.setProperty("invalid", invalid)
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _clear_invalid(self) -> None:
        for widget in (
            self.name_edit,
            self.host_edit,
            self.port_spin,
            self.username_edit,
            self.password_edit,
            self.path_edit,
        ):
            self._set_invalid(widget, False)

    def build_config(self) -> CameraConfig | None:
        self._clear_invalid()
        config = self._base_config.updated(
            camera_name=self.name_edit.text().strip(),
            host=self.host_edit.text().strip(),
            port=self.port_spin.value(),
            username=self.username_edit.text(),
            password=self.password_edit.text(),
            transport=self.transport_control.value(),
            quality=self.quality_control.value(),
            stream_path=self.path_edit.text().strip(),
            show_clock=self.clock_check.isChecked(),
        )
        try:
            config.validate(require_connection=True)
        except ConfigError as exc:
            message = str(exc)
            self.error_label.setText(message)
            self.error_label.show()
            lowered = message.lower()
            if "имя камеры" in lowered:
                self._set_invalid(self.name_edit, True)
                self.name_edit.setFocus()
            elif "ip-адрес" in lowered or "адреса" in lowered:
                self._set_invalid(self.host_edit, True)
                self.host_edit.setFocus()
            elif "порт" in lowered:
                self._set_invalid(self.port_spin, True)
                self.port_spin.setFocus()
            elif "логин" in lowered:
                self._set_invalid(self.username_edit, True)
                self.username_edit.setFocus()
            elif "пароль" in lowered:
                self._set_invalid(self.password_edit, True)
                self.password_edit.setFocus()
            else:
                self._set_invalid(self.path_edit, True)
                self.path_edit.setFocus()
            return None

        self.error_label.hide()
        return config

    def show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.show()
