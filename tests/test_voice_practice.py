from dataclasses import replace
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from proximic_ring.asr.factory import ASRBackendSettings
from proximic_ring.gestures.gesture_settings import GestureBindings
from proximic_ring.text_processing.model import LLMSettings
from proximic_ring.ui.voice.practice_controller import PracticeConfiguration, VoicePracticeController


@pytest.fixture
def practice():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(["practice-tests", "-platform", "offscreen"])
    configuration = PracticeConfiguration("ring", "", True, "volcengine", ASRBackendSettings(), LLMSettings(), GestureBindings())
    controller = VoicePracticeController(lambda: configuration)
    yield controller
    controller.cancel()
    app.processEvents()


def wait_for(predicate):
    from PySide6.QtTest import QTest
    for _ in range(100):
        if predicate():
            return
        QTest.qWait(20)
    assert predicate()


def voice(controller):
    controller.consume_audio(np.full(8000, 0.08, dtype=np.float32))


def test_audio_test_uses_real_pcm_and_never_invokes_recognition(practice, monkeypatch):
    monkeypatch.setattr(practice, "_recognize", lambda *args: pytest.fail("recording test must not call ASR"))
    assert not practice.consume_audio(np.ones(320))
    assert practice.start("audio")
    assert practice.level == 0
    voice(practice)
    assert practice.level > 0
    assert practice.seconds == 0.5
    practice.finish()
    assert practice.audioAvailable and not practice.active
    assert not practice.recording and not practice.processing
    practice.cancel()
    assert not practice.audioAvailable


def test_cancellation_drops_late_asr_and_queued_gestures(practice):
    assert practice.start("dictation")
    old = practice._generation
    practice.setText("before")  # Locked while recording.
    practice.cancel()
    assert not practice.active
    practice.setText("user draft")
    practice._apply_result(old, "dictation", "late response", "")
    practice._apply_gesture(old, "double-tap")
    assert practice.text == "user draft"
    assert not practice.recording
    assert not practice.consume_gesture(SimpleNamespace(name="tap"))


def test_busy_runtime_rejects_practice_without_stealing_the_active_utterance(practice):
    practice.arm()
    assert not practice.consume_audio(np.ones(320), runtime_busy=True)
    assert not practice.active
    assert not practice.recording
    assert "结束当前输入" in practice.message


def test_full_recognition_polish_and_edit_are_local_and_undoable(practice, monkeypatch):
    import proximic_ring.ui.voice.practice_controller as module
    calls = []
    results = iter(["今天三点见面。", "改为四点"])

    class Backend:
        def start(self): calls.append("start")
        def feed(self, block): calls.append(len(block))
        def finish(self, audio):
            assert len(audio) == 8000
            return next(results)
        def abort(self): calls.append("closed")

    monkeypatch.setattr(module, "asr_backend_kind", lambda _: "streaming")
    monkeypatch.setattr(module, "create_streaming_asr_backend", lambda *_: Backend())
    def process(text, mode, settings, target=""):
        if mode == "polish":
            assert text == "今天三点见面。"
            return "今天下午三点见面。"
        assert text == "改为四点"
        assert target == "今天下午三点见面。"
        return "今天下午四点见面。"
    monkeypatch.setattr(practice, "_process_text", process)
    completed = []
    practice.completed.connect(completed.append)
    assert practice.arm()
    assert practice.consume_gesture(SimpleNamespace(name="double-tap"))
    wait_for(lambda: practice.recording)
    voice(practice)
    assert practice.consume_gesture(SimpleNamespace(name="tap"))
    wait_for(lambda: practice.text == "今天三点见面。")
    practice.polish()
    wait_for(lambda: practice.text == "今天下午三点见面。")
    assert practice.start("edit")
    voice(practice)
    practice.finish()
    wait_for(lambda: practice.text == "今天下午四点见面。")
    assert completed == ["dictation", "polish", "edit"]
    assert sum(v for v in calls if isinstance(v, int)) == 16000
    practice.undo()
    assert practice.text == "今天下午三点见面。"


def test_down_swipe_finishes_and_then_polishes(practice, monkeypatch):
    def recognize(token, config, mode, target, audio, cancel):
        practice._result.emit(token, mode, "原句", "")
    monkeypatch.setattr(practice, "_recognize", recognize)
    monkeypatch.setattr(practice, "_process_text", lambda *args: "润色结果")
    practice.start("dictation")
    voice(practice)
    practice.consume_gesture(SimpleNamespace(name="swipe-down"))
    wait_for(lambda: practice.text == "润色结果")
    assert not practice.recording and not practice.processing


def test_microphone_is_closed_on_stop_without_erasing_recording(practice, monkeypatch):
    import proximic_ring.ui.voice.practice_controller as module
    from PySide6.QtTest import QTest
    read_entered, closed = threading.Event(), threading.Event()
    class Microphone:
        def __init__(self, selection): assert selection == "chosen-mic"
        def open(self): pass
        def read(self, size):
            read_entered.set()
            closed.wait(2)
            return None
        def close(self): closed.set()
    config = replace(practice._configuration(), source="microphone", microphone="chosen-mic", shared=False)
    practice._configuration = lambda: config
    monkeypatch.setattr(module, "MicrophoneSource", Microphone)
    practice.start("audio")
    wait_for(read_entered.is_set)
    practice._feed(np.full(8000, .1), practice._generation)
    practice.finish()
    wait_for(closed.is_set)
    QTest.qWait(50)
    assert practice.audioAvailable
    assert "录音完成" in practice.message


def test_silence_and_device_errors_do_not_report_success(practice):
    completed = []
    practice.completed.connect(completed.append)
    practice.start("audio")
    practice.consume_audio(np.zeros(8000))
    practice.finish()
    assert not completed and "有效声音" in practice.message
    practice.start("dictation")
    practice._apply_fault(practice._generation, "device unavailable")
    assert not practice.active and not practice.recording
    assert not completed


def test_ring_gestures_work_after_audio_test(practice, monkeypatch):
    practice.start("audio")
    voice(practice)
    practice.finish()
    assert practice.arm()
    practice.consume_gesture(SimpleNamespace(name="double-tap"))
    assert practice.recording and practice.mode == "dictation"


def test_invalid_audio_cancels_instead_of_sending_nan_to_asr(practice):
    practice.start("dictation")
    practice.consume_audio(np.array([float("nan")], dtype=np.float32))
    assert not practice.active and not practice.recording
    assert "无效音频" in practice.message
