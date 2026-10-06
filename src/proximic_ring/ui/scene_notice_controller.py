"""Observe real scene entry for feedback; never select a scope or dispatch keys."""
import sys
import threading
import time

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot, Qt

from ..native_access import NativeAccessChannel
from ..scene_capabilities import BROWSERS, SCENE_LABELS
from ..scene_diagnostics import new_trace_id
from .application_onboarding import foreground_candidate


class SceneNoticeController(QObject):
    changed = Signal()
    _result = Signal(int, int, object, int, float, object, str)

    def __init__(self, service, parent=None, *, channel=None, foreground=None, clock=None, busy=None):
        super().__init__(parent)
        self.service = service
        self.catalog = service.catalog
        self._channel = channel or NativeAccessChannel()  # Separate from the gesture/voice channel.
        self._foreground = foreground or foreground_candidate
        self._clock = clock or time.monotonic
        self._busy = busy or (lambda: False)
        self._closed = False
        self._stamp = None
        self._generation = -1
        self._epoch = 0
        self._sequence = 0
        self._inflight = None
        self._next_read = 0.0
        self._paused = False
        self._active = None
        self._page = ''
        self._absence_since = None
        self._candidate = None
        self._hits = self._misses = 0
        self._notice = {}
        self._last_error = ''
        self._last_status = None
        self._last_observation = None
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1000)
        self._poll_timer.timeout.connect(self.poll)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.setInterval(3200)
        self._hide_timer.timeout.connect(self.hide)
        self._confirm_timer = QTimer(self)
        self._confirm_timer.setSingleShot(True)
        self._confirm_timer.setInterval(150)
        self._confirm_timer.timeout.connect(self.poll)
        self._result.connect(self._receive, Qt.QueuedConnection)

    @Property(bool, notify=changed)
    def visible(self):
        return bool(self._notice)

    @Property('QVariantMap', notify=changed)
    def notice(self):
        return dict(self._notice)

    def start(self):
        if not self._closed and sys.platform == 'darwin':
            self._poll_timer.start()
            self._log('observer_started', poll_ms=self._poll_timer.interval())
            self.poll()

    def _front(self):
        try:
            return self._foreground()
        except Exception:
            return None

    def _log(self, reason, **facts):
        self.service._diagnostics.record(new_trace_id(), 'scene_notice', reason, **facts)

    @Slot()
    def hide(self):
        self._confirm_timer.stop()
        self._hide_timer.stop()
        if self._notice:
            self._notice = {}
            self.changed.emit()

    def _reset(self, front, generation):
        self._epoch += 1
        self._stamp = front
        self._generation = generation
        self._active = self._candidate = None
        self._page = ''
        self._absence_since = None
        self._hits = self._misses = 0
        self._next_read = 0.0
        self._last_observation = None
        self.hide()

    @Slot()
    def poll(self):
        if self._closed:
            return
        front, generation = self._front(), self.service._generation
        interval = 400 if front and front['value'].casefold() in BROWSERS else 1000
        if self._poll_timer.interval() != interval:
            self._poll_timer.setInterval(interval)
        if front != self._stamp or generation != self._generation:
            self._reset(front, generation)
        configured = self.catalog.configured_scenes()
        busy = self._busy()
        reason = (busy if isinstance(busy, str) else 'interaction_busy') if busy else (
            'foreground_unavailable' if not front else
            'application_not_configured' if not configured.get(front['value']) else '')
        status = (reason, front['value'] if front else '')
        if status != self._last_status:
            self._last_status = status
            self._log('observation_paused' if reason else 'observation_resumed',
                      reason_code=reason, app=status[1])
        if reason:
            if not self._paused:
                self._epoch += 1
            self._paused = True
            self._candidate, self._hits, self._misses = None, 0, 0
            self.hide()
            return
        self._paused = False
        if self._inflight is not None or self._clock() < self._next_read:
            return
        self._sequence += 1
        token, epoch, started, expected = self._sequence, self._epoch, self._clock(), dict(front)
        self._inflight = token
        def read():
            result, error = None, ''
            try:
                result = self._channel.call('scene_observe', expected_bundle=expected['value'], expected_pid=expected['pid'])
            except Exception as exc:
                error = getattr(exc, 'reason', '') or type(exc).__name__
            if not self._closed:
                self._result.emit(token, epoch, expected, generation, started, result, error)
        threading.Thread(target=read, name='SceneEntryObservation', daemon=True).start()

    @Slot(int, int, object, int, float, object, str)
    def _receive(self, token, epoch, front, generation, started, target, error):
        if token != self._inflight:
            return
        self._inflight = None
        if (self._closed or epoch != self._epoch or generation != self.service._generation
                or front != self._front() or self._busy() or self._clock() - started > 2.0):
            self._candidate, self._hits, self._misses = None, 0, 0
            self.hide()
            return
        if error:
            self._next_read = self._clock() + 5.0
            self._candidate, self._hits, self._misses = None, 0, 0
            self.hide()
            if error != self._last_error:
                self._log('observation_unavailable', app=front['value'], reason_code=error)
            self._last_error = error
            return
        self._last_error = ''
        self._observe(target, front)

    def _observe(self, target, front):
        if (not isinstance(target, dict) or target.get('bundle') != front['value']
                or target.get('pid') != front['pid']):
            self._candidate, self._hits, self._misses = None, 0, 0
            self.hide()
            return
        scene = target.get('scene', '')
        browser = front['value'].casefold() in BROWSERS
        page = target.get('page_token', '') if browser else ''
        page_changed = bool(page and page != self._page)
        if page_changed:
            self.hide()
            self._page = page
            self._active = self._candidate = None
            self._hits = self._misses = 0
            self._absence_since = None
        configured = self.catalog.configured_scenes().get(front['value'], [])
        native = target.get('diagnostic') or {}
        context = target.get('input_context', 'unknown')
        reason = ('text_focus' if context == 'text' and not (browser and page) else
                  native.get('reason', 'blocked_window') if target.get('blocked') else
                  native.get('reason', 'no_content_evidence') if not scene else
                  'unconfigured_scene' if scene not in configured else
                  'unknown_focus' if context != 'nontext' and not (browser and page) else 'ready')
        # Keep failed automatic observations traceable without writing every
        # idle poll, timestamps, control labels or user content to the log.
        observation = (scene, context, bool(target.get('blocked')), reason, page)
        if observation != self._last_observation:
            self._last_observation = observation
            self._log('observation_result', app=front['value'], pid=front['pid'],
                      scene=scene, input_context=context, reason_code=reason,
                      page_changed=page_changed,
                      configured_scenes=configured, native_trace=target.get('trace_id', ''), native=native)
        # Focus interruptions pause feedback without announcing repeated entry
        # when the same player's search field or modal is dismissed.
        if target.get('blocked') or (context == 'text' and not (browser and page)):
            self._candidate, self._hits, self._misses = None, 0, 0
            self._absence_since = None
            self.hide()
            return
        if browser and not page:
            # Missing identity during navigation/focus changes is not evidence
            # of leaving and re-entering the same page. Never confirm across it.
            self._candidate, self._hits = None, 0
            self._absence_since = None
            self.hide()
            return
        if scene not in configured:
            self._candidate, self._hits = None, 0
            self._misses += 1
            self.hide()
            if browser:
                evidence = native.get('recognition') or {}
                absent = (not scene and evidence.get('reason') == 'no_web_player'
                          and evidence.get('player_count') == 0
                          and evidence.get('scan_limited') is False
                          and evidence.get('scanned_nodes', 0) > 1)
                if not absent:
                    self._absence_since = None
                    return
                if self._absence_since is None:
                    self._absence_since = self._clock()
                if self._clock() - self._absence_since < 1.2:
                    return
            if self._misses >= 2 and self._active:
                self._log('scene_left', app=front['value'], scene=self._active[2])
                self._active = None
            return
        self._misses = 0
        self._absence_since = None
        if target.get('input_context') != 'nontext' and not (browser and page):
            self._candidate, self._hits = None, 0
            self.hide()
            return
        key = (front['value'], front['pid'], scene, page)
        if key == self._active:
            self._candidate, self._hits = None, 0
            return  # Do not extend the toast or repeat it on every gesture/poll.
        self.hide()  # A previous mode is no longer the current observation.
        self._hits = self._hits + 1 if key == self._candidate else 1
        self._candidate = key
        if self._hits < 2:
            # Confirm with a fresh capture promptly, not a second full idle tick.
            # This never bypasses foreground, busy, age or configuration checks.
            self._confirm_timer.start()
            return
        self._active = key
        label = self.catalog._apps[front['value']]['label']
        self._notice = dict(title='已进入' + SCENE_LABELS[scene] + '模式', application=label, scene=scene)
        self._log('scene_entered', app=front['value'], pid=front['pid'], scene=scene,
                  native_trace=target.get('trace_id', ''), native=target.get('diagnostic', {}))
        self.changed.emit()
        self._hide_timer.start()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._epoch += 1
        self._poll_timer.stop()
        self.hide()
        threading.Thread(target=self._channel.close, name='SceneNoticeCleanup', daemon=True).start()
