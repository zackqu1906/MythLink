from pathlib import Path
import os

import pytest

from test_ui_application_mapping import inline_ui, mapping_ui, wait_for_icons

SAFARI = "com.apple.Safari"


@pytest.mark.parametrize("size", [(1440, 1200), (940, 700)])
def test_website_presets_drafts_custom_sites_and_removal(mapping_ui, tmp_path, size):
    from PySide6.QtCore import QObject, QPointF
    from PySide6.QtTest import QTest
    c, bridge, root, catalog, _, click, _ = mapping_ui
    before, messages = c.gestureBindings, list(bridge.messages)
    root.resize(*size)
    catalog._candidates = [dict(value=SAFARI, label="Safari", path="/Applications/Safari.app")]
    assert catalog.addApplication(SAFARI)
    catalog.selectScene("video")
    assert catalog.bindings[SAFARI]["tap"]["id"] == "web-video:play"
    catalog.setBinding(SAFARI, "tap", "")  # Exercise an unsaved draft independently of the default.
    catalog.selectScene("regular")
    QTest.qWait(60)
    click("gestureApplication_" + SAFARI)
    assert not root.findChild(QObject, "gestureWebsiteBar").property("visible")
    click("videoGestureScene")
    assert root.findChild(QObject, "gestureWebsiteBar").property("visible")
    click("gestureCard_tap")
    click("menuAction_web-video:play")
    editor = root.findChild(QObject, "gestureActionEditor")
    assert editor.property("dirty")
    click("addGestureWebsiteButton")
    QTest.qWait(180)
    field = root.findChild(QObject, "gestureWebsiteDomain")
    field.setProperty("text", "invalid host")
    click("confirmAddGestureWebsite")
    dialog = root.findChild(QObject, "addGestureWebsiteDialog")
    assert dialog.property("visible") and dialog.property("errorMessage")
    click("suggestBilibiliWebsite")
    assert field.property("text") == "bilibili.com"
    click("confirmAddGestureWebsite")
    QTest.qWait(300)
    assert not dialog.property("visible") and catalog.selectedWebsite == "bilibili.com", (dialog.property("application"), dialog.property("errorMessage"), catalog._bundle, catalog._scene, catalog.websites)
    assert editor.property("savedId") == "web-video:play" and not editor.property("dirty")
    assert len(catalog.bindings[SAFARI]) == 5 and not catalog.voice_overridden(SAFARI)
    click("gestureCard_swipe-right")
    assert editor.property("savedId") == "web-video:forward"
    click("menuAction_web-video:backward")
    click("gestureWebsite_")
    click("gestureCard_tap")
    assert editor.property("dirty") and editor.property("draftId") == "web-video:play"
    click("saveMenuMappingButton")
    click("gestureWebsite_bilibili.com")
    click("gestureCard_swipe-right")
    assert editor.property("dirty") and editor.property("draftId") == "web-video:backward"
    click("saveMenuMappingButton")
    assert catalog.bindings[SAFARI]["swipe-right"]["shortcut"] == "Left"
    shots = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    page = root.findChild(QObject, "gesturesPage")
    scroll = page.property("contentItem")
    scroll.setProperty("contentY", 0)
    QTest.qWait(100)
    wait_for_icons(root)
    assert root.grabWindow().save(str(shots / f"safari-websites-{size[0]}.png"))
    scroll.setProperty("contentY", max(0, scroll.property("contentHeight") - page.property("availableHeight")))
    QTest.qWait(100)
    assert root.grabWindow().save(str(shots / f"safari-website-editor-{size[0]}.png"))
    save = root.findChild(QObject, "saveMenuMappingButton")
    assert 0 <= save.mapToScene(QPointF()).y() < root.height() - save.height() + 1
    click("menuAction_web-video:forward")  # Draft discarded only for the removed website.
    click("removeGestureWebsiteButton")
    QTest.qWait(150)
    assert root.findChild(QObject, "removeGestureWebsiteDialog").property("visible")
    click("confirmRemoveGestureWebsite")
    QTest.qWait(100)
    assert catalog.selectedWebsite == "" and len(catalog.websites) == 1
    assert catalog.bindings[SAFARI]["tap"]["shortcut"] == "Space"
    assert not editor.property("dirty")
    click("addGestureWebsiteButton")
    QTest.qWait(160)
    field.setProperty("text", "https://player.example.com/watch?private=not-saved")
    click("confirmAddGestureWebsite")
    QTest.qWait(150)
    assert catalog.selectedWebsite == "player.example.com" and catalog.bindings[SAFARI] == {}
    assert root.findChild(QObject, "gestureWebsiteHint").property("text").find("player.example.com") >= 0
    assert c.gestureBindings == before and bridge.messages == messages
