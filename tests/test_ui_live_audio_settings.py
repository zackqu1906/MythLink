import json
import os
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QMetaObject, Q_ARG
from PySide6.QtTest import QTest

from proximic_ring.live_audio_settings import AudioConfiguration, LiveAudioSettings
from test_inline_ui import inline_ui


def item(root, name):
    result = root.findChild(QObject, name)
    assert result is not None
    return result


@pytest.fixture
def connected_ui(inline_ui, monkeypatch):
    import proximic_ring.ui.controller as module

    c, bridge, _, root, event = inline_ui
    initial = c._active_audio_configuration()
    c._audio_settings_changes = LiveAudioSettings(initial)
    c._runtime_active = c._connected = True
    c.connectedChanged.emit()
    busy = [True]
    monkeypatch.setattr(c._ring_gestures, "speech_busy", lambda: busy[0])
    row = dict(name="Computer microphone", api="Core Audio", index=1)
    row.update(value=json.dumps(row), label="Computer microphone")
    monkeypatch.setattr(module, "input_device_choices", lambda: [row])
    c._apply_microphone_scan_finished([row], "")
    root.show()
    QMetaObject.invokeMethod(root, "showSettings", Q_ARG("QVariant", 0))
    QTest.qWait(120)
    yield c, bridge, root, busy, row, initial
    c._audio_settings_timer.stop()


@pytest.mark.parametrize("size", [(1440, 940), (940, 700)])
def test_connected_selection_waits_for_complete_turn_and_commits_after_audio_ack(connected_ui, tmp_path, size):
    c, bridge, root, busy, row, initial = connected_ui
    root.resize(*size)
    phase, messages, bindings = c.inlineInput.status, list(bridge.messages), c.gestureBindings
    changes = c._audio_settings_changes
    combo = item(root, "audioSourceCombo")
    combo.setProperty("currentIndex", 1)
    QMetaObject.invokeMethod(combo, "activated", Q_ARG("int", 1))
    c.speechControlMode = "proximity"
    assert c._active_audio_configuration() == initial
    assert changes.claim() is None and "处理完成后生效" in c.audioSettingsStatus
    assert c._settings.value("audio/source", "ring") == "ring"
    assert item(root, "audioSourceCombo").property("currentIndex") == 1
    assert item(root, "speechControlModeCombo").property("currentIndex") == 0
    assert combo.property("displayText") == row["name"]
    assert root.findChild(QObject, "microphoneDeviceCombo") is None
    assert all(item(root, name).property("enabled") for name in (
        "audioSourceCombo", "speechControlModeCombo"))
    # A pending text/edit result continues to block, even after voice becomes idle.
    busy[0] = False
    c._interaction_recognition_suspended = True
    c._update_audio_settings_readiness()
    assert changes.claim() is None
    c._interaction_recognition_suspended = False
    c._update_audio_settings_readiness()
    request = changes.claim()
    c._audio_configuration_started(changes)
    assert request is not None and "正在应用" in c.audioSettingsStatus
    assert c.audioSource == "ring" and c.speechControlMode == "gesture"
    changes.complete(request, success=True)
    c._audio_configuration_finished(changes, request, "")
    assert c.audioSource == "microphone" and c.speechControlMode == "proximity"
    assert c.microphoneDevice == row["value"] and not changes.pending
    assert c._settings.value("audio/source") == "microphone" and c.connected
    assert "已生效" in c.audioSettingsStatus and not c.audioSettingsError
    assert c.inlineInput.status == phase and bridge.messages == messages and c.gestureBindings == bindings
    QTest.qWait(400)  # Let the Material popup transition finish before capture.
    shots = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(shots / f"connected-audio-settings-{size[0]}.png"))


def test_failed_switch_reverts_controls_and_does_not_persist_failed_choice(connected_ui):
    c, _, root, busy, row, initial = connected_ui
    before = {key: c._settings.value(key) for key in c._settings.allKeys()}
    busy[0] = False
    c.selectAudioInput(row["value"])
    c.speechControlMode = "proximity"
    changes = c._audio_settings_changes
    request = changes.claim()
    assert request is not None
    changes.complete(request, success=False)
    c._audio_configuration_finished(changes, request, "麦克风不可用")
    assert c._active_audio_configuration() == initial
    assert {key: c._settings.value(key) for key in c._settings.allKeys()} == before
    assert "已恢复" in c.audioSettingsStatus and c.audioSettingsError and c.connected
    assert item(root, "audioSourceCombo").property("currentIndex") == 0
    assert item(root, "speechControlModeCombo").property("currentIndex") == 1


def test_rapid_changes_keep_newest_selection_and_ignore_old_connection_completion(connected_ui):
    c, _, root, busy, row, _ = connected_ui
    busy[0] = False
    c.selectAudioInput(row["value"])
    changes = c._audio_settings_changes
    first = changes.claim()
    c.microphoneDevice = row["value"]
    c.speechControlMode = "proximity"
    changes.complete(first, success=True)
    c._audio_configuration_finished(changes, first, "")
    assert c.speechControlMode == "gesture" and changes.pending
    assert item(root, "speechControlModeCombo").property("currentIndex") == 0
    second = changes.claim()
    assert second.configuration == AudioConfiguration("microphone", row["value"], "proximity")
    # The next BLE connection must reject this old connection's queued result.
    c._audio_settings_changes = LiveAudioSettings(c._active_audio_configuration())
    changes.complete(second, success=True)
    c._audio_configuration_finished(changes, second, "")
    assert c.speechControlMode == "gesture"


def test_disconnect_discards_pending_selection_without_persisting_it(connected_ui):
    c, _, _, _, row, initial = connected_ui
    c.selectAudioInput(row["value"])
    c._apply_runtime_finished("")
    assert c._audio_settings_changes is None and not c._audio_settings_timer.isActive()
    assert c._active_audio_configuration() == initial
    assert "取消" in c.audioSettingsStatus


def test_single_menu_selects_a_real_device_in_one_request(connected_ui):
    c, _, root, busy, row, initial = connected_ui
    busy[0] = False
    combo = item(root, "audioSourceCombo")
    assert c.audioInputs == [{"label": "Ring 麦克风", "value": "ring"},
                             {"label": row["name"], "value": row["value"]}]
    assert root.findChild(QObject, "microphoneDeviceRow") is None
    combo.setProperty("currentIndex", 1)
    QMetaObject.invokeMethod(combo, "activated", Q_ARG("int", 1))
    changes = c._audio_settings_changes
    request = changes.claim()
    assert request.revision == 1
    assert request.configuration == AudioConfiguration("microphone", row["value"], "gesture")
    assert c._active_audio_configuration() == initial
    assert combo.property("displayText") == row["name"]


def test_device_refresh_keeps_selection_and_does_not_offer_missing_inputs(connected_ui):
    c, _, root, _, row, _ = connected_ui
    c.selectAudioInput(row["value"])
    # PortAudio indexes can change after another device is connected.
    refreshed = dict(row, index=7)
    refreshed["value"] = json.dumps({key: refreshed[key] for key in ("name", "api", "index")})
    c._apply_microphone_scan_finished([refreshed], "")
    assert c.audioInputSelection == refreshed["value"]
    assert item(root, "audioSourceCombo").property("currentIndex") == 1
    assert len(c.audioInputs) == 2
    c._apply_microphone_scan_finished([], "")
    assert c.audioInputs == [{"label": "Ring 麦克风", "value": "ring"}]
    assert item(root, "audioSourceCombo").property("currentIndex") == -1
    assert row["name"] in c.audioInputLabel and "未检测到" in c.audioInputLabel
    desired = c._audio_settings_changes.desired
    c.selectAudioInput(refreshed["value"])  # A stale menu click cannot select an absent input.
    assert c._audio_settings_changes.desired == desired
    c.selectAudioInput("ring")
    assert item(root, "audioSourceCombo").property("currentIndex") == 0


def test_duplicate_device_names_have_distinct_menu_labels(connected_ui):
    c, _, _, _, row, _ = connected_ui
    duplicate = dict(row, index=2, value="second-device")
    another_api = dict(row, api="Other Audio", value="third-device")
    c._apply_microphone_scan_finished([row, duplicate, another_api], "")
    labels = [item["label"] for item in c.audioInputs]
    assert len(set(labels)) == 4
