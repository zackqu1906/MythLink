"""Local account, three-row history, and the shared Figma preferences shell."""
import os
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QMetaObject, QPoint, QPointF, QSettings, Qt, QCoreApplication
from PySide6.QtGui import QGuiApplication, QWheelEvent
from PySide6.QtTest import QTest, QSignalSpy

from test_inline_ui import inline_ui
from test_ui_figma_redesign import click
from test_ui_shell import visual_child
from test_ui_voice_page import seed_history


def screenshot(root, tmp_path, name):
    folder = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    folder.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(folder / name))


@pytest.mark.parametrize("size", [(1200, 780), (940, 700)])
def test_local_login_accepts_unvalidated_text_persists_and_signs_out(inline_ui, tmp_path, size):
    from proximic_ring.ui.shared.local_profile import LocalProfile
    controller, bridge, _, root, event = inline_ui
    event(phase="idle", ready=True)
    root.resize(*size); root.show(); click(root, "mainNav0")
    before = list(bridge.messages)
    settings = {k: controller._settings.value(k) for k in controller._settings.allKeys()}
    click(root, "accountEntryButton"); QTest.qWait(160)
    screenshot(root, tmp_path, f"login-{size[0]}.png")
    name = root.findChild(QObject, "accountNameField")
    email = root.findChild(QObject, "accountEmailField")
    name.setProperty("text", "<测试用户> 无需验证")
    email.setProperty("text", "随意填写，不是邮箱")
    click(root, "accountSaveButton"); QTest.qWait(120)
    assert controller.localProfile.signedIn
    assert root.findChild(QObject, "accountHeaderName").property("text") == "<测试用户> 无需验证"
    assert root.findChild(QObject, "accountHeaderEmail").property("text") == "随意填写，不是邮箱"
    restored = LocalProfile(QSettings(controller._settings.fileName(), QSettings.IniFormat))
    assert restored.signedIn and restored.email == controller.localProfile.email
    header = root.findChild(QObject, "accountEntryButton")
    assert header.width() == 212 and header.height() == 48
    for label, height in [("accountHeaderName", 24), ("accountHeaderEmail", 22)]:
        item = root.findChild(QObject, label)
        assert item.width() == 164 and item.height() == height
    screenshot(root, tmp_path, f"signed-in-home-{size[0]}.png")
    click(root, "accountEntryButton"); QTest.qWait(160)
    screenshot(root, tmp_path, f"account-filled-{size[0]}.png")
    click(root, "accountEditButton")
    name.setProperty("text", "未保存的名字")
    QMetaObject.invokeMethod(root.findChild(QObject, "runtimeSettingsDialog"), "close")
    click(root, "accountEntryButton"); QTest.qWait(120)
    assert name.property("text") == controller.localProfile.displayName
    click(root, "accountSignOutButton"); QTest.qWait(120)
    assert not controller.localProfile.signedIn
    assert root.findChild(QObject, "accountHeaderName").property("text") == "登录"
    assert {k: controller._settings.value(k) for k in controller._settings.allKeys()} == settings
    assert bridge.messages == before


@pytest.mark.parametrize("size", [(1200, 780), (940, 700)])
def test_sidebar_mouse_navigation_keeps_one_preferences_window_open(inline_ui, size):
    controller, bridge, _, root, event = inline_ui
    event(phase="idle", ready=True)
    root.resize(*size); root.show(); root.setProperty("currentPage", 1)
    QTest.qWait(100)
    shell = root.findChild(QObject, "runtimeSettingsDialog")
    showing = QSignalSpy(shell.aboutToShow)
    hiding = QSignalSpy(shell.aboutToHide)
    before = list(bridge.messages)

    def mouse_click(name):
        item = visual_child(root.contentItem(), name) or root.findChild(QObject, name)
        assert item is not None and item.property("visible"), name
        position = item.mapToScene(QPointF(item.width()/2, item.height()/2)).toPoint()
        assert 0 <= position.x() < root.width() and 0 <= position.y() < root.height()
        QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, position)

    for entry, section in [("sidebarAccountButton", 0), ("runtimeSettingsButton", 1), ("helpButton", 2)]:
        mouse_click(entry); QTest.qWait(200)
        assert shell.property("opened") and shell.property("section") == section
        assert visual_child(root.contentItem(), f"preferencesNav{section}").property("checked")
        opened, closed = showing.count(), hiding.count()
        # Real pointer events must reach the sidebar even with the modal content
        # mounted. No opacity animation or overlay teardown during page changes.
        for target in [0, 1, 2, 1, 0, 2, 2]:
            mouse_click(f"preferencesNav{target}")
            for _ in range(8):
                QTest.qWait(20)
                assert shell.property("opened") and shell.property("opacity") == 1
                assert shell.property("section") == target
                assert showing.count() == opened and hiding.count() == closed
            assert visual_child(root.contentItem(), f"preferencesNav{target}").property("checked")
        mouse_click("preferencesNav1")
        mouse_click("settingsAudioEntry")
        assert root.findChild(QObject, "settingsAudioGroup").property("visible")
        mouse_click("preferencesNav1")
        assert not root.findChild(QObject, "settingsAudioGroup").property("visible")
        assert root.findChild(QObject, "settingsPermissionsGroup").property("visible")
        assert showing.count() == opened and hiding.count() == closed
        mouse_click("settingsApplyButton"); QTest.qWait(160)
        assert not shell.property("visible")
    assert bridge.messages == before


@pytest.mark.parametrize("size", [(1200, 780), (940, 700)])
def test_home_history_fits_three_rows_caps_ten_and_shows_footer(inline_ui, tmp_path, size):
    controller, bridge, _, root, event = inline_ui
    event(phase="idle", ready=True)
    rows = seed_history(controller)
    controller._voice_history_entries = rows + [dict(rows[0], interactionId=f"older-{i}", text=f"更早的记录 {i}") for i in range(8)]
    controller.voiceHistoryChanged.emit()
    root.resize(*size); root.show(); click(root, "mainNav0"); QTest.qWait(150)
    before = list(bridge.messages)
    history = root.findChild(QObject, "homeVoiceHistoryList")
    assert history.property("count") == 10
    assert controller.voiceHistoryModel.rowCount() == 11
    first = [visual_child(history, f"homeVoiceHistoryEntry{i}") for i in range(3)]
    assert all(first)
    assert history.height() == pytest.approx(sum(item.height() for item in first) + 2 * history.property("spacing"))
    assert not first[0].property("compact")
    QMetaObject.invokeMethod(visual_child(first[0], "voiceHistoryCopyButton"), "click")
    assert QGuiApplication.clipboard().text() == rows[0]["candidateText"]
    QMetaObject.invokeMethod(visual_child(first[0], "voiceHistoryDeleteButton"), "click"); QTest.qWait(80)
    confirm = root.findChild(QObject, "deleteHistoryDialog")
    assert confirm.property("visible") and confirm.property("interactionId") == rows[0]["interactionId"]
    QMetaObject.invokeMethod(confirm, "reject"); QTest.qWait(120)
    outer = root.findChild(QObject, "homePage").property("contentItem")
    outer.setProperty("contentY", max(0, outer.property("contentHeight") - outer.height()))
    QTest.qWait(100)
    screenshot(root, tmp_path, f"home-three-rows-{size[0]}.png")
    outer_y = outer.property("contentY")
    position = history.mapToScene(QPointF(history.width()/2, 40))
    event = QWheelEvent(position, QPointF(root.mapToGlobal(position.toPoint())), QPoint(0, -300), QPoint(0, -360), Qt.NoButton, Qt.NoModifier, Qt.ScrollUpdate, False)
    QCoreApplication.sendEvent(root, event); QTest.qWait(150)
    assert history.property("contentY") > 0
    assert outer.property("contentY") == outer_y
    assert bridge.messages == before
    # The limit belongs to Home only; the footer follows the tenth record.
    QMetaObject.invokeMethod(history, "positionViewAtEnd"); QTest.qWait(100)
    last = visual_child(history, "homeVoiceHistoryEntry9")
    assert last.property("entry")["interactionId"] == "older-6"
    assert visual_child(history, "homeVoiceHistoryEntry10") is None
    hint = visual_child(history, "homeHistoryLimitHint")
    assert "最新 10 条" in hint.property("text") and "历史记录" in hint.property("text")
    hint_y = hint.mapToItem(history, QPointF()).y()
    assert 0 <= hint_y < hint_y + hint.height() <= history.height()
    screenshot(root, tmp_path, f"home-ten-records-footer-{size[0]}.png")
    click(root, "homeAllHistoryButton"); QTest.qWait(80)
    assert root.property("currentPage") == 4
    assert controller.filteredVoiceHistoryModel.rowCount() == 11
    click(root, "mainNav0"); QTest.qWait(80)
    # New/deleted records and resize must recalculate the first three rows.
    history.setProperty("contentY", 0)
    controller._voice_history_entries = rows[:1]
    controller.voiceHistoryChanged.emit(); QTest.qWait(100)
    assert history.property("count") == 1
    controller._voice_history_entries = []
    controller.voiceHistoryChanged.emit(); QTest.qWait(100)
    assert history.property("count") == 0
    assert not hint.property("visible")


@pytest.mark.parametrize("size", [(1200, 780), (940, 700)])
def test_preferences_navigation_logs_agent_release_notes_and_startup(inline_ui, tmp_path, monkeypatch, size):
    from proximic_ring.ui.shared.startup import LoginRegistration
    controller, bridge, _, root, event = inline_ui
    controller.startup._registration = LoginRegistration(home=tmp_path, platform="darwin", frozen=False)
    controller.startup.refresh()
    event(phase="idle", ready=True)
    root.resize(*size); root.show()
    before = list(bridge.messages)
    click(root, "runtimeSettingsButton"); QTest.qWait(150)
    settings = root.findChild(QObject, "runtimeSettingsDialog")
    assert settings.property("section") == 1
    assert settings.property("leftPadding") == 256 and settings.property("topPadding") == 88
    screenshot(root, tmp_path, f"preferences-settings-{size[0]}.png")
    toggle = root.findChild(QObject, "launchAtLoginSwitch")
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, toggle.mapToScene(QPointF(toggle.width()/2, toggle.height()/2)).toPoint())
    assert controller.startup.enabled and toggle.property("checked")
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, toggle.mapToScene(QPointF(toggle.width()/2, toggle.height()/2)).toPoint())
    assert not controller.startup.enabled and not toggle.property("checked")
    def reject_startup(_enabled):
        raise PermissionError("test write denied")
    monkeypatch.setattr(controller.startup._registration, "set_enabled", reject_startup)
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, toggle.mapToScene(QPointF(toggle.width()/2, toggle.height()/2)).toPoint())
    assert not toggle.property("checked") and not controller.startup.enabled
    assert "无法修改" in controller.startup.error
    click(root, "runtimeLogButton"); QTest.qWait(150)
    assert root.findChild(QObject, "runtimeLogDialog").property("visible")
    assert not settings.property("visible")
    QMetaObject.invokeMethod(root.findChild(QObject, "runtimeLogDialog"), "close")
    click(root, "runtimeSettingsButton"); QTest.qWait(100)
    click(root, "settingsAgentEntry"); QTest.qWait(120)
    assert root.findChild(QObject, "settingsAgentGroup").property("visible")
    screenshot(root, tmp_path, f"preferences-agent-{size[0]}.png")
    click(root, "preferencesNav2"); QTest.qWait(150)
    help_dialog = root.findChild(QObject, "helpPreferencesPage")
    assert help_dialog.property("visible") and settings.property("visible")
    assert settings.property("section") == 2
    screenshot(root, tmp_path, f"preferences-help-{size[0]}.png")
    click(root, "releaseNotesButton"); QTest.qWait(150)
    assert root.findChild(QObject, "releaseNotesDialog").property("visible")
    assert controller.appVersion in root.findChild(QObject, "releaseNotesVersion").property("text")
    screenshot(root, tmp_path, f"release-notes-{size[0]}.png")
    QMetaObject.invokeMethod(root.findChild(QObject, "releaseNotesDialog"), "close")
    click(root, "preferencesNav0"); QTest.qWait(150)
    assert root.findChild(QObject, "accountPreferencesPage").property("visible")
    assert not help_dialog.property("visible")
    assert bridge.messages == before
