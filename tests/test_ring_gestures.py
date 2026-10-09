import threading
from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication

from proximic_ring import app_runtime
from proximic_ring.app_gestures import AppBinding, default_profiles, profiles_to_json
from proximic_ring.asr.controller import ProximitySessionController
from proximic_ring.gesture_settings import GestureBindings, RING_RESERVED_GESTURES, reserve_ring_gestures
from test_app_gestures import route


def request(controller, name, *, busy=False, deliver=True, connection=None):
    result = controller.ringGestures.filter(SimpleNamespace(name=name), busy,
                                           connection or controller._disconnect_event)
    if deliver:
        QCoreApplication.processEvents()
    return result


def test_modes_gate_legacy_actions_and_preview_does_not_change_mode(route):
    c, apps, _, _, sent, _, _ = route
    service = c.ringGestures
    shown = []
    service.showRequested.connect(lambda *args: shown.append(args))
    assert service.mode == "input"
    request(c, "index-pinch")
    request(c, "index-pinch")
    assert shown == [("input", ""), ("input", "")]
    queued = service.envelope(SimpleNamespace(name="circle-clockwise"))
    request(c, "middle-pinch", deliver=False)
    assert not request(c, "tap", deliver=False)  # Before Qt commits the switch.
    assert not service.accepts(queued)
    QCoreApplication.processEvents()
    assert service.mode == "operation" and shown[-1] == ("operation", "")
    for name in ("tap", "snap", "swipe-left", "swipe-right", "circle-clockwise", "circle-counterclockwise"):
        assert not request(c, name)
    c._apply_gesture(queued, c._disconnect_event)
    assert not sent
    service.showMenu()
    assert service.mode == "operation"
    request(c, "middle-pinch")
    assert service.mode == "input" and request(c, "tap")
    c._apply_gesture(queued, c._disconnect_event)  # Old actions stay stale after switching back.
    assert not sent
    fresh = service.envelope(SimpleNamespace(name="circle-clockwise"))
    c._apply_gesture(fresh, c._disconnect_event)
    assert sent == [("codex", "Cmd+Shift+]")]
    assert all(not rows["new"].enabled and rows["new"].gesture == ""
               for app, rows in apps._profiles.items() if app != "wechat")


@pytest.mark.parametrize("phase", ["starting", "listening", "finishing", "editing"])
@pytest.mark.parametrize("name", ["middle-pinch", "clench", "swipe-down"])
def test_busy_commands_are_discarded_at_recognition_not_replayed(route, phase, name):
    c, _, inline, _, sent, _, _ = route
    shown = []
    c.ringGestures.showRequested.connect(lambda *args: shown.append(args))
    inline._view.update(phase=phase)
    request(c, name, deliver=False)
    inline._view.update(phase="dictated")  # Finishes before Qt delivery.
    QCoreApplication.processEvents()
    assert c.ringGestures.mode == "input"
    assert shown[-1] == ("input", "请先结束本句")
    inline.settled.emit("dictated", "done")
    QCoreApplication.processEvents()
    assert c.ringGestures.mode == "input" and not sent


def test_audio_preparation_and_gui_writeback_both_block_switch_but_allow_hints(route):
    c, _, inline, _, _, _, _ = route
    shown = []
    c.ringGestures.showRequested.connect(lambda *args: shown.append(args))
    request(c, "middle-pinch", busy=True)
    assert shown[-1] == ("input", "请先结束本句")
    request(c, "index-pinch", busy=True)
    assert shown[-1] == ("input", "")
    request(c, "middle-pinch", deliver=False)
    inline._view.update(awaiting_readback=True)
    QCoreApplication.processEvents()
    assert c.ringGestures.mode == "input" and shown[-1][1] == "请先结束本句"
    inline._view.update(awaiting_readback=False, phase="edited")
    request(c, "middle-pinch")
    assert c.ringGestures.mode == "operation"


def test_disconnect_stale_and_expired_requests_cannot_change_mode(route, monkeypatch):
    from proximic_ring.ui import ring_gesture_controller as module
    c, _, _, _, _, _, _ = route
    hidden = []
    c.ringGestures.hideRequested.connect(lambda: hidden.append(True))
    token = c._disconnect_event
    request(c, "middle-pinch", deliver=False)
    token.set()
    c._connected = False
    c.connectedChanged.emit()
    c._disconnect_event = threading.Event()
    c._connected = True
    c.connectedChanged.emit()
    QCoreApplication.processEvents()
    assert c.ringGestures.mode == "input" and hidden
    assert not request(c, "middle-pinch", connection=token)
    request(c, "middle-pinch", deliver=False)
    now = module.time.monotonic()
    monkeypatch.setattr(module.time, "monotonic", lambda: now + 2)
    QCoreApplication.processEvents()
    assert c.ringGestures.mode == "input"
    assert request(c, "tap")  # A discarded request released the transition gate.


def test_saved_bindings_release_global_gestures_and_preserve_custom_actions(route):
    from proximic_ring.ui.app_gesture_controller import AppGestureController
    c, _, _, _, _, _, _ = route
    old = GestureBindings(confirm=("middle-pinch", "index-pinch"), undo=("tap", "snap"),
                          switch_mode=("swipe-down", "swipe-right"))
    assert reserve_ring_gestures(old) == GestureBindings(
        undo=("", "snap"), switch_mode=("", "swipe-right"))
    profiles = default_profiles()
    profiles["codex"]["new"] = AppBinding("middle-pinch", "Cmd+Alt+N")
    profiles["workbuddy"]["new"] = AppBinding("snap", "Cmd+N")
    c._settings.setValue("gestures/appProfiles", profiles_to_json(profiles))
    c._settings.setValue("gestures/inputSourceGesture", "index-pinch")
    restored = AppGestureController(c)
    assert restored._profiles["codex"]["new"] == AppBinding("", "Cmd+Alt+N", False)
    assert restored._profiles["workbuddy"]["new"] == profiles["workbuddy"]["new"]
    assert restored.inputSourceGesture == ""
    for gesture in RING_RESERVED_GESTURES:
        assert not c.setGestureBinding("confirm", 1, gesture)
        assert not restored.setBinding("codex", "new", gesture, "Cmd+N", True)
        assert not restored.setInputSourceGesture(gesture)
    restored.resetApp("codex")
    assert restored._profiles["codex"]["new"] == AppBinding("", "Cmd+N", False)


@pytest.mark.parametrize("app_mapping", [False, True])
@pytest.mark.parametrize("voice_override", [False, True])
def test_real_runtime_gates_confirm_before_audio_and_keeps_input_tap_endpoint(route, monkeypatch, app_mapping, voice_override):
    import proximic_ring.firmware_gestures as host
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
                gesture("middle-pinch")
                gesture("tap")  # Pending GUI transition also blocks the audio endpoint.
                QCoreApplication.processEvents()
                assert c.ringGestures.mode == "operation" and not gate.gesture_busy
                for name in ("tap", "swipe-left", "swipe-right", "circle-clockwise"):
                    gesture(name)
                assert not gate.gesture_busy
                assert [event.name for event in actions] == (["circle-clockwise"] if app_mapping else [])
            elif self.reads == 2:
                gesture("middle-pinch")
                QCoreApplication.processEvents()
                assert c.ringGestures.mode == "input"
                if voice_override:
                    gesture("tap")
                    assert not gate.gesture_busy and not gate.active
                    assert catalog.setBinding("com.openai.codex", "swipe-left", "")
                gesture("tap")
                assert gate.gesture_busy and not gate.active
                gesture("middle-pinch")  # Start queued, no GUI listening status yet.
                gesture("index-pinch")
                QCoreApplication.processEvents()
                assert c.ringGestures.mode == "input"
                assert ("input", "请先结束本句") in shown
            elif self.reads == 3:
                assert gate.active and len(started) == 1
                gesture("swipe-right")
                gesture("tap")
                gesture("middle-pinch")
                QCoreApplication.processEvents()
                assert c.ringGestures.mode == "input"
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
    app_runtime.RecognitionRuntime(app_runtime.RuntimeSettings(speech_control_mode="gesture")).run(
        disconnect, recognition, on_update=lambda _: None, on_state=lambda _: None,
        on_connected=lambda: None, on_disconnected=lambda: None, on_started=lambda: None,
        on_gesture=actions.append,
        on_gesture_detected=feedback,
        gesture_filter=lambda event, busy: c.ringGestures.filter(event, busy, disconnect))
    assert [event.name for event in actions] == (["circle-clockwise"] if app_mapping else []) + ["swipe-right"]
    assert len(finals) == len(started) == 1
    assert detected == (["middle-pinch", "tap", "tap", "swipe-left", "swipe-right", "circle-clockwise",
                         "middle-pinch"] + (["tap"] if voice_override else [])
                        + ["tap", "middle-pinch", "index-pinch", "swipe-right", "tap", "middle-pinch"])


def test_lock_consumes_gestures_before_voice_focus_scroll_and_menu(route):
    c, _, _, _, sent, _, _ = route
    ring = c.ringGestures
    shown = []
    ring.showRequested.connect(lambda *args: shown.append(args))
    queued = ring.envelope(SimpleNamespace(name="swipe-up"))
    request(c, "middle-pinch", deliver=False)
    c._proximity._gesture_state = (True, "")
    c._proximity.changed.emit()
    for name in ["snap", "swipe-up", "tap", "swipe-down", "index-pinch", "clench", "middle-pinch"]:
        assert not request(c, name)
    ring.showMenu()
    assert not ring.accepts(queued)
    assert not shown and not sent and ring.mode == "input"
    c._proximity._gesture_state = (False, "")
    c._proximity.changed.emit()
    assert not ring.accepts(queued)  # Pre-lock actions remain stale after unlock.
    assert request(c, "tap")
