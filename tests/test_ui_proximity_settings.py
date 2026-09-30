"""Settings UI uses the existing proximity controller with a fake native process."""
from pathlib import Path
import os

import pytest

from test_inline_ui import inline_ui
from test_proximity import Process, calibrate


@pytest.fixture
def proximity_ui(inline_ui, tmp_path, monkeypatch):
    from proximic_ring.ui import proximity_controller

    controller, bridge, _, root, _ = inline_ui
    helper = tmp_path / "helper"
    helper.touch()
    monkeypatch.setattr(proximity_controller, "helper_path", lambda: helper)
    calls = []

    class FakeProcess(Process):
        def start(self, path, args):
            calls.append(list(args))
            super().start(path, args)

    proximity = controller.proximity
    proximity._factory = FakeProcess
    yield controller, proximity, bridge, root, calls
    proximity.close()


def item(root, name):
    from PySide6.QtCore import QObject
    result = root.findChild(QObject, name)
    assert result is not None, name
    return result


def click(root, name):
    from PySide6.QtCore import QMetaObject
    assert QMetaObject.invokeMethod(item(root, name), "click")


def open_details(root):
    from PySide6.QtCore import QMetaObject
    from PySide6.QtTest import QTest
    root.setProperty("currentPage", 0)
    root.show()
    QMetaObject.invokeMethod(item(root, "runtimeSettingsDialog"), "open")
    click(root, "settingsCategory9")
    QTest.qWait(150)
    dialog = item(root, "runtimeSettingsDialog")
    for _ in range(30):
        if dialog.property("opacity") >= 0.999:
            break
        QTest.qWait(20)
    assert dialog.property("opacity") >= 0.999


def finish_status(proximity, **overrides):
    process = proximity._process
    process.send(dict(event="status", **dict(permission=True, password=True, password_access=True) | overrides))
    process.finished.emit(0, 0)


def screenshot(root, tmp_path, name):
    shots = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(shots / name))


def scroll_bottom(root):
    from PySide6.QtTest import QTest
    flick = item(root, "runtimeSettingsScroll").property("contentItem")
    flick.setProperty("contentY", max(0, flick.property("contentHeight") - flick.height()))
    QTest.qWait(40)


@pytest.mark.parametrize("size", [(1440, 940), (940, 700)])
def test_proximity_details_and_advanced_disclosure_do_not_change_configuration(proximity_ui, tmp_path, size):
    from PySide6.QtCore import QPointF
    from PySide6.QtTest import QTest

    controller, proximity, bridge, root, calls = proximity_ui
    root.resize(*size)
    before = {key: controller._settings.value(key) for key in controller._settings.allKeys()}
    messages = list(bridge.messages)
    bindings = controller.gestureBindings
    open_details(root)
    assert calls == [["status"]]
    finish_status(proximity, password=False, password_access=False, permission=False)
    QTest.qWait(30)
    assert not item(root, "proximityAdvancedSettings").property("visible")
    assert not item(root, "proximity_awaySamples").property("visible")
    screenshot(root, tmp_path, f"proximity-top-{size[0]}.png")
    scroll_bottom(root)
    screenshot(root, tmp_path, f"proximity-bottom-{size[0]}.png")
    click(root, "proximityAdvancedButton")
    QTest.qWait(20)
    assert item(root, "proximity_awaySamples").property("visible")
    scroll_bottom(root)
    screenshot(root, tmp_path, f"proximity-advanced-{size[0]}.png")
    for name in ("settingsApplyButton", "proximityRevealComponent"):
        control = item(root, name)
        point = control.mapToScene(QPointF())
        assert 0 <= point.x() <= root.width() - control.width()
        assert 0 <= point.y() <= root.height() - control.height()
    click(root, "settingsBackButton")
    assert item(root, "runtimeSettingsDialog").property("currentPage") == 0
    assert not item(root, "settingsPage9").property("showAdvanced")
    assert calls == [["status"]]
    assert not proximity.enabled
    assert bridge.messages == messages and controller.gestureBindings == bindings
    assert {key: controller._settings.value(key) for key in controller._settings.allKeys()} == before


def test_failed_enable_returns_switch_to_real_state(proximity_ui):
    controller, proximity, _, root, calls = proximity_ui
    open_details(root)
    finish_status(proximity, password=False, password_access=False, permission=False)
    click(root, "proximityEnabled")
    assert not proximity.enabled
    assert not item(root, "proximityEnabled").property("checked")
    assert "连接戒指" in item(root, "proximityError").property("text")
    assert not controller._settings.contains("proximity/enabled")
    assert calls == [["status"]]


@pytest.mark.parametrize("enabled", [False, True])
def test_calibration_progress_and_cancel_preserve_existing_baseline_and_switch(proximity_ui, tmp_path, enabled):
    from PySide6.QtTest import QTest

    controller, proximity, _, root, calls = proximity_ui
    proximity.useDevice("a44fbefd-c2d7-46ce-a9a2-731f1d5b2cf3", "Ring · 界面预览")
    open_details(root)
    finish_status(proximity)
    calibrate(proximity)
    proximity.enabled = enabled
    before = controller._settings.value("proximity/calibrations/" + proximity._target)
    click(root, "proximityCalibrate")
    assert calls[-1][0] == "calibrate"
    process = proximity._process
    process.send(dict(event="calibration_progress", elapsed=4.0, samples=8))
    QTest.qWait(40)
    assert item(root, "proximityCalibrationProgress").property("value") == 4.0
    assert not item(root, "proximityEnabled").property("enabled")
    assert not item(root, "proximityConfigurePassword").property("enabled")
    assert item(root, "proximityCancelCalibration").property("visible")
    screenshot(root, tmp_path, f"proximity-calibrating-{enabled}.png")
    click(root, "proximityCancelCalibration")
    QTest.qWait(30)
    assert process.killed
    assert proximity.enabled == enabled
    assert item(root, "proximityEnabled").property("checked") == enabled
    assert not item(root, "proximityCancelCalibration").property("visible")
    assert "原校准数据已保留" in item(root, "proximityCalibrationStatus").property("text")
    assert controller._settings.value("proximity/calibrations/" + proximity._target) == before
    click(root, "proximityAdvancedButton")
    for name in ("proximity_awaySamples", "proximity_lostDelay"):
        assert item(root, name).property("enabled") == (not enabled)
    if enabled:
        assert calls[-1][0] == "monitor"
    else:
        assert proximity._process is None


@pytest.mark.parametrize("button,command", [
    ("proximityConfigurePassword", "password"),
    ("proximityAuthorizePassword", "authorize-password"),
    ("proximityForgetPassword", "forget"),
    ("proximityRequestPermissions", "authorize"),
])
def test_setup_buttons_keep_the_existing_native_routes(proximity_ui, button, command):
    _, proximity, _, root, calls = proximity_ui
    open_details(root)
    finish_status(proximity, password_access=False)
    assert item(root, button).property("visible")
    click(root, button)
    assert calls[-1] == [command]
    assert proximity.busy
    assert not item(root, button).property("enabled")
    assert not proximity.enabled
