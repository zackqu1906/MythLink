"""UI navigation must not mutate the existing speech or gesture configuration."""
from pathlib import Path
import os

import pytest

from test_inline_ui import inline_ui  # Reuse the isolated controller and fake IME bridge.


def visual_child(item, name):
    if item.objectName() == name:
        return item
    for child in item.childItems():
        found = visual_child(child, name)
        if found is not None:
            return found


@pytest.mark.parametrize("size", [(1440, 940), (940, 700)])
def test_shell_navigation_preserves_live_state_and_configuration(inline_ui, tmp_path, size):
    from PySide6.QtCore import QObject, QMetaObject, QPointF
    from PySide6.QtTest import QTest

    controller, bridge, _, root, _ = inline_ui
    root.resize(*size)
    root.show()
    QTest.qWait(80)
    settings_before = {key: controller._settings.value(key) for key in controller._settings.allKeys()}
    bindings_before = controller.gestureBindings
    profiles_before = controller.appGestures.profiles
    messages_before = list(bridge.messages)
    mode_before = controller.ringGestures.mode
    shots = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    for index, page in enumerate(("homePage", "mainPageScroll", "gesturesPage", "touchpadPage")):
        nav = visual_child(root.contentItem(), f"mainNav{index}")
        QMetaObject.invokeMethod(nav, "click")
        QTest.qWait(100)
        assert root.property("currentPage") == index
        surface = root.findChild(QObject, page)
        assert surface.property("visible")
        origin = surface.mapToScene(QPointF())
        assert origin.x() >= root.property("sidebarWidth")
        assert origin.x() + surface.width() <= root.width()
        assert origin.y() + surface.height() <= root.height()
        assert root.grabWindow().save(str(shots / f"{page}-{size[0]}.png"))
    assert visual_child(root.contentItem(), "mainNav4") is None
    assert controller.gestureBindings == bindings_before
    assert controller.appGestures.profiles == profiles_before
    assert controller.ringGestures.mode == mode_before
    assert bridge.messages == messages_before
    assert {key: controller._settings.value(key) for key in controller._settings.allKeys()} == settings_before

    # The home history link must reveal the existing voice history, not a new page.
    root.setProperty("currentPage", 0)
    QMetaObject.invokeMethod(root.findChild(QObject, "homeAllHistoryButton"), "click")
    QTest.qWait(100)
    assert root.property("currentPage") == 1
    history = root.findChild(QObject, "voiceHistoryCard")
    origin = history.mapToScene(QPointF())
    assert 0 <= origin.y() < root.height()


def test_voice_gestures_are_inspectable_but_cannot_open_binding_editor(inline_ui):
    from PySide6.QtCore import QObject, QMetaObject
    from PySide6.QtTest import QTest

    controller, bridge, _, root, _ = inline_ui
    root.setProperty("currentPage", 2)
    root.show()
    QTest.qWait(100)
    before = controller.gestureBindings
    messages_before = list(bridge.messages)
    page = root.findChild(QObject, "gesturesPage")
    edit = root.findChild(QObject, "gestureActionEditor")
    for key in ("tap", "swipe-left", "swipe-right"):
        card = visual_child(page, "gestureCard_" + key)
        QMetaObject.invokeMethod(card, "click")
        QTest.qWait(20)
        assert page.property("selectedGesture") == key
        assert not edit.property("editable")
        assert not root.findChild(QObject, "runtimeSettingsDialog").property("visible")
    assert controller.gestureBindings == before
    assert bridge.messages == messages_before


def test_settings_open_unified_advanced_window_directly(inline_ui, tmp_path):
    from PySide6.QtCore import QObject, QMetaObject
    from PySide6.QtTest import QTest

    _, _, _, root, _ = inline_ui
    root.setProperty("currentPage", 0)
    root.show()
    QMetaObject.invokeMethod(root.findChild(QObject, "runtimeSettingsButton"), "click")
    QTest.qWait(150)
    assert root.findChild(QObject, "settingsCategory2") is None
    assert root.findChild(QObject, "audioEncodingCombo") is None
    assert root.findChild(QObject, "settingsCategory4") is None
    assert root.findChild(QObject, "settingsCategory5") is None
    assert root.findChild(QObject, "moreSettingsButton") is None
    assert root.findChild(QObject, "asrAdvancedButton") is None
    assert root.findChild(QObject, "llmAdvancedButton") is None
    shots = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(shots / "settings.png"))
    QMetaObject.invokeMethod(root.findChild(QObject, "advancedSettingsButton"), "click")
    QTest.qWait(160)
    assert root.findChild(QObject, "advancedSettingsDialog").property("visible")
    assert not root.findChild(QObject, "runtimeSettingsDialog").property("visible")
    assert root.findChild(QObject, "settingsPage4").property("visible")
    assert root.findChild(QObject, "settingsPage5").property("visible")
    QMetaObject.invokeMethod(root.findChild(QObject, "advancedSettingsDoneButton"), "click")
    QTest.qWait(160)
    assert root.findChild(QObject, "runtimeSettingsDialog").property("visible")
    QMetaObject.invokeMethod(root.findChild(QObject, "runtimeSettingsDialog"), "close")


@pytest.mark.parametrize("size", [(1440, 940), (940, 700)])
def test_settings_form_navigation_preserves_configuration_and_live_voice(inline_ui, tmp_path, size):
    from PySide6.QtCore import QObject, QMetaObject, QPointF
    from PySide6.QtTest import QTest

    controller, bridge, _, root, _ = inline_ui
    root.resize(*size)
    root.setProperty("currentPage", 0)
    root.show()
    QTest.qWait(80)
    before = {key: controller._settings.value(key) for key in controller._settings.allKeys()}
    bindings = controller.gestureBindings
    messages = list(bridge.messages)
    phase = controller.inlineInput.status
    dialog = root.findChild(QObject, "runtimeSettingsDialog")
    scroll = root.findChild(QObject, "runtimeSettingsScroll")
    QMetaObject.invokeMethod(dialog, "open")
    QTest.qWait(150)
    assert root.findChild(QObject, "settingsAudioGroup").property("visible")
    assert root.findChild(QObject, "settingsInputGroup").property("visible")
    assert root.findChild(QObject, "settingsCategory3") is None
    assert root.findChild(QObject, "microphoneDeviceRow") is None
    assert not root.findChild(QObject, "voiceSensitivityRow").property("visible")
    shots = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(shots / f"settings-top-{size[0]}.png"))
    flick = scroll.property("contentItem")
    flick.setProperty("contentY", max(0, flick.property("contentHeight") - flick.height()))
    QTest.qWait(40)
    assert root.grabWindow().save(str(shots / f"settings-bottom-{size[0]}.png"))
    # Bottom controls remain reachable without moving the fixed completion bar.
    for name in ("advancedSettingsButton", "settingsApplyButton"):
        control = root.findChild(QObject, name)
        point = control.mapToScene(QPointF())
        assert 0 <= point.x() and point.x() + control.width() <= root.width()
        assert 0 <= point.y() and point.y() + control.height() <= root.height()
    QMetaObject.invokeMethod(root.findChild(QObject, "advancedSettingsButton"), "click")
    QTest.qWait(160)
    QMetaObject.invokeMethod(root.findChild(QObject, "advancedSettingsDoneButton"), "click")
    QTest.qWait(160)
    assert flick.property("contentY") == max(0, flick.property("contentHeight") - flick.height())
    QMetaObject.invokeMethod(root.findChild(QObject, "settingsApplyButton"), "click")
    QTest.qWait(150)
    assert not dialog.property("visible")
    assert controller.gestureBindings == bindings
    assert controller.inlineInput.status == phase
    assert bridge.messages == messages
    assert {key: controller._settings.value(key) for key in controller._settings.allKeys()} == before


def test_grouped_setting_changes_only_the_selected_preference(inline_ui):
    from PySide6.QtCore import QObject, QMetaObject
    from PySide6.QtTest import QTest

    controller, bridge, _, root, _ = inline_ui
    root.show()
    QMetaObject.invokeMethod(root.findChild(QObject, "runtimeSettingsDialog"), "open")
    QTest.qWait(150)
    before = {key: controller._settings.value(key) for key in controller._settings.allKeys()}
    bindings = controller.gestureBindings
    phase = controller.inlineInput.status
    control = root.findChild(QObject, "multiUndoSwitch")
    QMetaObject.invokeMethod(control, "click")
    assert controller.multiUndoEnabled
    assert bridge.messages[-1]["type"] == "configure"
    assert bridge.messages[-1]["multi_undo_enabled"]
    after = {key: controller._settings.value(key) for key in controller._settings.allKeys()}
    assert {key for key in before.keys() | after.keys() if before.get(key) != after.get(key)} == {"input/multiUndoEnabled"}
    assert controller.gestureBindings == bindings
    assert controller.inlineInput.status == phase
    QMetaObject.invokeMethod(root.findChild(QObject, "runtimeSettingsDialog"), "close")
