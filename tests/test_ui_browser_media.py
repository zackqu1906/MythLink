from pathlib import Path
import os
import pytest
from test_ui_application_mapping import inline_ui, mapping_ui, wait_for_icons

SAFARI = "com.apple.Safari"

@pytest.mark.parametrize("size", [(1440, 1200), (940, 700)])
def test_automatic_scenes_shared_navigation_and_independent_drafts(mapping_ui, tmp_path, size):
    from PySide6.QtCore import QObject, QPointF
    from PySide6.QtTest import QTest
    c, bridge, root, catalog, _, click, _ = mapping_ui
    before, messages = c.gestureBindings, list(bridge.messages)
    root.resize(*size)
    catalog._candidates = [dict(value=SAFARI, label="Safari", path="/Applications/Safari.app")]
    assert catalog.addApplication(SAFARI)
    QTest.qWait(60)
    click("gestureApplication_" + SAFARI)
    assert root.findChild(QObject, "gestureWebsiteBar") is None
    assert root.findChild(QObject, "addGestureWebsiteButton") is None
    assert root.findChild(QObject, "addGestureWebsiteDialog") is None
    editor = root.findChild(QObject, "gestureActionEditor")
    click("videoGestureScene")
    click("gestureCard_circle-clockwise")
    assert editor.property("sharedSceneNavigation") and not editor.property("editable")
    assert editor.property("savedId") == "browser:next"
    assert "Ctrl+Tab" in root.findChild(QObject,"selectedGestureDescription").property("text")
    click("pdfGestureScene")
    click("gestureCard_circle-counterclockwise")
    assert editor.property("savedId") == "browser:previous" and not editor.property("editable")
    click("regularGestureScene")
    assert editor.property("editable") and editor.property("sharedNavigation")
    click("videoGestureScene")
    click("gestureCard_swipe-right")
    click("menuAction_web-video:backward")
    assert editor.property("dirty")
    click("pdfGestureScene")
    assert not editor.property("dirty")
    click("videoGestureScene")
    assert editor.property("dirty") and editor.property("draftId") == "web-video:backward"
    click("saveMenuMappingButton")
    assert catalog.bindings[SAFARI]["swipe-right"]["shortcut"] == "Left"
    assert not editor.property("dirty")
    click("gestureCard_circle-clockwise")
    shots=Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR",str(tmp_path)));shots.mkdir(parents=True,exist_ok=True)
    page=root.findChild(QObject,"gesturesPage");scroll=page.property("contentItem")
    scroll.setProperty("contentY",0);QTest.qWait(100);wait_for_icons(root)
    assert root.grabWindow().save(str(shots/f"browser-auto-scenes-{size[0]}.png"))
    # Re-evaluate shared state when switching applications without changing gesture.
    other="com.apple.Music"
    catalog._candidates=[dict(value=other,label="Music")];assert catalog.addApplication(other)
    click("gestureApplication_"+other);click("musicGestureScene")
    assert not editor.property("sharedNavigation") and editor.property("editable")
    assert c.gestureBindings==before and bridge.messages==messages
