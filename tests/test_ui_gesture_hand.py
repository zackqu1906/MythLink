"""The v6 hand workbench shows automatically saved mappings with one-step undo."""
import pytest
from PySide6.QtCore import QObject, QMetaObject, QPointF, Qt
from PySide6.QtTest import QTest
from test_inline_ui import inline_ui
from test_ui_application_mapping import mapping_ui, BUNDLE
from test_ui_shell import visual_child


def find(root, name):
    item = visual_child(root.contentItem(), name) or root.findChild(QObject, name)
    assert item is not None, name
    return item


def click(root, name):
    assert QMetaObject.invokeMethod(find(root, name), "click")
    QTest.qWait(40)


def stage(root, identity):
    click(root, "gestureMappingDropdown")
    editor = find(root, "gestureActionEditor")
    if identity:
        options = editor.property("availableActions")
        if hasattr(options, "toVariant"):
            options = options.toVariant()
        action = next(a for a in options if a["id"] == identity)
        find(root, "menuActionSearch").setProperty("text", action["label"])
        QTest.qWait(40)
    click(root, "menuAction_" + identity)


def idle(controller):
    controller._inline_input._view.update(phase="idle", has_composition=False, edit_requested=False)
    controller._utterance_active = False
    controller._pending_inline_audio_start = None


@pytest.mark.parametrize("size", [(1200, 780), (940, 700), (940, 560), (1200, 560)])
def test_hand_layout_starts_unselected_and_all_triggers_are_reachable(mapping_ui, size):
    c, bridge, root, catalog, _, _, _ = mapping_ui
    root.resize(*size)
    page = find(root, "gesturesPage")
    page.setProperty("selectedGesture", "")
    before = dict(c.ringGestures.globalBindings), c.gestureBindings, list(bridge.messages)
    QTest.qWait(80)
    editor = find(root, "gestureActionEditor")
    assert not editor.property("editable")
    assert not find(root, "undoMenuMappingButton").property("enabled")
    assert root.findChild(QObject, "saveMenuMappingButton") is None
    hand = find(root, "gestureHandImage")
    from PySide6.QtQml import QQmlExpression, QQmlEngine
    expression = QQmlExpression(QQmlEngine.contextForObject(hand), hand, "status === Image.Ready")
    assert expression.evaluate()[0] and hand.property("mirror")
    assert hand.width() == hand.height() and hand.width() >= 380
    corner = find(root, "gestureVoicePriorityCard")
    panel = find(root, "gestureMappingPanel")
    if panel.property("compact"):
        assert not hand.isVisible()
    else:
        hand_right = hand.mapToScene(QPointF(hand.width(), 0)).x()
        assert corner.mapToScene(QPointF()).x() >= hand_right + 8
    assert root.findChild(QObject, "gestureVoiceRecognitionButton") is None
    from PySide6.QtCore import QRectF
    # Top-level cards must stay disjoint and inside the scaled workbench.
    cards = [find(root, "gestureCard_" + key) for key in (
        "circle-clockwise", "circle-counterclockwise", "tap", "swipe-up",
        "swipe-down", "swipe-left", "swipe-right", "index-pinch", "middle-pinch", "clench", "snap")]
    rectangles = []
    for card in cards + [corner]:
        rect = card.mapRectToItem(panel, QRectF(0, 0, card.width(), card.height()))
        assert rect.left() >= -0.01 and rect.top() >= -0.01
        assert rect.right() <= panel.width() + 0.01 and rect.bottom() <= panel.height() + 0.01
        assert not any(rect.intersects(other) for other in rectangles)
        rectangles.append(rect)
    assert len({card.height() for card in cards}) == 1
    click(root, "voiceGesturePriorityRulesButton")
    assert find(root, "voiceGesturePriorityRules").property("visible")
    QMetaObject.invokeMethod(find(root, "voiceGesturePriorityRules"), "close")
    QTest.qWait(100)
    for name in ("gestureMappingDropdown", "undoMenuMappingButton", "gestureAutoSaveStatus"):
        control = find(root, name)
        pos = control.mapToScene(QPointF())
        assert 0 <= pos.x() and pos.x() + control.width() <= root.width()
        assert 0 <= pos.y() and pos.y() + control.height() <= root.height() - 24
    keys = ["circle-clockwise", "circle-counterclockwise", "tap", "double-tap", "swipe-up", "swipe-down", "swipe-left", "swipe-right", "index-pinch", "double-index-pinch", "middle-pinch", "double-middle-pinch", "clench", "snap"]
    for key in keys:
        # Include real nested-control hit testing instead of just invoking handlers.
        control = find(root, "gestureCard_" + key)
        pos = control.mapToScene(QPointF(18 if key in ("tap", "index-pinch", "middle-pinch") else control.width()/2, control.height()/2)).toPoint()
        QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, pos)
        QTest.qWait(20)
        assert page.property("selectedGesture") == key
        group = ("rotate" if key.startswith("circle-") else "index" if "index-pinch" in key
                 else "middle" if "middle-pinch" in key else "fist" if key == "clench"
                 else "touch" if key in ("tap", "double-tap") or key.startswith("swipe-") else "")
        if group:
            assert find(root, "gestureConnector_" + group).property("highlighted")
    assert (dict(c.ringGestures.globalBindings), c.gestureBindings, list(bridge.messages)) == before


def test_auto_saved_edits_and_undo_are_scoped_to_the_latest_change(mapping_ui):
    c, bridge, root, catalog, _, navigate, add = mapping_ui
    add()
    before, messages = c.gestureBindings, list(bridge.messages)
    stage(root, "next-tab")
    editor = find(root, "gestureActionEditor")
    assert catalog.bindings[BUNDLE]["circle-clockwise"]["id"] == "next-tab"
    assert editor.property("feedback") == "已自动保存"
    navigate("gestureCard_snap")
    stage(root, "previous-tab")
    navigate("gestureCard_circle-clockwise")
    assert editor.property("selectedActionId") == "next-tab"
    assert not find(root, "undoMenuMappingButton").property("enabled")
    navigate("gestureCard_snap")
    assert find(root, "undoMenuMappingButton").property("enabled")
    click(root, "undoMenuMappingButton")
    assert "snap" not in catalog.bindings[BUNDLE]
    assert catalog.bindings[BUNDLE]["circle-clockwise"]["id"] == "next-tab"
    assert not find(root, "undoMenuMappingButton").property("enabled")
    assert c.gestureBindings == before and bridge.messages == messages


def test_failed_auto_save_keeps_saved_value_and_success_can_be_undone(mapping_ui):
    c, _, root, _, _, _, _ = mapping_ui
    idle(c)
    before = dict(c.ringGestures.globalBindings)
    c._inline_input._view["phase"] = "listening"
    stage(root, "window_selector")
    editor = find(root, "gestureActionEditor")
    assert editor.property("feedbackError")
    assert dict(c.ringGestures.globalBindings) == before
    assert find(root, "gestureAutoSaveError").property("visible")
    idle(c)
    stage(root, "window_selector")
    assert c.ringGestures.globalBindings["window_selector"] == "circle-clockwise"
    assert not editor.property("feedbackError")
    click(root, "gestureMappingDropdown")
    click(root, "gestureCategory_system")
    details = find(root, "menuActionDescription_window_selector").property("text")
    assert "选择要切换到的窗口" in details and "此手势当前用于" in details
    assert find(root, "menuAction_window_selector").property("checked")
    QMetaObject.invokeMethod(find(root, "gestureMappingMenu"), "close")
    click(root, "undoMenuMappingButton")
    assert dict(c.ringGestures.globalBindings) == before


def test_custom_shortcut_auto_saves_after_recording_and_can_be_undone(mapping_ui):
    _, _, root, catalog, _, _, add = mapping_ui
    add()
    click(root, "gestureMappingDropdown")
    find(root, "menuActionSearch").setProperty("text", "自定义快捷键")
    QTest.qWait(40)
    click(root, "customShortcutButton")
    find(root, "customShortcutName").setProperty("text", "测试动作")
    click(root, "customShortcutEntryMode")
    find(root, "customShortcutText").setProperty("text", "Cmd+Shift+K")
    click(root, "useCustomShortcutButton")
    QTest.qWait(100)
    assert not find(root, "customShortcutDialog").property("visible")
    assert catalog.bindings[BUNDLE]["circle-clockwise"]["shortcut"] == "Cmd+Shift+K"
    click(root, "undoMenuMappingButton")
    assert not catalog.bindings[BUNDLE]


def test_restore_defaults_and_removing_application_invalidate_undo(mapping_ui):
    c, _, root, catalog, _, navigate, _ = mapping_ui
    bundle = "com.microsoft.Powerpoint"
    catalog._candidates = [dict(value=bundle, label="PowerPoint", path="")]
    assert catalog.addApplication(bundle)
    QTest.qWait(60)
    navigate("gestureApplication_" + bundle)
    navigate("gestureCard_swipe-right")
    stage(root, "next-tab")
    navigate("presentationGestureScene")
    stage(root, "powerpoint:previous")
    navigate("regularGestureScene")
    editor = find(root, "gestureActionEditor")
    assert editor.property("selectedActionId") == "next-tab"
    assert catalog.restoreDefaultMappings(bundle)
    assert not c.gestureMappingHistory.canUndo
    navigate("presentationGestureScene")
    assert editor.property("savedId") == "powerpoint:next"
    stage(root, "powerpoint:previous")
    assert c.gestureMappingHistory.canUndo
    assert catalog.removeApplication(bundle)
    assert not c.gestureMappingHistory.canUndo
    assert catalog.addApplication(bundle)
    QTest.qWait(40)
    navigate("gestureApplication_" + bundle)
    assert not find(root, "undoMenuMappingButton").property("enabled")


def test_hover_is_stable_and_global_menu_excludes_voice_actions(mapping_ui):
    _, _, root, _, _, _, _ = mapping_ui
    chip = find(root, "gestureCard_snap")
    pos = chip.mapToScene(QPointF(chip.width()/2, chip.height()/2)).toPoint()
    QTest.mouseMove(root, pos)
    QTest.qWait(180)
    color = chip.property("background").property("color")
    QTest.qWait(700)
    assert chip.property("hovered") and chip.property("background").property("color") == color
    click(root, "gestureMappingDropdown")
    assert find(root, "gestureMappingMenu").property("visible")
    assert rows(root)[0]['label'] == '无'
    assert not any(row.get('category') == 'voice' for row in rows(root))
    find(root, "menuActionSearch").setProperty("text", "语音")
    QTest.qWait(30)
    assert not any(row.get('voiceAction') for row in rows(root))
    QMetaObject.invokeMethod(find(root, "gestureMappingMenu"), "close")


def rows(root):
    value = find(root, "gestureActionEditor").property("menuRows")
    return value.toVariant() if hasattr(value, "toVariant") else value


def test_application_and_system_shortcuts_are_separate_and_explained(mapping_ui):
    c, _, root, catalog, _, _, add = mapping_ui
    add()
    from test_ui_application_mapping import ACTIONS
    system = dict(id="system-emoji", label="表情与符号", path="Apple › 表情与符号", shortcut="Ctrl+Cmd+Space", available=True)
    catalog._apply_result("menu", catalog._generation["menu"], BUNDLE, dict(actions=ACTIONS + [system]), "")
    assert catalog.setCustomBinding(BUNDLE, "snap", "自己的动作", "Cmd+Shift+K")
    click(root, "gestureMappingDropdown")
    assert "文本编辑" in find(root, "menuActionDescription_application").property("text")
    assert "自己录制" in find(root, "menuActionDescription_shortcut").property("text")
    assert "不会改成全局绑定" in find(root, "gestureMappingScopeDescription").property("text")
    click(root, "gestureCategory_application")
    assert {a["id"] for a in rows(root)} == {a["id"] for a in ACTIONS}
    assert find(root, "menuActionShortcut_next-tab").property("text") == "Ctrl+Tab"
    details = find(root, "menuActionDescription_next-tab").property("text")
    assert "文本编辑" in details and "窗口 › 下一个标签页" in details and "可用" in details
    # Searches within a category must not leak other categories' shortcuts.
    find(root, "menuActionSearch").setProperty("text", "聚焦")
    QTest.qWait(30)
    assert rows(root) == []
    click(root, "gestureMenuBack")
    click(root, "gestureCategory_shortcut")
    ids = {a["id"] for a in rows(root)}
    assert "system-emoji" in ids and "keyboard:spotlight" in ids
    assert any(i.startswith("custom:") for i in ids)
    assert not ids.intersection(a["id"] for a in ACTIONS)
    find(root, "menuActionSearch").setProperty("text", "Apple")
    QTest.qWait(30)
    assert [a["id"] for a in rows(root)] == ["system-emoji"]
    details = find(root, "menuActionDescription_system-emoji").property("text")
    assert "字符检视器" in details and "系统菜单：Apple › 表情与符号" in details


@pytest.mark.parametrize("application_scope", [True, False])
def test_system_keyboard_presets_use_existing_binding_scope(mapping_ui, application_scope):
    c, _, root, catalog, _, navigate, add = mapping_ui
    idle(c)
    if application_scope:
        add()
    before_globals = dict(c.ringGestures.globalBindings)
    click(root, "gestureMappingDropdown")
    # Every top-level category keeps its explanation visible without scrolling.
    listing = find(root, "menuActionList")
    details = find(root, "menuActionDescription_shortcut")
    bottom = details.mapToItem(listing, QPointF(0, details.height())).y()
    assert bottom <= listing.height()
    click(root, "gestureCategory_shortcut")
    description = find(root, "menuActionDescription_keyboard:spotlight").property("text")
    assert "搜索栏" in description and "系统中改键" in description
    assert find(root, "menuActionShortcut_keyboard:spotlight").property("text") == "Cmd+Space"
    click(root, "menuAction_keyboard:spotlight")
    editor = find(root, "gestureActionEditor")
    assert not editor.property("feedbackError")
    if application_scope:
        assert catalog.bindings[BUNDLE]["circle-clockwise"]["shortcut"] == "Cmd+Space"
        assert dict(c.ringGestures.globalBindings) == before_globals
    else:
        saved = c._global_gesture_bindings.custom_for("circle-clockwise")
        assert saved.shortcut == "Cmd+Space" and not catalog.apps
    # Saved presets remain selected and explained, using their persisted custom ID.
    click(root, "gestureMappingDropdown")
    click(root, "gestureCategory_shortcut")
    choices = [a for a in rows(root) if a["label"] == "聚焦搜索"]
    assert len(choices) == 1
    assert find(root, "menuAction_" + choices[0]["id"]).property("checked")
    assert "搜索栏" in find(root, "menuActionDescription_" + choices[0]["id"]).property("text")


def test_browser_media_actions_are_not_described_as_key_presses(mapping_ui):
    _, _, root, catalog, _, navigate, _ = mapping_ui
    bundle = "com.google.Chrome"
    catalog._candidates = [dict(value=bundle, label="Google Chrome", path="")]
    assert catalog.addApplication(bundle)
    QTest.qWait(40)
    navigate("gestureApplication_" + bundle)
    navigate("videoGestureScene")
    navigate("gestureCard_tap")
    click(root, "gestureMappingDropdown")
    click(root, "gestureCategory_application")
    action = next(a for a in rows(root) if a["id"].startswith("web-video:"))
    assert find(root, "menuActionShortcut_" + action["id"]).property("text") == ""
    assert "不发送键盘按键" in find(root, "menuActionDescription_" + action["id"]).property("text")
