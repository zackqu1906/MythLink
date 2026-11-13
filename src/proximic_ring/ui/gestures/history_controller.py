from PySide6.QtCore import Property, Signal, Slot

from ..voice.history_export_controller import HistoryExportController
from ...gestures.gesture_settings import GESTURE_LABELS
from ...scenes.registry import SCENE_LABELS
from ..export_picker import application_label


class GestureHistoryController(HistoryExportController):
    recordsChanged = Signal()
    openRequested = Signal()

    def __init__(self, store, parent):
        super().__init__(store, parent, kind="gesture")
        self._records = []

    @Property("QVariantList", notify=recordsChanged)
    def records(self):
        return self._records

    @Slot()
    def refresh(self):
        self._records = []
        for record in self._store.records(limit=500):
            row = dict(record)
            row["gestureLabel"] = GESTURE_LABELS.get(row["gesture"], row["gesture"])
            row["applicationLabel"] = application_label(row["application"])
            row["sceneLabel"] = SCENE_LABELS.get(row["scene"], row["scene"]) or "未知场景"
            row["statusLabel"] = {"observed": "未确认触发", "triggered": "已触发", "confirmed": "已确认", "not_triggered": "未触发"}.get(row["status"], row["status"])
            self._records.append(row)
        self.recordsChanged.emit()

    @Slot()
    def show(self):
        self.refresh()
        self.openRequested.emit()

    @Slot(str, str, str)
    def annotate(self, identity, feedback, expected):
        self._store.feedback(identity, feedback, expected)
        self.refresh()

    @Slot(str)
    def deleteRecord(self, identity):
        self._store.delete(identity)
        self.refresh()
