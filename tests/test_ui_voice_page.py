"""Device controls stay on Home; history presentation never changes live input."""
import os
import json
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
             candidateText="我们明晚开会。", editBeforeText="我们今晚开会。", editSummary="今晚 → 明晚"),
        dict(common, interactionId="ui-preview-dictation", mode="dictation", text="这是一段语音输入。", outcome="applied",
             candidateText="这是一段语音输入。" * 35),
        dict(common, interactionId="ui-preview-cancelled", mode="edit", text="取消本次修改", outcome="cancelled",
             candidateText="这段候选文字没有应用", candidateAvailable=False),
    ]
    controller._voice_history_entries = rows
    controller.voiceHistoryChanged.emit()
    return rows


@pytest.mark.parametrize("size", [(1440, 940), (940, 700)])
@pytest.mark.parametrize("routing", ["manual", "auto"])
def test_history_details_open_once_for_both_modes(inline_ui, tmp_path, monkeypatch, size, routing):
    from PySide6.QtCore import QCoreApplication, QObject, QMetaObject, QPoint, QPointF, Qt
    from PySide6.QtGui import QImage, QPainter, QColor, QFont, QWheelEvent
    from PySide6.QtTest import QTest
    from test_shared_voice_capture import raw_capture, finish_asr
    from proximic_ring.text_processing.screen_context import filter_context
    from proximic_ring.text_processing.model import InputModeRoutingResult, TextProcessingRequest, TextProcessingResult, LLMSettings
    controller, bridge, _, root, _ = inline_ui
    collector = controller._voice_history
    collector.record_screen_capture(7, raw_capture(), routing=routing, input_context={
        "original": "输入框原文",
        "long_text": "没有空格的长文本" * 120,
        "long_identifier": "https://example.test/" + "abcdefgh0123456789" * 100,
    })
    finish_asr(collector)
    source = "输入框原文\n" + "没有空格的长文本" * 120 + "\nhttps://example.test/" + "abcdefgh0123456789" * 100
    collector.record_text_request(TextProcessingRequest(20, 7, "edit", "把今晚改成明晚",
        LLMSettings(enabled=True, model="deepseek-test"), source, routing_mode=routing))
    collector.record_llm_result(20, TextProcessingResult(20, 7, "edit", "把今晚改成明晚", "我们明晚开会。", .8, True,
        target_text=source, routing_mode=routing))
    identifier = collector.interaction_id_for_session(7)
    if routing == "auto":
        collector.record_routing_result(InputModeRoutingResult(10, 7, "你好", "dictation", .1,
            task_type="dictation", screen_context=filter_context(raw_capture())))
    # A synthetic target frame allows visual QA without capturing the real desktop.
    image = QImage(800, 600, QImage.Format_RGB32)
    image.fill(QColor("#f5f5f5"))
    painter = QPainter(image)
    painter.fillRect(0, 0, 240, 600, QColor("#e0e5ea"))
    painter.fillRect(240, 456, 560, 144, QColor("#ffffff"))
    painter.setPen(QColor("#303743")); painter.setFont(QFont("PingFang SC", 24))
    painter.drawText(20, 120, "联系人")
    painter.drawText(280, 48, "测试对话 · 合成截图")
    painter.drawText(280, 220, "请联系张伟")
    painter.drawText(280, 510, "输入框草稿")
    painter.end()
    saved = collector._interaction_data(identifier)
    assert image.save(str(collector._interaction_dir(identifier) / saved["screen_evidence"]["screenshot_file"]))
    reads = []
    load = collector.load_details
    def tracked(identifier):
        reads.append(identifier)
        return load(identifier)
    monkeypatch.setattr(collector, "load_details", tracked)
    controller._refresh_voice_history_entries()
    root.resize(*size); root.setProperty("currentPage", 4); root.show(); QTest.qWait(120)
    view = root.findChild(QObject, "voiceHistoryList")
    entry = visual_child(view, "voiceHistoryEntry")
    before = (list(bridge.messages), controller.inlineInput._view.copy())
    assert not reads and visual_child(entry, "voiceHistoryDetails") is None
    assert visual_child(entry, "voiceHistoryDetailsToggle") is None
    QMetaObject.invokeMethod(visual_child(entry, "voiceHistoryMoreButton"), "click")
    QTest.qWait(100)
    menu = entry.findChild(QObject, "voiceHistoryRecordMenu")
    assert menu.property("visible")
    toggle = entry.findChild(QObject, "voiceHistoryDetailsButton")
    QMetaObject.invokeMethod(toggle, "click")
    QTest.qWait(220)
    panel = visual_child(entry, "voiceHistoryDetails")
    assert panel is not None and reads == [identifier]
    assert visual_child(entry, "automaticVoiceDetailsToggle") is None
    directory = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    directory.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(directory / f"{routing}-history-simple-{size[0]}.png"))
    assert visual_child(panel, "voiceHistoryDetail_llm_0").property("text") == "我们明晚开会。"
    assert not visual_child(panel, "voiceHistoryDetailScroll_llm_0_input").property("visible")
    QMetaObject.invokeMethod(visual_child(panel, "voiceHistoryInputToggle_llm_0"), "click")
    QMetaObject.invokeMethod(visual_child(panel, "voiceHistoryOutputToggle_raw_ocr"), "click")
    QMetaObject.invokeMethod(visual_child(panel, "voiceHistoryScreenshotToggle"), "click")
    QTest.qWait(100)
    screenshot = visual_child(panel, "voiceHistoryScreenshot")
    assert screenshot.property("paintedWidth") > 0
    point = screenshot.mapToItem(entry, QPointF())
    assert point.x() >= 0 and point.x() + screenshot.width() <= entry.width()
    raw_ocr = visual_child(panel, "voiceHistoryDetail_raw_ocr")
    assert "联系人" in raw_ocr.property("text") and "输入框草稿" in raw_ocr.property("text")
    assert raw_ocr.property("readOnly") and raw_ocr.property("selectByMouse")
    for key in ("input_context", "mode", "feedback", "asr_updates", "events", "imu_samples", "ocr_filter"):
        assert visual_child(panel, "voiceHistoryDetail_" + key) is None
    assert "输入框原文" in visual_child(panel, "voiceHistoryDetail_llm_0_input").property("text")
    assert root.grabWindow().save(str(directory / f"{routing}-history-{size[0]}.png"))
    assert (bridge.messages, controller.inlineInput._view) == before
    ocr_y = raw_ocr.mapToItem(view, QPointF()).y()
    view.setProperty("contentY", max(0, view.property("contentY") + ocr_y - 90)); QTest.qWait(80)
    assert root.grabWindow().save(str(directory / f"{routing}-history-ocr-{size[0]}.png"))
    # Long Chinese text and unbroken identifiers wrap within the fixed frame.
    text = visual_child(panel, "voiceHistoryDetail_llm_0_input")
    scroll = visual_child(panel, "voiceHistoryDetailScroll_llm_0_input")
    viewport = scroll.property("contentItem")
    assert text.width() == pytest.approx(scroll.property("availableWidth"))
    assert text.property("contentWidth") <= text.width() - text.property("leftPadding") - text.property("rightPadding") + 1
    assert scroll.property("clip") and viewport.property("contentHeight") > viewport.height()
    assert panel.width() - scroll.mapToItem(panel, QPointF()).x() - scroll.width() >= 40
    scroll_y = scroll.mapToItem(view, QPointF()).y()
    view.setProperty("contentY", view.property("contentY") + scroll_y - 80)
    QTest.qWait(80)
    def scroll_at(point):
        QTest.mouseMove(root, point)
        if size[0] == 940:
            # macOS trackpad gestures carry pixel deltas and scroll phases.
            # QTest.wheelEvent rejects the zero-delta begin/end events.
            for phase, delta in ((Qt.ScrollBegin, 0), (Qt.ScrollUpdate, -36),
                                 (Qt.ScrollUpdate, -36), (Qt.ScrollEnd, 0)):
                event = QWheelEvent(QPointF(point), QPointF(root.mapToGlobal(point)),
                    QPoint(0, delta), QPoint(0, delta), Qt.NoButton, Qt.NoModifier, phase, False)
                QCoreApplication.sendEvent(root, event)
                QTest.qWait(20)
        else:
            QTest.wheelEvent(root, point, QPoint(0, -120))
        QTest.qWait(350)
    outer_y = view.property("contentY")
    inner_point = scroll.mapToScene(QPointF(scroll.width() / 2, 90)).toPoint()
    scroll_at(inner_point)
    assert viewport.property("contentY") > 0
    assert view.property("contentY") == pytest.approx(outer_y)
    inner_y = viewport.property("contentY")
    assert root.grabWindow().save(str(directory / f"{routing}-history-wrap-{size[0]}.png"))
    # The 40 px blank lane bypasses the inner scroll box for outer scrolling.
    outer_point = scroll.mapToScene(QPointF(scroll.width() + 20, 90)).toPoint()
    scroll_at(outer_point)
    assert view.property("contentY") > outer_y
    assert viewport.property("contentY") == pytest.approx(inner_y)
    # The same menu action also collapses the details.
    QMetaObject.invokeMethod(visual_child(entry, "voiceHistoryMoreButton"), "click")
    QTest.qWait(100)
    QMetaObject.invokeMethod(toggle, "click"); QTest.qWait(150)
    assert visual_child(entry, "voiceHistoryDetails") is None and not entry.property("detailsVisible")
    assert view.property("contentY") <= max(0, view.property("contentHeight") - view.height()) + 1


def test_dictation_priority_history_is_yellow_without_changing_live_input(inline_ui, tmp_path):
    from PySide6.QtCore import QObject, QMetaObject
    from PySide6.QtGui import QColor
    from PySide6.QtTest import QTest
    from test_automatic_history import record_example
    controller, bridge, _, root, _ = inline_ui
    collector, _, _, _ = record_example(tmp_path / 'history', task='dictation', conflict=True)
    controller._voice_history = collector
    record = collector.load_entries()[0]
    record.update(recognized=True, mode='dictation', outcome='applied', candidateText='他说改一下', candidateAvailable=True)
    controller._voice_history_entries = [record]
    controller.voiceHistoryChanged.emit()
    root.resize(1200, 820); root.setProperty('currentPage', 4); root.show(); QTest.qWait(120)
    before = (list(bridge.messages), controller.inlineInput._view.copy())
    entry = visual_child(root.findChild(QObject, 'voiceHistoryList'), 'voiceHistoryEntry')
    assert entry.property('routingWarning')
    assert entry.property('color') == QColor('#FFF8DB')
    assert visual_child(entry, 'voiceHistoryRoutingWarning').property('visible')
    assert '听写' in visual_child(entry, 'voiceHistoryModeLabel').property('text')
    entry.setProperty('detailsVisible', True); QTest.qWait(30)
    assert visual_child(entry, 'voiceHistoryDetailsWarning').property('visible')
    directory = Path(os.environ.get('MYTHLINK_SCREENSHOT_DIR', str(tmp_path)))
    directory.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(directory / 'automatic-history-warning.png'))
    assert (bridge.messages, controller.inlineInput._view) == before


@pytest.mark.parametrize("size", [(1440, 940), (940, 700)])
def test_voice_gesture_entry_preserves_defaults_without_sending_commands(inline_ui, tmp_path, size):
    from PySide6.QtCore import QObject, QPointF
    from PySide6.QtTest import QTest
    controller, bridge, _, root, _ = inline_ui
    root.resize(*size)
    root.setProperty("currentPage", 1)
    root.show()
    QTest.qWait(80)
    before = controller.gestureBindings
    messages = list(bridge.messages)
    button = root.findChild(QObject, "openVoiceGestureSettingsButton")
    assert button.property("visible")
    point = button.mapToScene(QPointF())
    assert 0 <= point.x() and point.x() + button.width() <= root.width()
    click(root, "openVoiceGestureSettingsButton")
    QTest.qWait(100)
    dialog = root.findChild(QObject, "runtimeSettingsDialog")
    assert not dialog.property("visible") and root.property("currentPage") == 1
    voice_dialog = root.findChild(QObject, "voiceGestureBindingDialog")
    assert voice_dialog.property("visible")
    assert controller.gestureBindings == before and bridge.messages == messages
    card = root.findChild(QObject, "voiceGestureSettingsCard")
    position = card.mapToScene(QPointF())
    assert position.y() >= 0 and position.y() + card.height() <= root.height()
    directory = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    directory.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(directory / f"input-gestures-{size[0]}.png"))



@pytest.mark.parametrize("size", [(1440, 940), (940, 700)])
def test_voice_overview_follows_global_bindings_and_settings_routes_to_same_editor(inline_ui, size):
    from PySide6.QtCore import QObject, QMetaObject, Q_ARG
    from PySide6.QtTest import QTest
    controller, bridge, _, root, _ = inline_ui
    root.resize(*size)
    root.setProperty("currentPage", 1)
    root.show()
    QTest.qWait(60)
    before = (controller.inlineInput._view.copy(), controller.inlineInput._utterance_id,
              controller.inlineInput._seq, controller.recognitionEnabled)
    bridge.messages.clear()
    assert controller.setVoiceGestureAction('swipe-down', 'send')
    assert controller.setVoiceGestureAction('clench', 'polish')
    QTest.qWait(30)
    send = visual_child(root.contentItem(), 'voiceCurrentBinding_send').property('modelData')
    polish = visual_child(root.contentItem(), 'voiceCurrentBinding_polish').property('modelData')
    assert send['gestureLabel'] == '下滑' and polish['gestureLabel'] == '握拳'
    assert polish['gesture'] == 'clench'
    assert (controller.inlineInput._view, controller.inlineInput._utterance_id,
            controller.inlineInput._seq, controller.recognitionEnabled) == before
    assert [m['type'] for m in bridge.messages] == ['configure_hud', 'configure_hud']
    assert root.findChild(QObject, 'voiceGestureSettingsCard') is None
    assert root.findChild(QObject, 'undoGesture0') is None
    dialog = root.findChild(QObject, 'runtimeSettingsDialog')
    QMetaObject.invokeMethod(dialog, 'open')
    QMetaObject.invokeMethod(dialog, 'navigate', Q_ARG('QVariant', 2))
    QTest.qWait(50)
    click(root, 'configureGlobalVoiceGesturesButton')
    QTest.qWait(250)
    assert not dialog.property('visible') and root.property('currentPage') == 1
    assert root.findChild(QObject, 'voiceGestureBindingDialog').property('visible')
    assert root.findChild(QObject, 'voiceGestureSettingsCard') is not None


def test_voice_preview_assets_match_the_native_figma_originals():
    import json, hashlib
    root = Path(__file__).resolve().parents[1]
    ui = root / "src/proximic_ring/ui/assets/figma/voice-hud"
    native = root / "native/ProxiMicInput/Resources/VoiceHud"
    manifest = json.loads((ui / "manifest.json").read_text())
    for name, metadata in manifest.items():
        data = (ui / (name + ".svg")).read_bytes()
        assert data == (native / (name + ".svg")).read_bytes()
        assert hashlib.sha256(data).hexdigest() == metadata["sha256"]
        if name.startswith("gesture-"):
            assert data == (ui.parent / metadata["source_file"]).read_bytes()


def test_application_shortcut_settings_open_the_explicit_application_catalog(inline_ui):
    from PySide6.QtCore import QObject
    from PySide6.QtTest import QTest
    _, _, _, root, _ = inline_ui
    click(root, "openAppGestureSettingsButton")
    QTest.qWait(30)
    assert root.property("currentPage") == 2
    assert not root.findChild(QObject, "runtimeSettingsDialog").property("visible")


@pytest.mark.parametrize("size", [(1440, 940), (940, 700)])
def test_hud_size_setting_is_persistent_and_display_only(inline_ui, tmp_path, size):
    from PySide6.QtCore import QObject, QMetaObject, QPointF, QSettings
    from PySide6.QtTest import QTest
    controller, bridge, _, root, _ = inline_ui
    root.resize(*size)
    root.show()
    dialog = root.findChild(QObject, "runtimeSettingsDialog")
    QMetaObject.invokeMethod(dialog, "open")
    QTest.qWait(180)
    click(root, "settingsGeneralEntry")
    QTest.qWait(60)
    slider = root.findChild(QObject, "voiceHudScaleSlider")
    assert slider is not None and slider.property("visible")
    assert slider.property("value") == controller.voiceHudScale == 80
    before = (controller.gestureBindings, controller.inlineInput._view.copy(),
              controller.inlineInput._seq, controller.inlineInput._utterance_id,
              controller.recognitionEnabled)
    bridge.messages.clear()
    slider.setProperty("value", 120)
    QMetaObject.invokeMethod(slider, "moved")
    QTest.qWait(30)
    assert controller.voiceHudScale == 120
    controller._settings.sync()
    saved = QSettings(controller._settings.fileName(), QSettings.IniFormat)
    assert int(saved.value("ui/voiceHudScale")) == 120
    assert bridge.messages[-1]["type"] == "configure_hud"
    assert bridge.messages[-1]["scale_percent"] == 120
    assert root.findChild(QObject, "voiceHudScaleLabel").property("text") == "120%"
    assert (controller.gestureBindings, controller.inlineInput._view,
            controller.inlineInput._seq, controller.inlineInput._utterance_id,
            controller.recognitionEnabled) == before
    click(root, "resetVoiceHudScaleButton")
    QTest.qWait(30)
    assert slider.property("value") == controller.voiceHudScale == 80
    assert bridge.messages[-1]["scale_percent"] == 80
    assert all(message["type"] == "configure_hud" for message in bridge.messages)
    flick = root.findChild(QObject, "runtimeSettingsScroll").property("contentItem")
    group = root.findChild(QObject, "settingsAppearanceGroup")
    top = group.mapToItem(flick, QPointF()).y() + flick.property("contentY")
    flick.setProperty("contentY", min(top, max(0, flick.property("contentHeight") - flick.height())))
    QTest.qWait(60)
    position = slider.mapToScene(QPointF())
    assert 0 <= position.x() and position.x() + slider.width() <= root.width()
    assert 0 <= position.y() and position.y() + slider.height() <= root.height()
    directory = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    directory.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(directory / f"hud-size-settings-{size[0]}.png"))


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


@pytest.mark.parametrize("saved_selector", ["remembered-ring", "", "   "])
def test_home_restores_saved_ring_connection_actions(request, tmp_path, monkeypatch, saved_selector):
    from PySide6.QtCore import QObject, QSettings
    from PySide6.QtTest import QTest

    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    settings.setValue("ring/name", "Ringo 8F56")
    settings.setValue("ring/selector", saved_selector)
    settings.sync()
    controller, _, _, root, _ = request.getfixturevalue("inline_ui")
    starts, scans = [], []
    monkeypatch.setattr(controller, "_start_selected_device", lambda: starts.append(controller.selector))
    monkeypatch.setattr(controller, "_scan_devices_once", lambda: scans.append("scan"))
    root.setProperty("currentPage", 0)
    root.show()
    QTest.qWait(60)

    connection = root.findChild(QObject, "homeConnectionButton")
    other = root.findChild(QObject, "homeDevicePickerButton")
    picker = root.findChild(QObject, "devicePicker")
    assert starts == scans == []
    assert other.property("text") == "切换设备"
    if saved_selector.strip():
        assert controller.deviceName == "Ringo 8F56"
        assert connection.property("text") == "重新连接"
        assert other.property("visible") and other.property("enabled")
        click(root, "homeConnectionButton")
        assert starts == [saved_selector] and scans == []
        assert not picker.property("visible")
        click(root, "homeDevicePickerButton")
    else:
        assert connection.property("text") == "蓝牙连接"
        assert not other.property("visible")
        click(root, "homeConnectionButton")
        assert starts == []
    QTest.qWait(100)
    assert scans == ["scan"] and picker.property("visible")


def test_home_reconnect_and_voice_toggle_use_separate_existing_operations(inline_ui, monkeypatch):
    from types import SimpleNamespace
    from PySide6.QtCore import QObject, QPointF, Qt
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
    root.setProperty("currentPage", 0); QTest.qWait(60)
    button = root.findChild(QObject, "homeConnectionButton")
    background = button.property("background")
    assert button.property("text") == "断开设备"
    assert background.property("color").name() == "#b44852"
    point = button.mapToScene(QPointF(button.width()/2, button.height()/2)).toPoint()
    QTest.mouseMove(root, point); QTest.qWait(30)
    assert background.property("color").name() == "#c35a64"
    QTest.mousePress(root, Qt.LeftButton, Qt.NoModifier, point)
    assert background.property("color").name() == "#933843"
    monkeypatch.setattr(controller, "_worker", SimpleNamespace(is_alive=lambda: True))
    QTest.mouseRelease(root, Qt.LeftButton, Qt.NoModifier, point)
    assert not controller.connected and controller.busy
    assert button.property("text") == "正在断开…"
    assert not button.property("enabled") and not button.property("hoverEnabled")
    assert background.property("color").name() == "#b44852"


@pytest.mark.parametrize("size", [(1440, 940), (940, 700)])
def test_history_outcomes_copy_and_live_updates_preserve_input(inline_ui, monkeypatch, tmp_path, size):
    from PySide6.QtCore import QObject, QMetaObject, QPointF
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtTest import QTest
    from proximic_ring.ui.shared.clipboard import QtClipboardBridge
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


@pytest.mark.parametrize("size", [(1440, 940), (940, 700)])
def test_voice_overview_shows_complete_current_functions_names_and_icons(inline_ui, tmp_path, size):
    from PySide6.QtCore import QPointF
    from PySide6.QtTest import QTest
    from test_ui_gesture_hand import find
    controller, bridge, _, root, _ = inline_ui
    root.resize(*size)
    root.setProperty("currentPage", 1)
    root.show()
    assignments = {
        "confirm": ("double-index-pinch", "双次食拇捏合"),
        "end": ("double-middle-pinch", "双次中拇捏合"),
        "polish": ("circle-clockwise", "顺时针"),
        "switch_mode": ("circle-counterclockwise", "逆时针"),
        "undo": ("index-pinch", "食拇捏合"),
        "send": ("middle-pinch", "中拇捏合"),
    }
    before_input = controller.inlineInput._view.copy(), controller.inlineInput._seq
    bridge.messages.clear()
    for action, (gesture, _) in assignments.items():
        assert controller.setVoiceGestureAction(gesture, action)
    QTest.qWait(180)
    assert visual_child(root.contentItem(), "voiceCurrentBinding_start_end") is None
    cards = []
    for action, (gesture, title) in assignments.items():
        card = find(root, "voiceCurrentBinding_" + action)
        name = find(root, "voiceCurrentGesture_" + action)
        assert name.property("text") == title and not name.property("truncated")
        assert not find(root, "voiceCurrentFunction_" + action).property("truncated")
        preview = find(root, "voiceCurrentPreview_" + action)
        assert preview.property("gestures").toVariant() == [gesture]
        assert visual_child(preview, "voicePreviewCount_" + gesture).isVisible() == gesture.startswith("double-")
        point = card.mapToScene(QPointF())
        assert 0 <= point.x() and point.x() + card.width() <= root.width()
        assert 0 <= point.y() and point.y() + card.height() <= root.height()
        cards.append((point.x(), point.y(), card.width(), card.height()))
    for i, (x, y, w, h) in enumerate(cards):
        for bx, by, bw, bh in cards[i + 1:]:
            assert x + w <= bx or bx + bw <= x or y + h <= by or by + bh <= y
    directory = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    directory.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(directory / f"voice-overview-{size[0]}.png"))
    assert (controller.inlineInput._view, controller.inlineInput._seq) == before_input
    assert all(message["type"] == "configure_hud" for message in bridge.messages)
    saved = controller.gestureBindings
    history = controller.gestureMappingHistory
    assert history.apply("voice", "", "regular", "snap", {"voiceAction": "start_end"})
    QTest.qWait(40)
    assert find(root, "voiceCurrentGesture_start_end").property("text") == "响指"
    assert visual_child(root.contentItem(), "voiceCurrentBinding_confirm") is None
    assert visual_child(root.contentItem(), "voiceCurrentBinding_end") is None
    assert history.undo(history.contextKey)
    QTest.qWait(40)
    assert controller.gestureBindings == saved
    assert find(root, "voiceCurrentGesture_confirm").property("text") == "双次食拇捏合"
    assert history.apply("voice", "", "regular", "double-index-pinch", {"voiceAction": ""})
    QTest.qWait(40)
    assert find(root, "voiceCurrentGesture_confirm").property("text") == "无"
    assert find(root, "voiceCurrentPreview_confirm").property("gestures").toVariant() == []


@pytest.mark.parametrize("size", [(1200, 780), (940, 700)])
def test_voice_binding_summary_matches_figma_inline_style(inline_ui, tmp_path, size):
    from PySide6.QtCore import QPointF
    from PySide6.QtQml import QQmlEngine, QQmlExpression
    from PySide6.QtTest import QTest
    from test_ui_gesture_hand import find
    controller, _, _, root, _ = inline_ui
    root.resize(*size)
    root.setProperty("currentPage", 1)
    controller._connected = True
    controller.connectedChanged.emit()
    assert controller.setVoiceGestureAction("tap", "start_end")
    root.show()
    QTest.qWait(180)
    for action in ("start_end", "send", "polish", "undo", "switch_mode"):
        entry = find(root, "voiceCurrentBinding_" + action)
        assert entry.height() == 44
        assert entry.property("color") is None  # Plain inline entries, without card surfaces.
        icon = find(root, "voiceCurrentPreview_" + action)
        name = find(root, "voiceCurrentGesture_" + action)
        function = find(root, "voiceCurrentFunction_" + action)
        assert (icon.width(), icon.height()) == (20, 20)
        assert name.property("font").pixelSize() == function.property("font").pixelSize() == 16
        assert name.mapToScene(QPointF()).y() == function.mapToScene(QPointF()).y()
        assert not name.property("truncated") and not function.property("truncated")
        image = visual_child(icon, "voicePreviewGesture_" + entry.property("modelData")["gesture"])
        ready = QQmlExpression(QQmlEngine.contextForObject(image), image, "status === Image.Ready")
        assert ready.evaluate()[0]
        assert "redesign-567-32820-" in image.property("source").toString()
        assert 0 < image.width() <= 20 and 0 < image.height() <= 20
    if size[0] == 1200:
        assert find(root, "voiceCurrentBindings").height() == 44
        assert find(root, "voiceInputCard").height() == 136
    directory = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    directory.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(directory / f"voice-figma-default-{size[0]}.png"))
