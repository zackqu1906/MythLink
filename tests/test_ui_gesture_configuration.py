"""Real pointer navigation and the independent voice/app/global save paths."""
from PySide6.QtCore import QObject, QPointF, Qt, QMetaObject
from PySide6.QtTest import QTest
import pytest

from test_inline_ui import inline_ui
from test_ui_application_mapping import mapping_ui, BUNDLE
from test_ui_gesture_hand import find, click, idle, stage


def pointer_click(root, name):
    item = find(root, name)
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, point)
    QTest.qWait(40)


def test_voice_configuration_navigation_and_shared_enable_state(mapping_ui):
    c, bridge, root, _, _, _, _ = mapping_ui
    before = c.gestureBindings, list(bridge.messages)
    root.setProperty("currentPage", 1)
    QTest.qWait(60)
    pointer_click(root, "openVoiceGestureSettingsButton")
    assert root.property("currentPage") == 2
    assert find(root, "gestureActionEditor").property("voiceScope")
    assert find(root, "gesturesPage").property("selectedGesture") == c.gestureBindings["confirm"][0]
    assert find(root, "gestureCard_tap").property("doubleTrigger")
    assert root.findChild(QObject, "voiceGestureBindingDialog") is None
    for enabled in (False, True):
        c._recognition_enabled = enabled
        c.recognitionEnabledChanged.emit()
        QTest.qWait(20)
        for name in ("voiceRecognitionButton", "gestureVoiceRecognitionButton"):
            assert find(root, name).property("checked") == enabled
    assert (c.gestureBindings, list(bridge.messages)) == before


def test_voice_submenu_saves_without_overwriting_global_or_application(mapping_ui):
    c, bridge, root, catalog, _, navigate, add = mapping_ui
    idle(c)
    add()
    assert catalog.setBinding(BUNDLE, "snap", "next-tab")
    assert c.ringGestures.setGlobalGestureAction("snap", "window_selector")
    before_app = catalog.bindings
    before_global = dict(c.ringGestures.globalBindings)
    before_voice = c.gestureBindings
    navigate("globalGestureScope")
    navigate("gestureCard_snap")
    click(root, "gestureMappingDropdown")
    click(root, "gestureCategory_voice")
    menu = find(root, "gestureMappingMenu")
    assert menu.property("visible") and menu.property("width") == 296
    assert find(root, "menuAction_voice:start_end").height() == 44
    assert not find(root, "menuActionDescription_voice:start_end").property("visible")
    bridge.messages.clear()
    click(root, "menuAction_voice:start_end")
    assert c.gestureBindings["confirm"] == ["double-tap", "snap"]
    assert c.gestureBindings["end"] == ["tap", "snap"]
    assert catalog.bindings == before_app and dict(c.ringGestures.globalBindings) == before_global
    assert [message["type"] for message in bridge.messages] == ["configure_hud"]
    # Clearing in the voice editing scope releases only this voice assignment.
    click(root, "gestureMappingDropdown")
    click(root, "gestureMenuBack")
    click(root, "menuAction_")
    assert c.gestureBindings == before_voice
    assert catalog.bindings == before_app and dict(c.ringGestures.globalBindings) == before_global


def test_app_tooltip_and_catalog_refresh_do_not_swallow_first_click(mapping_ui):
    _, _, root, catalog, _, _, add = mapping_ui
    other = "com.example.Other"
    add()
    add(other)
    pointer_click(root, "gestureApplication_" + BUNDLE)
    first = find(root, "gestureApplication_" + BUNDLE)
    QTest.mouseMove(root, first.mapToScene(QPointF(20, 18)).toPoint())
    QTest.qWait(500)
    assert first.findChild(QObject, "applicationTooltip_" + BUNDLE).property("visible")
    second = find(root, "gestureApplication_" + other)
    point = second.mapToScene(QPointF(20, 18)).toPoint()
    QTest.mouseMove(root, point)
    QTest.mousePress(root, Qt.LeftButton, Qt.NoModifier, point)
    catalog.changed.emit()  # A menu/binding update can arrive between press/release.
    QTest.qWait(20)
    assert find(root, "gestureApplication_" + other) is second
    QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, point)
    QTest.qWait(60)
    assert catalog.selectedApp == other
    assert second.property("checked")
    pointer_click(root, "gestureApplication_" + BUNDLE)
    assert catalog.selectedApp == BUNDLE


def test_voice_functions_can_repeat_and_only_global_scope_has_voice_menu(mapping_ui):
    c, _, root, _, _, navigate, add = mapping_ui
    for gesture in ("swipe-up", "swipe-down", "tap", "snap"):
        navigate("gestureCard_" + gesture)
        stage(root, "voice:undo")
        assert c.voiceGestureAction(gesture)["voiceAction"] == "undo"
    assert len([g for g in c.gestureBindings["undo"] if g]) == 5
    assert not any(c.gestureBindings["end"])
    add()
    navigate("gestureCard_swipe-down")
    click(root, "gestureMappingDropdown")
    rows = find(root, "gestureActionEditor").property("menuRows").toVariant()
    assert all(row.get("category") != "voice" for row in rows)
    find(root, "menuActionSearch").setProperty("text", "语音")
    QTest.qWait(40)
    assert find(root, "gestureActionEditor").property("menuRows").toVariant() == []


def test_v5_cards_show_saved_mapping_for_scope_and_single_double_trigger(mapping_ui):
    c, _, root, catalog, _, navigate, add = mapping_ui
    idle(c)
    label = lambda key: find(root, "gestureMappingLabel_" + key)
    assert c.ringGestures.setGlobalCustomBinding("snap", "全局动作", "Cmd+Shift+K")
    QTest.qWait(40)
    assert label("snap").isVisible() and label("snap").property("text") == "全局动作"
    navigate("gestureCard_snap")
    stage(root, "window_selector")
    assert label("snap").property("text") == "窗口选择"
    click(root, "undoMenuMappingButton")
    add()
    assert label("snap").property("text") == "全局动作"  # Inherited mapping.
    assert catalog.setBinding(BUNDLE, "snap", "next-tab")
    assert catalog.setBinding(BUNDLE, "tap", "next-tab")
    assert catalog.setBinding(BUNDLE, "double-tap", "previous-tab")
    QTest.qWait(40)
    assert label("snap").property("text") == catalog.bindings[BUNDLE]["snap"]["label"]
    for key in ("tap", "double-tap"):
        pointer_click(root, "gestureSingle_tap" if key == "tap" else "gestureCard_double-tap")
        assert label("tap").property("text") == catalog.bindings[BUNDLE][key]["label"]
    add("com.example.Other")
    assert label("snap").property("text") == "全局动作"
    root.setProperty("currentPage", 1)
    QTest.qWait(40)
    pointer_click(root, "openVoiceGestureSettingsButton")
    for key in ("tap", "double-tap"):
        pointer_click(root, "gestureSingle_tap" if key == "tap" else "gestureCard_double-tap")
        voice_label = find(root, "gestureVoiceMappingLabel_tap")
        assert voice_label.isVisible() and voice_label.property("text") == c.voiceGestureAction(key)["label"]
    assert c.setVoiceGestureAction("snap", "start_end")
    QTest.qWait(40)
    assert find(root, "gestureVoiceMappingLabel_snap").property("text") == "开始 / 定稿"
    assert label("snap").property("text") == "全局动作"
    assert catalog.bindings[BUNDLE]["snap"]["id"] == "next-tab"
    pointer_click(root, "globalGestureScope")
    assert label("snap").property("text") == "全局动作"


@pytest.mark.parametrize("size", [(1200, 780), (940, 700)])
def test_enabled_voice_bindings_are_visible_with_each_scopes_saved_functions(mapping_ui, size):
    c, bridge, root, catalog, _, _, add = mapping_ui
    idle(c)
    root.resize(*size)
    add()
    add("com.example.Other")
    pointer_click(root, "gestureApplication_" + BUNDLE)
    assert catalog.setBinding(BUNDLE, "swipe-up", "next-tab")
    assert catalog.setBinding(BUNDLE, "tap", "previous-tab")
    assert c.setVoiceGestureAction("snap", "undo")
    before = c.gestureBindings, dict(c.ringGestures.globalBindings), catalog.bindings, list(bridge.messages)
    c._recognition_enabled = True
    c.recognitionEnabledChanged.emit()
    for scope in ("globalGestureScope", "gestureApplication_" + BUNDLE, "gestureApplication_com.example.Other"):
        pointer_click(root, scope)
        for key in ("swipe-up", "swipe-down", "swipe-left", "swipe-right", "tap", "snap"):
            label = find(root, "gestureVoiceMappingLabel_" + key)
            assert label.isVisible() and label.property("text") == c.voiceGestureAction(key)["label"]
            assert find(root, "gestureVoiceContext_" + key).property("text") == "输入时"
        up = find(root, "gestureCard_swipe-up")
        assert up.property("dualMapping")
        ordinary = find(root, "gestureMappingLabel_swipe-up")
        expected = catalog.bindings[BUNDLE]["swipe-up"]["label"] if scope.endswith(BUNDLE) else catalog.globalOccupancy["swipe-up"]
        assert ordinary.isVisible() and ordinary.property("text") == expected
        pointer_click(root, "gestureVoiceMappingLabel_swipe-up")
        assert find(root, "gestureActionEditor").property("voiceScope") == (scope == "globalGestureScope")
        if scope == "globalGestureScope":
            assert find(root, "gestureActionEditor").property("savedId") == "voice:send"
        pointer_click(root, "gestureMappingLabel_swipe-up")
        assert not find(root, "gestureActionEditor").property("voiceScope")
        assert "同一次手势只执行其中一个功能" in up.property("hint")
        # Neither the two captions nor neighboring cards may overlap at either size.
        voice = find(root, "gestureVoiceMappingLabel_swipe-up")
        assert voice.y() + voice.height() <= ordinary.y()
        assert ordinary.y() + ordinary.height() <= up.height() - 6
        for upper, lower in (("swipe-up", "swipe-down"), ("swipe-left", "swipe-right"), ("middle-pinch", "clench")):
            a, b = find(root, "gestureCard_" + upper), find(root, "gestureCard_" + lower)
            assert a.y() + a.height() + 6 <= b.y()
        pointer_click(root, "gestureCard_double-tap")
        assert find(root, "gestureVoiceMappingLabel_tap").property("text") == c.voiceGestureAction("double-tap")["label"]
        pointer_click(root, "gestureSingle_tap")
    pointer_click(root, "gestureApplication_com.example.Other")
    c._recognition_enabled = False
    c.recognitionEnabledChanged.emit()
    QTest.qWait(40)
    assert not find(root, "gestureVoiceMappingLabel_swipe-up").isVisible()
    assert not find(root, "gestureCard_swipe-up").property("dualMapping")
    assert find(root, "gestureMappingLabel_swipe-up").property("text") == catalog.globalOccupancy["swipe-up"]
    assert (c.gestureBindings, dict(c.ringGestures.globalBindings), catalog.bindings, list(bridge.messages)) == before


@pytest.mark.parametrize("application_scope", [False, True])
def test_inline_keyboard_recording_auto_saves_in_original_scope(mapping_ui, application_scope):
    c, _, root, catalog, _, _, add = mapping_ui
    idle(c)
    if application_scope:
        add()
    before_voice, before_global = c.gestureBindings, dict(c.ringGestures.globalBindings)
    click(root, "gestureMappingDropdown")
    click(root, "gestureCategory_shortcut")
    assert find(root, "gestureMappingMenu").property("height") == 196
    field = find(root, "gestureShortcutRecorder")
    field.forceActiveFocus()
    QTest.keyClick(root, Qt.Key_K, Qt.ControlModifier | Qt.ShiftModifier)
    QTest.qWait(60)
    assert not c.appGestures.recording
    assert find(root, "undoMenuMappingButton").property("enabled")
    if application_scope:
        assert catalog.bindings[BUNDLE]["circle-clockwise"]["shortcut"] == "Cmd+Shift+K"
        assert dict(c.ringGestures.globalBindings) == before_global
    else:
        assert c._global_gesture_bindings.custom_for("circle-clockwise").shortcut == "Cmd+Shift+K"
    assert c.gestureBindings == before_voice
    click(root, "gestureMappingDropdown")
    click(root, "gestureCategory_shortcut")
    field.forceActiveFocus()
    assert c.appGestures.recording
    QMetaObject.invokeMethod(find(root, "gestureMappingMenu"), "close")
    QTest.qWait(100)
    assert not c.appGestures.recording
