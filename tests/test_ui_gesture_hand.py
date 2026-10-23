"""The v6 hand workbench stages edits without changing saved gesture behavior."""
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


@pytest.mark.parametrize("size", [(1200, 780), (940, 700)])
def test_hand_layout_starts_unselected_and_all_triggers_are_reachable(mapping_ui, size):
    c, bridge, root, catalog, _, _, _ = mapping_ui
    root.resize(*size)
    page = find(root, "gesturesPage")
    page.setProperty("selectedGesture", "")
    before = dict(c.ringGestures.globalBindings), c.gestureBindings, list(bridge.messages)
    QTest.qWait(80)
    editor = find(root, "gestureActionEditor")
    assert not editor.property("editable")
    assert not find(root, "saveMenuMappingButton").property("enabled")
    hand = find(root, "gestureHandImage")
    from PySide6.QtQml import QQmlExpression, QQmlEngine
    expression = QQmlExpression(QQmlEngine.contextForObject(hand), hand, "status === Image.Ready")
    assert expression.evaluate()[0] and hand.property("mirror")
    assert (hand.width(), hand.height()) == (432, 432)
    for name in ("gestureMappingDropdown", "resetMenuMappingButton", "saveMenuMappingButton"):
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
    assert (dict(c.ringGestures.globalBindings), c.gestureBindings, list(bridge.messages)) == before


def test_unsaved_edits_are_scoped_and_reset_does_not_write(mapping_ui):
    c, bridge, root, catalog, _, navigate, add = mapping_ui
    add()
    before, messages = c.gestureBindings, list(bridge.messages)
    stage(root, "next-tab")
    editor = find(root, "gestureActionEditor")
    assert editor.property("dirty") and not catalog.bindings[BUNDLE]
    navigate("gestureCard_snap")
    stage(root, "previous-tab")
    navigate("gestureCard_circle-clockwise")
    assert editor.property("selectedActionId") == "next-tab"
    click(root, "resetMenuMappingButton")
    assert not editor.property("dirty") and not catalog.bindings[BUNDLE]
    navigate("gestureCard_snap")
    assert editor.property("selectedActionId") == "previous-tab"
    click(root, "saveMenuMappingButton")
    assert catalog.bindings[BUNDLE]["snap"]["id"] == "previous-tab"
    assert not editor.property("dirty")
    assert c.gestureBindings == before and bridge.messages == messages


def test_global_draft_waits_for_save_and_rejection_preserves_saved_value(mapping_ui):
    c, _, root, _, _, navigate, _ = mapping_ui
    idle(c)
    before = dict(c.ringGestures.globalBindings)
    stage(root, "window_selector")
    editor = find(root, "gestureActionEditor")
    assert dict(c.ringGestures.globalBindings) == before and editor.property("dirty")
    c._inline_input._view["phase"] = "listening"
    click(root, "saveMenuMappingButton")
    assert editor.property("feedbackError") and editor.property("dirty")
    assert dict(c.ringGestures.globalBindings) == before
    idle(c)
    click(root, "saveMenuMappingButton")
    assert c.ringGestures.globalBindings["window_selector"] == "circle-clockwise"
    assert not editor.property("dirty")
    # Opening/closing the menu preserves the actual saved selection.
    click(root, "gestureMappingDropdown")
    click(root, "gestureCategory_system")
    details = find(root, "menuActionDescription_window_selector").property("text")
    assert "选择要切换到的窗口" in details and "此手势当前用于" in details
    assert find(root, "menuAction_window_selector").property("checked")
    QMetaObject.invokeMethod(find(root, "gestureMappingMenu"), "close")


def test_custom_shortcut_is_a_draft_until_explicit_save(mapping_ui):
    c, _, root, catalog, _, _, add = mapping_ui
    add()
    click(root, "gestureMappingDropdown")
    click(root, "gestureCategory_shortcut")
    click(root, "customShortcutButton")
    find(root, "customShortcutName").setProperty("text", "测试动作")
    click(root, "customShortcutEntryMode")
    find(root, "customShortcutText").setProperty("text", "Cmd+Shift+K")
    click(root, "useCustomShortcutButton")
    QTest.qWait(100)
    assert not find(root, "customShortcutDialog").property("visible")
    assert not catalog.bindings[BUNDLE]
    assert find(root, "gestureActionEditor").property("dirty")
    click(root, "saveMenuMappingButton")
    assert catalog.bindings[BUNDLE]["circle-clockwise"]["shortcut"] == "Cmd+Shift+K"


def test_scene_drafts_are_discarded_when_restoring_or_removing_application(mapping_ui):
    _, _, root, catalog, _, navigate, _ = mapping_ui
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
    assert not editor.property("dirty")
    navigate("presentationGestureScene")
    assert not editor.property("dirty") and editor.property("savedId") == "powerpoint:next"
    stage(root, "powerpoint:previous")
    assert catalog.removeApplication(bundle)
    assert catalog.addApplication(bundle)
    QTest.qWait(40)
    navigate("gestureApplication_" + bundle)
    assert not editor.property("dirty")


def test_hover_is_stable_and_voice_category_opens_existing_mapping_dialog(mapping_ui):
    _, _, root, _, _, _, _ = mapping_ui
    chip = find(root, "gestureCard_snap")
    pos = chip.mapToScene(QPointF(chip.width()/2, chip.height()/2)).toPoint()
    QTest.mouseMove(root, pos)
    QTest.qWait(180)
    color = chip.property("background").property("color")
    QTest.qWait(700)
    assert chip.property("hovered") and chip.property("background").property("color") == color
    click(root, "gestureMappingDropdown")
    click(root, "gestureCategory_voice")
    assert find(root, "voiceGestureBindingDialog").property("visible")
    QMetaObject.invokeMethod(find(root, "voiceGestureBindingDialog"), "close")


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
    assert dict(c.ringGestures.globalBindings) == before_globals
    if application_scope:
        assert catalog.bindings[BUNDLE] == {}
    click(root, "saveMenuMappingButton")
    editor = find(root, "gestureActionEditor")
    assert not editor.property("dirty") and not editor.property("feedbackError")
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
    assert find(root, "menuActionShortcut_" + action["id"]).property("text") == "播放器控制"
    assert "不发送键盘按键" in find(root, "menuActionDescription_" + action["id"]).property("text")
