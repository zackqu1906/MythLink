"""Opt-in input-source borrowing with a native, verified sentence suspension."""
from dataclasses import dataclass, field
import threading
import time
import uuid

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal

from ...voice.ime.input_method_install import BUNDLE_ID
from ...diagnostics.scene_diagnostics import native_resume_reason


@dataclass(eq=False)
class _ResumeRequest:
    token: str
    deadline: float
    done: threading.Event = field(default_factory=threading.Event)
    cancelled: threading.Event = field(default_factory=threading.Event)
    failure: str | None = "input_method_resume_session_lost"


class InputSourceReturn(QObject):
    _resumeRequested = Signal(object)
    _resumeCancelled = Signal(object)
    _gestureFinished = Signal()

    def __init__(self, inline):
        super().__init__(inline)
        self.inline = inline
        self.enabled = False
        self.original = ""
        self.origin = None
        self.saved = None
        self.stage = ""
        self.helper = None
        self.request = None
        self.probe = None
        self._recovering = False
        self._gesture_waiting = False
        self._gesture_timer = QTimer(self)
        self._gesture_timer.setSingleShot(True)
        self._gesture_timer.timeout.connect(self._finish_gesture)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._timed_out)
        self._poll = QTimer(self)
        self._poll.setInterval(100)
        self._poll.timeout.connect(self._ping)
        self._resumeRequested.connect(self._resume, Qt.QueuedConnection)
        self._resumeCancelled.connect(self._cancel_resume, Qt.QueuedConnection)
        self._gestureFinished.connect(self._finish_gesture, Qt.QueuedConnection)

    def configure(self, enabled):
        self.enabled = bool(enabled)
        if not self.enabled:
            self.discard()

    def remember_probe(self, target, previous):
        # The gesture worker transfers immutable metadata, never a CF pointer.
        if self.enabled and target and self._external(previous):
            self.probe = (tuple(target), previous, time.monotonic())

    @staticmethod
    def _external(source):
        return isinstance(source, str) and bool(source) and source not in {BUNDLE_ID, BUNDLE_ID + ".dictation"}

    def begin(self, origin, target):
        probe, self.probe = self.probe, None
        self.discard()
        if not self.enabled:
            return
        self.origin = origin
        if probe and target and tuple(target) == probe[0] and time.monotonic() - probe[2] < 1:
            self.original = probe[1]

    def selected(self, message):
        previous = message.get("previous_source")
        if self.enabled and not self.original and self._external(previous):
            self.original = previous

    def schedule(self):
        if not self.enabled or not self.original or self.saved is not None:
            return
        c = self.inline
        identity = (c._epoch, c._utterance_id, c._view.get("revision", 0))
        # Run after every settled subscriber (including queued polish/send).
        QTimer.singleShot(0, lambda: self._suspend(identity))

    def _suspend(self, identity):
        c = self.inline
        owner = c.parent()
        gestures = getattr(owner, "_app_gestures", None)
        if (not self.enabled or not self.original or self.saved is not None
                or self._gesture_waiting or c._closed or not c.ready or not c._utterance_id or c.stroke_mode
                or c._input_preparation or c._begin_origin or c._awaiting_settlement()
                or c._view.get("has_composition")
                or c._view.get("phase") not in {"dictated", "edited", "undone"}
                or identity != (c._epoch, c._utterance_id, c._view.get("revision", 0))
                or not self.origin or c._foreground_identity() != self.origin
                or getattr(gestures, "_pending", None) is not None
                or getattr(owner, "_inline_requests", None)):
            return
        self.saved = dict(token=uuid.uuid4().hex, epoch=c._epoch,
                          utterance_id=c._utterance_id, client_id=c._client_id,
                          view=dict(c._view), session_id=c._session_id,
                          sessions=dict(c._utterance_sessions))
        self.stage = "suspending"
        self._timer.start(1500)
        if not c._send("suspend_input", token=self.saved["token"], revision=identity[2]):
            self.discard()

    def handle(self, message):
        kind = message.get("type")
        if kind in {"connected", "disconnected"}:
            self.discard("foreground_or_connection_changed")
            return False
        saved = self.saved
        if saved is None or message.get("epoch") != saved["epoch"]:
            return kind in {"input_suspended", "input_resumed"}
        if kind == "input_suspended":
            if self.stage != "suspending" or message.get("token") != saved["token"]:
                return True
            if not message.get("success"):
                self.discard()
                self.inline._bridge.send({"type": "ping"})
                return True
            self._idle()
            if self.inline._foreground_identity() != self.origin:
                self.discard()
                return True
            self.stage = "returning"
            if message.get("restorable") is False:
                self.saved = None
                self.stage = "returning_final"
            helper = self.inline._source_switch_factory(self)
            self.helper = helper
            helper.event.connect(lambda event: self._returned(helper, event))
            try:
                helper.restore(self.origin, self.original, BUNDLE_ID + ".dictation")
            except Exception:
                self._returned(helper, {"event": "error"})
            return True
        if kind == "input_resumed":
            if (self.stage != "resuming" or message.get("token") != saved["token"]
                    or self.request is None):
                return True
            state = message.get("state")
            if self.request.cancelled.is_set() or time.monotonic() >= self.request.deadline:
                self.finish("input_method_resume_timeout")
                return True
            if self.inline._foreground_identity() != self.origin:
                self.finish("foreground_or_connection_changed")
                return True
            if message.get("success") is not True:
                self.finish(native_resume_reason(message.get("reason") or message.get("error")))
                return True
            if (not isinstance(state, dict) or not state.get("ready") or not state.get("client_id")
                    or state.get("utterance_id") != saved["utterance_id"]):
                self.finish("input_method_resume_unverified")
                return True
            c = self.inline
            c._client_id = str(state["client_id"])
            c._utterance_id = saved["utterance_id"]
            c._view = dict(state)
            c._session_id = saved["session_id"]
            c._utterance_sessions = saved["sessions"]
            c._sentence_origin = self.origin
            self.saved = None
            self.stage = ""
            self._gesture_waiting = True
            # Expired/dropped gestures must also release the borrowed source.
            self._gesture_timer.start(1100)
            self._finish_request(None)
            c.changed.emit()
            return True
        if kind == "state" and self.stage in {"suspending", "returning", "selecting", "resuming"}:
            if (self.stage in {"suspending", "returning"} and message.get("ready")
                    and message.get("client_id") != saved["client_id"]):
                self.discard()
                return False
            if self.stage == "selecting" and self.helper is None and message.get("ready"):
                self._resume_native(message)
            return True
        if kind == "interrupted" and message.get("utterance_id") == saved["utterance_id"]:
            return True
        return False

    def _idle(self):
        c = self.inline
        c._utterance_id = c._client_id = ""
        c._session_id = 0
        c._view = {"phase": "idle", "ready": False,
                   "application": (self.saved or {}).get("view", {}).get("application", "")}
        c.changed.emit()

    def _returned(self, helper, message):
        if self.helper is not helper or self.stage not in {"returning", "returning_final"}:
            return
        final = self.stage == "returning_final"
        self.helper = None
        helper.cancel()
        self._timer.stop()
        self.stage = "" if final else "suspended"
        if final:
            self.original = ""
            self.origin = None
        self.inline._diagnose("input_source_return", reason=str(message.get("reason") or message.get("event")))

    def finish(self, failure="input_method_resume_session_lost"):
        """A sent/replaced sentence no longer needs a resumable snapshot."""
        original, origin = self.original, self.origin
        self.discard(failure)
        if not self.enabled or not original or not origin or self.inline._foreground_identity() != origin:
            return
        self.original, self.origin = original, origin
        self.stage = "returning_final"
        self._timer.start(1500)
        helper = self.inline._source_switch_factory(self)
        self.helper = helper
        helper.event.connect(lambda message: self._returned(helper, message))
        try:
            helper.restore(origin, original, BUNDLE_ID + ".dictation")
        except Exception:
            self._returned(helper, {"event": "error"})

    def resume_gesture_failure(self):
        saved = self.saved
        if not self.enabled or saved is None:
            return None
        # Ring/Agent gesture workers wait; the GUI services selection and IPC.
        if QThread.currentThread() == self.thread():
            return "input_method_not_ready"
        request = _ResumeRequest(saved["token"], time.monotonic() + 1.5)
        self._resumeRequested.emit(request)
        if not request.done.wait(max(0, request.deadline - time.monotonic())):
            request.cancelled.set()
            self._resumeCancelled.emit(request)
            return "input_method_resume_timeout"
        return request.failure

    def _resume(self, request):
        if request.cancelled.is_set() or time.monotonic() >= request.deadline:
            request.failure = "input_method_resume_timeout"
            request.done.set()
            return
        if (not self.enabled or not self.saved or request.token != self.saved["token"]
                or self.request is not None):
            request.done.set()
            return
        if self.inline._foreground_identity() != self.origin:
            request.failure = "foreground_or_connection_changed"
            request.done.set()
            return
        if self.stage in {"suspending", "returning"}:
            QTimer.singleShot(20, lambda: self._resume(request))
            return
        self.request = request
        self.stage = "selecting"
        self._recovering = False
        self._timer.start(max(1, int((request.deadline - time.monotonic()) * 1000)))
        self._select()

    def _select(self):
        helper = self.inline._source_switch_factory(self)
        self.helper = helper
        helper.event.connect(lambda message: self._selected(helper, message))
        try:
            (helper.recover if self._recovering else helper.start)(self.origin)
        except Exception:
            self.finish("input_method_activation_failed")

    def _selected(self, helper, message):
        if self.helper is not helper or self.stage != "selecting":
            return
        self.helper = None
        helper.cancel()
        if self.request is None or self.request.cancelled.is_set() or time.monotonic() >= self.request.deadline:
            self.finish("input_method_resume_timeout")
            return
        if message.get("event") != "selected":
            self.finish("input_method_resume_timeout" if message.get("error") == "输入法切换超时，请稍后重试"
                        else "input_method_activation_failed")
            return
        if self.inline._foreground_identity() != self.origin:
            self.finish("foreground_or_connection_changed")
            return
        previous = message.get("previous_source")
        if self._external(previous):
            self.original = previous
        if message.get("already_selected") is True and not message.get("focus_restored") and not self._recovering:
            self._recovering = True
            self._select()
            return
        self._poll.start()
        self._ping()

    def _ping(self):
        if self.stage != "selecting" or not self.saved or self.helper is not None:
            return
        if self.inline._foreground_identity() != self.origin:
            self.discard("foreground_or_connection_changed")
            return
        self.inline._bridge.send({"type": "ping", "request_id": "resume:" + self.saved["token"],
                                  "capture_context": False})

    def _resume_native(self, state):
        c, saved = self.inline, self.saved
        if (not saved or not self.request or self.request.cancelled.is_set()
                or state.get("request_id") != "resume:" + saved["token"]
                or not state.get("client_id") or state.get("application") != self.origin[0]
                or c._foreground_identity() != self.origin):
            return
        self._poll.stop()
        self.stage = "resuming"
        c._seq += 1
        if not c._bridge.send({"type": "resume_input", "client_id": state["client_id"],
                               "utterance_id": saved["utterance_id"], "seq": c._seq,
                               "token": saved["token"]}):
            self.finish("foreground_or_connection_changed")

    def gesture_finished(self):
        if self._gesture_waiting:
            self._gestureFinished.emit()

    def _finish_gesture(self):
        if self._gesture_waiting:
            self._gesture_waiting = False
            self._gesture_timer.stop()
            self.schedule()

    def _finish_request(self, failure):
        self._timer.stop()
        self._poll.stop()
        if self.helper is not None:
            helper, self.helper = self.helper, None
            helper.cancel()
        request, self.request = self.request, None
        if request is not None:
            request.failure = failure
            request.done.set()

    def _cancel_resume(self, request):
        if self.request is request:
            self.finish("input_method_resume_timeout")

    def _timed_out(self):
        if self.stage in {"selecting", "resuming"}:
            self.finish("input_method_resume_timeout")
        else:
            self.discard()

    def discard(self, failure="input_method_resume_session_lost"):
        self._gesture_timer.stop()
        self._gesture_waiting = False
        saved, self.saved = self.saved, None
        self.stage = ""
        self.original = ""
        self.origin = None
        self._finish_request(failure)
        c = self.inline
        if saved and c._bridge and c._epoch == saved["epoch"]:
            c._bridge.send({"type": "discard_suspended_input", "token": saved["token"]})
