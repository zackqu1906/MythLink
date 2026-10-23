import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication

from proximic_ring.voice import app_runtime
from proximic_ring.gestures.app_gestures import AppBinding, default_profiles, profiles_to_json
from proximic_ring.asr.controller import ProximitySessionController
from proximic_ring.gestures.gesture_settings import GestureBindings, RING_RESERVED_GESTURES, reserve_ring_gestures
from test_app_gestures import route


def deliver_gestures(controller):
    QCoreApplication.processEvents()
    deadline = time.monotonic() + 2
    while controller.ringGestures._pending_global is not None and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(.001)
    assert controller.ringGestures._pending_global is None


def request(controller, name, *, busy=False, deliver=True, connection=None):
    result = controller.ringGestures.filter(SimpleNamespace(name=name), busy,
                                           connection or controller._disconnect_event)
    if deliver:
        deliver_gestures(controller)
    return result


def test_retired_mode_gesture_never_toggles_or_blocks_native_start(route):
    c, _, _, _, sent, _, _ = route
    for _ in range(2):
        assert not request(c, "middle-pinch")
        assert request(c, "double-tap")
    assert not sent and "switch_mode" not in c.ringGestures.globalBindings


@pytest.mark.parametrize("phase", ["starting", "listening", "finishing", "editing", "polishing"])
def test_busy_global_commands_are_discarded_at_recognition_not_replayed(route, phase, monkeypatch):
    c, _, inline, _, sent, _, _ = route
    starts = []
    monkeypatch.setattr(c.ringGestures._selector, "start", lambda *a: starts.append(True))
    inline._view.update(phase=phase)
    request(c, "clench", deliver=False)
    inline._view.update(phase="dictated")
    QCoreApplication.processEvents()
    assert not starts and not sent


def test_audio_preparation_keeps_start_end_but_suppresses_global_actions(route):
    c, _, _, _, _, _, _ = route
    assert request(c, "tap", busy=True)
    assert not request(c, "clench", busy=True)


def test_disabled_recommendation_input_keeps_firmware_worker_alive(route):
    from proximic_ring.gestures.firmware_gestures import FirmwareGestureWorker

    c, _, _, _, sent, _, _ = route
    assert c.agent.recommendations.input is None
    assert not c.agent.owns_input_focus()
    c._gesture_bindings = GestureBindings(confirm=("snap", "double-tap"), end=("tap", ""))
    accepted = []

    def dispatch(event):
        accepted.append((event.name, c.ringGestures.filter(
            event, True, c._disconnect_event)))

    worker = FirmwareGestureWorker(on_gesture=dispatch)
    worker.start()
    try:
        # Exercise immediate delivery and the pending single-tap timeout from
        # the reported traceback, then prove the next event still arrives.
        for seq, (class_id, name) in enumerate(((6, "snap"), (5, "tap"), (6, "snap")), 1):
            worker.submit(SimpleNamespace(kind="trigger", protocol_version=2,
                class_id=class_id, name=name, confidence=.99, seq=seq,
                center_uptime_ms=seq * 1000, uptime_ms=seq * 1000))
            deadline = time.monotonic() + 2
            while (worker.snapshot()["processed_events"] < seq
                   and worker.error is None and time.monotonic() < deadline):
                time.sleep(.005)
            assert worker.error is None
            assert worker.snapshot()["processed_events"] == seq
            assert worker._thread.is_alive()
        assert accepted == [("snap", True), ("tap", True), ("snap", True)]
        assert not c._disconnect_event.is_set() and not sent
    finally:
        worker.close()


@pytest.mark.parametrize("owner_focus", ["none", "agent", "recommendation"])
def test_optional_editor_focus_preserves_endpoint_arbitration(route, monkeypatch, owner_focus):
    c, service, _, _, sent, _, _ = route
    from proximic_ring.agent_workflow import pipeline_policy
    monkeypatch.setattr(pipeline_policy, "LEGACY_WORKFLOW_ENABLED", owner_focus == "recommendation")
    monkeypatch.setattr(c.agent, "owns_input_focus", lambda: owner_focus == "agent")
    if owner_focus == "recommendation":
        c.agent.recommendations.input = SimpleNamespace(
            owns_input_focus=lambda: True, filter_gesture=lambda *args: False,
            close=lambda: None)
    routed = []
    envelope = service.envelope
    monkeypatch.setattr(service, "envelope", lambda *args, **kwargs:
                        routed.append(True) or envelope(*args, **kwargs))
    assert request(c, "tap")
    assert bool(routed) is (owner_focus == "none")
    assert not sent


def test_lock_consumes_start_end_and_app_gestures_before_target_reads(route, monkeypatch):
    c, apps, _, _, sent, _, _ = route
    c._proximity._gesture_state = (True, "")
    c._proximity.changed.emit()
    monkeypatch.setattr(c.ringGestures, "session_blocked", lambda: True)
    monkeypatch.setattr(apps.backend, "capture", lambda **kw: pytest.fail("locked capture"))
    for name in ("tap", "double-tap", "swipe-left", "swipe-up", "clench", "index-pinch"):
        assert not request(c, name)
    assert not sent


@pytest.mark.parametrize("app_mapping", [False, True])
@pytest.mark.parametrize("voice_override", [False, True])
def test_real_runtime_gates_confirm_before_audio_and_keeps_input_double_tap_endpoint(route, monkeypatch, app_mapping, voice_override):
    import proximic_ring.gestures.firmware_gestures as host
    c, _, inline, _, _, _, _ = route
    if app_mapping:
        from test_application_menus import ACTION, configure
        catalog = configure(c.appGestures, monkeypatch, "com.openai.codex")
        assert catalog.setBinding("com.openai.codex", "circle-clockwise", ACTION["id"])
    if voice_override:
        from test_application_menus import ACTION, configure
        catalog = configure(c.appGestures, monkeypatch, "com.openai.codex")
        if not app_mapping:
            catalog.clearApplicationBindings("com.openai.codex")
        assert catalog.setBinding("com.openai.codex", "swipe-left", ACTION["id"])
    state, started, finals, actions, shown = {}, [], [], [], []
    detected = []
    def feedback(event):
        detected.append(event.name)
        raise RuntimeError("optional feedback unavailable")
    c.ringGestures.showRequested.connect(lambda *args: shown.append(args))
    recognition, disconnect = threading.Event(), c._disconnect_event
    recognition.set()

    def gesture(name):
        thread = threading.Thread(target=lambda: state["callback"](SimpleNamespace(name=name)))
        thread.start()
        thread.join(1)
        assert not thread.is_alive()

    class Dispatcher:
        def __init__(self, *, on_gesture):
            state["callback"] = on_gesture
        error = None
        def start(self): pass
        def submit(self, event): state["callback"](event)
        def close(self): pass
        def snapshot(self): return {}

    class Sink:
        def start(self, audio): started.append(audio)
        def feed(self, audio): pass
        def end(self, audio): finals.append(audio)
        def abort(self): pass
        def close(self): pass

    class Source:
        error = None
        def __init__(self, **kwargs): self.reads = 0
        def connect(self): pass
        def start_stream(self, **kwargs): pass
        def close(self): pass
        def read(self, frames):
            self.reads += 1
            gate = state["gate"]
            if self.reads == 1:
                # IMK confirms a text client even when AX exposes only a page.
                from dataclasses import replace
                service = c.appGestures
                service.backend.target = replace(service.backend.target, input_context="nontext", focus="page")
                from proximic_ring.voice.ime.focus_probe import InputFocus
                monkeypatch.setattr(c._inline_input, 'capture_start_focus', lambda: InputFocus('text',
                    ('e', 'c', '', service.backend.target.bundle, service.backend.target.pid, (0, 0))))
                gesture("middle-pinch")  # Retired mode switch does nothing.
                gesture("double-tap")
                assert gate.gesture_busy and not gate.active
                QCoreApplication.processEvents()
            elif self.reads == 2:
                assert gate.active and len(started) == 1
                gesture("circle-clockwise")  # Busy audio never navigates.
                gesture("tap")
                QCoreApplication.processEvents()
            else:
                assert len(finals) == 1 and not gate.active
                disconnect.set()
                return None
            return np.ones(frames, dtype=np.float32) * .1

    def build(args, detector, **kwargs):
        state["gate"] = ProximitySessionController(
            Sink(), start_on_gesture=True, min_utterance_s=.02,
            on_session_end=recognition.clear)
        return state["gate"]

    monkeypatch.setattr(host, "FirmwareGestureWorker", Dispatcher)
    monkeypatch.setattr(app_runtime, "RingAudioSource", Source)
    monkeypatch.setattr(app_runtime, "_build_session_controller", build)
    def route_after_feedback(event, busy):
        # Scene/focus IO can block here. Recognition must already be published,
        # including when its optional observer raises an exception.
        assert detected[-1] == event.name
        return c.ringGestures.filter(event, busy, disconnect)
    app_runtime.RecognitionRuntime(app_runtime.RuntimeSettings(speech_control_mode="gesture")).run(
        disconnect, recognition, on_update=lambda _: None, on_state=lambda _: None,
        on_connected=lambda: None, on_disconnected=lambda: None, on_started=lambda: None,
        on_gesture=actions.append,
        on_gesture_detected=feedback,
        gesture_filter=route_after_feedback)
    assert actions == []  # Focus routing queues non-endpoint gestures itself.
    assert len(finals) == len(started) == 1
    assert detected == ["middle-pinch", "double-tap", "circle-clockwise", "tap"]
