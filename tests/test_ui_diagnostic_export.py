from datetime import datetime, timedelta
import json
from pathlib import Path
import threading
import time
from zipfile import ZipFile

import pytest
from PySide6.QtCore import QObject, QMetaObject, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog

from test_inline_ui import inline_ui
from test_ui_figma_redesign import click


@pytest.fixture(autouse=True)
def export_selection(monkeypatch, tmp_path):
    from proximic_ring.diagnostics.export_scope import ExportScope
    from proximic_ring.diagnostics.log_export import LogRange
    monkeypatch.setattr("proximic_ring.ui.diagnostic_export_controller.choose_export_scope",
                        lambda **kwargs: kwargs["on_selected"]((ExportScope(frozenset({"com.apple.safari"})), LogRange.create(kwargs.get("period", "all")), tmp_path)))


def wait_finished(exporter):
    for _ in range(400):
        QTest.qWait(10)
        time.sleep(.005)
        if not exporter.busy:
            return
    pytest.fail("Diagnostic export did not finish")


@pytest.mark.parametrize("size", [(1200, 780), (940, 700), (940, 560)])
@pytest.mark.parametrize("index,period", [(0, "today"), (1, "week"), (2, "all")])
def test_settings_first_level_exports_selected_range_without_blocking(inline_ui, monkeypatch, tmp_path, size, index, period):
    import proximic_ring.ui.diagnostic_export_controller as module
    controller, bridge, _, root, _ = inline_ui
    exporter = controller.diagnosticExport
    exporter._ime_root = tmp_path / "ime"
    now = datetime.now().astimezone()
    for days, marker in [(0, "today-evidence"), (3, "week-evidence"), (20, "old-evidence")]:
        from proximic_ring.diagnostics.diagnostic_log import format_record
        line = format_record(marker, attribution={"application": "Safari"})
        controller._diagnostic_log.append(f"[{(now - timedelta(days=days)).isoformat()}]" + line.partition("]")[2])
    controller._diagnostic_log.record("global-device-error", source="device", level="ERROR")
    controller._diagnostic_log.record("other-app-error", attribution={"application": "TextEdit"})
    from test_history_management import add_record
    add_record(controller._voice_history, 901, "private-voice-content")
    path = tmp_path / (period + ".zip")
    monkeypatch.setattr(module, "suggested_filename", lambda selection: path.name)
    started, release = threading.Event(), threading.Event()
    actual, calls = module.export_diagnostics, []

    def slow_export(*args, **kwargs):
        calls.append(threading.get_ident())
        started.set()
        assert release.wait(4)
        return actual(*args, **kwargs)

    monkeypatch.setattr(module, "export_diagnostics", slow_export)
    root.resize(*size)
    root.setProperty("topPadding", 28)
    root.show()
    click(root, "runtimeSettingsButton")
    QTest.qWait(200)
    button = root.findChild(QObject, "settingsExportDiagnosticLogButton")
    assert root.findChild(QObject, "diagnosticLogRangeSelect") is None
    assert button.property("visible")
    assert not root.findChild(QObject, "settingsAudioGroup").property("visible")
    assert not root.findChild(QObject, "runtimeLogDialog").property("visible")
    from proximic_ring.ui.export_picker import choose_export_scope
    monkeypatch.setattr(module, "choose_export_scope", choose_export_scope)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a: str(tmp_path))
    scroll = root.findChild(QObject, "runtimeSettingsScroll")
    flick = scroll.property("contentItem")
    row = root.findChild(QObject, "diagnosticLogExportRow")
    y = row.mapToItem(scroll, QPointF()).y()
    flick.setProperty("contentY", max(0, min(flick.property("contentY") + y - 90,
        flick.property("contentHeight") - flick.height())))
    QTest.qWait(80)
    before = list(bridge.messages)
    position = button.mapToScene(QPointF(button.width()/2, button.height()/2)).toPoint()
    assert 0 < position.x() < root.width() and 0 < position.y() < root.height()
    QTest.mouseClick(root, Qt.LeftButton, Qt.NoModifier, position)
    QTest.qWait(180)
    selection = controller.exportSelection
    assert selection.kind == "diagnostic" and selection.applications == []
    click(root, "exportRange_" + period)
    click(root, "chooseExportFolderButton")
    dialog = root.findChild(QObject, "exportApplicationDialog")
    confirm = root.findChild(QObject, "confirmExportButton")
    background = dialog.property("background")
    bottom = background.mapToScene(QPointF()).y() + background.height()
    assert confirm.mapToScene(QPointF()).y() + confirm.height() <= bottom - 12
    assert root.grabWindow().save(f"/tmp/mythlink-diagnostic-export-{size[0]}x{size[1]}.png")
    click(root, "confirmExportButton")
    try:
        assert started.wait(1), exporter.message
        assert exporter.busy
        QTest.qWait(50)
        assert not button.property("enabled")
        assert not root.findChild(QObject, "exportDiagnosticLogButton").property("enabled")
        exporter.export("all")
        assert len(calls) == 1 and calls[0] != threading.get_ident()
    finally:
        release.set()
    wait_finished(exporter)
    assert not exporter.error, exporter.message
    assert button.property("enabled") and bridge.messages == before
    status = root.findChild(QObject, "diagnosticLogExportStatus")
    assert status.property("visible") and "已导出" in status.property("text")
    with ZipFile(path) as archive:
        assert json.loads(archive.read("manifest.json"))["range"] == period
        assert json.loads(archive.read("manifest.json"))["voice_history_requested"] is False
        log = archive.read("diagnostic.log").decode()
        assert "today-evidence" in log
        assert "global-device-error" in log and "other-app-error" in log
        assert ("week-evidence" in log) == (index > 0)
        assert ("old-evidence" in log) == (index == 2)
        state = json.loads(archive.read("runtime.json"))["state"]
        assert state["run_id"] == controller._diagnostic_run_id
        assert "permissions" in state and "application" in state
        assert not any(name.startswith("interactions/") for name in archive.namelist())
    folder_calls = []
    monkeypatch.setattr(module.QDesktopServices, "openUrl", lambda url: folder_calls.append(url.toLocalFile()) or True)
    QMetaObject.invokeMethod(root.findChild(QObject, "openDiagnosticExportFolderButton"), "click")
    assert folder_calls == [str(tmp_path)]
    QTest.qWait(100)
    assert root.grabWindow().save(f"/tmp/mythlink-diagnostic-settings-{size[0]}-{period}.png")


def test_cancel_dialog_and_failure_allow_retry(inline_ui, monkeypatch, tmp_path):
    import proximic_ring.ui.diagnostic_export_controller as module
    controller, _, _, _, _ = inline_ui
    exporter = controller.diagnosticExport
    exporter._ime_root = tmp_path / "ime"
    choose = module.choose_export_scope
    monkeypatch.setattr(module, "choose_export_scope", lambda **kwargs: kwargs["on_selected"](None))
    exporter.export("today")
    assert not exporter.busy and not exporter.error and not exporter.path
    monkeypatch.setattr(module, "choose_export_scope", choose)
    actual = module.export_diagnostics
    def fail(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(module, "export_diagnostics", fail)
    exporter.export("today")
    wait_finished(exporter)
    assert exporter.error and "disk full" in exporter.message and not exporter.path
    monkeypatch.setattr(module, "export_diagnostics", actual)
    exporter.export("all")
    wait_finished(exporter)
    assert not exporter.error and Path(exporter.path).is_file()
