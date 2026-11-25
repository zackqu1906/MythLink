"""Exercise the desktop entry point against the R1-PC API without Bluetooth I/O."""

from concurrent.futures import ThreadPoolExecutor
from queue import Queue
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from proximic_ring.audio.microphone import SYSTEM_DEFAULT_MICROPHONE
from proximic_ring.gestures.gesture_settings import GestureBindings
from proximic_ring.voice import app_runtime

sdk = pytest.importorskip("ring_python_sdk_r1")


@pytest.mark.parametrize("microphone_available", [True, False])
def test_r1_connection_uses_sdk_and_pc_microphone(monkeypatch, tmp_path, microphone_available):
    from proximic_ring.audio import r1_session, ring
    import ring_python_sdk

    stop, recognition, started, processed = (threading.Event() for _ in range(4))
    sources, gestures, battery, unavailable = (Queue() for _ in range(4))
    toggles = Queue()
    calls, inputs = [], []
    states, rings = [], []

    class R1:
        def __init__(self, *, on_state, on_battery, on_touchpad_status):
            self.on_state = on_state
            self.on_battery = on_battery
            self.on_touchpad_status = on_touchpad_status
            self.connected = False
            self.touchpad = SimpleNamespace(start=self.start_touchpad, stop=self.stop_touchpad,
                                            status=sdk.TouchpadStatus())
            rings.append(self)

        async def connect(self, target, *, auto_reconnect):
            calls.append(("connect", target, auto_reconnect))
            self.on_state(sdk.ConnectionState.PAIRING)
            self.connected, self.address = True, target
            self.info = sdk.DeviceInfo("1.00.021-test", True)
            return self.info

        async def close(self):
            calls.append(("close",))
            self.connected = False

        async def read_battery(self):
            self.on_battery(sdk.BatteryStatus(72, 0))

        async def set_gesture_mode(self, mode):
            calls.append(("gesture", mode))
            if mode == sdk.GestureMode.NORMAL:
                self.on_gesture(sdk.GestureEvent(1, 0x88, 10, 0.0))

        async def start_touchpad(self):
            self.touchpad.status = sdk.TouchpadStatus(sdk.TouchpadStage.RUNNING, 42, 1)
            self.on_touchpad(sdk.Motion(sdk.EventMeta(42, 1, 1, 10_000), 2, 1.5, -2., "SINGLE", None))
            self.touchpad.status = sdk.TouchpadStatus(sdk.TouchpadStage.STARTING, 42, 2)
            self.on_touchpad_status(self.touchpad.status)
            self.touchpad.status = sdk.TouchpadStatus(sdk.TouchpadStage.RUNNING, 42, 2)
            self.on_touchpad(sdk.Motion(sdk.EventMeta(42, 2, 1, 20_000), 0, 1., -1., "SINGLE", None))

        async def stop_touchpad(self):
            self.touchpad.status = sdk.TouchpadStatus()
            self.on_touchpad_status(self.touchpad.status)

    def microphone(selection):
        inputs.append(selection)

        def open_input():
            if not microphone_available:
                raise RuntimeError("System microphone is unavailable")

        def read(frames):
            return None if stop.wait(.01) else np.zeros(frames, dtype=np.float32)

        return SimpleNamespace(error=None, device_name="PC", open=open_input, read=read, close=lambda: None)

    def model(*args, **kwargs):
        def toggle(_prepare, *, allow_start, allow_end):
            action = "end" if controller.active else "start"
            controller.active = not controller.active
            toggles.put((action, allow_start, allow_end))
            return action

        controller = SimpleNamespace(active=False, close=lambda: None,
                                     process=lambda *args, **kwargs: processed.set(),
                                     request_gesture_toggle=toggle)
        return controller

    monkeypatch.setattr(ring_python_sdk, "RingSession", lambda **kw: pytest.fail("R1 selected the Ringo SDK"))
    monkeypatch.setattr(r1_session, "Ring", R1)
    monkeypatch.setattr(ring, "_INITIAL_MIC_SETTLE_S", 0)
    monkeypatch.setattr(app_runtime, "MicrophoneSource", microphone)
    monkeypatch.setattr(app_runtime, "_build_session_controller", model)
    pinch = ("index-pinch", "")
    settings = app_runtime.RuntimeSettings(ring_selector="r1-identifier", ring_family="R1", data_dir=tmp_path,
                                           speech_control_mode="gesture", desktop_output=False, push_to_talk=False,
                                           asr_end_on_tap=True, gesture_bindings=GestureBindings(confirm=pinch, end=pinch))
    recognition.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(
            app_runtime.RecognitionRuntime(settings).run, stop, recognition,
            on_state=states.append, on_update=lambda _: None, on_connected=lambda: None,
            on_disconnected=lambda: None, on_started=started.set, on_gesture=gestures.put,
            on_battery=lambda *values: battery.put(values), on_ring_source=sources.put,
            on_voice_availability=unavailable.put,
        )
        try:
            assert started.wait(5)
            source = sources.get_nowait()
            assert calls[0] == ("connect", "r1-identifier", False)
            assert "请确认系统蓝牙配对" in states
            assert source.firmware_identity == {"address": "r1-identifier", "family": "R1", "version": "1.00.021-test"}
            assert battery.get(timeout=3) == (72, None, 0)
            assert gestures.get(timeout=3).name == "double-index-pinch"
            assert inputs == [SYSTEM_DEFAULT_MICROPHONE] and not source.audio_enabled
            if microphone_available:
                assert processed.wait(3)
                for sequence, action in enumerate(("start", "end"), 2):
                    rings[0].on_gesture(sdk.GestureEvent(sequence, 8, sequence * 1000, 0.9))
                    assert toggles.get(timeout=3) == (action, True, True)
            else:
                assert unavailable.get_nowait() == "System microphone is unavailable"
                assert not recognition.is_set() and not processed.is_set()

            moves, resets, ended = [], [], []
            source.start_touchpad(protocol="raw2", on_event=resets.append, on_pointer_move=moves.append,
                                  on_stopped=ended.append, duration_s=None).result(3)
            assert [(event.step, event.dx, event.dy, event.session_id) for event in moves] == [
                (3, 1.5, -2., "42"), (1, 1., -1., "42"),
            ]
            assert [(event.state, event.generation) for event in resets] == [("reset", 2)]
            source.stop_touchpad().result(3)
            assert ended == [None] and source.gestures_active and source.error is None
            assert not worker.done()
        finally:
            stop.set()
            worker.result(timeout=8)
    assert calls.count(("close",)) == 1


def test_r1_sparse_motion_reaches_stroke_recognition_and_stationary_taps():
    import time

    from proximic_ring.audio.r1_session import R1Session
    from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
    from proximic_ring.touchpad.stroke.stroke_menu import trial_contact
    from proximic_ring.touchpad.touchpad_strokes import TouchpadStrokeOutput

    ready = threading.Event()
    strokes, taps, ended = Queue(), Queue(), Queue()
    output = TouchpadStrokeOutput(connection=threading.Event(), on_ready=ready.set,
        on_end=lambda reason, error: ended.put(error), on_stroke=strokes.put, on_tap=lambda: None,
        on_tap_contact=taps.put, contact_classifier=trial_contact, raw2=True, r1=True)
    output.start()
    try:
        assert ready.wait(1)
        output.activate()
        adapter = object.__new__(R1Session)
        stream = SimpleNamespace(status=sdk.TouchpadStatus(sdk.TouchpadStage.RUNNING, 7, 1))
        adapter.ring = SimpleNamespace(touchpad=stream)
        adapter._touchpad_event = output.submit
        sequence = iter(range(1, 100))

        def meta():
            return sdk.EventMeta(7, stream.status.generation, next(sequence), time.perf_counter_ns())

        def motion(step, dx, dy=0., episode=None):
            event = sdk.Motion(meta(), step - 1, dx, dy, 'SINGLE', episode)
            adapter._pointer_event(event, output.submit_pointer)

        def edge(phase, episode, start, end=None):
            now = time.perf_counter_ns()
            contact = sdk.ContactRange(start - 1, now, None if end is None else end - 1,
                                       None if end is None else now)
            event = sdk.ContactUpdate(meta(), episode, phase, 'SINGLE', contact)
            adapter._pointer_event(event, output.submit_pointer)

        motion(102, 3.)
        edge('BEGIN', 1, 100)
        for step in (105, 108, 111, 114):
            motion(step, 3., episode=1)
        # Motion received before the confirmed END is trimmed at the edge.
        motion(119, 0., 60., episode=1)
        edge('END', 1, 100, 118)
        points = strokes.get(timeout=2)
        assert len(points) == 18 and points[2] == (3., 0.) and points[-1] == (15., 0.)
        assert StrokeDictionary().recognize(points)['category'] == 'h'

        edge('BEGIN', 2, 130)
        edge('END', 2, 130, 141)
        assert taps.get(timeout=2)['intent_decision']['action'] == 'tap'

        edge('BEGIN', 3, 149)
        adapter._touchpad_status(sdk.TouchpadStatus(sdk.TouchpadStage.STARTING, 7, 2))
        stream.status = sdk.TouchpadStatus(sdk.TouchpadStage.RUNNING, 7, 2)
        edge('END', 3, 149, 160)
        edge('BEGIN', 1, 2)
        edge('END', 1, 2, 12)
        assert taps.get(timeout=2)['start_step'] == 2
        assert taps.empty() and strokes.empty()
    finally:
        output.stop()
        assert ended.get(timeout=2) is None
