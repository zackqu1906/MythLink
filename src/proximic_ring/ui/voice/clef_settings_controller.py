"""Settings for a direct connection to the user's public Clef service."""
from dataclasses import asdict
import threading

from PySide6.QtCore import QObject, Property, Signal, Slot

from ...text_processing.clef_connection import ClefServerSettings, ClefConnection, ConnectedClefRouter


class ClefSettingsController(QObject):
    changed = Signal()
    _statusReceived = Signal(str)
    _finished = Signal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._busy = False
        self._status = "填写服务器地址和公网端口后测试连接"
        values = {}
        for key, default in asdict(ClefServerSettings()).items():
            # Reuse only the previous address, never its SSH port or credentials.
            if key == "host":
                default = settings.value("clef/ssh/host", default)
            value = settings.value("clef/http/" + key, default)
            try:
                values[key] = int(value) if isinstance(default, int) else str(value or "")
            except (ValueError, TypeError):
                values[key] = default
        self.connection = ClefConnection(ClefServerSettings(**values), on_status=self._statusReceived.emit)
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
            config = ClefServerSettings(host=str(values.get("host", "")).strip(),
                port=int(values.get("port", 20031)))
            config.validate()
        except (ValueError, TypeError) as exc:
            self._receive_status(str(exc))
            return
        for key, value in asdict(config).items():
            self._settings.setValue("clef/http/" + key, value)
        self._settings.sync()
        self._busy = True
        self._receive_status("正在测试连接…")
        def work():
            try:
                self.connection.configure(config)
                self.connection.check()
                # Verify an actual inference response, not just the health endpoint.
                self.router.classify("连接测试", "", {})
                message = "连接成功 · 自动路由可用"
            except Exception as exc:
                message = str(exc)
            self._finished.emit(message)
        threading.Thread(target=work, name="ClefConnectionTest", daemon=True).start()
