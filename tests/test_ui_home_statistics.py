import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_inline_ui import inline_ui
from test_ui_shell import visual_child
from test_ui_voice_page import seed_history
from test_history_management import add_record


def item(root, name):
    from PySide6.QtCore import QObject
    found = root.findChild(QObject, name) or visual_child(root.contentItem(), name)
    assert found is not None, name
    return found


def click(root, name):
    from PySide6.QtCore import QMetaObject
    assert QMetaObject.invokeMethod(item(root, name), "click")


def seed_statistics(controller):
    now = datetime(2026, 9, 29, 12, tzinfo=timezone(timedelta(hours=8)))
    controller.homeStatistics._clock = lambda: now
    base = seed_history(controller)[0]
    rows = [dict(base, interactionId="today-input", mode="dictation", text="今天的语音输入。",
                 candidateText="测试输入" * 300, createdAt=now.isoformat(), durationSeconds=90),
            dict(base, interactionId="today-edit", mode="edit", createdAt=now.isoformat(), durationSeconds=30),
            dict(base, interactionId="yesterday", mode="dictation", candidateText="昨日" * 100,
                 createdAt=(now - timedelta(days=1)).isoformat(), durationSeconds=60),
            dict(base, interactionId="failed", outcome="apply_failed", candidateAvailable=False,
                 candidateText="", createdAt=now.isoformat(), durationSeconds=5)]
    controller._voice_history_entries = rows
    controller.voiceHistoryChanged.emit()
    return rows


@pytest.mark.parametrize("size", [(1440, 940), (940, 700)])
def test_home_statistics_scope_explanation_and_layout_preserve_voice(inline_ui, tmp_path, size):
    from PySide6.QtCore import QPointF
    from PySide6.QtTest import QTest
    controller, bridge, _, root, _ = inline_ui
    rows = seed_statistics(controller)
    before = list(bridge.messages), controller.gestureBindings, controller.appGestures.profiles
    preferences = {key: controller._settings.value(key) for key in controller._settings.allKeys()}
    root.resize(*size)
    root.setProperty("currentPage", 0)
    root.show()
    QTest.qWait(180)
    assert controller.homeStatistics.active
    assert item(root, "homeStatValue_inputCharacters").property("text") == "1200"
    assert item(root, "homeStatValue_voiceSeconds").property("text") == "2.1"
    assert item(root, "homeStatValue_completedOperations").property("text") == "2"
    assert item(root, "homeStatHint_inputCharacters").property("text") == "较昨日 +1000 字"
    shots = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(shots / f"home-statistics-{size[0]}.png"))
    for key in ("inputCharacters", "voiceSeconds", "completedOperations"):
        card = item(root, "homeStat_" + key)
        origin = card.mapToScene(QPointF())
        assert origin.x() >= root.property("sidebarWidth")
        assert origin.x() + card.width() <= root.width()
        value = item(root, "homeStatValue_" + key)
        local = value.mapToItem(card, QPointF())
        assert local.x() >= 0 and local.x() + value.width() <= card.width()
    click(root, "homeStatsAll")
    assert item(root, "homeStatValue_inputCharacters").property("text") == "1400"
    assert item(root, "homeStatValue_completedOperations").property("text") == "3"
    controller.filteredVoiceHistoryModel.searchText = "没有匹配的词"
    assert controller.filteredVoiceHistoryModel.count == 0
    assert item(root, "homeStatValue_inputCharacters").property("text") == "1400"
    click(root, "homeStatsInfo")
    QTest.qWait(150)
    dialog = item(root, "homeStatisticsExplanation")
    assert dialog.property("visible")
    assert root.grabWindow().save(str(shots / f"home-statistics-help-{size[0]}.png"))
    button = item(root, "closeHomeStatisticsExplanation")
    position = button.mapToScene(QPointF())
    assert 0 <= position.y() and position.y() + button.height() <= root.height()
    click(root, "closeHomeStatisticsExplanation")
    root.setProperty("currentPage", 1)
    assert not controller.homeStatistics.active
    assert controller.voiceHistoryEntries == rows
    assert (bridge.messages, controller.gestureBindings, controller.appGestures.profiles) == before
    assert {key: controller._settings.value(key) for key in controller._settings.allKeys()} == preferences


def test_home_statistics_follow_real_record_undo_delete_and_clear(inline_ui):
    from PySide6.QtTest import QTest
    controller, bridge, _, root, _ = inline_ui
    store = controller._voice_history
    first = add_record(store, 301, "测试输入")
    second = add_record(store, 302, "修改指令")
    store.record_application(interaction_id=second, mode="edit", action="applied", final_text="编辑后的完整段落")
    controller._refresh_voice_history_entries()
    root.setProperty("currentPage", 0)
    root.show()
    QTest.qWait(180)
    before = list(bridge.messages), controller.gestureBindings
    stats = controller.homeStatistics
    assert stats.summary["today"]["inputCharacters"] == 4
    assert stats.summary["today"]["completedOperations"] == 2
    recorded_time = stats.summary["total"]["voiceSeconds"]
    store.record_application(interaction_id=first, mode="dictation", action="undone")
    controller._refresh_voice_history_entries()
    QTest.qWait(180)
    assert stats.summary["today"]["inputCharacters"] == 0
    assert stats.summary["today"]["completedOperations"] == 1
    assert stats.summary["total"]["voiceSeconds"] == recorded_time
    assert controller.deleteVoiceHistory(first)
    QTest.qWait(180)
    assert stats.summary["total"]["records"] == 1
    assert stats.summary["total"]["voiceSeconds"] < recorded_time
    controller.clearVoiceHistory()
    QTest.qWait(180)
    assert stats.summary["total"]["records"] == 0
    for key in ("inputCharacters", "voiceSeconds", "completedOperations"):
        assert item(root, "homeStatValue_" + key).property("text") == "0"
    assert (bridge.messages, controller.gestureBindings) == before


def test_hidden_home_refreshes_on_return_and_missing_data_is_explained(inline_ui):
    from PySide6.QtTest import QTest
    controller, _, _, root, _ = inline_ui
    root.show()
    root.setProperty("currentPage", 0)
    assert controller.homeStatistics.summary["total"]["records"] == 0
    root.setProperty("currentPage", 1)
    rows = seed_statistics(controller)
    assert controller.homeStatistics.summary["total"]["records"] == 0
    controller._voice_history_entries = [dict(rows[0], durationSeconds=None, candidateAvailable=False, createdAt="")]
    controller.voiceHistoryChanged.emit()
    root.setProperty("currentPage", 0)
    QTest.qWait(150)
    assert controller.homeStatistics.summary["today"]["records"] == 0
    assert controller.homeStatistics.summary["total"]["records"] == 1
    assert controller.homeStatistics.summary["undatedRecords"] == 1
    click(root, "homeStatsAll")
    assert item(root, "homeStatValue_inputCharacters").property("text") == "—"
    assert item(root, "homeStatValue_voiceSeconds").property("text") == "—"
    assert item(root, "homeStatValue_completedOperations").property("text") == "1"
