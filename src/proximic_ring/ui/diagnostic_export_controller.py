"""Shared background diagnostic export for settings and the live log window."""
from datetime import datetime
from pathlib import Path
import sys
import threading
import time

from PySide6.QtCore import QObject, Property, Signal, Slot, QUrl, Qt
from PySide6.QtGui import QDesktopServices

from ..diagnostics.log_export import LogRange, export_diagnostics, suggested_filename, safe_configuration
from ..voice.history_export import ExportCancelled
from .export_picker import choose_export_scope


def runtime_snapshot(owner):
    """Read cached Qt state on its owning thread; never request permissions."""
    def properties(obj, names):
        values = {}
        for name in names:
            try:
                values[name] = getattr(obj, name)
            except Exception as exc:
                values[name] = {"unavailable": str(exc)}
        return values

    return safe_configuration(dict(
        captured_at=datetime.now().astimezone().isoformat(), run_id=owner._diagnostic_run_id,
        application=properties(owner, ("connected", "busy", "running", "deviceName", "statusTitle",
            "statusDetail", "inputMode", "audioSettings", "asrBackend", "asrModel", "llmProvider", "llmModel")),
        input_method=properties(owner.inlineInput, ("enabled", "ready", "active", "connectionStatus", "inputMethodVersion", "error")),
        permissions=properties(owner.inlineInput.permissions, ("checking", "accessibilityStatusText",
            "bluetoothStatusText", "microphoneStatusText", "screenRecordingStatusText")),
        touchpad=properties(owner.touchpad, ("state", "message", "inputMode", "raw2Supported")),
        proximity=properties(owner.proximity, ("status", "permissionReady", "busy", "deviceName")),
        settings={key: owner._settings.value(key) for key in owner._settings.allKeys()
                  if key.startswith(("input/", "audio/", "gestures/", "touchpad/", "proximity/"))},
    ))


class DiagnosticExportController(QObject):
    changed = Signal()
    _finished = Signal(object, str)
    _progress = Signal(int, int)

    def __init__(self, owner):
        super().__init__(owner)
        self._owner = owner
        self._busy = self._error = False
        self._message = self._path = ""
        self._cancel = threading.Event()
        self._ime_root = Path.home() / "Library/Application Support/ProxiMic/ime" if sys.platform == "darwin" else None
        cancel = self._cancel
        self.destroyed.connect(lambda *_: cancel.set())
        self._finished.connect(self._complete, Qt.QueuedConnection)
        self._progress.connect(self._show_progress, Qt.QueuedConnection)

    @Property(bool, notify=changed)
    def busy(self): return self._busy

    @Property(bool, notify=changed)
    def error(self): return self._error

    @Property(str, notify=changed)
    def message(self): return self._message

    @Property(str, notify=changed)
    def path(self): return self._path

    @Slot(str)
    def export(self, period):
        if self._busy or self._cancel.is_set():
            return
        try:
            selection = LogRange.create(period)
        except ValueError as exc:
            self._error, self._message = True, str(exc)
            self.changed.emit()
            return
        # All entry points share one asynchronous selection dialog.
        self._busy, self._error = True, False
        self._previous_message = self._message
        self._message = "请选择时间范围与保存文件夹…"
        self.changed.emit()
        try:
            choose_export_scope(owner=self._owner, on_selected=self._selected, kind="diagnostic", period=period)
        except Exception as exc:
            self._complete(None, str(exc))

    def _selected(self, chosen):
        if chosen is None or self._cancel.is_set():
            self._busy, self._message = False, self._previous_message
            self.changed.emit()
            return
        _, selection, directory = chosen
        period = selection.key
        try:
            destination = Path(directory) / suggested_filename(selection)
            state = runtime_snapshot(self._owner)
            self._owner._event_log("USER_ACTION", action="export_diagnostic_log", period=period)
        except Exception as exc:
            self._complete(None, str(exc))
            return
        writer = self._owner._diagnostic_log
        ime_root = self._ime_root
        self._message = "正在收集完整日志和诊断信息…"
        self.changed.emit()

        def work():
            result, error = None, ""
            last_progress = 0.0

            def progress(done, total):
                nonlocal last_progress
                now = time.monotonic()
                if now - last_progress >= .15 or done == total:
                    last_progress = now
                    if not self._cancel.is_set():
                        self._progress.emit(done, total)
            try:
                from ..touchpad.click_diagnostics import flush_click_diagnostics
                from ..diagnostics.runtime_diagnostics import _capture
                drained = flush_click_diagnostics()
                if _capture is not None:
                    _capture.flush()
                state["touchpad_log_queue_drained"] = drained
                result = export_diagnostics(writer, destination, LogRange.create(period), state=state,
                    ime_root=ime_root, cancel=self._cancel, on_progress=progress)
            except ExportCancelled:
                return
            except Exception as exc:
                error = str(exc)
            if not self._cancel.is_set():
                try:
                    self._finished.emit(result, error)
                except RuntimeError:
                    pass  # Qt owner was destroyed while the worker was finishing.
        threading.Thread(target=work, name="DiagnosticExport", daemon=True).start()

    @Slot(int, int)
    def _show_progress(self, done, total):
        if self._busy:
            self._message = f"正在收集诊断记录：{done} / {total}"
            self.changed.emit()

    @Slot(object, str)
    def _complete(self, result, error):
        self._busy, self._error = False, bool(error)
        if error:
            self._message = f"导出诊断日志失败：{error}"
        else:
            self._path = result["path"]
            self._message = f"已导出 {result['log_lines']} 行日志及运行诊断信息：{self._path}"
            if result["warning_count"]:
                self._message += f"；{result['warning_count']} 项缺失或读取提示，详见包内清单。"
            self._owner._append_log(self._message)
        self.changed.emit()

    @Slot()
    def openFolder(self):
        if self._path and not QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self._path).parent))):
            self._error, self._message = True, f"无法打开文件夹，诊断包位于：{self._path}"
            self.changed.emit()

    def close(self):
        self._cancel.set()
