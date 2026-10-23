"""Global gestures must keep Qt responsive while AX validates its own window."""
from dataclasses import replace
import threading
import time

import pytest
from PySide6.QtCore import QObject, QCoreApplication, QThread, Signal, Slot, Qt

from test_app_gestures import route
from test_ring_gestures import request, deliver_gestures


@pytest.mark.parametrize("action", ["window_selector", "show_menu", "scroll_up", "scroll_down"])
def test_native_validation_can_wait_for_gui_and_action_returns_to_gui(route, monkeypatch, action):
    c, service, _, backend, _, _, _ = route
    ring = c.ringGestures
    assert ring.setGlobalGestureAction("clench", action)
    backend.target = replace(backend.target, bundle="com.proximic.voice", input_context="nontext")
    gui_thread = QCoreApplication.instance().thread()
    calls, worker_threads = [], []

    class AXResponder(QObject):
        readRequested = Signal(object)

        def __init__(self):
            super().__init__()
            self.readRequested.connect(self.answer, Qt.QueuedConnection)

        @Slot(object)
        def answer(self, answered):
            assert QThread.currentThread() == gui_thread
            answered.set()

    responder = AXResponder()
    def validate(*args, **kwargs):
        worker_threads.append(QThread.currentThread())
        answered = threading.Event()
        responder.readRequested.emit(answered)
        return answered.wait(.5)  # Like AX, this needs the target GUI to reply.
    monkeypatch.setattr(backend, "same_target", validate)
    def dispatched(*args):
        calls.append(QThread.currentThread())
    monkeypatch.setattr(ring._selector, "start", dispatched)
    monkeypatch.setattr(ring._scroll, "start", dispatched)
    monkeypatch.setattr(ring, "showMenu", dispatched)
    assert not request(c, "clench")
    assert worker_threads and all(thread != gui_thread for thread in worker_threads)
    assert calls == [gui_thread]
    assert ring._transition is None


@pytest.mark.parametrize("change", ["settings", "generation", "connection", "disconnect", "busy", "expired", "closed", "target", "exception"])
def test_pending_validation_rechecks_state_and_records_why_it_dropped(route, monkeypatch, change):
    c, service, inline, backend, _, _, _ = route
    ring = c.ringGestures
    backend.target = replace(backend.target, input_context="nontext")
    entered, release = threading.Event(), threading.Event()
    calls, traces = [], []
    monkeypatch.setattr(ring._selector, "start", lambda *args: calls.append(True))
    monkeypatch.setattr(service, "_trace", lambda *args, **kw: traces.append((args, kw)))
    def validate(*args, **kwargs):
        entered.set()
        assert release.wait(1)
        if change == "exception":
            raise TimeoutError("native validation timed out")
        return change != "target"
    monkeypatch.setattr(backend, "same_target", validate)
    request(c, "clench", deliver=False)
    QCoreApplication.processEvents()
    assert entered.wait(.5)
    assert ring._pending_global is not None and ring._transition is not None
    try:
        if change == "settings": service._generation += 1
        if change == "generation": ring._generation += 1
        if change == "connection": c._disconnect_event = threading.Event()
        if change == "disconnect": c._disconnect_event.set()
        if change == "busy": inline._view["phase"] = "listening"
        if change == "expired":
            real_monotonic = time.monotonic
            monkeypatch.setattr(time, "monotonic", lambda: real_monotonic() + 2)
        if change == "closed": ring.close()
    finally:
        release.set()
    deliver_gestures(c)
    assert not calls and ring._transition is None
    expected = {"settings": "settings_changed", "generation": "gesture_generation_changed",
                "connection": "connection_changed", "disconnect": "connection_changed",
                "busy": "speech_busy", "expired": "event_expired", "closed": "controller_closed",
                "target": "target_changed", "exception": "target_validation_failed"}[change]
    assert any(args[1:3] == ("outcome", expected) for args, _ in traces)
