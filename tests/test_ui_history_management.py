"""History management stays isolated from the current voice/gesture session."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_inline_ui import inline_ui
from test_ui_shell import visual_child
from test_ui_voice_page import seed_history
from test_history_management import add_record


def item(root, name):
    from PySide6.QtCore import QObject
    result = root.findChild(QObject, name)
    assert result is not None, name
    return result


def invoke(root, name, method="click"):
    from PySide6.QtCore import QMetaObject
    assert QMetaObject.invokeMethod(item(root, name), method)


def search(root, text):
    field = item(root, "voiceHistorySearch")
    field.setProperty("text", text)
    from PySide6.QtCore import QMetaObject
    QMetaObject.invokeMethod(field, "accepted")


def select(root, name, index):
    from PySide6.QtCore import QMetaObject, Q_ARG
    control = item(root, name)
    control.setProperty("currentIndex", index)
    assert QMetaObject.invokeMethod(control, "activated", Q_ARG(int, index))


def shot(root, tmp_path, name):
    from PySide6.QtTest import QTest
    shots = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    QTest.qWait(180)
    assert root.grabWindow().save(str(shots / name))


def first_entry(root):
    return visual_child(item(root, "voiceHistoryList"), "voiceHistoryEntry")


def request_delete(root, entry=None):
    from PySide6.QtCore import QMetaObject
    from PySide6.QtTest import QTest
    entry = entry or first_entry(root)
    QMetaObject.invokeMethod(visual_child(entry, "voiceHistoryMoreButton"), "click")
    QTest.qWait(30)
    invoke(entry, "voiceHistoryDeleteButton")


@pytest.mark.parametrize("size", [(1440, 940), (940, 700)])
def test_search_filters_reset_and_empty_state_preserve_live_input(inline_ui, tmp_path, size):
    from PySide6.QtCore import QMetaObject, QPointF
    from PySide6.QtTest import QTest
    controller, bridge, _, root, _ = inline_ui
    rows = seed_history(controller)
    before = list(bridge.messages)
    bindings = controller.gestureBindings
    root.resize(*size)
    root.show()
    QMetaObject.invokeMethod(item(root, "mainPageScroll"), "scrollToHistory")
    search(root, "明晚")
    QTest.qWait(100)
    assert item(root, "voiceHistoryList").property("count") == 1
    select(root, "voiceHistoryModeFilter", 2)
    select(root, "voiceHistoryOutcomeFilter", 1)
    assert item(root, "voiceHistoryList").property("count") == 1
    shot(root, tmp_path, f"history-filtered-{size[0]}.png")
    select(root, "voiceHistoryOutcomeFilter", 4)
    QTest.qWait(50)
    assert item(root, "voiceHistoryList").property("count") == 0
    assert item(root, "voiceHistoryEmptyTitle").property("text") == "没有匹配的记录"
    assert item(root, "clearVoiceHistoryButton").property("enabled")
    for name in ("voiceHistorySearch", "voiceHistoryModeFilter", "voiceHistoryOutcomeFilter", "voiceHistoryResetFilters"):
        control = item(root, name)
        position = control.mapToScene(QPointF())
        assert 0 <= position.x() and position.x() + control.width() <= root.width()
    invoke(root, "voiceHistoryResetFilters")
    assert item(root, "voiceHistoryList").property("count") == 3
    assert item(root, "voiceHistorySearch").property("text") == ""
    assert controller.voiceHistoryEntries == rows
    assert bridge.messages == before and controller.gestureBindings == bindings


def test_expand_and_collapse_keep_full_copy_text(inline_ui):
    from PySide6.QtCore import QMetaObject
    from PySide6.QtTest import QTest
    controller, bridge, _, root, _ = inline_ui
    rows = seed_history(controller)
    root.resize(940, 700)
    root.show()
    select(root, "voiceHistoryModeFilter", 1)
    QTest.qWait(100)
    entry = first_entry(root)
    button = visual_child(entry, "voiceHistoryExpandButton")
    text = visual_child(entry, "voiceHistoryPrimaryText")
    assert button.property("visible") and text.property("truncated")
    before = list(bridge.messages)
    assert entry.property("copyText") == rows[1]["candidateText"]
    collapsed_height = entry.height()
    QMetaObject.invokeMethod(button, "click")
    QTest.qWait(30)
    assert entry.property("textExpanded") and not text.property("truncated")
    assert entry.height() > collapsed_height
    QMetaObject.invokeMethod(button, "click")
    QTest.qWait(30)
    assert not entry.property("textExpanded") and text.property("truncated")
    assert entry.property("copyText") == rows[1]["candidateText"]
    assert bridge.messages == before


def test_delete_confirmation_uses_stable_id_when_new_record_arrives(inline_ui, tmp_path):
    from PySide6.QtCore import QMetaObject
    from PySide6.QtTest import QTest
    controller, bridge, _, root, _ = inline_ui
    store = controller._voice_history
    ids = [add_record(store, n, f"待处理记录 {n}") for n in (101, 102, 103)]
    controller._refresh_voice_history_entries()
    root.show()
    QTest.qWait(100)
    selected = first_entry(root)
    target_id = selected.property("displayedId")
    assert target_id == ids[-1]
    before = list(bridge.messages)
    phase = controller.inlineInput.status
    bindings = controller.gestureBindings
    request_delete(root, selected)
    QTest.qWait(150)
    dialog = item(root, "deleteHistoryDialog")
    assert dialog.property("visible") and store._interaction_dir(target_id).exists()
    invoke(root, "cancelDeleteHistoryButton")
    QTest.qWait(150)
    assert store._interaction_dir(target_id).exists()
    request_delete(root)
    new_id = add_record(store, 104, "新插入的记录")
    controller._refresh_voice_history_entries()
    QTest.qWait(100)
    assert dialog.property("interactionId") == target_id
    shot(root, tmp_path, "history-delete-confirmation.png")
    invoke(root, "confirmDeleteHistoryButton")
    QTest.qWait(150)
    assert not dialog.property("visible")
    assert not store._interaction_dir(target_id).exists()
    assert {row["interactionId"] for row in controller.voiceHistoryEntries} == {ids[0], ids[1], new_id}
    assert controller.filteredVoiceHistoryModel.count == 3
    assert bridge.messages == before and controller.inlineInput.status == phase
    assert controller.gestureBindings == bindings


def test_failed_delete_stays_reviewable_and_stops_only_matching_playback(inline_ui, monkeypatch):
    from PySide6.QtCore import QMetaObject
    from PySide6.QtTest import QTest
    controller, _, _, root, _ = inline_ui
    store = controller._voice_history
    first = add_record(store, 201)
    second = add_record(store, 202)
    controller._refresh_voice_history_entries()
    stopped, sources = [], []
    controller._voice_player = SimpleNamespace(stop=lambda: stopped.append(True), setSource=sources.append)
    controller._playing_voice_path = str(store._interaction_dir(first) / "audio.wav")
    assert controller.deleteVoiceHistory(second)
    assert stopped == [] and controller.playingVoicePath.endswith(first + "/audio.wav")
    root.show()
    QTest.qWait(100)
    selected = first_entry(root)
    request_delete(root, selected)
    original = store.delete_entry
    def fail(_):
        raise OSError("simulated deletion failure")
    monkeypatch.setattr(store, "delete_entry", fail)
    invoke(root, "confirmDeleteHistoryButton")
    assert item(root, "deleteHistoryDialog").property("visible")
    assert item(root, "deleteHistoryError").property("text") == "删除未完成，请重试。"
    assert store._interaction_path(first).exists()
    assert stopped == [True] and len(sources) == 1 and sources[0].isEmpty()
    monkeypatch.setattr(store, "delete_entry", original)
    invoke(root, "confirmDeleteHistoryButton")
    QTest.qWait(150)
    assert not item(root, "deleteHistoryDialog").property("visible")
    assert controller.voiceHistoryEntries == []
