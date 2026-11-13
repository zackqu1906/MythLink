import json
from types import SimpleNamespace

from PySide6.QtCore import QObject
from PySide6.QtTest import QTest

from test_inline_ui import inline_ui


def test_discrete_attempt_recording_ui_and_annotations_do_not_dispatch_input(inline_ui, monkeypatch):
    from proximic_ring.gestures.firmware_gestures import FirmwareGestureEvent
    controller, bridge, _, root, _ = inline_ui
    monkeypatch.setattr(controller.appGestures, "foreground_identity", lambda: ("com.apple.Safari", 91))
    before = list(bridge.messages)
    event = FirmwareGestureEvent(1, "swipe-up", .9, 22, 3, object())
    assert controller.ringGestures.filter(event, False, object()) is False  # stale connection
    row, = controller._gesture_history.records()
    assert row["record_id"] == event.record_id and row["status"] == "not_triggered"
    assert row["gesture"] == "swipe-up" and row["application"] == "com.apple.Safari"
    controller._apply_runtime_status("[GESTURE_RECOGNIZED] " + json.dumps({
        "record_id": event.record_id, "name": "swipe-up", "reason": "global_mode_route"}))
    from proximic_ring.diagnostics.export_scope import log_attributions
    lines = controller._diagnostic_log.path.read_text().splitlines()
    recognized = next(line for line in lines if "[GESTURE_RECOGNIZED]" in line and event.record_id in line)
    assert ("com.apple.safari", "") in log_attributions(recognized)
    target = SimpleNamespace(bundle="com.apple.Safari", pid=91, scene="video", input_context="nontext", blocked=False, browser_target={})
    controller.appGestures._trace(event.record_id, "outcome", "shortcut_posted", target=target, action="next", shortcut="Right", native={"secret": "excluded-diagnostic"})
    controller.gestureHistory.show()
    root.resize(940, 700)
    root.show()
    QTest.qWait(100)
    dialog = root.findChild(QObject, "gestureHistoryDialog")
    assert dialog.property("visible")
    row, = controller.gestureHistory.records
    assert row["status"] == "triggered" and row["scene"] == "video"
    assert "excluded-diagnostic" not in json.dumps(row)
    controller.gestureHistory.annotate(event.record_id, "wrong_action", "暂停播放")
    assert controller.gestureHistory.records[0]["expected_action"] == "暂停播放"
    assert controller.gestureHistory.records[0]["action"] != "暂停播放"
    QTest.qWait(250)
    assert dialog.property("width") > 500 and dialog.property("height") > 400
    assert dialog.property("opacity") == 1
    assert root.grabWindow().save("/tmp/mythlink-gesture-history.png")
    controller.gestureHistory.deleteRecord(event.record_id)
    controller._gesture_history.observe(event.record_id, gesture="swipe-up", status="confirmed")
    assert controller._gesture_history.records() == []
    assert bridge.messages == before
