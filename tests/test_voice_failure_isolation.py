"""Incomplete voice setup must not tear down independent Ring features."""
from dataclasses import replace
from types import SimpleNamespace
import threading
import time

import numpy as np
import pytest

from proximic_ring.audio.live_audio_settings import AudioConfiguration, LiveAudioSettings
from proximic_ring.voice import app_runtime
from proximic_ring.voice.app_runtime import RecognitionRuntime, RuntimeSettings


def eventually(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    assert predicate()


@pytest.fixture
def run_ring(monkeypatch):
    import proximic_ring.gestures.firmware_gestures as firmware

    runs = []

    def start(settings, **overrides):
        stop, enabled, retry, started = (threading.Event() for _ in range(4))
        enabled.set()
        ring = SimpleNamespace(error=None, connects=0, closes=0, reads=0,
                               audio_enabled=True, gestures_active=True, starts=[])
        logs, errors, availability, disconnected, routed, health = ([] for _ in range(6))

        def connect():
            ring.connects += 1

        def close():
            ring.closes += 1

        def read(frames):
            ring.reads += 1
            stop.wait(.01)
            return None if stop.is_set() else np.zeros(frames, dtype=np.float32)

        ring.connect, ring.close, ring.read = connect, close, read
        ring.start_stream = lambda **kw: ring.starts.append(kw)
        ring.set_audio_enabled = lambda value: setattr(ring, "audio_enabled", value)
        monkeypatch.setattr(app_runtime, "RingAudioSource", lambda **kw: ring)

        class Gestures:
            error = None

            def __init__(self, *, on_gesture):
                self.callback = on_gesture

            def start(self): pass
            def close(self): pass
            def submit(self, event): self.callback(event)
            def snapshot(self): return {"worker_alive": True}

        monkeypatch.setattr(firmware, "FirmwareGestureWorker", Gestures)

        def run():
            try:
                RecognitionRuntime(settings).run(
                    stop, enabled, retry_voice_event=retry,
                    on_update=lambda update: None, on_state=logs.append,
                    on_connected=lambda: None,
                    on_disconnected=lambda: disconnected.append(True),
                    on_started=started.set, on_voice_availability=availability.append,
                    gesture_filter=lambda event, busy: routed.append(event.name) or False,
                    on_gesture_health=health.append,
                    **overrides,
                )
            except BaseException as exc:
                errors.append(exc)

        worker = threading.Thread(target=run, daemon=True)
        state = SimpleNamespace(stop=stop, enabled=enabled, retry=retry, started=started,
                                ring=ring, logs=logs, errors=errors, availability=availability,
                                disconnected=disconnected, routed=routed, health=health,
                                worker=worker)
        runs.append(state)
        worker.start()
        assert started.wait(3), errors
        return state

    yield start
    for state in runs:
        state.stop.set()
        state.worker.join(3)
        assert not state.worker.is_alive()
        assert not state.errors
        assert state.ring.closes == 1
        assert state.disconnected == [True]


@pytest.mark.parametrize("failure", ["local_repo", "nano_repo", "detector", "weights", "api_key"])
@pytest.mark.parametrize("audio_source", ["ring", "microphone"])
def test_missing_voice_configuration_keeps_ring_and_gestures(run_ring, monkeypatch, tmp_path, failure, audio_source):
    settings = RuntimeSettings(speech_control_mode="gesture", audio_source=audio_source,
                               desktop_output=False, push_to_talk=False)
    if failure == "local_repo":
        settings = replace(settings, streaming_sensevoice_repo=tmp_path / "absent")
        expected = "目录不存在"
    elif failure == "nano_repo":
        settings = replace(settings, asr_backend="funasr_nano")
        expected = "必须配置"
    elif failure == "detector":
        settings = replace(settings, speech_control_mode="proximity", detector_model=tmp_path / "absent.pt")
        expected = "检测模型不存在"
    elif failure == "weights":
        def missing_weights(*args, **kwargs):
            raise FileNotFoundError("本地语音模型权重缺失")
        monkeypatch.setattr(app_runtime, "_build_session_controller", missing_weights)
        expected = "模型权重缺失"
    else:
        # Exercise the actual online backend's missing-key validation; no network.
        monkeypatch.delenv("VOLC_ASR_API_KEY", raising=False)
        settings = replace(settings, asr_backend="volcengine", asr_model="seedasr-streaming")
        expected = "API Key"

    mic = SimpleNamespace(error=None, close=lambda: None,
                          open=lambda: pytest.fail("voice unavailable must not open MIC"))
    monkeypatch.setattr(app_runtime, "MicrophoneSource", lambda **kw: mic)
    state = run_ring(settings)
    assert len(state.availability) == 1 and expected in state.availability[0]
    assert not state.enabled.is_set() and not state.stop.is_set()
    assert not state.ring.audio_enabled
    assert state.ring.starts == [{"buffer_audio": False}]
    assert state.ring.reads == state.ring.closes == 0
    assert state.worker.is_alive() and not state.disconnected
    state.ring.gesture_observer(SimpleNamespace(name="tap"))
    state.ring.gesture_observer(SimpleNamespace(name="swipe-left"))
    assert state.routed == ["tap", "swipe-left"]
    eventually(lambda: state.health and state.health[-1])


def test_repeated_failure_then_latest_settings_recover_without_reconnecting(run_ring, monkeypatch):
    settings = RuntimeSettings(speech_control_mode="gesture", desktop_output=False, push_to_talk=False)
    latest, attempted = [settings], []
    processed = threading.Event()

    class Controller:
        active = False
        gesture_busy = False
        def reset(self): pass
        def abort(self): pass
        def close(self): pass
        def process(self, *args, **kwargs): processed.set()

    def build(options, *args, **kwargs):
        attempted.append(options.asr[0])
        if options.asr[0] != "volcengine":
            raise FileNotFoundError("本地模型尚未下载")
        assert options.asr_option == ["volcengine.api_key=test-key"]
        return Controller()

    monkeypatch.setattr(app_runtime, "_build_session_controller", build)
    state = run_ring(settings, voice_settings_provider=lambda: latest[0])
    state.retry.set()
    eventually(lambda: len(state.availability) == 2)
    assert state.ring.closes == 0 and state.ring.reads == 0
    latest[0] = replace(settings, asr_backend="volcengine", asr_api_key="test-key")
    state.retry.set()
    eventually(lambda: state.availability[-1] == "")
    state.enabled.set()
    assert processed.wait(3)
    assert attempted == ["streaming_sensevoice", "streaming_sensevoice", "volcengine"]
    assert state.ring.audio_enabled
    assert state.ring.connects == 1 and len(state.ring.starts) == 1
    assert not state.disconnected and not state.stop.is_set()


def test_audio_preferences_can_change_while_voice_is_unavailable(run_ring, monkeypatch, tmp_path):
    settings = RuntimeSettings(speech_control_mode="gesture", desktop_output=False,
                               streaming_sensevoice_repo=tmp_path / "absent", push_to_talk=False)
    mailbox = LiveAudioSettings(AudioConfiguration("ring", "", "gesture"))
    state = run_ring(settings, audio_settings=mailbox)
    mailbox.allowed.set()
    configuration = AudioConfiguration("microphone", "computer-mic", "gesture")
    mailbox.select(audio_source=configuration.audio_source, microphone_device=configuration.microphone_device)
    eventually(lambda: mailbox.applied == configuration)
    assert not state.ring.audio_enabled and state.ring.reads == 0
    assert state.ring.connects == 1 and state.ring.closes == 0


def test_real_device_failure_still_disconnects_when_voice_is_unavailable(run_ring, tmp_path):
    state = run_ring(RuntimeSettings(speech_control_mode="gesture", streaming_sensevoice_repo=tmp_path / "absent"))
    state.ring.error = RuntimeError("BLE connection lost")
    eventually(lambda: state.disconnected)
    state.worker.join(3)
    assert state.stop.is_set() and not state.worker.is_alive()
