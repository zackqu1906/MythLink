"""Focus-bound clipboard recommendations rendered by the native IME palette."""
from collections import OrderedDict
from dataclasses import dataclass, field
import sys
import threading
import time
import uuid

from PySide6.QtCore import QObject, QTimer, Qt, Signal
from PySide6.QtGui import QGuiApplication

from ..desktop.browser_action_queue import BrowserActionQueue
from ...ring_devices import matches_tap_gesture


@dataclass
class ClipboardCandidate:
    revision: int
    expires: float
    accepted_apps: set = field(default_factory=set)


@dataclass
class ClipboardOffer:
    token: str
    field: object
    revision: int
    epoch: str
    connection: object
    expires: float
    cancelled: threading.Event = field(default_factory=threading.Event)
    shown: bool = False
    accepting: bool = False
    voice_requested: bool = False


class ClipboardSuggestionController(QObject):
    _gesture = Signal(str, object)
    _prepareVoice = Signal(object, object)
    _voiceResult = Signal(str)

    def __init__(self, owner, router, *, backend=None, revision=None, enabled=None,
                 clock=time.monotonic):
        super().__init__(router)
        self.owner, self.router = owner, router
        self._enabled = (sys.platform == 'darwin' and QGuiApplication.platformName() == 'cocoa'
                         if enabled is None else enabled)
        self._backend = backend
        if revision is None:
            from ...platforms.macos.clipboard_suggestion import clipboard_revision
            revision = clipboard_revision
        self._revision = revision
        self._clock = clock
        self._candidate = None
        # The pasteboard exposes a change count, not the original copy time.
        # Do not treat unknown-age content present at launch as freshly copied.
        try:
            self._observed_revision = revision() if self._enabled else None
        except Exception:
            self._observed_revision = None
        self._offer = None
        self._seen = OrderedDict()
        self._closed = False
        self._polling = False
        self._generation = 0
        self._quiet_until = 0.
        self._focus_key = None
        self._typed_field = None
        self._typing_revision = None
        self._pointer_guard = None
        self._monitors = []
        self._queue = BrowserActionQueue(self)
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.poll)
        self._gesture.connect(self._apply_gesture, Qt.QueuedConnection)
        self._prepareVoice.connect(self._prepare_voice, Qt.QueuedConnection)
        self._voiceResult.connect(self._voice_result, Qt.QueuedConnection)
        inline = getattr(owner, '_inline_input', None)
        self._enabled = bool(self._enabled and inline is not None)
        if inline is not None:
            inline.clipboardEvent.connect(self._native_event)
            inline.changed.connect(self._activity_changed)
        owner.connectedChanged.connect(self._activity_changed)
        router._screenshot.changed.connect(self._activity_changed)
        touchpad = getattr(owner, '_touchpad', None)
        if touchpad is not None:
            touchpad.changed.connect(self._activity_changed)
        if self._enabled:
            self._install_monitors()
            self._timer.start()

    def _available(self):
        if self._closed or not self._enabled:
            return False
        bridge = self.owner._inline_input._bridge
        return (not self._closed and self._enabled and self.owner._connected
                and bridge is not None and bridge.connected
                and not self.owner._disconnect_event.is_set()
                and not self.router.session_blocked() and not self.router.speech_busy()
                and not self.router._screenshot.active
                and not self.owner._touchpad.blocksVoice
                and not self.router._selector.blocked.is_set())

    def _send(self, kind, **fields):
        bridge = self.owner._inline_input._bridge
        return bool(bridge and bridge.send(dict(type=kind, **fields)))

    def _install_monitors(self):
        if QGuiApplication.platformName() != 'cocoa':
            return
        import AppKit as A
        mask = A.NSEventMaskKeyDown | A.NSEventMaskLeftMouseDown | A.NSEventMaskRightMouseDown
        def local(event):
            self._user_input(keyboard=event.type() == A.NSEventTypeKeyDown); return event
        self._monitors = [A.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(mask, local),
                          A.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(mask, local)]

    def _user_input(self, *, keyboard=True):
        self._quiet_until = self._clock() + .35
        self._typed_field = self._focus_key
        try:
            self._typing_revision = self._revision() if keyboard else None
        except Exception:
            self._typing_revision = None
        self.dismiss()

    def _activity_changed(self):
        if self._offer and not self._available():
            self.dismiss()

    def dismiss(self):
        self._generation += 1
        offer, self._offer = self._offer, None
        if offer:
            if offer.voice_requested:
                self._pointer_guard = (offer.token, self._clock() + .5)
            offer.cancelled.set()
            self._send('clipboard_dismiss', token=offer.token)

    @property
    def pointer_context(self):
        offer = self._offer
        if offer and offer.shown and self._clock() < offer.expires:
            return (offer.token, 'clipboard')
        if self._pointer_guard and self._clock() < self._pointer_guard[1]:
            return (self._pointer_guard[0], 'clipboard')
        return None

    def _capture(self):
        if self._backend is None:
            from ...platforms.macos.clipboard_suggestion import MacClipboardSuggestionAccess
            self._backend = MacClipboardSuggestionAccess()
        return self._backend.capture()

    def poll(self):
        if self._closed or not self._enabled:
            return
        now = self._clock()
        try:
            revision = self._revision()
        except Exception:
            self.dismiss(); return
        if revision != self._observed_revision:
            self._observed_revision = revision
            self._candidate = ClipboardCandidate(revision, now + 60) if revision is not None else None
            self._seen.clear()
        candidate = self._candidate
        # Observe copies even while voice, screenshots, or a disconnected ring
        # prevent displaying offers. Entering an editor must not restart the age.
        if candidate is None or now >= candidate.expires or not self._available():
            self.dismiss(); return
        if self._offer and (revision != self._offer.revision or now >= self._offer.expires):
            self.dismiss()
        if revision is None or now < self._quiet_until or self._polling:
            return
        generation = self._generation
        epoch = self.owner._inline_input._bridge.epoch
        self._polling = True
        def complete(focused, error):
            self._polling = False
            if (generation != self._generation or not self._available()
                    or epoch != self.owner._inline_input._bridge.epoch
                    or candidate is not self._candidate or self._clock() >= candidate.expires):
                return
            if error or focused is None:
                self.dismiss(); return
            try:
                unchanged = self._revision() == revision
            except Exception:
                unchanged = False
            if not unchanged:
                self.dismiss(); return
            self._focus_key = focused.key
            if (self._typing_revision == revision
                    and (self._typed_field is None or self._typed_field == focused.key)):
                self._typed_field = focused.key
                return
            if self._offer and focused.key != self._offer.field.key:
                self.dismiss()
            if (self._offer or self._seen.get(focused.key) == revision
                    or self._app_key(focused) in candidate.accepted_apps):
                return
            offer = ClipboardOffer(uuid.uuid4().hex, focused, revision, epoch,
                                   self.owner._disconnect_event, min(self._clock() + 5, candidate.expires))
            if self._send('clipboard_offer', token=offer.token, revision=revision,
                          foreground_pid=focused.target.pid, rect=list(focused.rect)):
                self._offer = offer
        self._queue.submit(self._capture, lambda: 'closed' if self._closed else '', complete)

    @staticmethod
    def _app_key(focused):
        bundle = getattr(focused.target, 'bundle', '')
        return ('bundle', bundle) if bundle else ('pid', focused.target.pid)

    def _native_event(self, message):
        offer = self._offer
        if (not offer or message.get('epoch') != offer.epoch
                or message.get('token') != offer.token):
            return
        if message.get('visible') and not offer.cancelled.is_set():
            if self._clock() >= offer.expires:
                self.dismiss(); return
            offer.shown = True
            offer.expires = min(self._clock() + 5, self._candidate.expires)
            self._seen[offer.field.key] = offer.revision
            self._seen.move_to_end(offer.field.key)
            while len(self._seen) > 256:
                self._seen.popitem(last=False)
        elif offer.voice_requested or (offer.accepting and not offer.cancelled.is_set()):
            return  # The acceptance itself hides the palette before posting Cmd+V.
        else:
            self.dismiss()

    def consume(self, event, connection):
        """Firmware-thread gate, before mappings or touchpad click delivery."""
        offer = self._offer
        name = str(getattr(event, 'name', ''))
        if matches_tap_gesture(name, self.owner._device_family):
            name = 'tap'
        if (not offer or not offer.shown or self._clock() >= offer.expires
                or connection is not offer.connection or connection.is_set()
                or name not in {'tap', 'swipe-right'}):
            return False
        if name == 'tap':
            offer.voice_requested = True
            offer.cancelled.set()  # Also abort a queued/in-progress focus check.
        elif offer.accepting or offer.cancelled.is_set():
            return True
        else:
            offer.accepting = True
        self._gesture.emit(name, offer)
        return True

    def _apply_gesture(self, name, offer):
        if (offer is not self._offer or offer.connection is not self.owner._disconnect_event
                or offer.connection.is_set()):
            return
        if name == 'swipe-right' and offer.cancelled.is_set():
            return  # Leave the offer for the already queued tap to start voice.
        self._send('clipboard_dismiss', token=offer.token)
        if name == 'tap':
            self.dismiss()
            commands = getattr(self.owner, '_utterance_commands', None)
            if (commands is not None and commands.connection is offer.connection
                    and self.owner._recognition_event.is_set()):
                commands.request('start', lambda callback: self._prepareVoice.emit(callback, offer.connection),
                                 self._voiceResult.emit)
            else:
                self.owner._app_gestures.notify('请先开启语音输入')
            return
        if offer.cancelled.is_set() or not self._available():
            self.dismiss(); return
        if self._clock() >= offer.expires:
            self.dismiss(); return
        candidate = self._candidate
        def complete(result, error):
            if offer is not self._offer or offer.cancelled.is_set():
                return
            if result and not error and candidate is self._candidate:
                candidate.accepted_apps.add(self._app_key(offer.field))
            self.dismiss()
            if error:
                self.owner._app_gestures.notify('粘贴未完成，请在当前输入框重试')
        self._queue.submit(lambda: self._backend.paste(offer.field, offer.revision, offer.cancelled),
                           lambda: 'cancelled' if offer.cancelled.is_set() else '', complete)

    def _prepare_voice(self, callback, connection):
        if callback is None:
            return
        if self._closed or connection is not self.owner._disconnect_event or connection.is_set():
            callback(False); return
        self.owner._prepare_inline_gesture_start(callback, connection)

    def _voice_result(self, action):
        if not self._closed and action == 'unavailable':
            self.owner._app_gestures.notify('语音暂未开始，请重试')

    def close(self):
        self.dismiss()
        self._closed = True
        self._timer.stop()
        self._queue.close()
        if self._monitors:
            import AppKit as A
            for monitor in self._monitors:
                if monitor is not None:
                    A.NSEvent.removeMonitor_(monitor)
            self._monitors = []
