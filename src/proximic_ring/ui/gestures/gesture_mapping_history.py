"""One reversible, automatically saved mapping edit across the three stores."""
from copy import deepcopy

from PySide6.QtCore import QObject, Property, Signal, Slot

from ...gestures.gesture_settings import GESTURE_LABELS


class GestureMappingHistory(QObject):
    changed = Signal()

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self._last = None
        self._changing = False
        self._error = ""
        owner.gestureSettingsChanged.connect(self._source_changed)
        owner.ringGestures.changed.connect(self._source_changed)
        owner.appGestures.catalog.changed.connect(self._source_changed)

    def _snapshot(self, scope, bundle):
        if scope == "voice":
            return self.owner._gesture_bindings
        if scope == "global":
            return self.owner._global_gesture_bindings
        return deepcopy(self.owner.appGestures.catalog._apps.get(bundle))

    def _source_changed(self):
        if self._changing:
            return
        if self._last and self._snapshot(self._last["scope"], self._last["bundle"]) != self._last["after"]:
            self._last = None  # Never undo over a later/default/menu-discovery change.
        self.changed.emit()

    @Property(str, notify=changed)
    def contextKey(self):
        return self._last["context"] if self._last else ""

    @Property(bool, notify=changed)
    def canUndo(self):
        return self._last is not None

    @Property(str, notify=changed)
    def error(self):
        return self._error

    @Slot(str, str, str, str, "QVariantMap", result=bool)
    def apply(self, scope, bundle, scene, gesture, action):
        catalog, ring = self.owner.appGestures.catalog, self.owner.ringGestures
        self._error = ""
        if scope not in {"voice", "global", "application"} or gesture not in GESTURE_LABELS:
            return False
        if scope == "application" and (bundle != catalog.selectedApp or scene != catalog.selectedScene):
            self._error = "应用或场景已变化，请重新选择"
            self.changed.emit()
            return False
        context = f"{bundle}/{scene}/{gesture}" if scope == "application" else f"{scope}/{gesture}"
        before = self._snapshot(scope, bundle)
        self._changing = True
        try:
            if scope == "voice":
                success = self.owner.setVoiceGestureAction(gesture, action.get("voiceAction", ""))
            elif action.get("newCustom"):
                success = (catalog.setCustomBinding(bundle, gesture, action["label"], action["shortcut"])
                           if scope == "application" else ring.setGlobalCustomBinding(gesture, action["label"], action["shortcut"]))
            else:
                success = (catalog.setBinding(bundle, gesture, action.get("id", "")) if scope == "application"
                           else ring.setGlobalGestureAction(gesture, action.get("id", "")))
            if success:
                after = self._snapshot(scope, bundle)
                if after != before:
                    self._last = dict(scope=scope, bundle=bundle, context=context, before=before, after=after)
            else:
                self._error = (self.owner.gestureSettingsError if scope == "voice" else
                               catalog.message if scope == "application" else self.owner.appGestures.notice)
        finally:
            self._changing = False
            self.changed.emit()
        return success

    @Slot(str, result=bool)
    def undo(self, context):
        record = self._last
        if not record or record["context"] != context:
            return False
        if self._snapshot(record["scope"], record["bundle"]) != record["after"]:
            self._source_changed()
            return False
        self._changing = True
        self._error = ""
        success = False
        try:
            if record["scope"] == "voice":
                self.owner.appGestures.validate_voice(record["before"])
                self.owner._save_gesture_bindings(record["before"])
                success = True
            elif record["scope"] == "global":
                success = self.owner.ringGestures._save_global_bindings(record["before"])
                if not success:
                    self._error = self.owner.appGestures.notice
            else:
                catalog = self.owner.appGestures.catalog
                catalog._apps = {**catalog._apps, record["bundle"]: deepcopy(record["before"])}
                catalog._save()
                success = True
            if success:
                self._last = None
        except ValueError as exc:
            self._error = str(exc)
        finally:
            self._changing = False
            self.changed.emit()
        return success
