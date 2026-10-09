"""The opt-in switch is live even during speech; input ownership is unchanged."""
import os
from pathlib import Path

from PySide6.QtCore import QObject, QMetaObject, Q_ARG
from PySide6.QtTest import QTest

from test_inline_ui import inline_ui
from test_ui_touchpad import prepare


def test_settings_toggle_is_persistent_and_does_not_change_speech_or_bindings(inline_ui, tmp_path):
    controller, bridge, _, root, _ = inline_ui
    before = (controller.gestureBindings, controller.recognitionEnabled, controller.inlineInput.status)
    messages = list(bridge.messages)
    root.show()
    QMetaObject.invokeMethod(root, "showSettings", Q_ARG("QVariant", 0))
    QTest.qWait(60)
    switch = root.findChild(QObject, "gestureTriggerHintsSwitch")
    assert switch is not None and switch.property("enabled") and not switch.property("checked")
    QMetaObject.invokeMethod(switch, "click")
    assert controller.gestureTrigger.enabled
    assert controller._settings.value("ui/gestureTriggerHints", type=bool)
    assert before == (controller.gestureBindings, controller.recognitionEnabled, controller.inlineInput.status)
    assert bridge.messages == messages
    group = root.findChild(QObject, "settingsAppearanceGroup")
    grab = group.grabToImage()
    QTest.qWait(80)
    shots = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    assert grab.image().save(str(shots / "gesture-trigger-settings.png"))
    # This setting is also available when the input method is disabled.
    controller.inlineInput._enabled = False
    controller.inlineInput.changed.emit()
    QTest.qWait(20)
    assert group.isVisible() and switch.isVisible()
    QMetaObject.invokeMethod(switch, "click")
    assert not controller.gestureTrigger.enabled
    QMetaObject.invokeMethod(root.findChild(QObject, "runtimeSettingsDialog"), "close")


def test_touchpad_recognition_callback_ignores_retired_output(inline_ui):
    controller, *_ = inline_ui
    tp, source = prepare(controller)
    controller.gestureTrigger.enabled = True
    shown = []
    controller.gestureTrigger.triggered.connect(shown.extend)
    tp.start()
    QTest.qWait(20)
    source.future.set_result(None)
    QTest.qWait(20)
    old = tp._run.output
    old.options["on_gesture"]("click")
    QTest.qWait(20)
    old.options["on_gesture"]("double-click")
    QTest.qWait(20)
    assert shown == ["click", "double-click"]
    tp.stop()
    QTest.qWait(20)
    old.options["on_gesture"]("click")
    QTest.qWait(20)
    assert shown == ["click", "double-click"]
