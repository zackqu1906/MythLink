import threading
from types import SimpleNamespace

import numpy as np
import pytest

from proximic_ring.voice import app_runtime
from proximic_ring.asr.controller import ProximitySessionController
from proximic_ring.audio.live_audio_settings import AudioConfiguration, LiveAudioSettings
from proximic_ring.gestures.gesture_settings import GestureBindings


@pytest.mark.parametrize('startup_failed', [False, True])
def test_touchpad_suspends_computer_capture_and_remote_pipeline_even_if_startup_fails(monkeypatch, startup_failed):
    state, processed, calls = {}, [], []
    class Ring:
        error = None
        def __init__(self, **kwargs):
            self._touchpad_audio_suspended = threading.Event()
            state['ring'] = self
        def connect(self): pass
        def start_stream(self, **kwargs): pass
        def close(self): pass
    class Mic:
        error = None
        device_name = 'Selected microphone'
        def __init__(self, **kwargs):
            self._capture_paused = threading.Event()
            self.proved = False
            state['mic'] = self
        def open(self): pass
        def close(self): calls.append('close')
        def pause_capture(self):
            self._capture_paused.set()
            calls.append('pause')
        def resume_capture(self):
            self._capture_paused.clear()
            calls.append('resume')
        def read(self, frames):
            assert not self._capture_paused.is_set()
            if not self.proved:
                self.proved = True
                return np.ones(frames, np.float32)
            state['recognition_before_eof'] = enabled.is_set()
            return None
    class Detector:
        def reset(self): pass
        def feed(self, block): pytest.fail('Suspended audio reached the detector')
    class Controller:
        active = False
        def reset(self): pass
        def discard_current(self): calls.append('discard')
        def cancel_pending(self): calls.append('cancel')
        def process(self, *args, **kwargs): processed.append(args)
        def close(self): pass
    def started():
        state['ring']._touchpad_audio_suspended.set()
        state['ring']._pause_external_audio()
        if startup_failed:
            state['ring']._touchpad_audio_suspended.clear()
    def status(message):
        if message == '[ASR] CANCELLED reason=touchpad':
            assert state['mic']._capture_paused.is_set() and not processed
            state['ring']._touchpad_audio_suspended.clear()
    monkeypatch.setattr(app_runtime, 'RingAudioSource', Ring)
    monkeypatch.setattr(app_runtime, 'MicrophoneSource', Mic)
    monkeypatch.setattr(app_runtime, '_build_detector', lambda *_: Detector())
    monkeypatch.setattr(app_runtime, '_build_session_controller', lambda *args, **kwargs: Controller())
    enabled = threading.Event(); enabled.set()
    app_runtime.RecognitionRuntime(app_runtime.RuntimeSettings(
        audio_source='microphone', speech_control_mode='proximity', asr_end_on_tap=False,
        push_to_talk=False,
    )).run(threading.Event(), enabled, on_update=lambda _: None, on_state=status,
           on_connected=lambda: None, on_disconnected=lambda: None, on_started=started)
    assert not processed and 'resume' in calls and calls.index('pause') < calls.index('resume')
    assert state['recognition_before_eof']  # EOF itself closes the recognition gate.
    if not startup_failed:
        assert calls.count('discard') == calls.count('cancel') == 1


def test_runtime_finishes_old_turn_waits_for_commit_and_keeps_gestures_and_session_ids(monkeypatch):
    import proximic_ring.gestures.firmware_gestures as gestures

    stop, recognition = threading.Event(), threading.Event()
    recognition.set()
    changes = LiveAudioSettings(AudioConfiguration())
    changes.allowed.set()
    state, finals, requests, session_ids, forwarded = {}, [], [], [], []
    calls = dict(connect=0, disconnect=0, gate=0, firmware=0)

    def gesture(name="tap"):
        worker = threading.Thread(target=lambda: state["gesture"](SimpleNamespace(name=name)))
        worker.start()
        worker.join(1)
        assert not worker.is_alive()

    def read(frames, source):
        step = state["step"] = state.get("step", 0) + 1
        if step == 1:
            gesture()
        elif step == 2:
            assert state["gate"].active
            changes.select(audio_source="microphone", microphone_device="mic-b")
            changes.allowed.set()  # Even a stale UI idle flag cannot interrupt audio.
        elif step == 3:
            assert source == "ring" and not changes.applying
            gesture()
        elif step == 4:
            assert len(finals) == 1 and not recognition.is_set()
            assert not changes.allowed.is_set() and source == "ring"
            gesture()  # Final result/edit still pending: must not start another utterance.
        elif step == 5:
            assert source == "ring" and not requests
            recognition.set()  # Simulated GUI completion of result + native edit.
            changes.allowed.set()
        elif step == 6:
            assert source == "microphone" and changes.applying
            assert changes.applied.audio_source == "microphone"
            gesture()  # Audio is ready but GUI has not committed metadata yet.
            gesture("circle-clockwise")
            assert not state["gate"].gesture_busy
        elif step == 7:
            changes.acknowledge(requests[0])
        elif step == 8:
            gesture()
        elif step == 9:
            gesture()
        elif step == 10:
            stop.set()
            return None
        return np.full(frames, .1 if source == "ring" else .2, np.float32)

    class Ring:
        error = None

        def __init__(self, **kwargs): pass
        def connect(self): calls["connect"] += 1
        def start_stream(self, **kwargs): pass
        def set_audio_enabled(self, enabled): state["ring_audio"] = enabled
        def read(self, frames): return read(frames, "ring")
        def close(self): calls["disconnect"] += 1

    class Mic:
        error = None
        device_name = "Test"

        def __init__(self, **kwargs): self.proved = False
        def open(self): pass
        def close(self): pass
        def read(self, frames):
            if not self.proved:
                self.proved = True
                return np.full(frames, .2, np.float32)
            return read(frames, "microphone")

    class Firmware:
        error = None
        def __init__(self, *, on_gesture): state["gesture"] = on_gesture
        def start(self): calls["firmware"] += 1
        def close(self): pass
        def submit(self, event): state["gesture"](event)
        def snapshot(self): return {}

    def build(args, detector, **kwargs):
        calls["gate"] += 1

        class Sink:
            def start(self, _audio):
                session_ids.append(len(session_ids) + 1)
                kwargs["raw_session_start_observer"](session_ids[-1])
            def feed(self, _audio): pass
            def end(self, audio): finals.append(audio.copy())
            def close(self): pass
            def abort(self): pass

        state["gate"] = ProximitySessionController(
            Sink(), start_on_gesture=True, end_on_tap=True, pre_roll_s=0,
            min_utterance_s=.02, on_session_end=kwargs["session_end_observer"],
        )
        return state["gate"]

    def configured(request, error):
        assert not error
        requests.append(request)
        # Deliberately defer ACK for two audio blocks.

    monkeypatch.setattr(app_runtime, "RingAudioSource", Ring)
    monkeypatch.setattr(app_runtime, "MicrophoneSource", Mic)
    monkeypatch.setattr(app_runtime, "_build_session_controller", build)
    monkeypatch.setattr(gestures, "FirmwareGestureWorker", Firmware)
    app_runtime.RecognitionRuntime(app_runtime.RuntimeSettings(
        speech_control_mode="gesture", asr_end_on_tap=True, push_to_talk=False,
        # This scenario uses tap to toggle both endpoints, regardless of UI defaults.
        gesture_bindings=GestureBindings(confirm=("tap", ""), end=("tap", "")),
    )).run(stop, recognition, on_update=lambda _: None, on_state=lambda _: None,
           on_connected=lambda: None, on_disconnected=lambda: None, on_started=lambda: None,
           on_session_ended=recognition.clear, on_gesture=forwarded.append,
           audio_settings=changes, on_audio_configured=configured)
    assert calls == dict(connect=1, disconnect=1, gate=1, firmware=1)
    assert session_ids == [1, 2] and len(requests) == 1
    assert len(finals) == 2 and all(np.allclose(a, value) for a, value in zip(finals, (.1, .2)))
    assert [event.name for event in forwarded] == ["circle-clockwise"]
