"""Device controls stay on Home; history presentation never changes live input."""
import os
from pathlib import Path

import pytest

from test_inline_ui import inline_ui
from test_ui_shell import visual_child


def click(root, name):
    from PySide6.QtCore import QMetaObject, QObject
    QMetaObject.invokeMethod(root.findChild(QObject, name), "click")


def seed_history(controller):
    common = dict(displayTime="11:25", durationLabel="4 秒", backend="test", dataSummary="音频已保存",
                  application="文本编辑", recognized=True, hasImu=False, audioPath="", recordPath="", candidateAvailable=True,
                  candidateEmpty=False, editSummary="", modeLabel="听写输入")
    rows = [
        dict(common, interactionId="ui-preview-edit", mode="edit", text="把今晚改成明晚", outcome="applied",
             candidateText="我们明晚开会。", editSummary="今晚 → 明晚"),
        dict(common, interactionId="ui-preview-dictation", mode="dictation", text="这是一段语音输入。", outcome="applied",
             candidateText="这是一段语音输入。" * 35),
        dict(common, interactionId="ui-preview-cancelled", mode="edit", text="取消本次修改", outcome="cancelled",
             candidateText="这段候选文字没有应用", candidateAvailable=False),
    ]
    controller._voice_history_entries = rows
    controller.voiceHistoryChanged.emit()
    return rows


def test_disconnected_voice_page_only_navigates_home_and_home_opens_bluetooth_picker(inline_ui, monkeypatch):
    from PySide6.QtCore import QObject
    from PySide6.QtTest import QTest
    controller, _, _, root, _ = inline_ui
    scans = []
    monkeypatch.setattr(controller, "_scan_devices_once", lambda: scans.append("scan"))
    controller._connected = False
    controller.connectedChanged.emit()
    controller._can_reconnect = False
    controller.reconnectAvailabilityChanged.emit()
    root.show()
    QTest.qWait(60)
    assert root.findChild(QObject, "primaryConnectionButton") is None
    assert root.findChild(QObject, "secondaryConnectionButton") is None
    assert not root.findChild(QObject, "voiceRecognitionButton").property("enabled")
    click(root, "voiceHomeButton")
    assert root.property("currentPage") == 0
    assert scans == []
    assert not root.findChild(QObject, "devicePicker").property("visible")
    click(root, "homeConnectionButton")
    QTest.qWait(100)
    assert root.property("currentPage") == 0
    assert root.findChild(QObject, "devicePicker").property("visible")
    assert scans == ["scan"]


def test_home_reconnect_and_voice_toggle_use_separate_existing_operations(inline_ui, monkeypatch):
    from PySide6.QtCore import QObject
    from PySide6.QtTest import QTest
    controller, _, _, root, _ = inline_ui
    starts = []
    monkeypatch.setattr(controller, "_start_selected_device", lambda: starts.append("connect"))
    controller._selector = "test-ring"
    controller._can_reconnect = True
    controller.reconnectAvailabilityChanged.emit()
    root.setProperty("currentPage", 0)
    root.show()
    QTest.qWait(60)
    assert root.findChild(QObject, "homeConnectionButton").property("text") == "重新连接"
    click(root, "homeConnectionButton")
    assert starts == ["connect"]
    controller._connected = True
    controller.connectedChanged.emit()
    controller._recognition_enabled = False
    controller.recognitionEnabledChanged.emit()
    root.setProperty("currentPage", 1)
    QTest.qWait(60)
    click(root, "voiceRecognitionButton")
    assert controller.recognitionEnabled and controller.connected
    click(root, "voiceRecognitionButton")
    assert not controller.recognitionEnabled and controller.connected
    assert starts == ["connect"]


@pytest.mark.parametrize("size", [(1440, 940), (940, 700)])
def test_history_outcomes_copy_and_live_updates_preserve_input(inline_ui, monkeypatch, tmp_path, size):
    from PySide6.QtCore import QObject, QMetaObject, QPointF
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtTest import QTest
    from proximic_ring.ui.clipboard import QtClipboardBridge
    controller, bridge, _, root, _ = inline_ui
    rows = seed_history(controller)
    root.resize(*size)
    root.show()
    QTest.qWait(100)
    view = root.findChild(QObject, "voiceHistoryList")
    entry = visual_child(view, "voiceHistoryEntry")
    assert entry is not None
    assert entry.property("copyText") == "我们明晚开会。"
    before = list(bridge.messages)
    clipboard = QtClipboardBridge()
    saved_clipboard = clipboard.snapshot() if QGuiApplication.clipboard().mimeData() is not None else None
    try:
        copy = visual_child(entry, "voiceHistoryCopyButton")
        QMetaObject.invokeMethod(copy, "click")
        QTest.qWait(20)
        assert QGuiApplication.clipboard().text() == "我们明晚开会。"
        assert bridge.messages == before
        # An undo update must not leave a successful candidate displayed or copied.
        rows[0] = dict(rows[0], outcome="undone")
        controller._voice_history_entries = rows
        controller.voiceHistoryChanged.emit()
        QTest.qWait(50)
        entry = visual_child(view, "voiceHistoryEntry")
        assert entry.property("undoneEntry")
        assert not visual_child(entry, "voiceHistoryCandidateText").property("visible")
        assert entry.property("copyText") == "把今晚改成明晚"
        # A successful empty replacement must not copy the editing command as the result.
        rows[0] = dict(rows[0], outcome="applied", candidateEmpty=True, candidateText="")
        controller._voice_history_entries = rows
        controller.voiceHistoryChanged.emit()
        QTest.qWait(50)
        entry = visual_child(view, "voiceHistoryEntry")
        assert not visual_child(entry, "voiceHistoryCopyButton").property("enabled")
    finally:
        if saved_clipboard is None or not (saved_clipboard.payloads or saved_clipboard.image):
            # Keep the offscreen clipboard empty; retaining a Python-owned empty
            # QMimeData until QApplication teardown can crash Qt's test backend.
            QGuiApplication.clipboard().clear()
        else:
            clipboard.restore(saved_clipboard)
    seed_history(controller)
    controller._connected = True
    controller.connectedChanged.emit()
    controller._recognition_enabled = True
    controller.recognitionEnabledChanged.emit()
    controller._set_status("语音已就绪", "点入文本框，使用手势开始说话", "running")
    controller._apply_runtime_battery(76, 3900, 0)
    QTest.qWait(100)
    shot_dir = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    shot_dir.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(shot_dir / f"voice-history-{size[0]}.png"))
    # Full-width long entries and their action buttons remain in the list bounds.
    for name in ("voiceHistoryCopyButton", "voiceHistoryMoreButton"):
        action = visual_child(view, name)
        pos = action.mapToItem(view, QPointF())
        assert 0 <= pos.x() and pos.x() + action.width() <= view.width()
    assert bridge.messages == before


def test_history_clear_requires_explicit_confirmation(inline_ui, monkeypatch):
    from PySide6.QtCore import QObject, QMetaObject
    from PySide6.QtTest import QTest
    controller, _, _, root, _ = inline_ui
    seed_history(controller)
    cleared = []
    monkeypatch.setattr(controller._voice_history, "clear", lambda: cleared.append(True))
    root.show()
    QTest.qWait(50)
    item = root.findChild(QObject, "clearVoiceHistoryButton")
    QMetaObject.invokeMethod(item, "click")
    dialog = root.findChild(QObject, "clearHistoryDialog")
    QTest.qWait(150)
    assert dialog.property("visible") and not cleared
    QMetaObject.invokeMethod(dialog, "reject")
    QTest.qWait(150)
    assert not cleared and len(controller.voiceHistoryEntries) == 3
    QMetaObject.invokeMethod(item, "click")
    QTest.qWait(150)
    QMetaObject.invokeMethod(dialog, "accept")
    assert cleared == [True]
    assert controller.voiceHistoryEntries == []
