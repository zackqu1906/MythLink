import pytest
from PySide6.QtCore import QObject, QMetaObject, QPointF, Qt
from PySide6.QtTest import QTest, QSignalSpy

from test_inline_ui import inline_ui
from test_ui_figma_redesign import click, visual_child


def click_in_settings(root, name):
    item = root.findChild(QObject, name)
    scroll = root.findChild(QObject, "runtimeSettingsScroll")
    flick = scroll.property("contentItem")
    y = item.mapToItem(scroll, QPointF()).y()
    flick.setProperty("contentY", max(0, min(flick.property("contentY") + y - 80,
        flick.property("contentHeight") - flick.height())))
    QTest.qWait(40)
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier,
                    item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint())


@pytest.mark.parametrize("closing", ["escape", "header", "footer"])
def test_closing_log_preserves_settings_and_scroll(inline_ui, closing):
    _, _, _, root, _ = inline_ui
    root.resize(940, 700)
    root.show()
    click(root, "runtimeSettingsButton")
    QTest.qWait(160)
    settings = root.findChild(QObject, "runtimeSettingsDialog")
    hidden = QSignalSpy(settings.aboutToHide)
    click_in_settings(root, "runtimeLogButton")
    QTest.qWait(180)
    flick = root.findChild(QObject, "runtimeSettingsScroll").property("contentItem")
    scroll_y = flick.property("contentY")
    dialog = root.findChild(QObject, "runtimeLogDialog")
    assert dialog.property("opened")
    if closing == "escape":
        QTest.keyClick(root, Qt.Key_Escape)
    elif closing == "header":
        click(root, "runtimeLogDialogCloseButton")
    else:
        click(root, "runtimeLogFooterCloseButton")
    QTest.qWait(160)
    assert not dialog.property("visible")
    assert settings.property("opened") and hidden.count() == 0
    assert flick.property("contentY") == scroll_y


def wait_discovered(selection):
    for _ in range(200):
        QTest.qWait(10)
        if not selection.loading:
            assert not selection.error
            return
    pytest.fail("Application discovery did not finish")


@pytest.mark.parametrize("size", [(1200, 780), (940, 700), (940, 560)])
def test_themed_picker_fresh_choice_icons_folder_and_cancel(inline_ui, monkeypatch, tmp_path, size):
    from PySide6.QtWidgets import QFileDialog
    from zipfile import ZipFile
    import json
    controller, bridge, _, root, _ = inline_ui
    for identity, app in [("one", "Safari"), ("two", "TextEdit"), ("three", "com.apple.notes")]:
        controller._gesture_history.observe(identity, gesture="tap", application=app)
    controller._gesture_history.records()
    selection = controller.exportSelection
    before = list(bridge.messages)
    root.resize(*size); root.show()
    root.setProperty("topPadding", 28)  # macOS traffic-light safe area.
    click(root, "runtimeSettingsButton"); QTest.qWait(160)
    click_in_settings(root, "exportGestureHistoryButton")
    wait_discovered(selection); QTest.qWait(180)
    assert selection.kind == "gesture"
    assert selection.selectedKeys == [] and not selection.canExport
    assert selection.directory.endswith("Downloads")
    # All three entry points are in the same section with equal button geometry.
    section = root.findChild(QObject, "recordsAndExportsSection")
    buttons = [section.findChild(QObject, name) for name in
               ("exportVoiceHistoryButton", "exportGestureHistoryButton", "settingsExportDiagnosticLogButton")]
    assert all(buttons) and len({(b.width(), b.height()) for b in buttons}) == 1
    dialog = root.findChild(QObject, "exportApplicationDialog")
    icon = visual_child(root.contentItem(), "exportApplicationIcon_0")
    assert icon and icon.property("bundle")
    native = icon.findChild(QObject, "nativeApplicationIcon")
    assert native.property("source").toString().startswith("image://applicationicons/")
    listing = root.findChild(QObject, "exportApplicationList")
    assert listing.height() >= 56
    assert dialog.property("height") <= root.height() - 48
    for name in ("chooseExportFolderButton", "confirmExportButton", "exportRange_all"):
        button = visual_child(root.contentItem(), name)
        point = button.mapToScene(QPointF()).y()
        background = dialog.property("background")
        bottom = background.mapToScene(QPointF()).y() + background.height()
        assert 0 <= point < point + button.height() <= bottom - 12
    destination = tmp_path / "chosen-folder"
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: str(destination))
    click(root, "chooseExportFolderButton")
    assert selection.directory == str(destination)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: "")
    click(root, "chooseExportFolderButton")
    assert selection.directory == str(destination)
    click(root, "exportRange_week")
    assert selection.period == "week"
    row = visual_child(root.contentItem(), "exportApplication_0")
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, row.mapToScene(QPointF(25,25)).toPoint())
    assert len(selection.selectedKeys) == 1 and selection.canExport
    chosen = selection.selectedKeys[:]
    # With LaunchServices available, verify actual native images, not just URLs.
    for i, app in enumerate(selection.applications):
        if app["path"]:
            app_icon = visual_child(root.contentItem(), "exportApplicationIcon_" + str(i))
            for _ in range(200):
                if app_icon.property("ready"):
                    break
                QTest.qWait(10)
            assert app_icon.property("ready"), app
    QTest.qWait(100)
    suffix = "-native" if all(app["path"] for app in selection.applications) else ""
    assert root.grabWindow().save(f"/tmp/mythlink-export-applications-{size[0]}x{size[1]}{suffix}.png")
    click(root, "cancelExportButton"); QTest.qWait(120)
    assert not controller.gestureHistory.busy and not controller.gestureHistory.error
    assert not destination.exists()
    assert root.findChild(QObject, "runtimeSettingsDialog").property("opened")
    # Reopening resets the application selection and the default folder.
    click_in_settings(root, "exportGestureHistoryButton")
    wait_discovered(selection); QTest.qWait(150)
    assert selection.selectedKeys == [] and not selection.canExport
    assert selection.directory.endswith("Downloads")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: str(destination))
    monkeypatch.setattr("proximic_ring.ui.voice.history_export_controller.QDesktopServices.openUrl", lambda *_: True)
    click(root, "chooseExportFolderButton")
    selection.toggle(chosen[0])
    click(root, "confirmExportButton")
    for _ in range(300):
        QTest.qWait(10)
        if not controller.gestureHistory.busy:
            break
    assert not controller.gestureHistory.busy and not controller.gestureHistory.error, controller.gestureHistory.message
    with ZipFile(controller.gestureHistory.path) as archive:
        records = [json.loads(line) for line in archive.read("records.jsonl").splitlines()]
        assert len(records) == 1
    QTest.qWait(150)
    assert root.findChild(QObject, "runtimeSettingsDialog").property("opened")
    assert bridge.messages == before


def test_cancelled_discovery_cannot_fill_a_new_dialog(inline_ui, monkeypatch):
    import threading
    import proximic_ring.ui.export_picker as module
    controller, _, _, root, _ = inline_ui
    started, release = threading.Event(), threading.Event()
    def slow(**kwargs):
        started.set()
        assert release.wait(3)
        return [("com.apple.safari", "Safari")]
    monkeypatch.setattr(module, "discover_options", slow)
    root.show()
    controller.gestureHistory.export()
    assert started.wait(1)
    selection = controller.exportSelection
    selection.cancel()
    assert not controller.gestureHistory.busy
    controller.diagnosticExport.export("today")
    assert selection.kind == "diagnostic" and not selection.loading
    release.set()
    QTest.qWait(250)
    assert selection.applications == [] and selection.canExport
    assert root.findChild(QObject, "exportApplicationDialog").property("opened")
    QTest.keyClick(root, Qt.Key_Escape); QTest.qWait(160)
    assert not selection.active and not controller.diagnosticExport.busy


def test_grouped_view_entries_and_nested_gesture_export(inline_ui):
    controller, _, _, root, _ = inline_ui
    root.resize(940, 700); root.show()
    click(root, "runtimeSettingsButton"); QTest.qWait(160)
    section = root.findChild(QObject, "recordsAndExportsSection")
    scroll = root.findChild(QObject, "runtimeSettingsScroll")
    flick = scroll.property("contentItem")
    flick.setProperty("contentY", flick.property("contentY") + section.mapToItem(scroll, QPointF()).y() - 16)
    QTest.qWait(80)
    assert root.grabWindow().save("/tmp/mythlink-records-settings.png")
    click_in_settings(root, "viewGestureHistoryButton"); QTest.qWait(160)
    gesture = root.findChild(QObject, "gestureHistoryDialog")
    assert gesture.property("opened")
    click(root, "gestureHistoryExportButton")
    wait_discovered(controller.exportSelection); QTest.qWait(160)
    assert not controller.exportSelection.canExport  # Empty history cannot export.
    click(root, "cancelExportButton"); QTest.qWait(160)
    settings = root.findChild(QObject, "runtimeSettingsDialog")
    assert gesture.property("opened") and settings.property("opened")
    QTest.keyClick(root, Qt.Key_Escape); QTest.qWait(160)
    assert not gesture.property("visible") and settings.property("opened")
    click_in_settings(root, "viewVoiceHistoryButton"); QTest.qWait(160)
    assert root.property("currentPage") == 4 and not settings.property("visible")
