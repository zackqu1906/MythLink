"""One settings list owns permission actions and displays live, evidence-based state."""
import pytest
from PySide6.QtCore import QObject, QMetaObject, Q_ARG
from PySide6.QtTest import QTest
from proximic_ring.platforms.macos.mac_permissions import PermissionState
from test_inline_ui import inline_ui


@pytest.mark.parametrize("size", [(1200, 780), (940, 700)])
def test_permissions_have_one_home_and_unknown_is_not_denied(inline_ui, monkeypatch, size):
    controller, bridge, _, root, event = inline_ui
    event(phase="idle", ready=True)
    monitor = controller.inlineInput.permissions
    QTest.qWait(30)
    monitor._timer.stop()
    monitor._generation += 1
    monkeypatch.setattr(monitor, "refresh", lambda: None)
    monkeypatch.setattr(monitor, "refreshScreenRecording", lambda: None)
    monitor._checked = True
    monitor._state = PermissionState()
    monitor._screen_checked = False
    monitor._screen_access = None
    monitor._device_access = {"bluetooth": None, "microphone": None}
    monitor.changed.emit()
    root.resize(*size); root.show()
    before = list(bridge.messages)
    QMetaObject.invokeMethod(root, "showSettings", Q_ARG("QVariant", 6))
    QTest.qWait(180)
    assert len(root.findChildren(QObject, "settingsPermissionsGroup")) == 1
    for removed in ("preferencesMicrophoneButton", "microphoneSettingsPermissionNotice", "inputMethodSetupButton"):
        assert root.findChild(QObject, removed) is None
    rows = [("Bluetooth", "bluetooth"), ("Microphone", "microphone")]
    for label, _ in rows:
        item = root.findChild(QObject, f"settings{label}Status")
        assert item.property("visible") and item.property("text") == "待确认"
    for granted, expected, color in [(False, "未开启", "#76520b"), (True, "已开启", "#16703f")]:
        monitor._state = PermissionState(granted, granted)
        monitor._screen_checked, monitor._screen_access = True, granted
        monitor._device_access = {"bluetooth": granted, "microphone": granted}
        monitor.changed.emit()
        for name in ("settingsBluetoothStatus", "settingsMicrophoneStatus", "settingsAccessibilityStatus", "screenRecordingPermissionStatus"):
            item = root.findChild(QObject, name)
            assert item.property("text") == expected
            assert item.property("color").name() == color
    # Exact existing OS destinations; rendering/reading state did not request access.
    from proximic_ring.ui.permissions import mac_permissions_controller as module
    destinations = []
    monkeypatch.setattr(module.QDesktopServices, "openUrl", lambda url: destinations.append(url.toString()) or True)
    for label, _ in rows:
        QMetaObject.invokeMethod(root.findChild(QObject, f"settings{label}Button"), "click")
    assert destinations == [
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Bluetooth",
        "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
    ]
    assert bridge.messages == before


def test_lock_permission_action_moves_to_settings_and_preserves_native_authorization(inline_ui, monkeypatch):
    controller, _, _, root, event = inline_ui
    event(phase="idle", ready=True)
    calls = []
    monkeypatch.setattr(controller.proximity, "_launch", lambda command, args=(): calls.append((command, args)))
    root.show()
    QMetaObject.invokeMethod(root, "showSettings", Q_ARG("QVariant", 6))
    QTest.qWait(180)
    buttons = root.findChildren(QObject, "proximityRequestPermissions")
    assert len(buttons) == 1 and buttons[0].property("visible")
    assert all(command == "status" for command, _ in calls)
    calls.clear()
    QMetaObject.invokeMethod(buttons[0], "click")
    assert calls == [("authorize", ())]
