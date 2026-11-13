import json
import os
from pathlib import Path
import threading
from zipfile import ZipFile

import pytest
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest

from test_inline_ui import inline_ui
from test_history_management import add_record as _add_record
from test_ui_figma_redesign import click


def add_record(store, session, text="语音记录"):
    identity = _add_record(store, session, text)
    store.record_application(session_id=session, action="applied", mode="dictation", application="Safari")
    return identity


@pytest.fixture(autouse=True)
def export_selection(monkeypatch):
    from proximic_ring.diagnostics.export_scope import ExportScope
    from proximic_ring.diagnostics.log_export import LogRange
    monkeypatch.setattr("proximic_ring.ui.voice.history_export_controller.choose_export_scope",
                        lambda **kwargs: kwargs["on_selected"]((ExportScope(frozenset({"com.apple.safari"})), LogRange.create("all"), kwargs["default_directory"])))


def wait_finished(exporter):
    for _ in range(200):
        QTest.qWait(10)
        if not exporter.busy:
            return
    pytest.fail("Export did not finish")


@pytest.mark.parametrize("size", [(1200, 780), (940, 700)])
@pytest.mark.parametrize("entrypoint", ["settings", "history"])
def test_both_pages_export_all_history_and_open_folder_without_blocking_live_input(inline_ui, monkeypatch, tmp_path, size, entrypoint):
    import proximic_ring.ui.voice.history_export_controller as module
    controller, bridge, _, root, _ = inline_ui
    store = controller._voice_history
    add_record(store, 901, "第一条")
    add_record(store, 902, "第二条")
    controller._refresh_voice_history_entries()
    controller.filteredVoiceHistoryModel.searchText = "不匹配任何记录"
    expected_ids = {p.parent.name for p in store.interactions_root.glob("*/record.json")}
    exporter = controller.voiceHistoryExport
    monkeypatch.setattr(exporter, "_destination", lambda: tmp_path / "downloads")
    folder_calls = []
    monkeypatch.setattr(module.QDesktopServices, "openUrl", lambda url: folder_calls.append(url.toLocalFile()) or True)
    started, release = threading.Event(), threading.Event()
    actual_export, calls = module.export_history, []
    def slow_export(*args, **kwargs):
        calls.append(threading.get_ident())
        started.set()
        assert release.wait(3)
        return actual_export(*args, **kwargs)
    monkeypatch.setattr(module, "export_history", slow_export)
    before_messages, before_bindings = list(bridge.messages), controller.gestureBindings
    root.resize(*size)
    root.show()
    if entrypoint == "settings":
        click(root, "runtimeSettingsButton")
        QTest.qWait(160)
        button = root.findChild(QObject, "exportVoiceHistoryButton")
        assert button.property("visible") and not root.findChild(QObject, "settingsAudioGroup").property("visible")
        scroll = root.findChild(QObject, "runtimeSettingsScroll")
        flick = scroll.property("contentItem")
        y = button.mapToItem(scroll, QPointF()).y()
        flick.setProperty("contentY", max(0, min(flick.property("contentY") + y - 100,
            flick.property("contentHeight") - flick.height())))
    else:
        click(root, "mainNav4")
        QTest.qWait(160)
        click(root, "voiceHistoryOptionsButton")
        QTest.qWait(120)
        button = root.findChild(QObject, "exportAllVoiceHistoryButton")
        assert button.property("visible")
    QTest.qWait(60)
    position = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
    assert 0 <= position.x() < root.width() and 0 <= position.y() < root.height()
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, position)
    try:
        assert started.wait(1) and exporter.busy
        QTest.qWait(50)  # Qt keeps painting/handling events while the worker waits.
        assert not button.property("enabled")
        assert not root.findChild(QObject, "exportVoiceHistoryButton").property("enabled")
        assert not root.findChild(QObject, "exportAllVoiceHistoryButton").property("enabled")
        status = root.findChild(QObject, "voiceHistoryExportStatus" if entrypoint == "settings" else "voiceHistoryPageExportStatus")
        assert status.property("visible") and "正在导出" in status.property("text")
        exporter.export()
        assert len(calls) == 1 and calls[0] != threading.get_ident()
    finally:
        release.set()
    wait_finished(exporter)
    assert not exporter.error and button.property("enabled")
    assert folder_calls == [str(tmp_path / "downloads")]
    assert "已导出" in status.property("text")
    with ZipFile(exporter.path) as archive:
        records = [json.loads(line) for line in archive.read("records.jsonl").splitlines()]
        assert {row["interaction_id"] for row in records} == expected_ids
    assert bridge.messages == before_messages and controller.gestureBindings == before_bindings
    if entrypoint == "settings":
        assert root.findChild(QObject, "openVoiceHistoryExportFolderButton").property("visible")
        click(root, "openVoiceHistoryExportFolderButton")
        assert folder_calls == [str(tmp_path / "downloads")] * 2
    else:
        click(root, "voiceHistoryOptionsButton")
    shots = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    QTest.qWait(150)
    assert root.grabWindow().save(str(shots / f"history-export-{entrypoint}-{size[0]}.png"))


def test_failed_export_shows_error_and_can_retry(inline_ui, monkeypatch, tmp_path):
    import proximic_ring.ui.voice.history_export_controller as module
    controller, _, _, _, _ = inline_ui
    add_record(controller._voice_history, 901)
    exporter = controller.voiceHistoryExport
    monkeypatch.setattr(exporter, "_destination", lambda: tmp_path / "downloads")
    folder_calls = []
    monkeypatch.setattr(module.QDesktopServices, "openUrl", lambda url: folder_calls.append(url.toLocalFile()) or True)
    actual = module.export_history
    monkeypatch.setattr(module, "export_history", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))
    exporter.export()
    wait_finished(exporter)
    assert exporter.error and "disk full" in exporter.message and not exporter.path
    assert not folder_calls
    monkeypatch.setattr(module, "export_history", actual)
    exporter.export()
    wait_finished(exporter)
    assert not exporter.error and Path(exporter.path).is_file()
    assert folder_calls == [str(tmp_path / "downloads")]
