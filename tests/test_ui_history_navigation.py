"""Native event -> original audio record -> visible history row across undo/next."""
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QUrl
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtTest import QTest

from test_inline_ui import inline_ui as base_inline_ui, _recording_test_begin_edit
from test_history_management import add_record
from test_history_navigation_outcomes import state
from proximic_ring.text_processing import TextProcessingResult


@pytest.fixture
def history_ui(tmp_path, monkeypatch, request):
    # Exercise the production controller/model/delegate without loading unrelated
    # settings pages. This also keeps the navigation matrix fast and isolated.
    load = QQmlApplicationEngine.load
    def load_history(engine, url):
        if url.toLocalFile().endswith("/Main.qml"):
            engine.loadData(b"""
                import QtQuick
                import QtQuick.Controls
                import "."
                Window {
                    width: 1100; height: 1000
                    property int currentPage: 4
                    ListView {
                        id: history
                        objectName: "voiceHistoryList"
                        anchors.fill: parent; spacing: 12
                        model: appController.filteredVoiceHistoryModel
                        delegate: VoiceHistoryEntry {
                            width: history.width; controller: appController
                        }
                    }
                }
            """, QUrl.fromLocalFile(str(Path(url.toLocalFile()).with_name("HistoryNavigationHarness.qml"))))
        else:
            load(engine, url)
    monkeypatch.setattr(QQmlApplicationEngine, "load", load_history)
    yield from base_inline_ui.__wrapped__(tmp_path, monkeypatch, request)


def test_mixed_old_edit_and_new_dictation_round_trip_updates_visible_history(history_ui):
    c, _, requests, root, event = history_ui
    first = _recording_test_begin_edit(c, event, requests)
    inline = c._inline_input
    old_source = inline._utterance_id
    c._apply_text_processed(TextProcessingResult(first.request_id, 1, "edit", "扩写", "结果", .1, True))
    event(phase="edited", ready=True, revision=1, retry_source_utterance_id=old_source)
    event("settled", phase="edited", text="结果", revision=1, source_utterance_id=old_source, error="")
    inline.begin(); inline.bind_session(2)
    new_source = inline._utterance_id
    add_record(c._modification_dataset, 2, "后来一句")
    event(phase="dictated", ready=True, revision=0, edit_requested=False)
    event("settled", phase="dictated", text="后来一句", revision=0, error="")
    root.setProperty("currentPage", 4)
    root.show()
    c._refresh_voice_history_entries()
    QTest.qWait(100)

    def visible_row(session):
        identity = c._modification_dataset.interaction_id_for_session(session)
        history = root.findChild(QObject, "voiceHistoryList")
        def scan(item):
            if item.objectName() == "voiceHistoryEntry" and item.property("displayedId") == identity:
                return item
            for child in item.childItems():
                found = scan(child)
                if found is not None:
                    return found
        result = scan(history)
        assert result is not None
        return result

    steps = [
        (new_source, "undone", "", "applied", "undone"),
        (old_source, "dictated", "扩写", "undone", "undone"),
        (old_source, "undone", "", "undone", "undone"),
        (old_source, "dictated", "扩写", "undone", "undone"),
        (old_source, "edited", "结果", "applied", "undone"),
        (new_source, "dictated", "后来一句", "applied", "applied"),
    ]
    for revision, (source, phase, text, older, newer) in enumerate(steps * 2, 2):
        event(phase=phase, ready=True, revision=revision, edit_requested=False,
              retry_source_utterance_id=source, has_composition=False, awaiting_readback=False,
              undo_history_depth=1)
        event("settled", phase=phase, text=text, revision=revision, source_utterance_id=source, error="")
        QTest.qWait(20)
        for session, expected, applied_text in [(1, older, "结果"), (2, newer, "后来一句")]:
            state(c._modification_dataset, expected, applied_text if expected == "applied" else "", session)
            row = visible_row(session)
            assert row.property("outcomeText") == ("已撤回" if expected == "undone" else "")
            assert row.property("resultAvailable") is (expected == "applied")
            identity = c._modification_dataset.interaction_id_for_session(session)
            details = c.voiceHistoryDetails(identity)
            assert details["summary"].endswith("已撤回" if expected == "undone" else "已写入")
            assert row.property("copyText") == (applied_text if expected == "applied" else "扩写" if session == 1 else "后来一句")
        assert inline._session_id == 2
    before = c._modification_dataset.load_entries()
    for mode in ("undo", "retry", "next"):
        c.historyActionMode = mode
        assert c._modification_dataset.load_entries() == before
    assert len(requests) == 1  # Cached next does not issue another model request.
