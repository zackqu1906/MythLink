"""Firmware gestures preserve application routing and GUI lifecycle."""

import asyncio
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from proximic_ring import app_runtime
from proximic_ring.audio.ring import RingAudioSource


@pytest.mark.parametrize("confirm_name,slot,live", [
    ("tap", 0, False), ("snap", 1, False), ("swipe-left", 0, True),
    ("swipe-up", 1, True),
    ("clench", 0, False), ("index-pinch", 1, True), ("middle-pinch", 0, False),
    ("circle-clockwise", 1, False), ("circle-counterclockwise", 0, True),
])
def test_tap_ends_on_audio_thread_without_gui_then_waits_for_processing(monkeypatch, confirm_name, slot, live):
    from proximic_ring.asr.controller import ProximitySessionController
    from proximic_ring.events import Stage2Event
    import proximic_ring.firmware_gestures as gestures
    from proximic_ring.gesture_settings import GestureBindings, GESTURE_LABELS

    recognition, disconnect = threading.Event(), threading.Event()
    recognition.set()
    state, logs, finals, actions, detector_calls = {}, [], [], [], []
    confirms = (confirm_name, "") if slot == 0 else ("", confirm_name)
    remaining = [name for name in GESTURE_LABELS if name != confirm_name]
    configured = GestureBindings(confirm=confirms, undo=tuple(remaining[:2]), switch_mode=tuple(remaining[2:4]))
    state["bindings"] = GestureBindings() if live else configured
    audio_thread = threading.get_ident()

    class FakeDispatcher:

        def __init__(self, *, on_gesture):
            state["gesture_callback"] = on_gesture

        error = None
        def start(self): pass
        def submit(self, event): state["gesture_callback"](event)
        def close(self): pass
        def snapshot(self): return {}

    def tap(name=None):
        thread = threading.Thread(target=lambda: state["gesture_callback"](
            SimpleNamespace(name=name or next(item for item in state["bindings"].confirm if item), confidence=0.99)
        ))
        thread.start()
        thread.join(1)
        assert not thread.is_alive()

    class Sink:
        def start(self, audio):
            pass

        def feed(self, audio):
            pass

        def end(self, audio):
            assert threading.get_ident() == audio_thread
            assert not recognition.is_set()
            finals.append(audio.copy())

        def close(self):
            pass

        def abort(self):
            pass

    class FakeSource:
        error = None
        read_count = 0

        def __init__(self, **kwargs):
            assert kwargs["imu_hz"] == 50

        def connect(self):
            pass

        def start_stream(self, **kwargs):
            pass

        def read(self, frames):
            self.read_count += 1
            if self.read_count == 1:
                tap()  # Idle tap cannot finish the imminent first ACTIVATE.
            elif self.read_count in (2, 3):
                state["bindings"] = configured  # Live edit without a reconnect.
                assert state["gate"].active and not finals
                assert len(detector_calls) == 1
                if self.read_count == 3 and confirm_name != "tap":
                    tap("tap")  # Former endpoint now belongs to a GUI action.
            elif self.read_count == 4:
                tap()
                tap()  # Only one endpoint may be queued.
            elif self.read_count == 5:
                assert len(finals) == 1
                assert not recognition.is_set()
                tap()  # Ignore taps while ASR/LLM/application is outstanding.
            elif self.read_count == 6:
                assert len(detector_calls) == 1
                recognition.set()  # Simulate normal pipeline completion.
            elif self.read_count == 7:
                assert state["gate"].active
                assert len(detector_calls) == 2
                assert len(finals) == 1
            else:
                disconnect.set()
                return None
            return np.full(frames, self.read_count / 10, dtype=np.float32)

        def close(self):
            # Teardown callbacks must not submit the second open utterance.
            tap()

    class FakeDetector:
        def reset(self):
            pass

        def feed(self, block):
            detector_calls.append(block.copy())
            return [Stage2Event(320, .02, -.98, .02, 2., (2., 0.), True)]

    def build_controller(args, _detector, **kwargs):
        assert args.asr_end_on_tap is True
        gate = ProximitySessionController(
            Sink(), pre_roll_s=0.02, min_utterance_s=0.02,
            end_on_tap=args.asr_end_on_tap, on_state=kwargs["on_state"],
            on_session_end=kwargs["session_end_observer"],
        )
        state["gate"] = gate
        return gate

    monkeypatch.setattr(gestures, "FirmwareGestureWorker", FakeDispatcher)
    monkeypatch.setattr(app_runtime, "RingAudioSource", FakeSource)
    monkeypatch.setattr(app_runtime, "_build_detector", lambda _args: FakeDetector())
    monkeypatch.setattr(app_runtime, "_build_session_controller", build_controller)
    app_runtime.RecognitionRuntime(
        app_runtime.RuntimeSettings(asr_end_on_tap=True, gesture_bindings=GestureBindings() if live else configured)
    ).run(
        disconnect, recognition,
        on_update=lambda _update: None, on_state=logs.append,
        on_connected=lambda: None, on_disconnected=lambda: None,
        on_started=lambda: None, on_session_ended=recognition.clear,
        on_gesture=actions.append,
        gesture_bindings_provider=(lambda: state["bindings"]) if live else None,
    )
    if confirm_name == "tap":
        assert actions == []
    else:
        assert len(actions) == 1
        forwarded = actions[0].event if live else actions[0]
        assert forwarded.name == "tap"
        if live:
            assert actions[0].bindings is configured
    # Confirmations themselves never wait for GUI dispatch or state changes.
    assert len(finals) == 1 and finals[0].size == 4 * 320
    assert logs.count(f"[手势] {confirm_name} → 结束本句") == 1
    assert any("END reason=gesture-tap" in line for line in logs)
    assert not any("手势不可用" in line or "手势已停止" in line for line in logs)


@pytest.fixture
def controller(tmp_path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QCoreApplication, QSettings
    import proximic_ring.ui.controller as controller_module

    app = QCoreApplication.instance() or QCoreApplication(["gesture-controls"])
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    monkeypatch.setattr(controller_module, "app_data_root", lambda: tmp_path)
    instance = controller_module.AppController(inline_input_enabled=False)
    instance._text_processing_worker.close(wait=True)
    instance._runtime_active = True
    instance._connected = True
    instance._desktop_output = False
    yield instance
    app.processEvents()
    instance._text_processing_worker.close(wait=True)
    instance._close_voice_history()


def test_gesture_callback_queues_to_gui_and_cancel_alternates_with_keyboard(controller):
    from PySide6.QtCore import QCoreApplication

    controller._recognition_enabled = True
    for gesture in ("swipe-left", "keyboard", "swipe-left"):
        controller._apply_runtime_status("[ASR] START t=1.000s")
        assert controller.interactionState == "listening"
        if gesture == "keyboard":
            controller.dispatchVoiceAction("cancel")
        else:
            callback = threading.Thread(target=lambda: controller._gestureRecognized.emit(
                SimpleNamespace(name=gesture, confidence=0.9), controller._disconnect_event
            ))
            callback.start()
            callback.join(1)
            assert controller.interactionState == "listening"
            QCoreApplication.processEvents()
        assert controller.interactionState == "cancelled"
        assert controller._cancel_utterance_event.is_set()


def test_desktop_runtime_enables_tap_while_other_gesture_actions_remain_independent(controller):
    controller._selector = "test-ring"
    controller._model_path = ""
    controller._streaming_repo = ""
    controller._asr_backend = "volcengine"
    assert controller._runtime_settings().to_namespace().asr_end_on_tap is True


def test_gesture_conversion_and_undo_use_existing_availability(controller, monkeypatch):
    calls = []
    operation = SimpleNamespace(auto_context=object())
    monkeypatch.setattr(controller, "_active_operation_stack", lambda: [operation])
    monkeypatch.setattr(controller, "_latest_operation", lambda: operation)
    monkeypatch.setattr(controller, "_active_undo_target", lambda: object())
    monkeypatch.setattr(controller, "_target_is_focused", lambda _target: controller._applied_target_foreground)
    monkeypatch.setattr(controller, "_operation_can_switch_mode", lambda _op: True)
    monkeypatch.setattr(controller, "undoLastApplied", lambda: calls.append("undo"))
    monkeypatch.setattr(controller, "cancelCurrentUtterance", lambda: calls.append("cancel"))
    monkeypatch.setattr(controller, "switchCurrentInputMode", lambda: calls.append("switch_mode"))
    controller._interaction_state = "applied"
    controller._applied_target_foreground = True
    controller._pending_text_requests.add(123)  # alternate result still loading
    original_mode = controller.inputMode

    def emit(name):
        controller._gestureRecognized.emit(SimpleNamespace(name=name), controller._disconnect_event)

    emit("swipe-left")
    controller.dispatchVoiceAction("undo")
    emit("swipe-down")
    emit("swipe-right")
    controller.dispatchVoiceAction("switch_mode")
    emit("swipe-up")
    assert calls == ["undo"] * 2 + ["switch_mode"] * 2
    assert controller.inputMode == original_mode

    emit("swipe-left")  # Native undo remains available after popup timeout.
    emit("swipe-right")  # Legacy Windows conversion is available after timeout.
    assert calls == ["undo"] * 2 + ["switch_mode"] * 2 + ["undo", "switch_mode"]
    controller._applied_target_foreground = False
    for name in ("swipe-up", "swipe-right", "swipe-left", "swipe-down", "tap", "snap"):
        emit(name)
    assert calls == ["undo"] * 2 + ["switch_mode"] * 2 + ["undo", "switch_mode"]


@pytest.mark.parametrize("boundary", ["old_connection", "disconnecting", "disconnected", "finished"])
def test_stale_connection_gesture_cannot_apply(controller, monkeypatch, boundary):
    calls = []
    monkeypatch.setattr(controller, "cancelCurrentUtterance", lambda: calls.append("cancel"))
    controller._interaction_state = "listening"
    token = controller._disconnect_event
    if boundary == "old_connection":
        token = threading.Event()
    elif boundary == "disconnecting":
        controller._disconnect_event.set()
    elif boundary == "disconnected":
        controller._connected = False
    elif boundary == "finished":
        controller._runtime_active = False
    controller._gestureRecognized.emit(SimpleNamespace(name="swipe-left"), token)
    assert calls == []
    log = controller._diagnostic_log.path.read_text()
    assert "GESTURE_ACTION" in log and 'action="ignored"' in log
    assert "reason=" in log


def test_ignored_gesture_logs_reason_and_health_does_not_replace_ui_status(controller):
    controller._interaction_state = "idle"
    controller._gestureRecognized.emit(
        SimpleNamespace(name="swipe-right", confidence=0.91, timestamp_ms=12345),
        controller._disconnect_event,
    )
    controller._gestureRecognized.emit(SimpleNamespace(name="tap"), controller._disconnect_event)
    log = controller._diagnostic_log.path.read_text()
    assert 'gesture="swipe-right"' in log and 'reason="no_convertible_result"' in log
    assert 'reason="unmapped_gesture"' in log and "device_timestamp_ms=12345" in log
    assert "[手势] 右滑 → 当前无可转换结果" in controller.logText
    assert "GESTURE_ACTION" not in controller.logText
    assert "confidence" not in controller.logText
    assert "tap" not in controller.logText
    controller._set_status("识别中", "等待说话", "running")
    controller._apply_runtime_status('[GESTURE_STATUS] {"received_events": 10}')
    assert controller._status_detail == "等待说话"
    assert "GESTURE_STATUS" not in controller.logText
    for _ in range(3):
        controller._apply_runtime_status('[GESTURE_STATUS] {"status": "error"}')
    assert controller.logText.count("[手势] 固件手势通道异常，请重连") == 1
    controller._apply_runtime_status('[GESTURE_STATUS] {"status": "running"}')
    assert controller.logText.count("[手势] 识别已恢复") == 1


@pytest.mark.parametrize("failing_consumer", ["dataset", "gesture"])
def test_imu_consumers_cannot_disable_each_other(failing_consumer):
    rows, samples = [], []

    def fail(_sample):
        raise RuntimeError("consumer failed")

    source = RingAudioSource(
        imu_observer=fail if failing_consumer == "dataset" else rows.append,
        imu_sample_observer=fail if failing_consumer == "gesture" else samples.append,
    )
    sample = SimpleNamespace(
        sample_index=0, packet_seq=0, uptime_ms=100,
        accel_ms2=(1, 2, 3), gyro_dps=(4, 5, 6), raw=(1, 2, 3, 4, 5, 6),
    )
    source._on_imu_sample(sample)
    source._on_imu_sample(sample)
    assert source.error is None
    assert len(samples if failing_consumer == "dataset" else rows) == 2
