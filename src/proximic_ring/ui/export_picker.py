"""A single flat application selection at each export, with no media options."""
import json
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal, Slot, Property, QStandardPaths
from PySide6.QtWidgets import QFileDialog

from ..diagnostics.export_scope import ExportScope, scope_options, attributions
from ..diagnostics.log_export import LogRange


def application_label(identity):
    from ..diagnostics.export_scope import application_id
    names = {"com.apple.safari": "Safari", "com.google.chrome": "Chrome", "com.tencent.xinwechat": "微信",
             "com.microsoft.outlook": "Outlook", "com.apple.mail": "邮件", "com.apple.notes": "备忘录",
             "com.apple.textedit": "文本编辑", "com.microsoft.vscode": "Visual Studio Code"}
    if application_id(identity) in names:
        return names[application_id(identity)]
    from .desktop.application_identity import application_presentation
    name = application_presentation(identity)["name"]
    if name in {"未识别应用", "未记录应用"}:
        return str(identity) or "未记录应用"
    return name


def discover_options(store=None, gestures=None):
    options = {}
    if store is not None:
        from ..voice.history_export import _record_snapshot
        with store._lock:
            record_ids = [p.parent.name for p in store.interactions_root.glob("*/record.json")]
        for identity in record_ids:
            try:
                with _record_snapshot(store, identity) as (record, _, files, _):
                    identities = attributions(record)
                    for name, stream in files.items():
                        if name.endswith(".jsonl"):
                            for line in stream:
                                try:
                                    identities.update(attributions(json.loads(line)))
                                except (ValueError, UnicodeError):
                                    pass
                    options.update(scope_options([[{"application": app, "website": site} for app, site in identities]]))
            except (OSError, ValueError, TypeError):
                continue
    if gestures is not None:
        options.update(scope_options(gestures.records()))
    result = []
    for key, label in sorted(options.items()):
        app, _, site = key.partition("|")
        try:
            label = application_label(app)
        except Exception:
            label = app
        result.append((key, label + (" · " + site if site else "")))
    return result


class ExportSelection(QObject):
    """One asynchronous selection shared by all export entry points."""
    changed = Signal()
    optionsChanged = Signal()
    _discovered = Signal(int, object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._callback = None
        self._generation = 0
        self._options, self._selected = [], set()
        self._kind, self._period, self._directory = "voice", "all", ""
        self._loading, self._error = False, ""
        self._discovered.connect(self._ready, Qt.QueuedConnection)

    @Property(bool, notify=changed)
    def active(self): return self._callback is not None

    @Property(str, notify=changed)
    def kind(self): return self._kind

    @Property(str, notify=changed)
    def title(self): return {"voice": "导出语音记录", "gesture": "导出手势记录", "diagnostic": "导出诊断日志"}[self._kind]

    @Property(bool, notify=changed)
    def loading(self): return self._loading

    @Property(str, notify=changed)
    def error(self): return self._error

    @Property(str, notify=changed)
    def directory(self): return self._directory

    @Property(str, notify=changed)
    def period(self): return self._period

    @Property("QVariantList", notify=optionsChanged)
    def applications(self): return self._options

    @Property("QStringList", notify=changed)
    def selectedKeys(self): return sorted(self._selected)

    @Property(bool, notify=changed)
    def canExport(self):
        return self.active and not self._loading and not self._error and (self._kind == "diagnostic" or bool(self._selected))

    def open(self, callback, *, store=None, gestures=None, period="all", default_directory=None, kind="voice"):
        if self.active:
            raise RuntimeError("请先完成或取消当前导出选择")
        LogRange.create(period)
        self._generation += 1
        generation = self._generation
        self._callback, self._kind, self._period = callback, kind, period
        self._directory = str(default_directory or QStandardPaths.writableLocation(QStandardPaths.DownloadLocation) or Path.home() / "Downloads")
        self._options, self._selected = [], set()
        self._loading, self._error = kind != "diagnostic", ""
        self.optionsChanged.emit()
        self.changed.emit()
        if not self._loading:
            return
        def work():
            try:
                from .desktop.application_identity import application_presentation
                options = []
                for key, label in discover_options(store=store, gestures=gestures):
                    app, _, site = key.partition("|")
                    presentation = application_presentation(app)
                    options.append(dict(key=key, label=label, name=application_label(app), website=site,
                                        bundle=presentation["bundle"] or app, path=presentation["path"]))
                error = ""
            except Exception:
                options, error = [], "无法读取应用列表，请关闭后重试"
            try:
                self._discovered.emit(generation, options, error)
            except RuntimeError:
                pass  # The owner can close while discovery is reading local records.
        threading.Thread(target=work, name="ExportApplications", daemon=True).start()

    @Slot(int, object, str)
    def _ready(self, generation, options, error):
        if generation != self._generation or not self.active:
            return
        self._options, self._error, self._loading = options, error, False
        self.optionsChanged.emit()
        self.changed.emit()

    @Slot(str)
    def setPeriod(self, period):
        if period in {"today", "week", "all"}:
            self._period = period
            self.changed.emit()

    @Slot(str)
    def toggle(self, key):
        if key not in {row["key"] for row in self._options}:
            return
        self._selected.symmetric_difference_update({key})
        self.changed.emit()

    @Slot(bool)
    def selectAll(self, selected):
        self._selected = {row["key"] for row in self._options} if selected else set()
        self.changed.emit()

    @Slot()
    def chooseDirectory(self):
        generation = self._generation
        directory = QFileDialog.getExistingDirectory(None, "选择导出文件夹", self._directory)
        if directory and self.active and generation == self._generation:
            self._directory = directory
            self.changed.emit()

    def _finish(self, result):
        callback, self._callback = self._callback, None
        self._generation += 1  # Discard any late discovery from this request.
        self._loading = False
        self.changed.emit()
        if callback is not None:
            callback(result)

    @Slot()
    def accept(self):
        if self.canExport:
            scope = None if self._kind == "diagnostic" else ExportScope(frozenset(self._selected))
            self._finish((scope, LogRange.create(self._period), Path(self._directory)))

    @Slot()
    def cancel(self):
        if self.active:
            self._finish(None)


def choose_export_scope(*, owner, on_selected, **options):
    """Present the shared QML dialog without a nested Qt event loop."""
    owner.exportSelection.open(on_selected, **options)
