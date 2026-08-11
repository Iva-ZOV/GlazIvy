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

from ..config import (
    CameraConfig,
    ConfigError,
    extract_stream_credentials,
    replace_stream_credentials,
)
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
        is_onvif = config.source == "onvif"
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
        self.host_edit.setReadOnly(is_onvif)
        self.port_spin = QSpinBox(self)
        self.port_spin.setRange(1, 65535)
        self.port_spin.setValue(config.port)
        self.host_block = FieldBlock("IP-адрес или имя", self.host_edit, self)
        self.port_block = FieldBlock("RTSP-порт", self.port_spin, self)
        if is_onvif:
            grid.addWidget(self.host_block, 1, 0, 1, 2)
        else:
            grid.addWidget(self.host_block, 1, 0)
            grid.addWidget(self.port_block, 1, 1)

        if is_onvif:
            preferred_url = (
                config.stream_url_hd if config.quality == "hd" else config.stream_url_sd
            )
            fallback_url = (
                config.stream_url_sd if config.quality == "hd" else config.stream_url_hd
            )
            username = ""
            password = ""
            for stream_url in (preferred_url, fallback_url):
                if not stream_url:
                    continue
                username, password = extract_stream_credentials(stream_url)
                if username or password:
                    break
            else:
                username = config.username
                password = config.password
        else:
            username = config.username
            password = config.password
        self._initial_credentials = (username, password)
        self.username_edit = QLineEdit(username, self)
        self.username_edit.setPlaceholderText("Логин RTSP")
        self.username_edit.setClearButtonEnabled(True)
        self.password_edit = QLineEdit(password, self)
        self.password_edit.setPlaceholderText("Пароль RTSP")
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        grid.addWidget(
            FieldBlock("Логин RTSP", self.username_edit, self),
            2,
            0,
        )
        grid.addWidget(
            FieldBlock("Пароль RTSP", self.password_edit, self),
            2,
            1,
        )

        self.show_password_check = QCheckBox("Показать пароль", self)
        self.show_password_check.toggled.connect(self._toggle_password)
        grid.addWidget(self.show_password_check, 3, 1)

        self.path_edit = QLineEdit(config.stream_path, self)
        self.path_edit.setPlaceholderText(
            "/user={user}&password={password}&channel=0&stream={stream}.sdp?real_stream"
        )
        self.path_edit.setClearButtonEnabled(True)
        self.path_block = FieldBlock("Шаблон пути потока", self.path_edit, self)
        grid.addWidget(self.path_block, 4, 0, 1, 2)

        path_help = QLabel(
            "Оставьте {user}, {password} и {stream} как подстановки. "
            "Для камеры Xiongmai подходит значение по умолчанию.",
            self,
        )
        path_help.setObjectName("helperText")
        path_help.setWordWrap(True)
        self.path_help = path_help
        grid.addWidget(path_help, 5, 0, 1, 2)

        self.onvif_help = QLabel(
            "Учётные данные получены от камеры автоматически при поиске. "
            "Менять их нужно только если вы сменили пароль на самой камере.",
            self,
        )
        self.onvif_help.setObjectName("helperText")
        self.onvif_help.setWordWrap(True)
        grid.addWidget(self.onvif_help, 4, 0, 1, 2)

        self.port_block.setVisible(not is_onvif)
        self.path_block.setVisible(not is_onvif)
        self.path_help.setVisible(not is_onvif)
        self.onvif_help.setVisible(is_onvif)

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
        changes: dict[str, object] = dict(
            camera_name=self.name_edit.text().strip(),
            host=self.host_edit.text().strip(),
            port=self.port_spin.value(),
            transport=self.transport_control.value(),
            quality=self.quality_control.value(),
            stream_path=self.path_edit.text().strip(),
        )
        if self._base_config.source == "onvif":
            username = self.username_edit.text()
            password = self.password_edit.text()
            if (username, password) != self._initial_credentials:
                changes.update(
                    username=username,
                    password=password,
                    stream_url_hd=(
                        replace_stream_credentials(
                            self._base_config.stream_url_hd,
                            username,
                            password,
                        )
                        if self._base_config.stream_url_hd
                        else ""
                    ),
                    stream_url_sd=(
                        replace_stream_credentials(
                            self._base_config.stream_url_sd,
                            username,
                            password,
                        )
                        if self._base_config.stream_url_sd
                        else ""
                    ),
                )
        else:
            changes.update(
                username=self.username_edit.text(),
                password=self.password_edit.text(),
            )
        config = self._base_config.updated(**changes)
        try:
            config.validate(require_connection=config.source != "onvif")
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
