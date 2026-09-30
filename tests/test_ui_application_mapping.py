"""Exercise the new mapping UI with a fake menu provider and isolated settings."""
from pathlib import Path
import os

import pytest

from test_inline_ui import inline_ui
from test_ui_shell import visual_child

BUNDLE = "com.example.Editor"
ACTIONS = [
    dict(id="next-tab", label="下一个标签页", path="窗口 › 下一个标签页", shortcut="Ctrl+Tab", available=True),
    dict(id="previous-tab", label="上一个标签页", path="窗口 › 上一个标签页", shortcut="Ctrl+Shift+Tab", available=True),
    dict(id="new-tab", label="新建标签页", path="文件 › 新建标签页", shortcut="Cmd+T", available=True),
    dict(id="reopen-tab", label="重新打开关闭的标签页", path="文件 › 重新打开关闭的标签页", shortcut="Cmd+Shift+T", available=False),
]


def wait_for_icons(root):
    import time
    from PySide6.QtCore import QCoreApplication
    def icons(item):
        result = [item] if item.objectName() == "nativeApplicationIcon" else []
        for child in item.childItems():
            result.extend(icons(child))
        return result
    for _ in range(150):
        QCoreApplication.processEvents()
        current = icons(root.contentItem())
        if current and all(item.parent().property("ready") for item in current):
            return
        time.sleep(.02)  # Let the Python async image provider acquire the GIL.
    assert current and all(item.parent().property("ready") for item in current)


@pytest.mark.parametrize("size", [(1440, 1040), (940, 700)])
def test_powerpoint_scene_tabs_reserved_gestures_and_separate_drafts(mapping_ui, tmp_path, size):
    from PySide6.QtCore import QObject
    from PySide6.QtTest import QTest
    from proximic_ring.gesture_scenes import POWERPOINT
    c, bridge, root, catalog, _, click, _ = mapping_ui
    root.resize(*size)
    before, messages = c.gestureBindings, list(bridge.messages)
    catalog._candidates = [dict(value=POWERPOINT, label="Microsoft PowerPoint", path="/System/Applications/TextEdit.app")]
    assert catalog.addApplication(POWERPOINT)
    QTest.qWait(60)
    click("gestureApplication_" + POWERPOINT)
    click("gestureCard_swipe-right")
    editor = root.findChild(QObject, "gestureActionEditor")
    assert not editor.property("editable") and catalog.bindingCount(POWERPOINT) == 0
    click("presentationGestureScene")
    assert editor.property("editable") and catalog.selectedScene == "presentation"
    click("menuAction_powerpoint:next")
    assert editor.property("dirty") and catalog.bindingCount(POWERPOINT) == 0
    click("regularGestureScene")
    assert not editor.property("editable") and not editor.property("dirty")
    click("presentationGestureScene")
    assert editor.property("dirty")
    click("saveMenuMappingButton")
    assert catalog.bindings[POWERPOINT]["swipe-right"]["shortcut"] == "Right"
    assert catalog.regularBindings[POWERPOINT] == {}
    click("gestureCard_swipe-left")
    click("menuAction_powerpoint:previous")
    click("saveMenuMappingButton")
    for gesture in ("tap", "clench", "swipe-up", "swipe-down"):
        click("gestureCard_" + gesture)
        assert editor.property("editable")
    for gesture in ("index-pinch", "middle-pinch"):
        click("gestureCard_" + gesture)
        assert not editor.property("editable")
    click("gestureCard_swipe-right")
    click("globalGestureScope")
    assert not editor.property("editable")
    click("gestureApplication_" + POWERPOINT)
    assert editor.property("editable")
    shots = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    page = root.findChild(QObject, "gesturesPage")
    if size[0] == 940:
        scroll = page.property("contentItem")
        scroll.setProperty("contentY", max(0, scroll.property("contentHeight") - page.property("availableHeight")))
    QTest.qWait(100)
    wait_for_icons(root)
    assert root.grabWindow().save(str(shots / f"powerpoint-scenes-{size[0]}.png"))
    click("menuAction_")
    click("saveMenuMappingButton")
    assert "swipe-right" not in catalog.bindings[POWERPOINT]
    assert c.gestureBindings == before and bridge.messages == messages


@pytest.mark.parametrize("bundle,label,profile", [
    ("com.kingsoft.wpsoffice.mac", "WPS Office", "wps"),
    ("com.apple.iWork.Keynote", "Keynote", "keynote"),
    ("org.libreoffice.script", "LibreOffice", "libreoffice"),
    ("asc.onlyoffice.ONLYOFFICE", "ONLYOFFICE", "onlyoffice"),
    ("test.slides.editor", "Slides Editor", "generic"),
])
def test_added_presenters_automatically_show_scene_without_extra_setup(mapping_ui, bundle, label, profile):
    from PySide6.QtCore import QObject
    from PySide6.QtTest import QTest
    c, bridge, root, catalog, _, click, _ = mapping_ui
    before, messages = c.gestureBindings, list(bridge.messages)
    catalog._candidates = [dict(value=bundle, label=label, path="", presentationProfile=profile)]
    assert catalog.addApplication(bundle)
    QTest.qWait(60)
    click("gestureApplication_" + bundle)
    bar = root.findChild(QObject, "applicationSceneBar")
    assert bar.property("visible") and catalog.bindingCount(bundle) == 0
    click("gestureCard_swipe-right")
    editor = root.findChild(QObject, "gestureActionEditor")
    assert not editor.property("editable")
    click("presentationGestureScene")
    assert editor.property("editable")
    assert "PowerPoint" not in root.findChild(QObject, "gestureSceneHint").property("text")
    if profile != "generic":
        catalog._busy = True
        catalog.discoveryChanged.emit()
        click("menuAction_" + profile + ":next")
        assert root.findChild(QObject, "saveMenuMappingButton").property("enabled")
        click("saveMenuMappingButton")
        assert catalog.bindings[bundle]["swipe-right"]["shortcut"] == "Right"
        assert "PowerPoint" not in root.findChild(QObject, "savedMenuBindingStatus").property("text")
    else:
        assert not any(a.get("preset") for a in catalog.actions)
        assert root.findChild(QObject, "customShortcutButton").property("visible")
    click("gestureCard_index-pinch")
    assert not editor.property("editable")
    assert c.gestureBindings == before and bridge.messages == messages


def test_wps_start_and_opposite_swipes_save_independently_in_their_scopes(mapping_ui):
    from PySide6.QtTest import QTest
    from proximic_ring.ui.application_mapping_controller import SETTINGS_KEY
    import json
    _, _, _, catalog, _, click, _ = mapping_ui
    bundle = "com.kingsoft.wpsoffice.mac"
    catalog._candidates = [dict(value=bundle, label="WPS Office", path="", presentationProfile="wps")]
    assert catalog.addApplication(bundle)
    QTest.qWait(60)
    click("gestureApplication_" + bundle)
    click("gestureCard_snap")
    click("menuAction_wps:start-first")
    click("saveMenuMappingButton")
    click("presentationGestureScene")
    click("gestureCard_swipe-left")
    click("menuAction_wps:previous")
    click("saveMenuMappingButton")
    click("gestureCard_swipe-right")
    click("menuAction_wps:next")
    click("saveMenuMappingButton")
    saved = json.loads(catalog.service.owner._settings.value(SETTINGS_KEY))[bundle]
    assert saved["bindings"]["snap"]["shortcut"] == "Cmd+Shift+Return"
    assert saved["scenes"]["presentation"]["swipe-left"]["shortcut"] == "Backspace"
    assert saved["scenes"]["presentation"]["swipe-right"]["shortcut"] == "Right"


@pytest.fixture
def mapping_ui(inline_ui, monkeypatch):
    from PySide6.QtCore import QObject, QMetaObject
    from PySide6.QtTest import QTest
    c, bridge, _, root, _ = inline_ui
    if not c.appGestures.supported:
        pytest.skip("Menu mappings use macOS Accessibility")
    catalog = c.appGestures.catalog
    requests = []
    def discover(kind, bundle=""):
        catalog._generation[kind] += 1
        requests.append((kind, bundle))
        result = ([dict(value=BUNDLE, label="文本编辑", path="/System/Applications/TextEdit.app", running=True),
                   dict(value="com.example.Other", label="终端", path="/System/Applications/Utilities/Terminal.app", running=False)]
                  if kind == "apps" else dict(actions=ACTIONS))
        catalog._apply_result(kind, catalog._generation[kind], bundle, result, "")
    monkeypatch.setattr(catalog, "_request", discover)
    root.setProperty("currentPage", 2)
    root.show()
    QTest.qWait(80)
    def click(name):
        item = root.findChild(QObject, name) or visual_child(root.contentItem(), name)
        assert item is not None, name
        assert QMetaObject.invokeMethod(item, "click")
        QTest.qWait(30)
    def add(bundle=BUNDLE):
        click("addGestureApplicationButton")
        QTest.qWait(180)
        root.findChild(QObject, "installedApplicationSearch").setProperty("text", bundle)
        QTest.qWait(40)
        click("addGestureApp_" + bundle)
        QTest.qWait(180)
    yield c, bridge, root, catalog, requests, click, add


def test_empty_application_list_and_explicit_add_do_not_create_bindings(mapping_ui, tmp_path):
    from PySide6.QtCore import QObject
    c, bridge, root, catalog, requests, click, add = mapping_ui
    before = c.gestureBindings
    messages = list(bridge.messages)
    assert catalog.apps == [] and requests == []
    assert root.findChild(QObject, "gestureProfilePicker") is None
    assert root.findChild(QObject, "gestureApplicationTabs").property("count") == 0
    editor = root.findChild(QObject, "gestureActionEditor")
    assert not editor.property("editable")
    assert not any("添加" in str(item.property("text") or "") and item.property("visible")
                   for item in editor.findChildren(QObject))
    click("addGestureApplicationButton")
    from PySide6.QtTest import QTest
    QTest.qWait(180)
    shots = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    wait_for_icons(root)
    assert root.grabWindow().save(str(shots / "add-application.png"))
    click("addGestureApp_" + BUNDLE)
    assert [app["value"] for app in catalog.apps] == [BUNDLE]
    assert root.findChild(QObject, "gestureApplicationTabs").property("count") == 1
    assert visual_child(root.contentItem(), "gestureApplication_" + BUNDLE).property("checked")
    assert catalog.bindings[BUNDLE] == {}
    assert catalog.actions == ACTIONS
    assert root.findChild(QObject, "gestureActionEditor").property("editable")
    assert c.gestureBindings == before and bridge.messages == messages


@pytest.mark.parametrize("size", [(1440, 940), (940, 700)])
def test_choose_save_clear_and_cancel_mapping_without_changing_voice(mapping_ui, tmp_path, size):
    from PySide6.QtCore import QObject, QPointF
    from PySide6.QtTest import QTest
    c, bridge, root, catalog, requests, click, add = mapping_ui
    root.resize(*size)
    before = c.gestureBindings
    messages = list(bridge.messages)
    add()
    editor = root.findChild(QObject, "gestureActionEditor")
    save = root.findChild(QObject, "saveMenuMappingButton")
    assert not save.property("enabled")
    click("menuAction_next-tab")
    assert editor.property("dirty") and save.property("enabled")
    assert catalog.bindings[BUNDLE] == {}  # Selection remains a draft until Save.
    click("gestureCard_circle-counterclockwise")
    assert not editor.property("dirty")
    click("gestureCard_circle-clockwise")
    assert editor.property("dirty") and editor.property("draftId") == "next-tab"
    click("saveMenuMappingButton")
    assert catalog.bindings[BUNDLE]["circle-clockwise"]["shortcut"] == "Ctrl+Tab"
    assert not editor.property("dirty") and "已保存" in editor.property("feedback")
    click("menuAction_previous-tab")
    click("cancelMenuMappingButton")
    assert editor.property("draftId") == "next-tab" and not editor.property("dirty")
    search = root.findChild(QObject, "menuActionSearch")
    search.setProperty("text", "未暴露的动作")
    QTest.qWait(30)
    assert root.findChild(QObject, "menuActionList").property("count") == 1  # Only 'None'.
    search.setProperty("text", "")
    shots = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    if size[0] == 940:
        page = root.findChild(QObject, "gesturesPage")
        # At the narrow layout the editor follows the gesture grid in one scroll area.
        scroll = page.property("contentItem")
        scroll.setProperty("contentY", max(0, scroll.property("contentHeight") - page.property("availableHeight")))
    QTest.qWait(100)
    wait_for_icons(root)
    assert root.grabWindow().save(str(shots / f"menu-mapping-{size[0]}.png"))
    save_position = save.mapToScene(QPointF())
    assert 0 <= save_position.y() and save_position.y() + save.height() <= root.height()
    for child in editor.childItems():
        if child.isVisible():
            pos = child.mapToItem(editor, QPointF())
            assert pos.x() >= 0 and pos.x() + child.width() <= editor.width() + 1
    click("menuAction_")
    click("saveMenuMappingButton")
    assert catalog.bindings[BUNDLE] == {} and c.gestureBindings == before
    assert bridge.messages == messages


def test_locked_gestures_and_stale_menu_result_cannot_save(mapping_ui):
    from PySide6.QtCore import QObject
    _, _, root, catalog, requests, click, add = mapping_ui
    add()
    editor = root.findChild(QObject, "gestureActionEditor")
    for gesture in ("tap", "swipe-left", "swipe-right", "clench", "swipe-up", "swipe-down"):
        click("gestureCard_" + gesture)
        assert not editor.property("editable")
        assert not root.findChild(QObject, "saveMenuMappingButton").property("visible")
    click("gestureCard_circle-clockwise")
    click("menuAction_next-tab")
    # A refreshed menu may remove a shortcut while a local draft is open.
    catalog._apply_result("menu", catalog._generation["menu"], BUNDLE, dict(actions=[]), "")
    assert editor.property("dirty") and not root.findChild(QObject, "saveMenuMappingButton").property("enabled")
    assert catalog.bindings[BUNDLE] == {}
    add("com.example.Other")
    assert editor.property("bundle") == "com.example.Other" and not editor.property("dirty")
    catalog.selectApplication(BUNDLE)
    assert editor.property("draftId") == "next-tab"


@pytest.mark.parametrize("size", [(1440, 940), (940, 700)])
def test_custom_shortcut_recording_draft_save_and_menu_error(mapping_ui, tmp_path, size):
    from PySide6.QtCore import QObject, QPointF, Qt
    from PySide6.QtTest import QTest
    c, bridge, root, catalog, _, click, add = mapping_ui
    root.resize(*size)
    add()
    before, messages = c.gestureBindings, list(bridge.messages)
    editor = root.findChild(QObject, "gestureActionEditor")
    catalog._apply_result("menu", catalog._generation["menu"], BUNDLE, None, "应用未公开菜单")
    click("customShortcutButton")
    QTest.qWait(180)
    dialog = root.findChild(QObject, "customShortcutDialog")
    assert dialog.property("opened"), {k: dialog.property(k) for k in ("visible", "width", "height", "x", "y")}
    root.findChild(QObject, "customShortcutName").setProperty("text", "上一个任务")
    field = root.findChild(QObject, "customShortcutRecorder")
    position = field.mapToScene(QPointF(field.width()/2, field.height()/2)).toPoint()
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, position)
    assert c.appGestures.recording
    QTest.keyClick(root, Qt.Key_BracketLeft, Qt.ControlModifier | Qt.ShiftModifier)
    assert dialog.property("capturedShortcut") == "Cmd+Shift+[" and not c.appGestures.recording
    shots = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(shots / f"custom-shortcut-{size[0]}.png"))
    click("useCustomShortcutButton")
    QTest.qWait(160)
    assert editor.property("dirty") and catalog.bindings[BUNDLE] == {}
    click("gestureCard_circle-counterclockwise")
    click("gestureCard_circle-clockwise")
    assert editor.property("dirty") and root.findChild(QObject, "saveMenuMappingButton").property("enabled")
    click("saveMenuMappingButton")
    assert catalog.bindings[BUNDLE]["circle-clockwise"]["shortcut"] == "Cmd+Shift+["
    assert "自定义快捷键" in root.findChild(QObject, "savedMenuBindingStatus").property("text")
    assert not editor.property("dirty") and c.gestureBindings == before and bridge.messages == messages
    click("customShortcutButton")
    QTest.qWait(180)
    assert dialog.property("capturedShortcut") == ""
    position = field.mapToScene(QPointF(field.width()/2, field.height()/2)).toPoint()
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, position)
    click("cancelCustomShortcutButton")
    QTest.qWait(180)
    assert not c.appGestures.recording and not editor.property("dirty")


def test_manual_custom_shortcut_validation_and_clearing_saved_custom_action(mapping_ui):
    from PySide6.QtCore import QObject
    from PySide6.QtTest import QTest
    _, _, root, catalog, _, click, add = mapping_ui
    add()
    click("customShortcutButton")
    QTest.qWait(150)
    root.findChild(QObject, "customShortcutName").setProperty("text", "窗口靠左")
    click("customShortcutEntryMode")
    text = root.findChild(QObject, "customShortcutText")
    text.setProperty("text", "Cmd+K, Cmd+N")
    click("useCustomShortcutButton")
    dialog = root.findChild(QObject, "customShortcutDialog")
    assert dialog.property("opened") and dialog.property("errorText") and catalog.bindings[BUNDLE] == {}
    text.setProperty("text", "ctrl+fn+left")
    click("useCustomShortcutButton")
    QTest.qWait(150)
    assert not dialog.property("visible") and catalog.bindings[BUNDLE] == {}
    click("saveMenuMappingButton")
    assert catalog.bindings[BUNDLE]["circle-clockwise"]["shortcut"] == "Ctrl+Fn+Left"
    listing = root.findChild(QObject, "menuActionList")
    assert listing.property("count") == len(ACTIONS) + 2
    catalog.clearApplicationBindings(BUNDLE)
    QTest.qWait(30)
    assert listing.property("count") == len(ACTIONS) + 1


def test_search_finds_unopened_apps_and_icon_tabs_switch_scope(mapping_ui, tmp_path):
    from PySide6.QtCore import QObject
    from PySide6.QtTest import QTest
    _, bridge, root, catalog, requests, click, add = mapping_ui
    messages = list(bridge.messages)
    click("addGestureApplicationButton")
    QTest.qWait(220)
    search = root.findChild(QObject, "installedApplicationSearch")
    listing = root.findChild(QObject, "availableGestureApplications")
    assert search.property("activeFocus")
    assert listing.property("count") == 1
    assert root.findChild(QObject, "applicationResultsHeading").property("text") == "当前已打开"
    search.setProperty("text", "终端")
    QTest.qWait(30)
    assert listing.property("count") == 1
    click("addGestureApp_com.example.Other")
    QTest.qWait(180)
    assert catalog.selectedApp == "com.example.Other"
    assert catalog.apps[0]["path"].endswith("Terminal.app")
    add()
    assert root.findChild(QObject, "gestureApplicationTabs").property("count") == 2
    wait_for_icons(root)
    shots = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(shots / "application-tabs-1440.png"))
    click("globalGestureScope")
    editor = root.findChild(QObject, "gestureActionEditor")
    assert not editor.property("editable")
    assert root.findChild(QObject, "gestureApplicationTabs").property("currentIndex") == -1
    click("gestureApplication_com.example.Other")
    assert catalog.selectedApp == "com.example.Other" and editor.property("editable")
    add("com.example.Other")
    assert len(catalog.apps) == 2  # Selecting an added application never duplicates it.
    assert bridge.messages == messages


def test_many_application_icons_keep_add_button_visible_in_small_window(mapping_ui, tmp_path):
    from PySide6.QtCore import QObject, QPointF
    from PySide6.QtTest import QTest
    _, _, root, catalog, requests, click, add = mapping_ui
    root.resize(940, 700)
    add()
    catalog._candidates += [dict(value=f"com.example.App{i}", label=f"应用 {i}",
                                path="/System/Applications/TextEdit.app", running=False) for i in range(14)]
    for i in range(14):
        catalog.addApplication(f"com.example.App{i}")
    QTest.qWait(120)
    tabs = root.findChild(QObject, "gestureApplicationTabs")
    assert tabs.property("count") == 15 and tabs.property("currentIndex") == 14
    selected = visual_child(root.contentItem(), "gestureApplication_com.example.App13")
    assert selected is not None
    pos = selected.mapToItem(tabs, QPointF())
    assert 0 <= pos.x() and pos.x() + selected.width() <= tabs.width() + 1
    button = root.findChild(QObject, "addGestureApplicationButton")
    pos = button.mapToScene(QPointF())
    assert 0 <= pos.x() and pos.x() + button.width() <= root.width()
    shots = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    wait_for_icons(root)
    assert root.grabWindow().save(str(shots / "application-icons-940.png"))


@pytest.mark.parametrize("size", [(1440, 940), (940, 700)])
def test_clear_app_confirmation_discards_only_its_drafts(mapping_ui, tmp_path, size):
    from PySide6.QtCore import QObject, QPointF
    from PySide6.QtTest import QTest
    controller, bridge, root, catalog, _, click, add = mapping_ui
    root.resize(*size)
    before = controller.gestureBindings
    messages = list(bridge.messages)
    add()
    click("menuAction_next-tab")
    click("saveMenuMappingButton")
    click("menuAction_previous-tab")
    add("com.example.Other")
    click("menuAction_new-tab")
    click("saveMenuMappingButton")
    click("menuAction_previous-tab")
    other_bindings = catalog.bindings["com.example.Other"]
    click("gestureApplication_" + BUNDLE)
    editor = root.findChild(QObject, "gestureActionEditor")
    click("manageApplicationButton")
    click("clearApplicationMappings")
    dialog = root.findChild(QObject, "applicationManagementDialog")
    assert dialog.property("application") == BUNDLE
    assert dialog.property("bindingCount") == 1 and dialog.property("draftCount") == 1
    click("cancelApplicationManagement")
    QTest.qWait(100)
    assert catalog.bindings[BUNDLE] and editor.property("dirty")
    click("manageApplicationButton")
    click("clearApplicationMappings")
    QTest.qWait(150)
    shots = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(shots / f"clear-application-{size[0]}.png"))
    button = root.findChild(QObject, "confirmApplicationManagement")
    position = button.mapToScene(QPointF())
    assert 0 <= position.x() and position.x() + button.width() <= root.width()
    assert 0 <= position.y() and position.y() + button.height() <= root.height()
    # Even if selection changes during the dialog, its reviewed application is fixed.
    catalog.selectApplication("com.example.Other")
    assert dialog.property("application") == BUNDLE
    click("confirmApplicationManagement")
    QTest.qWait(100)
    assert not dialog.property("visible")
    assert catalog.bindings[BUNDLE] == {} and catalog.selectedApp == "com.example.Other"
    assert len(catalog.apps) == 2 and catalog.bindings["com.example.Other"] == other_bindings
    assert editor.property("dirty") and editor.property("draftId") == "previous-tab"
    click("gestureApplication_" + BUNDLE)
    assert not editor.property("dirty")
    assert controller.gestureBindings == before and bridge.messages == messages


def test_remove_application_returns_to_global_and_readd_starts_empty(mapping_ui, tmp_path):
    from PySide6.QtCore import QObject
    from PySide6.QtTest import QTest
    controller, bridge, root, catalog, _, click, add = mapping_ui
    before = controller.gestureBindings
    messages = list(bridge.messages)
    add()
    click("menuAction_next-tab")
    click("saveMenuMappingButton")
    click("menuAction_previous-tab")
    click("manageApplicationButton")
    click("removeGestureApplication")
    click("cancelApplicationManagement")
    QTest.qWait(100)
    assert len(catalog.apps) == 1
    click("manageApplicationButton")
    click("removeGestureApplication")
    QTest.qWait(150)
    shots = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(shots / "remove-application.png"))
    click("confirmApplicationManagement")
    QTest.qWait(100)
    assert catalog.apps == [] and catalog.bindings == {}
    assert root.findChild(QObject, "gestureApplicationTabs").property("count") == 0
    assert not root.findChild(QObject, "gesturesPage").property("applicationScope")
    assert not root.findChild(QObject, "applicationManagementBar").property("visible")
    add()
    editor = root.findChild(QObject, "gestureActionEditor")
    assert catalog.bindings[BUNDLE] == {} and not editor.property("dirty")
    assert editor.property("draftId") == ""
    assert controller.gestureBindings == before and bridge.messages == messages


def test_saved_binding_status_distinguishes_missing_partial_and_failed_reads(mapping_ui, tmp_path):
    from PySide6.QtCore import QObject, QPointF
    from PySide6.QtTest import QTest
    _, bridge, root, catalog, _, click, add = mapping_ui
    add()
    click("menuAction_next-tab")
    click("saveMenuMappingButton")
    saved = catalog.bindings
    messages = list(bridge.messages)
    status = root.findChild(QObject, "savedMenuBindingStatus")
    assert status.property("text") == "上次读取时菜单可用"
    for result, error, expected in [
        (dict(actions=[dict(ACTIONS[0], available=False)]), "", "上次读取时菜单不可用"),
        (dict(actions=[dict(ACTIONS[0], available=None)]), "", "上次读取未能确认菜单状态"),
        (dict(actions=[], partial=True), "", "菜单未读全，暂未找到此快捷键"),
        (dict(actions=[]), "", "本次未找到此快捷键"),
        (None, "请先打开这个应用，再刷新快捷键", "尚未确认可用性 · 原绑定已保留"),
    ]:
        catalog._apply_result("menu", catalog._generation["menu"], BUNDLE, result, error)
        QTest.qWait(50)
        assert status.property("text") == expected
        assert catalog.bindings == saved
        save = root.findChild(QObject, "saveMenuMappingButton")
        assert save.mapToScene(QPointF()).y() + save.height() <= root.height()
    shots = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(shots / "application-menu-unavailable.png"))
    click("refreshApplicationMenu")
    assert status.property("text") == "上次读取时菜单可用"
    assert root.findChild(QObject, "applicationMenuReadTime").property("text").startswith("最近读取 ")
    assert bridge.messages == messages
