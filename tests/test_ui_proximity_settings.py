"""Settings UI uses the existing proximity controller with a fake native process."""
from pathlib import Path
import os

import pytest

from test_inline_ui import inline_ui
from test_proximity import Process, calibrate
from proximic_ring.ui.proximity.proximity_controller import ProximityController


_launch_presence = ProximityController._launch


@pytest.fixture
def proximity_ui(inline_ui, tmp_path, monkeypatch):
    from proximic_ring.ui.proximity import proximity_controller

    controller, bridge, _, root, _ = inline_ui
    helper = tmp_path / "helper"
    helper.touch()
    monkeypatch.setattr(proximity_controller, "helper_path", lambda: helper)
    calls = []

    class FakeProcess(Process):
        def start(self, path, args):
            calls.append(list(args))
            super().start(path, args)

    proximity = controller.proximity
    proximity._factory = FakeProcess
    # inline_ui suppresses all native launches; restore dispatch only after
    # installing our fake process so this suite can exercise the real protocol.
    monkeypatch.setattr(proximity, "_launch", _launch_presence.__get__(proximity))
    controller._connected = True
    controller.connectedChanged.emit()
    yield controller, proximity, bridge, root, calls
    proximity.close()


def item(root, name):
    from PySide6.QtCore import QObject
    result = root.findChild(QObject, name)
    if result is None:
        # Repeater delegates belong to the visual tree rather than the window's
        # QObject ownership tree.
        def visual_child(parent):
            if parent.objectName() == name:
                return parent
            for child in parent.childItems():
                found = visual_child(child)
                if found is not None:
                    return found
            return None
        result = visual_child(root.contentItem() if hasattr(root, "contentItem") else root)
    assert result is not None, name
    return result


def click(root, name):
    from PySide6.QtCore import QMetaObject
    assert QMetaObject.invokeMethod(item(root, name), "click")


def open_details(root):
    from PySide6.QtCore import QMetaObject, Q_ARG
    from PySide6.QtTest import QTest
    root.setProperty("currentPage", 0)
    root.show()
    QMetaObject.invokeMethod(item(root, "runtimeSettingsDialog"), "open")
    # The overview now routes to LockPage; exercise the retained settings pane
    # directly, with the main-page route covered by the live-signal test.
    QMetaObject.invokeMethod(item(root, "runtimeSettingsDialog"), "navigate", Q_ARG("QVariant", 9))
    QTest.qWait(150)
    dialog = item(root, "runtimeSettingsDialog")
    for _ in range(30):
        if dialog.property("opacity") >= 0.999:
            break
        QTest.qWait(20)
    assert dialog.property("opacity") >= 0.999


def finish_status(proximity, **overrides):
    process = proximity._process
    process.send(dict(event="status", **dict(permission=True, password=True, password_access=True) | overrides))
    process.finished.emit(0, 0)


def screenshot(root, tmp_path, name):
    shots = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(shots / name))


def scroll_bottom(root):
    from PySide6.QtTest import QTest
    flick = item(root, "runtimeSettingsScroll").property("contentItem")
    flick.setProperty("contentY", max(0, flick.property("contentHeight") - flick.height()))
    QTest.qWait(40)


@pytest.mark.parametrize("size", [(1200, 780), (940, 700)])
def test_live_signal_and_recalibration_on_lock_page(proximity_ui, tmp_path, size):
    from PySide6.QtCore import QPointF
    from PySide6.QtTest import QTest

    _, proximity, _, root, _ = proximity_ui
    root.resize(*size)
    proximity.useDevice("a44fbefd-c2d7-46ce-a9a2-731f1d5b2cf3", "Ringo test")
    root.setProperty("currentPage", 5); root.show(); QTest.qWait(40)
    finish_status(proximity); calibrate(proximity)
    proximity.setBaselineOption("enabled", True)
    proximity.enabled = True
    proc = proximity._process
    proc.send(dict(event="monitor", connected=True, permission=True, rssi=-73, raw_rssi=-80,
                   rssi_age_seconds=0, baseline=-64, lock=-72, unlock=-68,
                   activity_calibration_active=True, activity_calibration_samples=3,
                   activity_calibration_sources=["keyboardMouse"]))
    proc.send(dict(event="calibration_sample", raw_rssi=-80, decision_rssi=-73.2))
    QTest.qWait(30)
    assert item(root, "lockLiveRaw").property("text") == "-80.0 dBm"
    assert item(root, "lockLiveSmoothed").property("text") == "-73.2 dBm"
    assert item(root, "lockLiveBaseline").property("text") == "-64.0 dBm"
    assert item(root, "lockLiveThreshold").property("text") == "-72.0 dBm"
    assert "正在自动采样：3 / 5" in item(root, "lockLiveLearning").property("text")
    for name in ("Raw", "Smoothed", "Baseline", "Threshold", "Decision", "Learning"):
        control = item(root, "lockLive" + name)
        position = control.mapToScene(QPointF())
        assert 0 <= position.x() <= root.width() - control.width()
        assert 0 <= position.y() <= root.height() - control.height()
    screenshot(root, tmp_path, f"lock-live-signal-{size[0]}.png")
    proc.send(dict(event="baseline_updated", device=proximity._target,
                   revision=proximity._baseline_revision, baseline=-80, samples=[-80] * 5,
                   duration_seconds=2, sources=["keyboardMouse"]))
    QTest.qWait(30)
    assert item(root, "lockLiveBaseline").property("text") == "-80.0 dBm"
    assert item(root, "lockLiveThreshold").property("text") == "-88.0 dBm"
    open_details(root)
    assert item(root, "proximityLiveRaw").property("text") == "-80.0 dBm"
    assert item(root, "proximityLiveBaseline").property("text") == "-80.0 dBm"
    assert item(root, "proximityLiveThreshold").property("text") == "-88.0 dBm"
    proc.send(dict(event="monitor", connected=False))
    QTest.qWait(30)
    assert item(root, "proximityLiveRaw").property("text") == "—"
    assert item(root, "proximityLiveSmoothed").property("text") == "—"
    assert item(root, "proximityLiveBaseline").property("text") == "-80.0 dBm"


@pytest.mark.parametrize("mode", ["manual_enabled", "manual_disabled", "automatic"])
def test_calibration_updates_live_display_and_monitor_baseline(proximity_ui, mode):
    import json
    from PySide6.QtTest import QTest

    controller, proximity, _, root, calls = proximity_ui
    proximity.useDevice("a44fbefd-c2d7-46ce-a9a2-731f1d5b2cf3", "Ringo test")
    root.setProperty("currentPage", 5); root.show(); QTest.qWait(40)
    finish_status(proximity); calibrate(proximity)
    proximity.setBaselineOption("enabled", True)
    proximity.enabled = mode != "manual_disabled"
    previous_monitor = proximity._process

    if mode.startswith("manual"):
        click(root, "lockCalibrateButton")
        click(root, "lockStartCalibrationButton")
        if previous_monitor:
            assert not previous_monitor.killed and proximity._process is previous_monitor
        else:
            assert calls[-1] == ["calibrate", proximity._target]
    proc = proximity._process
    token = proximity._live_calibration_id
    proc.send(dict(event="calibration_sample", raw_rssi=-79, decision_rssi=-77.2))
    QTest.qWait(20)
    assert item(root, "lockLiveRaw").property("text") == "-79.0 dBm"
    assert item(root, "lockLiveSmoothed").property("text") == "-77.2 dBm"
    assert item(root, "lockLiveBaseline").property("text") == "-64.0 dBm"
    assert item(root, "lockLiveThreshold").property("text") == "-72.0 dBm"

    if mode.startswith("manual"):
        proc.send(dict(event="calibration_complete", device=proximity._target, baseline=-80,
                       samples=[-80] * 20, duration_seconds=10, capture_id=token))
    else:
        proc.send(dict(event="baseline_updated", device=proximity._target,
                       revision=proximity._baseline_revision, baseline=-80, samples=[-80] * 5,
                       duration_seconds=2, sources=["keyboardMouse"]))
    QTest.qWait(20)
    # Both routes notify QML immediately, without waiting for a monitor report.
    assert item(root, "lockLiveBaseline").property("text") == "-80.0 dBm"
    assert item(root, "lockLiveThreshold").property("text") == "-88.0 dBm"
    # Calibration changes boundaries, not the last real Bluetooth measurement.
    assert item(root, "lockLiveRaw").property("text") == "-79.0 dBm"
    saved = json.loads(controller._settings.value("proximity/calibrations/" + proximity._target))
    assert saved["baseline"] == -80

    if mode.startswith("manual"):
        if mode == "manual_disabled":
            proc.finished.emit(0, 0)
            assert proximity._process is None and not proximity.enabled
        else:
            proc.send(dict(event="calibration_finished", capture_id=token, result="saved"))
            assert proximity._process is previous_monitor and not proc.killed
    else:
        assert proximity._process is proc and not proc.killed
        assert proximity.learnedThresholds["lock"] == -88
        assert proximity.learnedThresholds["unlock"] == -84
    if proximity._process is not None:
        proximity._process.send(dict(event="calibration_sample", raw_rssi=-82, decision_rssi=-81.3))
        QTest.qWait(20)
        assert item(root, "lockLiveRaw").property("text") == "-82.0 dBm"
        assert item(root, "lockLiveSmoothed").property("text") == "-81.3 dBm"
    assert item(root, "lockLiveBaseline").property("text") == "-80.0 dBm"
    assert item(root, "lockLiveThreshold").property("text") == "-88.0 dBm"


@pytest.mark.parametrize("size", [(1200, 780), (940, 700)])
def test_signal_chart_scrolls_and_marks_actual_departure_states(proximity_ui, tmp_path, size):
    import math
    import time
    from PySide6.QtCore import QObject, QPointF
    from PySide6.QtTest import QTest, QSignalSpy

    _, proximity, _, root, _ = proximity_ui
    root.resize(*size)
    proximity.useDevice("a44fbefd-c2d7-46ce-a9a2-731f1d5b2cf3", "Ringo test")
    root.setProperty("currentPage", 5); root.show(); QTest.qWait(30)
    finish_status(proximity); calibrate(proximity, -68); proximity.enabled = True
    timeline = proximity.signalTimeline
    timeline.reset()
    now = time.monotonic()
    for i in range(116):
        elapsed = i * 0.5
        timeline._clock = lambda t=now - 58 + elapsed: t
        baseline = -64 if elapsed < 25 else -68
        raw = -62 + math.sin(i * 1.3) * 3 if elapsed < 42 else -64 - (elapsed - 42) * 1.4 + math.sin(i) * 2
        smoothed = raw if i == 0 else smoothed * 0.6 + raw * 0.4
        state = ("calibrating" if 8 <= elapsed < 18 else "protected" if 23 <= elapsed < 31
                 else "away" if elapsed >= 51 else "normal")
        timeline.record(dict(raw=raw, smoothed=smoothed, baseline=baseline, threshold=baseline - 8,
                             state=state, weak_samples=1 if state == "away" else 0), sample=True)
    timeline._clock = time.monotonic
    trace = dict(event="signal_trace", sample_id=112, raw_rssi=-84, decision_rssi=-81.2,
                 baseline=-68, lock=-76, signal_state="countdown", lock_departure_samples=2,
                 required_departure_samples=2, lock_countdown_seconds=5)
    chart = item(root, "lockLiveChart")
    canvas = chart.findChild(QObject, "proximitySignalCanvas")
    state_label = chart.findChild(QObject, "proximityChartState")
    painted = QSignalSpy(canvas.painted)
    proximity._process.send(dict(event="calibration_sample", sample_id=112, raw_rssi=-84, decision_rssi=-81.2))
    proximity._process.send(trace)
    # No one-second monitor packet or UI polling is needed to update the graph.
    assert chart.property("points")[-1]["smoothed"] == -81.2
    assert "5 秒后锁屏" in state_label.property("text")
    assert state_label.property("color").name() == "#d95536"
    before = chart.property("viewNow")
    QTest.qWait(80)
    assert chart.property("animating") and chart.property("viewNow") > before
    assert painted.count() > 0
    position = canvas.mapToScene(QPointF())
    assert position.x() >= 0 and position.x() + canvas.width() <= root.width()
    assert position.y() >= 0 and position.y() + canvas.height() <= root.height()
    screenshot(root, tmp_path, f"lock-chart-countdown-{size[0]}.png")
    preview = chart.grabToImage()
    QTest.qWait(40)
    shots = Path(os.environ.get("MYTHLINK_SCREENSHOT_DIR", str(tmp_path)))
    assert preview.image().save(str(shots / f"lock-chart-preview-{size[0]}.png"))
    proximity._process.send(trace | dict(signal_state="locked", lock_reason="weak_signal", lock_countdown_seconds=0))
    assert "远离触发" in state_label.property("text")
    assert state_label.property("color").name() == "#bc354c"
    QTest.qWait(30)
    screenshot(root, tmp_path, f"lock-chart-locked-{size[0]}.png")

    # Inspect historical values with actual pointer events. The card occupies
    # its own space, and updates on delivery of the move, without a hover timer.
    area = item(chart, "proximityChartInspectionArea")
    card = item(chart, "proximityChartReadout")
    card_position = card.mapToScene(QPointF())
    card_height = card.height()
    assert card_position.y() >= position.y() + canvas.height()
    assert card_position.x() >= 0 and card_position.x() + card.width() <= root.width()
    assert card_position.y() + card.height() <= root.height()
    fixed_now = time.monotonic()
    timeline._clock = lambda: fixed_now
    QTest.qWait(30)
    samples = [point for point in timeline.points if point["sample"]]
    for index in (5, 52, 90, 110, 30, 75):
        expected = samples[index]
        x = (expected["time"] - chart.property("viewNow") + 60000) / 60000 * area.width()
        target = area.mapToScene(QPointF(x, area.height() / 2)).toPoint()
        QTest.mouseMove(root, target, 0)
        selected = chart.property("inspected")
        if hasattr(selected, "toVariant"):
            selected = selected.toVariant()
        assert selected["time"] == expected["time"]
        for key in ("raw", "smoothed", "baseline", "threshold"):
            assert item(chart, "proximityChartInspection_" + key).property("text") == f"{expected[key]:.1f}"
        assert card.mapToScene(QPointF()) == card_position and card.height() == card_height
        if index == 52:
            assert item(chart, "proximityChartInspectionState").property("text") == "操作保护中"
            QTest.qWait(20)
            screenshot(root, tmp_path, f"lock-chart-inspection-{size[0]}.png")
        if index == 30:
            QTest.qWait(20)
            screenshot(root, tmp_path, f"lock-chart-calibration-band-{size[0]}.png")
    QTest.mouseMove(root, card_position.toPoint(), 0)
    assert item(chart, "proximityChartInspectionTime").property("text") == "移动到曲线查看读数"
    assert item(chart, "proximityChartInspection_raw").property("text") == "—"
    assert card.mapToScene(QPointF()) == card_position and card.height() == card_height

    # Empty history and missing intervals must not show an unrelated sample.
    timeline.reset()
    center = area.mapToScene(QPointF(area.width() / 2, area.height() / 2)).toPoint()
    QTest.mouseMove(root, center, 0)
    assert item(chart, "proximityChartInspectionTime").property("text") == "此处无有效采样"
    for age in (50, 10):
        timeline.record(dict(raw=-65, smoothed=-65, baseline=-64, threshold=-72, state="normal"),
                        sample=True, at=(fixed_now - age) * 1000)
    assert item(chart, "proximityChartInspection_raw").property("text") == "—"
    timeline._clock = time.monotonic
    root.setProperty("currentPage", 0); QTest.qWait(20)
    assert not chart.property("animating")


def test_baseline_evidence_switches_and_updated_thresholds(proximity_ui, tmp_path):
    from PySide6.QtTest import QTest
    controller, proximity, _, root, _ = proximity_ui
    proximity.useDevice("a44fbefd-c2d7-46ce-a9a2-731f1d5b2cf3", "Ringo test")
    open_details(root); finish_status(proximity); calibrate(proximity)
    assert not item(root, "proximityBaseline_enabled").property("checked")
    assert item(root, "proximityBaseline_keyboardMouse").property("checked")
    for name in ("gestures", "voice"):
        assert not item(root, "proximityBaseline_" + name).property("checked")
        click(root, "proximityBaseline_" + name)
        assert proximity.baselineOptions[name]
    click(root, "proximityBaseline_enabled")
    click(root, "proximityBaseline_keyboardMouse")
    assert proximity.baselineOptions == dict(enabled=True, keyboardMouse=False, gestures=True, voice=True)
    proximity.enabled = True
    process = proximity._process
    process.send(dict(event="baseline_updated", device=proximity._target,
                      revision=proximity._baseline_revision, baseline=-70,
                      samples=[-70] * 5, duration_seconds=2, sources=["voice"]))
    QTest.qWait(30)
    details = item(root, "proximityCalibrationDetails").property("text")
    assert "-70.0" in details and "-74.0" in details and "-78.0" in details
    for name in ("enabled", "keyboardMouse", "gestures", "voice"):
        assert item(root, "proximityBaseline_" + name).property("enabled")
    flick = item(root, "runtimeSettingsScroll").property("contentItem")
    group = item(root, "proximityBaseline_enabled")
    from PySide6.QtCore import QPointF
    point = group.mapToItem(flick, QPointF(0, 0))
    flick.setProperty("contentY", max(0, point.y() - 80))
    QTest.qWait(40)
    screenshot(root, tmp_path, "proximity-baseline-options.png")
    click(root, "proximityBaseline_voice")
    assert not proximity.baselineOptions["voice"] and proximity._process is process


@pytest.mark.parametrize("size", [(1200, 780), (940, 700)])
def test_proximity_details_and_advanced_disclosure_do_not_change_configuration(proximity_ui, tmp_path, size):
    from PySide6.QtCore import QPointF
    from PySide6.QtTest import QTest

    controller, proximity, bridge, root, calls = proximity_ui
    root.resize(*size)
    before = {key: controller._settings.value(key) for key in controller._settings.allKeys()}
    messages = list(bridge.messages)
    bindings = controller.gestureBindings
    open_details(root)
    assert calls == [["status"]]
    finish_status(proximity, password=False, password_access=False, permission=False)
    QTest.qWait(30)
    assert not item(root, "proximityAdvancedSettings").property("visible")
    assert not item(root, "proximity_awaySamples").property("visible")
    screenshot(root, tmp_path, f"proximity-top-{size[0]}.png")
    scroll_bottom(root)
    screenshot(root, tmp_path, f"proximity-bottom-{size[0]}.png")
    click(root, "proximityAdvancedButton")
    QTest.qWait(20)
    assert item(root, "proximity_awaySamples").property("visible")
    scroll_bottom(root)
    screenshot(root, tmp_path, f"proximity-advanced-{size[0]}.png")
    for name in ("settingsApplyButton", "proximityRevealComponent"):
        control = item(root, name)
        point = control.mapToScene(QPointF())
        assert 0 <= point.x() <= root.width() - control.width()
        assert 0 <= point.y() <= root.height() - control.height()
    click(root, "settingsBackButton")
    assert item(root, "runtimeSettingsDialog").property("currentPage") == 0
    assert not item(root, "settingsPage9").property("showAdvanced")
    assert calls == [["status"]]
    assert not proximity.enabled
    assert item(root, "proximityOverviewStatus").property("text") == "未开启"
    assert bridge.messages == messages and controller.gestureBindings == bindings
    assert {key: controller._settings.value(key) for key in controller._settings.allKeys()} == before


def test_failed_enable_returns_switch_to_real_state(proximity_ui):
    controller, proximity, _, root, calls = proximity_ui
    open_details(root)
    finish_status(proximity, password=False, password_access=False, permission=False)
    click(root, "proximityEnabled")
    assert not proximity.enabled
    assert not item(root, "proximityEnabled").property("checked")
    assert "连接戒指" in item(root, "proximityError").property("text")
    assert not controller._settings.contains("proximity/enabled")
    assert calls == [["status"]]


@pytest.mark.parametrize("enabled", [False, True])
def test_calibration_progress_and_cancel_preserve_existing_baseline_and_switch(proximity_ui, tmp_path, enabled):
    from PySide6.QtTest import QTest

    controller, proximity, _, root, calls = proximity_ui
    proximity.useDevice("a44fbefd-c2d7-46ce-a9a2-731f1d5b2cf3", "Ring · 界面预览")
    open_details(root)
    finish_status(proximity)
    calibrate(proximity)
    proximity.enabled = enabled
    assert item(root, "proximityOverviewStatus").property("text") == ("已开启" if enabled else "未开启")
    before = controller._settings.value("proximity/calibrations/" + proximity._target)
    click(root, "proximityCalibrate")
    assert calls[-1][0] == ("monitor" if enabled else "calibrate")
    process = proximity._process
    token = proximity._live_calibration_id
    process.send(dict(event="calibration_progress", elapsed=4.0, samples=8, capture_id=token))
    QTest.qWait(40)
    assert item(root, "proximityCalibrationProgress").property("value") == 4.0
    assert not item(root, "proximityEnabled").property("enabled")
    assert not item(root, "proximityConfigurePassword").property("enabled")
    assert item(root, "proximityCancelCalibration").property("visible")
    screenshot(root, tmp_path, f"proximity-calibrating-{enabled}.png")
    click(root, "proximityCancelCalibration")
    if enabled:
        process.send(dict(event="calibration_finished", capture_id=token, result="cancelled"))
    QTest.qWait(30)
    assert process.killed == (not enabled)
    assert proximity.enabled == enabled
    assert item(root, "proximityEnabled").property("checked") == enabled
    assert not item(root, "proximityCancelCalibration").property("visible")
    assert "原校准数据已保留" in item(root, "proximityCalibrationStatus").property("text")
    assert controller._settings.value("proximity/calibrations/" + proximity._target) == before
    click(root, "proximityAdvancedButton")
    for name in ("proximity_awaySamples", "proximity_lostDelay"):
        assert item(root, name).property("enabled") == (not enabled)
    if enabled:
        assert calls[-1][0] == "monitor"
    else:
        assert proximity._process is None


@pytest.mark.parametrize("button,command", [
    ("proximityConfigurePassword", "password"),
    ("proximityAuthorizePassword", "authorize-password"),
    ("proximityForgetPassword", "forget"),
])
def test_setup_buttons_keep_the_existing_native_routes(proximity_ui, button, command):
    # Authorization is covered at its single Settings entry by
    # test_ui_permissions_consolidated; password actions stay on the lock page.
    _, proximity, _, root, calls = proximity_ui
    open_details(root)
    finish_status(proximity, password_access=False)
    assert item(root, button).property("visible")
    click(root, button)
    assert calls[-1] == [command]
    assert proximity.busy
    assert not item(root, button).property("enabled")
    assert not proximity.enabled
