"""Opt-in foreground app discovery; never launches apps or sends test keystrokes."""
import json
import os
import sys
import threading
import time

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot, Qt

from ..application_catalog import application_metadata
from ..scenes.policy import primary_scene, scene_choices
from ..scenes.registry import SCENE_LABELS
from ..gesture_settings import GESTURE_LABELS
from ..scenes.registry import action_label
from ..scene_diagnostics import new_trace_id

DECISIONS_KEY = 'onboarding/gestureSuggestionsV1'
IGNORED = {'com.apple.dock', 'com.apple.controlcenter', 'com.apple.notificationcenterui',
           'com.apple.loginwindow', 'com.apple.systempreferences', 'com.apple.systemuiserver'}


def foreground_candidate():
    if sys.platform != 'darwin':
        return None
    from ..mac_workspace import frontmost_application
    app = frontmost_application()
    if app is None or int(app.processIdentifier()) == os.getpid() or int(app.activationPolicy()) != 0:
        return None
    bundle = str(app.bundleIdentifier() or '')
    path = str(app.bundleURL().path()) if app.bundleURL() else ''
    if not bundle or bundle.casefold() in IGNORED or not path.endswith('.app'):
        return None
    return dict(value=bundle, pid=int(app.processIdentifier()), path=path)


class ApplicationOnboarding(QObject):
    changed = Signal()
    _metadataReady = Signal(int, object, object)

    def __init__(self, catalog, parent=None, *, foreground=None, metadata=None, clock=None, busy=None):
        super().__init__(parent)
        self.catalog = catalog
        self._settings = catalog.service.owner._settings
        self._foreground = foreground or foreground_candidate
        self._metadata = metadata or application_metadata
        self._clock = clock or time.monotonic
        self._busy = busy or (lambda: False)
        self._closed = False
        self._epoch = 0
        self._last = None
        self._since = 0.0
        self._loading = False
        self._offer = {}
        self._target = None
        self._shown_at = 0.0
        self._trace = new_trace_id()
        self._seen = set()
        try:
            values = json.loads(str(self._settings.value(DECISIONS_KEY, '[]')))
            self._dismissed = {v for v in values if isinstance(v, str)} if isinstance(values, list) else set()
        except (ValueError, TypeError):
            self._dismissed = set()
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.poll)
        self._metadataReady.connect(self._ready, Qt.QueuedConnection)

    def start(self):
        if not self._closed and sys.platform == 'darwin':
            self._timer.start()

    def _log(self, reason, **facts):
        self.catalog.service._diagnostics.record(self._trace, 'application_onboarding', reason, **facts)

    @Property(bool, notify=changed)
    def visible(self):
        return bool(self._offer)

    @Property('QVariantMap', notify=changed)
    def offer(self):
        return dict(self._offer)

    @Property('QVariantList', notify=changed)
    def options(self):
        app = self._offer
        return [dict(value=scene, label=SCENE_LABELS[scene]) for scene in scene_choices(app.get('sceneProfiles', {}))]

    @Property('QVariantList', notify=changed)
    def preview(self):
        if not self._offer:
            return []
        app = self.catalog._new_application(self._offer)
        scenes = [('regular', app['bindings'])] + list(app.get('scenes', {}).items())
        rows = []
        for scene, items in scenes:
            for gesture, action in items.items():
                rows.append(dict(gesture=GESTURE_LABELS[gesture], action=action['label'],
                                 shortcut=action['shortcut'], pending=False,
                                 occupied=not self.catalog._can_bind_regular(gesture)))
        for scene, items in app.get('pendingDefaults', {}).items():
            for gesture, action in items.items():
                rows.append(dict(gesture=GESTURE_LABELS[gesture], action=action_label(scene, action),
                                 shortcut='等待菜单快捷键', pending=True,
                                 occupied=not self.catalog._can_bind_regular(gesture)))
        return rows

    def _hide(self):
        if self._offer:
            self._offer = {}
            self._target = None
            self.changed.emit()

    def _read_front(self):
        try:
            return self._foreground()
        except Exception:
            return None

    @Slot()
    def poll(self):
        if self._closed:
            return
        current = self._read_front()
        if current != self._last:
            self._epoch += 1
            self._last, self._since, self._loading = current, self._clock(), False
            self._hide()
        if (self._busy() or not self.catalog._config_valid or not current
                or self.catalog.owns(current['value'])):
            self._hide()
            return
        if self._offer:
            if self._clock() - self._shown_at >= 45:
                self._hide()  # Dwell timeout affects this run only, not saved decisions.
            return
        if (current['value'] in self._dismissed or current['value'] in self._seen
                or self._loading or self._clock() - self._since < 2):
            return
        self._loading = True
        epoch, snapshot = self._epoch, dict(current)
        def load():
            try:
                candidate = self._metadata(snapshot['path'])
            except Exception:
                candidate = None
            if not self._closed:
                self._metadataReady.emit(epoch, snapshot, candidate)
        threading.Thread(target=load, name='ApplicationSuggestion', daemon=True).start()

    @Slot(int, object, object)
    def _ready(self, epoch, target, candidate):
        if self._closed or epoch != self._epoch:
            return
        self._loading = False
        if target == self._read_front() and not candidate:
            self._seen.add(target['value'])  # Unreadable app metadata is not polled every second.
        if (target != self._read_front() or self._busy() or not self.catalog._config_valid
                or not candidate or candidate.get('value') != target['value']
                or self.catalog.owns(target['value']) or target['value'] in self._dismissed):
            return
        self._offer = dict(candidate)
        self._offer['primaryScene'] = primary_scene(candidate['value'], candidate.get('sceneProfiles', {}), candidate.get('primaryScene', ''))
        self._target = dict(target)
        self._trace = new_trace_id()
        self._shown_at = self._clock()
        self._seen.add(target['value'])
        self._log('suggestion_shown', app=target['value'], primary_scene=self._offer['primaryScene'])
        self.changed.emit()

    @Slot(str)
    def chooseScene(self, scene):
        if scene in {item['value'] for item in self.options}:
            self._offer['primaryScene'] = scene
            self.changed.emit()

    @Slot(result=bool)
    def accept(self):
        if (self._closed or not self._offer or self._busy()
                or self._target != self._read_front() or self.catalog.owns(self._offer['value'])):
            self._hide()
            return False
        candidate = dict(self._offer)
        success = self.catalog.addDiscoveredApplication(candidate)
        self._log('suggestion_accepted' if success else 'suggestion_rejected', app=candidate['value'],
                  primary_scene=candidate['primaryScene'], binding_count=self.catalog.bindingCount(candidate['value']))
        self._hide()
        return success

    @Slot()
    def dismiss(self):
        if self._offer:
            self._dismissed.add(self._offer['value'])
            self._settings.setValue(DECISIONS_KEY, json.dumps(sorted(self._dismissed)))
            self._log('suggestion_dismissed', app=self._offer['value'])
        self._hide()

    def close(self):
        self._closed = True
        self._epoch += 1
        self._timer.stop()
        self._hide()
