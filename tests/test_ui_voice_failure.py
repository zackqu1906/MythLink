"""Voice recovery stays inside the current Ring connection, including settings."""
import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QMetaObject
from PySide6.QtTest import QTest

from test_inline_ui import inline_ui


@pytest.mark.parametrize("trigger", ["button", "gesture"])
def test_first_voice_use_opens_settings_and_prepares_only_after_confirmation(inline_ui, trigger):
    c, _, _, root, event = inline_ui
    event(phase="idle", ready=True, utterance_id="")
    c._connected = c._runtime_active = True
    c._recognition_enabled = False
    c._busy = False
    c.connectedChanged.emit()
    c.recognitionEnabledChanged.emit()
    c.busyChanged.emit()
    root.show()
    root.setProperty("currentPage", 1)
    QTest.qWait(60)
    dialog = root.findChild(QObject, "runtimeSettingsDialog")
    c.startRecognition(remember=False)
    assert not dialog.property("visible") and not c._retry_voice_event.is_set()
    if trigger == "gesture":
        c._runtimeVoiceStartRequested.emit(threading.Event())
        assert not dialog.property("visible")
        c._runtimeVoiceStartRequested.emit(c._disconnect_event)
    else:
        QMetaObject.invokeMethod(root.findChild(QObject, "voiceRecognitionButton"), "click")
    QTest.qWait(100)
    backend = root.findChild(QObject, "asrBackendCombo")
    key = root.findChild(QObject, "asrApiKeyField")
    enable = root.findChild(QObject, "enableConfiguredVoiceButton")
    assert dialog.property("visible") and backend.property("visible") and backend.property("enabled")
    assert not c._retry_voice_event.is_set() and not c.recognitionEnabled
    assert not c._settings.contains("asr/configuredBackend")

    c.asrBackend = "volcengine"
    QTest.qWait(20)
    QMetaObject.invokeMethod(enable, "click")
    assert c.voiceRetryPending and c._retry_voice_event.is_set()
    assert not backend.property("enabled")
    c._retry_voice_event.clear()
    c._apply_runtime_voice_availability(c._disconnect_event, "尚未配置线上语音模型 API Key")
    QTest.qWait(20)
    assert c.connected and not c.recognitionEnabled
    assert not c._disconnect_event.is_set() and not c._recognition_event.is_set()
    assert c._bool_setting("input/voiceEnabled", False)
    assert "API Key" in c.voiceRuntimeError and "Ring 保持连接" in c.statusDetail
    assert backend.property("enabled")
    assert key.property("enabled")
    key.setProperty("text", "new-test-key")
    QMetaObject.invokeMethod(key, "editingFinished")
    assert c.asrApiKey == "new-test-key"

    c.startRecognition(remember=False)
    assert not c._retry_voice_event.is_set()  # Preference restore never spins retries.
    QMetaObject.invokeMethod(enable, "click")
    QTest.qWait(20)
    assert c.voiceRetryPending and c._retry_voice_event.is_set()
    assert c.connected and not c.recognitionEnabled
    assert not backend.property("enabled") and not key.property("enabled")
    c._retry_voice_event.clear()
    c._apply_runtime_voice_availability(c._disconnect_event, "")
    QTest.qWait(20)
    assert c.recognitionEnabled and c._recognition_event.is_set()
    assert c.connected and not c.voiceRetryPending and not c.voiceRuntimeError
    assert c._settings.value("asr/configuredBackend") == "volcengine"
    assert not backend.property("enabled")


def test_voice_failure_from_an_old_connection_cannot_pause_current_voice(inline_ui):
    c, *_ = inline_ui
    c._runtime_active = c._connected = c._recognition_enabled = True
    c._recognition_event.set()
    c._apply_runtime_voice_availability(threading.Event(), "old connection failure")
    assert c.recognitionEnabled and c._recognition_event.is_set()
    assert c.voiceRuntimeError == ""


def test_configured_local_voice_waits_for_explicit_use_when_cache_is_missing(inline_ui, monkeypatch, tmp_path):
    c, *_ = inline_ui
    c._connected = c._runtime_active = True
    c._recognition_enabled = False
    c._settings.setValue("asr/configuredBackend", c.asrBackend)
    c._settings.setValue("input/voiceEnabled", True)
    monkeypatch.setenv("MODELSCOPE_CACHE", str(tmp_path / "empty-cache"))

    c.startRecognition(remember=False)
    assert not c.voiceRetryPending and not c._retry_voice_event.is_set()
    c.startRecognition()
    assert c.voiceRetryPending and c._retry_voice_event.is_set()


def test_micless_ring_is_connected_without_a_ring_audio_option(inline_ui):
    c, _, _, root, event = inline_ui
    event(phase="idle", ready=True, utterance_id="")
    c._runtime_active = c._busy = True
    c._settings.setValue("touchpad/enabled", False)
    connection = c._disconnect_event
    reason = "当前戒指没有可用的麦克风，请选择电脑麦克风"
    c._apply_runtime_connected(connection)
    c._apply_runtime_voice_availability(connection, reason)
    c._touchpad.sourceReady.emit(SimpleNamespace(microphone_unavailable_reason=reason), connection)
    c._apply_runtime_started(connection)

    assert c.connectionState == "connected" and not c.recognitionEnabled
    assert c.statusKind == "paused" and c.statusDetail == reason
    assert not any(row["value"] == "ring" for row in c.audioInputs)
    assert c.audioInputLabel == "请选择电脑麦克风"
    c.startRecognition()
    assert not c._retry_voice_event.is_set()
    assert any(label.property("text") == reason for label in root.findChildren(QObject, "voiceStatusDetail"))

    c._apply_runtime_disconnected(connection)
    assert any(row["value"] == "ring" for row in c.audioInputs)
    assert not c.voiceRuntimeError


def test_missing_model_path_is_valid_ring_connection_settings(inline_ui, tmp_path):
    c, *_ = inline_ui
    c._selector = "test-ring"
    c._speech_control_mode = "proximity"
    c._model_path = str(tmp_path / "missing-detector.pt")
    c._asr_backend = "funasr_nano"
    c._funasr_repo = ""
    settings = c._runtime_settings()
    assert settings.ring_selector == "test-ring"
    assert settings.detector_model == tmp_path / "missing-detector.pt"
