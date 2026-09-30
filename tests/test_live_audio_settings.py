import threading
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from proximic_ring.app_runtime import RuntimeSettings
from proximic_ring.audio.reconfiguration import AudioInputPipeline
from proximic_ring.live_audio_settings import AudioConfiguration, LiveAudioSettings
from test_asr_controller import StreamingRecorder, make_gate


def test_mailbox_coalesces_choices_and_waits_for_gui_ack():
    initial = AudioConfiguration()
    changes = LiveAudioSettings(initial)
    changes.select(audio_source="microphone")
    changes.select(microphone_device="device-b")
    assert changes.claim() is None
    changes.allowed.set()
    first = changes.claim()
    assert first.configuration == AudioConfiguration("microphone", "device-b", "gesture")
    changes.select(audio_source="ring")  # Reverse a selection while IO is running.
    assert changes.claim() is None
    changes.complete(first, success=True)
    assert changes.applied.audio_source == "microphone" and changes.applying
    assert changes.claim() is None
    changes.acknowledge(first)
    second = changes.claim()
    assert second.configuration.audio_source == "ring"
    changes.complete(second, success=True)
    changes.acknowledge(second)
    assert not changes.pending


def test_failed_old_request_preserves_newer_choice_and_last_failure_reverts():
    changes = LiveAudioSettings(AudioConfiguration())
    changes.allowed.set()
    changes.select(audio_source="microphone", microphone_device="missing")
    first = changes.claim()
    changes.select(microphone_device="present")
    changes.complete(first, success=False)
    changes.acknowledge(first)
    second = changes.claim()
    assert second.configuration.microphone_device == "present"
    changes.complete(second, success=False)
    changes.acknowledge(second)
    assert changes.desired == changes.applied == AudioConfiguration()
    changes.close()
    changes.allowed.set()
    changes.select(audio_source="microphone")
    assert changes.claim() is None and not changes.complete(second, success=True)


class Ring:
    error = None

    def __init__(self):
        self.enabled = True
        self.calls = []
        self.fail_next_start = False

    def set_audio_enabled(self, enabled):
        self.calls.append(enabled)
        if enabled and self.fail_next_start:
            self.fail_next_start = False
            raise RuntimeError("Ring 没有音频")
        self.enabled = enabled


class Microphone:
    error = None
    device_name = "Test microphone"

    def __init__(self, *, selection):
        self.selection = selection
        self.closed = False
        self.reads = 0

    def open(self):
        if self.selection == "missing":
            raise RuntimeError("未找到所选麦克风")

    def read(self, frames):
        self.reads += 1
        if self.selection == "silent":
            raise RuntimeError("麦克风无音频")
        return np.full(frames, 0.2, dtype=np.float32)

    def close(self):
        self.closed = True


class Detector:
    config = SimpleNamespace(stage2_delay_s=0.3)

    def reset(self):
        pass


@pytest.fixture
def pipeline():
    sink = StreamingRecorder()
    gate = make_gate(sink, start_on_gesture=True, stage1_inactivity_s=1.25)
    ring = Ring()
    microphones = []
    detector_loads = []

    def create_microphone(**kwargs):
        microphone = Microphone(**kwargs)
        microphones.append(microphone)
        return microphone

    def create_detector(args):
        detector_loads.append(args)
        return Detector()

    pipeline = AudioInputPipeline(ring, None, None, gate,
        RuntimeSettings(speech_control_mode="gesture", asr_end_on_tap=True, push_to_talk=False),
        microphone_factory=create_microphone, detector_factory=create_detector, on_state=lambda _: None)
    yield pipeline, ring, gate, sink, microphones, detector_loads
    pipeline.close()


def test_repeated_source_and_mode_changes_keep_gate_and_sink(pipeline):
    pipeline, ring, gate, sink, microphones, loads = pipeline
    original_sink = gate.sink
    stop = threading.Event()
    for _ in range(3):
        assert not pipeline.apply(AudioConfiguration("microphone", "mic-a", "proximity"), stop)
        assert not gate.start_on_gesture and gate.end_on_tap
        assert gate.pre_roll_samples == 16000 and pipeline.detector is not None
        assert not ring.enabled and pipeline.audio_source.selection == "mic-a"
        assert not pipeline.apply(AudioConfiguration("microphone", "mic-b", "gesture"), stop)
        assert gate.start_on_gesture and gate.pre_roll_samples == 0
        assert not pipeline.apply(AudioConfiguration("ring", "mic-b", "gesture"), stop)
        assert pipeline.audio_source is ring and ring.enabled
        assert gate.sink is original_sink
    assert len(loads) == 1  # Detection model is reused, not repeatedly loaded.
    assert all(mic.closed for mic in microphones)
    assert not sink.started and not sink.ended


@pytest.mark.parametrize("selection", ["missing", "silent"])
def test_microphone_failure_restores_ring_without_closing_asr(pipeline, selection):
    pipeline, ring, gate, sink, microphones, _ = pipeline
    error = pipeline.apply(AudioConfiguration("microphone", selection, "proximity"), threading.Event())
    assert error and pipeline.configuration == AudioConfiguration()
    assert ring.enabled and gate.start_on_gesture and pipeline.detector is None
    assert all(mic.closed for mic in microphones) and not sink.ended


def test_ring_start_failure_recovers_selected_computer_microphone(pipeline):
    pipeline, ring, _, _, microphones, _ = pipeline
    stop = threading.Event()
    original = AudioConfiguration("microphone", "mic-a", "gesture")
    assert not pipeline.apply(original, stop)
    ring.fail_next_start = True
    assert "Ring" in pipeline.apply(AudioConfiguration(), stop)
    assert pipeline.configuration == original and not ring.enabled
    assert pipeline.microphone.selection == "mic-a" and pipeline.microphone.reads == 1
    assert microphones[0].closed


def test_missing_detector_restores_previous_microphone(pipeline):
    pipeline, ring, gate, _, _, _ = pipeline
    stop = threading.Event()
    previous = AudioConfiguration("microphone", "mic-a", "gesture")
    assert not pipeline.apply(previous, stop)
    pipeline._detector_factory = lambda _: (_ for _ in ()).throw(FileNotFoundError("检测模型缺失"))
    assert "模型缺失" in pipeline.apply(AudioConfiguration("ring", "mic-a", "proximity"), stop)
    assert pipeline.configuration == previous and gate.start_on_gesture and not ring.enabled


def test_queued_gesture_cannot_be_discarded_by_reconfiguration(pipeline):
    pipeline, ring, gate, _, _, _ = pipeline
    assert gate.request_gesture_toggle() == "start"
    with pytest.raises(RuntimeError, match="尚未结束"):
        pipeline.apply(AudioConfiguration("microphone", "mic-a", "gesture"), threading.Event())
    assert not ring.calls and gate.gesture_busy


def test_disconnect_during_model_load_does_not_open_replacement_audio(pipeline):
    pipeline, ring, _, _, microphones, _ = pipeline
    stop = threading.Event()

    def load(_):
        stop.set()
        return Detector()

    pipeline._detector_factory = load
    with pytest.raises(RuntimeError, match="取消"):
        pipeline.apply(AudioConfiguration("microphone", "mic-a", "proximity"), stop)
    assert ring.calls == [False] and not microphones


def test_failed_recovery_reports_failure_instead_of_claiming_rollback(pipeline):
    pipeline, ring, _, _, _, _ = pipeline
    ring.fail_next_start = True
    with pytest.raises(RuntimeError, match="无法恢复"):
        pipeline.apply(AudioConfiguration("microphone", "missing", "gesture"), threading.Event())


def test_blank_exception_still_reports_failed_change(pipeline):
    pipeline, ring, gate, _, _, _ = pipeline
    pipeline._detector_factory = lambda _: (_ for _ in ()).throw(RuntimeError())
    error = pipeline.apply(AudioConfiguration("ring", "", "proximity"), threading.Event())
    assert error and ring.enabled and gate.start_on_gesture
    assert pipeline.configuration == AudioConfiguration()


@pytest.mark.parametrize("initial_mode", ["gesture", "proximity"])
def test_windows_hold_to_talk_hook_follows_live_control_mode(pipeline, monkeypatch, initial_mode):
    from proximic_ring import push_to_talk

    pipeline, _, gate, _, _, _ = pipeline
    hooks, reported = [], []

    class Hotkey:
        def __init__(self, *, on_error, on_change):
            self.state = push_to_talk.PushToTalkState(on_change=on_change)
            self.closed = False
            hooks.append(self)

        def is_active(self):
            return self.state.is_active()

        def close(self):
            self.closed = True
            self.state.set_active(False)

    monkeypatch.setattr(push_to_talk, "WindowsPushToTalkHotkey", Hotkey)
    pipeline.settings = replace(pipeline.settings, push_to_talk=True, speech_control_mode=initial_mode)
    pipeline.configuration = replace(pipeline.configuration, speech_control_mode=initial_mode)
    if initial_mode == "proximity":
        pipeline.detector = pipeline._cached_detector = Detector()
    pipeline._on_push_to_talk = reported.append
    pipeline.initialize_controls()
    if initial_mode == "gesture":
        assert not hooks and gate.manual_active is None
        assert not pipeline.apply(AudioConfiguration("ring", "", "proximity"), threading.Event())
    assert len(hooks) == 1
    hooks[-1].state.set_active(True)
    assert gate.manual_active() and reported[-1] is True
    hooks[-1].state.set_active(False)
    assert not gate.manual_active() and reported[-1] is False
    assert not pipeline.apply(AudioConfiguration(), threading.Event())
    assert hooks[-1].closed and gate.manual_active is None
    assert not pipeline.apply(AudioConfiguration("ring", "", "proximity"), threading.Event())
    assert len(hooks) == 2 and not hooks[-1].closed
    pipeline.close()
    assert all(hook.closed for hook in hooks)
