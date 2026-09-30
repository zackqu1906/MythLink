from pathlib import Path
import os
import time

import pytest
from PySide6.QtCore import QMetaObject, QObject, QPointF, Q_ARG
from PySide6.QtTest import QTest

from test_inline_ui import inline_ui
from test_permission_setup import PermissionApp


def item(root, name):
    result = root.findChild(QObject, name)
    assert result is not None, name
    return result


def capture(root, tmp_path, name):
    shots = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(shots / name))


@pytest.mark.parametrize("size", [(1440, 940), (940, 700)])
def test_native_permissions_without_wizard_preserve_voice_and_leave_settings_accessible(inline_ui, monkeypatch, tmp_path, size):
    c, bridge, _, root, _ = inline_ui
    setup = c.permissionSetup
    monitor = c.inlineInput.permissions
    QTest.qWait(30)
    monitor._timer.stop()
    monitor._generation += 1
    monitor._checking = False
    monkeypatch.setattr(monitor, "refresh", lambda: None)
    monkeypatch.setattr(monitor, "refreshScreenRecording", lambda: None)
    monkeypatch.setattr(monitor, "refreshDevicePermissions", monitor.changed.emit)
    states = dict(bluetooth=True, microphone=False, accessibility=False, screen=False)
    requests = []
    monkeypatch.setattr(monitor, "permissionGranted", lambda kind: states.get(kind, False))
    monkeypatch.setattr(monitor, "requestSystemPermission", lambda kind: requests.append(kind) or True)
    monkeypatch.setattr(monitor, "states", states, raising=False)
    monkeypatch.setattr(monitor, "requests", requests, raising=False)
    native = PermissionApp(monitor)
    monkeypatch.setattr(setup, "_app", native)
    monkeypatch.setattr(setup, "_is_active", lambda: True)
    before, messages, phase = c.gestureBindings, list(bridge.messages), c.inlineInput.status
    root.resize(*size)
    root.setProperty("currentPage", 0)
    root.show()
    setup.startIfNeeded()
    QTest.qWait(60)
    assert root.findChild(QObject, "permissionSetupDialog") is None
    assert requests == ["microphone"] and setup.busy
    capture(root, tmp_path, f"native-permissions-home-{size[0]}.png")
    native.complete(False)
    QTest.qWait(40)
    assert requests == ["microphone", "accessibility"]
    assert not root.findChild(QObject, "runtimeSettingsDialog").property("visible")
    monitor.systemPermissionRequestFinished.emit("accessibility", True)
    QTest.qWait(40)
    assert requests == ["microphone", "accessibility"]  # API return is not a grant/dismissal.
    states["accessibility"] = True
    monitor.changed.emit()
    QTest.qWait(40)
    assert requests[-1] == "screen"
    states["screen"] = True
    monitor.systemPermissionRequestFinished.emit("screen", True)
    QTest.qWait(40)
    assert not setup.active and not states["microphone"]
    setup.startIfNeeded()
    QTest.qWait(40)
    assert not setup.active and requests == ["microphone", "accessibility", "screen"]
    QMetaObject.invokeMethod(root, "showSettings", Q_ARG("QVariant", 0))
    QTest.qWait(180)
    button = item(root, "permissionSetupButton")
    assert button.property("text") == "请求权限" and button.property("enabled")
    assert button.height() <= 44
    # Explicit request skips the denied microphone; no custom modal or forced Settings window.
    QMetaObject.invokeMethod(button, "click")
    QTest.qWait(40)
    assert not setup.active and requests == ["microphone", "accessibility", "screen"]
    scroll = item(root, "runtimeSettingsScroll")
    flick = scroll.property("contentItem")
    offset = flick.property("contentY") + button.mapToScene(QPointF()).y() - root.height() / 2
    flick.setProperty("contentY", max(0, min(offset, flick.property("contentHeight") - scroll.property("availableHeight"))))
    QTest.qWait(60)
    assert 0 <= button.mapToScene(QPointF()).y() < root.height() - button.height()
    capture(root, tmp_path, f"native-permissions-settings-{size[0]}.png")
    assert c.gestureBindings == before and bridge.messages == messages and c.inlineInput.status == phase


@pytest.mark.parametrize("size", [(1440, 940), (940, 700)])
def test_regular_input_enhancement_is_live_without_reconnecting(inline_ui, monkeypatch, tmp_path, size):
    c, bridge, _, root, _ = inline_ui
    import proximic_ring.ui.controller as module
    monkeypatch.setattr(module, "input_device_choices", lambda: [])
    c._connected = c._runtime_active = True
    c.connectedChanged.emit()
    before, messages, phase = c.gestureBindings, list(bridge.messages), c.inlineInput.status
    disconnect_token = c._disconnect_event
    root.resize(*size)
    root.show()
    QMetaObject.invokeMethod(root, "showSettings", Q_ARG("QVariant", 0))
    QTest.qWait(150)
    slider = item(root, "asrGainSlider")
    assert slider.property("visible") and slider.property("enabled")
    assert len(root.findChildren(QObject, "asrGainSlider")) == 1
    assert item(root, "settingsPage4").findChild(QObject, "asrGainSlider") is None
    slider.setProperty("value", 6.0)
    QMetaObject.invokeMethod(slider, "moved")
    assert c.asrGainDb == 6.0 and c._settings.value("asr/gainDb", type=float) == 6.0
    assert c.connected and c._disconnect_event is disconnect_token and not disconnect_token.is_set()
    # Enumerating devices is allowed, but cannot silently replace the active selection.
    for _ in range(100):
        if not c.microphoneScanBusy:
            break
        QTest.qWait(10)
    assert item(root, "refreshMicrophonesButton").property("enabled")
    c.refreshMicrophones()
    for _ in range(100):
        time.sleep(0.005)
        QTest.qWait(5)
        if not c.microphoneScanBusy:
            break
    assert not c.microphoneScanBusy
    for name in ("audioSourceCombo", "speechControlModeCombo"):
        assert item(root, name).property("enabled")
    assert c.gestureBindings == before and bridge.messages == messages and c.inlineInput.status == phase
    capture(root, tmp_path, f"regular-input-enhancement-{size[0]}.png")
