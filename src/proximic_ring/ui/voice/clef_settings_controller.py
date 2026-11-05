"""Small settings surface for the app-owned SSH connection."""
from dataclasses import asdict
import threading

from PySide6.QtCore import QObject, Property, Signal, Slot

from ...text_processing.clef_connection import ClefSSHSettings, ClefSSHConnection, ConnectedClefRouter


class ClefSettingsController(QObject):
    changed = Signal()
    _statusReceived = Signal(str)
    _finished = Signal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._busy = False
        self._status = "填写 SSH 信息后测试连接"
        values = {}
        for key, default in asdict(ClefSSHSettings()).items():
            value = settings.value("clef/ssh/" + key, default)
            try:
                values[key] = int(value) if isinstance(default, int) else str(value or "")
            except (ValueError, TypeError):
                values[key] = default
        self.connection = ClefSSHConnection(ClefSSHSettings(**values), on_status=self._statusReceived.emit)
        self.router = ConnectedClefRouter(self.connection)
        self._statusReceived.connect(self._receive_status)
        self._finished.connect(self._complete)

    @Property("QVariantMap", notify=changed)
    def config(self):
        return asdict(self.connection.settings)

    @Property(str, notify=changed)
    def status(self):
        return self._status

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Slot(str)
    def _receive_status(self, status):
        self._status = status
        self.changed.emit()

    @Slot(str)
    def _complete(self, status):
        self._busy = False
        self._receive_status(status)

    @Slot("QVariantMap")
    def connectServer(self, values):
        if self._busy:
            return
        owner = self.parent()
        inline = getattr(owner, "_inline_input", None)
        if (getattr(owner, "textProcessing", False) or
                (inline is not None and inline._view.get("phase") in {"starting", "listening", "finishing", "editing", "polishing"})):
            self._receive_status("请先结束当前语音处理，再修改或测试连接")
            return
        try:
            config = ClefSSHSettings(host=str(values.get("host", "")).strip(),
                port=int(values.get("port", 22)), user=str(values.get("user", "")).strip(),
                key_file=str(values.get("key_file", "")).strip(), service_port=int(values.get("service_port", 30000)))
            config.validate()
        except (ValueError, TypeError) as exc:
            self._receive_status(str(exc))
            return
        for key, value in asdict(config).items():
            self._settings.setValue("clef/ssh/" + key, value)
        self._settings.sync()
        self._busy = True
        self._receive_status("正在测试连接…")
        def work():
            try:
                self.connection.configure(config)
                self.connection.check()
                # Also checks inference schema/authentication, not just an open SSH socket.
                self.router.classify("连接测试", "", {})
                message = "连接成功 · 自动路由可用"
            except Exception as exc:
                message = str(exc)
            self._finished.emit(message)
        threading.Thread(target=work, name="ClefConnectionTest", daemon=True).start()

    @Slot(result=str)
    def choosePrivateKey(self):
        from pathlib import Path
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(None, "选择 SSH 私钥", str(Path.home() / ".ssh"))
        return path
