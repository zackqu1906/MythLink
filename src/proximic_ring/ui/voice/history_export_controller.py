"""Shared asynchronous, local-only export for settings and voice history."""
from pathlib import Path
import threading
import time

from PySide6.QtCore import QObject, Property, Signal, Slot, QStandardPaths, QUrl, Qt
from PySide6.QtGui import QDesktopServices

from ...voice.history_export import export_history, ExportCancelled


class HistoryExportController(QObject):
    changed = Signal()
    _finished = Signal(object, str)
    _progress = Signal(int, int)

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self._store = store
        self._busy = False
        self._message = ""
        self._path = ""
        self._error = False
        self._cancel = threading.Event()
        cancel = self._cancel
        self.destroyed.connect(lambda *_: cancel.set())
        self._finished.connect(self._complete, Qt.QueuedConnection)
        self._progress.connect(self._show_progress, Qt.QueuedConnection)

    @Property(bool, notify=changed)
    def busy(self):
        return self._busy

    @Property(str, notify=changed)
    def message(self):
        return self._message

    @Property(bool, notify=changed)
    def error(self):
        return self._error

    @Property(str, notify=changed)
    def path(self):
        return self._path

    def _destination(self):
        return Path(QStandardPaths.writableLocation(QStandardPaths.DownloadLocation) or Path.home() / "Downloads")

    @Slot()
    def export(self):
        if self._busy or self._cancel.is_set():
            return
        self._busy, self._error = True, False
        self._message = "正在导出语音历史…"
        self.changed.emit()
        destination = self._destination()

        def work():
            last_progress = 0.0
            def progress(done, total):
                nonlocal last_progress
                now = time.monotonic()
                if now - last_progress >= .15 or done == total:
                    last_progress = now
                    self._progress.emit(done, total)
            result, error = None, ""
            try:
                result = export_history(self._store, destination, on_progress=progress, cancel=self._cancel)
            except ExportCancelled:
                return
            except Exception as exc:
                error = f"导出未完成：{exc}"
            if not self._cancel.is_set():
                try:
                    self._finished.emit(result, error)
                except RuntimeError:
                    pass  # The settings owner was destroyed during export.
        threading.Thread(target=work, name="VoiceHistoryExport", daemon=True).start()

    @Slot(int, int)
    def _show_progress(self, done, total):
        if self._busy:
            self._message = f"正在导出语音历史：{done} / {total}"
            self.changed.emit()

    @Slot(object, str)
    def _complete(self, result, error):
        self._busy, self._error = False, bool(error)
        if error:
            self._message = error
        else:
            self._path = result["path"]
            self._message = f"已导出 {result['record_count']} 条记录到下载文件夹。"
            if result["skipped_count"] or result["missing_file_count"]:
                self._message += f"跳过 {result['skipped_count']} 条，缺少 {result['missing_file_count']} 个附件，详情见包内清单。"
        self.changed.emit()
        if not error:
            self.openFolder()

    @Slot()
    def openFolder(self):
        if self._path:
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self._path).parent))):
                self._error = True
                self._message = f"无法打开文件夹，导出文件位于：{self._path}"
                self.changed.emit()

    def close(self):
        self._cancel.set()
