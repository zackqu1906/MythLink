"""Global Ring modes, ahead of both the audio endpoint and app shortcuts."""
from __future__ import annotations

from dataclasses import dataclass, replace
import threading
import time

from PySide6.QtCore import QObject, Property, Qt, Signal, Slot

from ..app_gestures import QUIET_PHASES
from .text_focus_controller import TextFocusController
from .page_scroll_controller import PageScrollController
from .window_selector_controller import WindowSelectorController


@dataclass(frozen=True)
class ModeGestureEvent:
    source: object
    generation: int


@dataclass(frozen=True)
class MenuRequest:
    name: str
    generation: int
    created: float
    busy: bool
    transition: object = None
    stamp: object = None
    selection: object = None


class RingGestureController(QObject):
    changed = Signal()
    showRequested = Signal(str, str)
    hideRequested = Signal()
    _requested = Signal(object, object)

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self._lock = threading.Lock()
        self._mode = "input"
        self._generation = 0
        self._session_was_blocked = False
        self._transition = None
        self._selector = WindowSelectorController(self)
        self._fields = TextFocusController(self)
        self._scroll = PageScrollController(self)
        self._scroll.notice.connect(lambda message: self.showRequested.emit(self.mode, message))
        self._fields.notice.connect(lambda message: self.showRequested.emit(self.mode, message))
        self._selector.notice.connect(lambda message: self.showRequested.emit(self.mode, message))
        self._selector.changed.connect(self._fields.sync)
        self.changed.connect(self._fields.sync)
        # Always queue: firmware callbacks must never manipulate Qt windows.
        self._requested.connect(self._apply, Qt.QueuedConnection)
        owner.connectedChanged.connect(self._connection_changed)

    @Property(str, notify=changed)
    def mode(self):
        with self._lock:
            return self._mode

    @Property(str, notify=changed)
    def modeLabel(self):
        return "输入模式" if self.mode == "input" else "操作模式"

    @Property(QObject, constant=True)
    def textFields(self):
        return self._fields

    @Property(QObject, constant=True)
    def windowSelector(self):
        return self._selector

    def session_blocked(self):
        proximity = getattr(self.owner, "_proximity", None)
        return proximity is not None and proximity.gestures_blocked

    def lock_state_changed(self):
        blocked = self.session_blocked()
        if blocked == self._session_was_blocked:
            return
        self._session_was_blocked = blocked
        if blocked:
            with self._lock:
                self._generation += 1
                self._transition = None
            self.hideRequested.emit()
            self._selector.cancel()
            self.owner._app_gestures.cancel_pending()
        self._fields.sync()

    def speech_busy(self):
        owner = self.owner
        state = owner._app_gestures.phase_state()
        # Retained dictated/edited text is not a busy sentence. Preparation,
        # composition, final writeback and actual edit jobs are busy.
        return bool(
            state["phase"] not in QUIET_PHASES
            or state["composing"] or state["writing"] or state["edit_requested"]
            or owner._pending_inline_audio_start or owner._utterance_active
            or owner._app_gestures._pending is not None
            or (not owner._inline_enabled() and (
                owner._interaction_state in {"listening", "processing"}
                or owner._pending_text_requests or owner._pending_mode_routes
                or owner._pending_dictation_result is not None
            ))
        )

    def filter(self, event, audio_busy: bool, connection) -> bool:
        """Worker-thread gate. True preserves the existing voice/app route."""
        if connection is not self.owner._disconnect_event or connection.is_set():
            return False
        name = str(getattr(event, "name", ""))
        proximity = getattr(self.owner, "_proximity", None)
        if proximity is not None and proximity.filter_gesture(name, connection):
            return False
        busy = bool(audio_busy or self.speech_busy())
        if self._selector.blocked.is_set():
            self._selector.enqueue(name, connection, busy)
            return False
        picker = self._fields.picker
        selection_gestures = {"swipe-up", "swipe-down", "swipe-left", "swipe-right", "tap"}
        stamp = (self._fields.capture_stamp() if self.mode == "input" and
                 (name == "swipe-down" or (picker.active.is_set() and name in selection_gestures))
                 and not busy else None)
        if self.mode == "operation" and name in {"swipe-up", "swipe-down"} and not busy:
            stamp = self._scroll.capture_stamp()
        if name == "clench" and not busy:
            stamp = self._selector.capture_stamp()
        with self._lock:
            if self._scroll.pending.is_set() and name != "index-pinch":
                if self._scroll.applying.is_set() or name in {"swipe-up", "swipe-down"}:
                    return False
            if self._fields.pending.is_set() and name != "index-pinch":
                # A mode switch may cancel discovery. During the short actual
                # focus write, suppress gestures until its result is known.
                if name not in {"middle-pinch", "clench"} or self._fields.applying.is_set():
                    return False
            if self._transition is not None and name != "index-pinch":
                # In particular, a tap must not start audio while Qt is
                # deciding the preceding mode change.
                return False
            ticket = None
            if self._mode == "input":
                if picker.active.is_set() or (name == "swipe-down" and not busy):
                    ticket = picker.claim(name, stamp, voice_busy=busy)
                    if ticket is not None:
                        if ticket["action"] == "enter":
                            self._generation += 1  # Invalidate old queued app actions.
                        request = MenuRequest(name, self._generation, time.monotonic(), busy,
                                              stamp=stamp, selection=ticket)
                        self._requested.emit(request, connection)
                        return ticket["action"] == "voice"
                    if name not in {"middle-pinch", "index-pinch", "clench"}:
                        return False
            if name not in {"middle-pinch", "index-pinch", "clench", "swipe-down"}:
                if self._mode == "input":
                    return True
                if name != "swipe-up":
                    return False
            transition = object() if name in {"middle-pinch", "clench"} and not busy else None
            if transition is not None:
                self._transition = transition
            request = MenuRequest(name, self._generation, time.monotonic(), busy, transition, stamp)
        self._requested.emit(request, connection)
        return False

    def envelope(self, event):
        # Capture before the native target lookup, which may take time. A
        # queued input action cannot leak across a mode change and back.
        with self._lock:
            generation = self._generation
        return ModeGestureEvent(self.owner._app_gestures.envelope(event), generation)

    def accepts(self, event):
        with self._lock:
            return (not self.session_blocked() and self._mode == "input" and self._transition is None
                    and not self._selector.blocked.is_set()
                    and not self._fields.picker.active.is_set()
                    and (not isinstance(event, ModeGestureEvent)
                         or event.generation == self._generation))

    @Slot()
    def showMenu(self):
        if self.session_blocked():
            return
        self._fields.refresh()
        self.showRequested.emit(self.mode, "")

    @Slot(object, object)
    def _apply(self, request, connection):
        selection_handled = False
        try:
            owner = self.owner
            if (self.session_blocked() or connection is not owner._disconnect_event or connection.is_set()
                    or not owner._runtime_active or not owner._connected
                    or time.monotonic() - request.created > 1.0):
                return
            if request.name == "index-pinch":
                self.showMenu()
                self._log(request.name, "show")
                return
            with self._lock:
                if request.generation != self._generation:
                    return
            if request.selection and request.selection["action"] == "voice":
                self._fields.picker.handle(request.selection)
                selection_handled = True
                return
            # Keep the recognition-time busy flag even if finalization finished
            # before Qt delivered the gesture. Never queue it for later.
            if request.busy or self.speech_busy():
                self.showRequested.emit(self.mode, "请先结束本句")
                self._log(request.name, "blocked", "speech_busy")
                return
            if request.selection:
                self._fields.picker.handle(request.selection)
                selection_handled = True
                self._log(request.name, "select_input_field")
                return
            if request.name == "middle-pinch":
                with self._lock:
                    self._mode = "operation" if self._mode == "input" else "input"
                    self._generation += 1
                owner._app_gestures.cancel_pending()
                self.changed.emit()
                self.showMenu()
                self._log(request.name, "switch")
                return
            if request.name == "swipe-down" and self.mode == "input":
                return
            if self.mode == "operation" and request.name in {"swipe-up", "swipe-down"}:
                self._scroll.start(request, connection)
                return
            if request.name == "clench":
                with self._lock:
                    self._generation += 1  # Old application actions cannot cross the overview.
                    current = replace(request, generation=self._generation)
                self._selector.start(current, connection)
        finally:
            if request.selection and not selection_handled:
                self._fields.picker.discard(request.selection)
            with self._lock:
                if request.transition is not None and self._transition is request.transition:
                    self._transition = None

    def _log(self, gesture, action, reason=""):
        self.owner._event_log("RING_GESTURE", gesture=gesture, action=action,
                              mode=self.mode, reason=reason)

    @Slot()
    def _connection_changed(self):
        # Disconnect/reconnect invalidates pending mode commands and app events
        # but preserves the chosen global mode for this application run.
        with self._lock:
            self._generation += 1
            self._transition = None
        if not self.owner._connected:
            self.hideRequested.emit()
            self._selector.cancel()
        else:
            self._selector.prepare()
        self._fields.sync()

    def close(self):
        self._fields.close()
        self._scroll.close()
        self._selector.close()
