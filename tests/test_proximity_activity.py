"""Activity-gated baseline collection; no real BLE, credentials, or locking."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading

import pytest
from PySide6.QtCore import QCoreApplication, QSettings

from test_proximity import Process, prepare, proximity
from proximic_ring.ui.proximity import proximity_controller as module


def update(c, **overrides):
    return dict(event="baseline_updated", device=c._target, revision=c._baseline_revision,
                baseline=-70, samples=[-70] * 5, duration_seconds=2,
                sources=["keyboardMouse"]) | overrides


def test_switches_are_independent_persisted_and_apply_without_restart(proximity):
    c, settings = proximity
    prepare(c); c.enabled = True
    process = c._process
    assert c.baselineOptions == dict(enabled=False, keyboardMouse=True, gestures=False, voice=False)
    c.setBaselineOption("enabled", True)
    assert process.writes[-1] == b"baseline-options 1 1 -64\n"
    c.setBaselineOption("gestures", True)
    c.setBaselineOption("voice", True)
    c.setBaselineOption("keyboardMouse", False)
    assert process.writes[-1] == b"baseline-options 4 6 -64\n"
    c.setBaselineOption("enabled", False)
    assert process.writes[-1] == b"baseline-options 5 0 -64\n"
    assert c._process is process and not process.killed
    restored = module.ProximityController(settings=settings, process_factory=Process)
    try:
        assert restored.baselineOptions == c.baselineOptions
    finally:
        restored.close()


def test_automatic_baseline_and_both_thresholds_survive_relaunch(proximity):
    c, settings = proximity
    prepare(c); c.enabled = True; c.setBaselineOption("enabled", True)
    process = c._process
    process.send(update(c))
    assert c.savedCalibration["baseline"] == -70
    assert c.learnedThresholds["lock"] == -78 and c.learnedThresholds["unlock"] == -74
    assert c._process is process
    target = c._target
    c.enabled = False
    c.enabled = True
    assert c._process.args[-1] == "-70"
    c._process.send(dict(event="monitor", permission=True, connected=True, locked=False))
    assert b"baseline-options 1 1 -70\n" in c._process.writes
    restored = module.ProximityController(settings=settings, process_factory=Process)
    try:
        restored.useDevice(target, "same ring")
        assert restored.savedCalibration == c.savedCalibration
        restored.useDevice("023cb503-2538-4c12-80a2-e4ed8f214fc1", "other ring")
        assert restored.savedCalibration == {}
    finally:
        restored.close()


@pytest.mark.parametrize("bad", [dict(device="wrong-ring"), dict(sources=["voice"]),
    dict(duration_seconds=1.9), dict(duration_seconds=121), dict(samples=[-70] * 4),
    dict(baseline=-71), dict(sources=[]), dict(samples=[127] * 11)])
def test_invalid_auto_capture_cannot_replace_baseline(proximity, bad):
    c, settings = proximity
    prepare(c); c.enabled = True; c.setBaselineOption("enabled", True)
    previous = dict(c.savedCalibration)
    process = c._process
    process.send(update(c, **bad))
    assert c.savedCalibration == previous and process.killed
    assert json.loads(settings.value("proximity/calibrations/" + c._target)) == previous


def test_disabled_or_stale_options_never_save_late_results(proximity):
    c, _ = proximity
    prepare(c); c.enabled = True; c.setBaselineOption("enabled", True)
    stale = update(c)
    c.setBaselineOption("gestures", True)
    c._process.send(stale)
    assert c.savedCalibration["baseline"] == -64
    current = update(c)
    c.setBaselineOption("enabled", False)
    c._process.send(current)
    assert c.savedCalibration["baseline"] == -64


def test_storage_failure_stops_monitor_and_retains_previous_baseline(proximity, monkeypatch):
    c, settings = proximity
    prepare(c); c.enabled = True; c.setBaselineOption("enabled", True)
    previous = dict(c.savedCalibration)
    monkeypatch.setattr(settings, "status", lambda: QSettings.AccessError)
    process = c._process
    process.send(update(c))
    assert process.killed and c.savedCalibration == previous and "无法保存" in c.error
    assert json.loads(settings.value("proximity/calibrations/" + c._target)) == previous


def test_gesture_and_voice_evidence_is_fresh_selected_and_cannot_unlock(proximity):
    c, _ = proximity
    prepare(c); c.enabled = True; c.setBaselineOption("enabled", True)
    connection = threading.Event()
    c.filter_gesture("snap", connection)
    QCoreApplication.processEvents()
    assert not any(b"baseline-activity" in x for x in c._process.writes)
    c.setBaselineOption("gestures", True); c.setBaselineOption("voice", True)
    c.filter_gesture("snap", connection)
    c.record_voice_activity("测试语音", 1, connection)
    c.record_voice_activity("测试语音", 1, connection)
    QCoreApplication.processEvents()
    assert c._process.writes.count(b"baseline-activity 3 gestures\n") == 1
    assert c._process.writes.count(b"baseline-activity 3 voice\n") == 1
    before = len(c._process.writes)
    c._gesture_state = (True, "")
    c.filter_gesture("tap", connection)
    c.record_voice_activity("锁屏期间", 1, connection)
    QCoreApplication.processEvents()
    assert len(c._process.writes) == before
    assert not any(b"gesture-unlock" in x for x in c._process.writes)


def test_queued_activity_cannot_cross_switch_or_process_boundaries(proximity):
    c, _ = proximity
    prepare(c); c.enabled = True
    c.setBaselineOption("enabled", True); c.setBaselineOption("gestures", True)
    connection = threading.Event()
    c.record_activity("gestures", connection)
    c.setBaselineOption("gestures", False); c.setBaselineOption("gestures", True)
    QCoreApplication.processEvents()
    assert not any(b"baseline-activity" in x for x in c._process.writes)
    c.record_activity("gestures", connection)
    c.enabled = False; c.enabled = True
    QCoreApplication.processEvents()
    assert not any(b"baseline-activity" in x for x in c._process.writes)
    connection.set()
    c.record_activity("gestures", connection)
    QCoreApplication.processEvents()
    assert not any(b"baseline-activity" in x for x in c._process.writes)


@pytest.mark.parametrize("age,accepted", [(0.5, True), (2, True), (4.9, True), (5, False), (-1, False)])
def test_activity_tolerates_short_ui_delays_without_accepting_old_events(proximity, monkeypatch, age, accepted):
    c, _ = proximity
    prepare(c); c.enabled = True
    c.setBaselineOption("enabled", True); c.setBaselineOption("voice", True)
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(module.ProximityController, "lock_connection_ready", property(lambda self: True))
    c._apply_activity_evidence("voice", threading.Event(), 100 - age, c._process, c._baseline_revision)
    assert any(b"baseline-activity" in w for w in c._process.writes) == accepted
    if accepted:
        assert c._process.writes[-2:] == [b"host-connected 1\n", b"baseline-activity 2 voice\n"]


def test_activity_during_lock_preparation_is_forwarded_but_locked_activity_is_not(proximity):
    c, _ = proximity
    prepare(c); c.enabled = True
    c.setBaselineOption("enabled", True); c.setBaselineOption("gestures", True)
    conn = threading.Event(); proc = c._process
    proc.send(dict(event="monitor", permission=True, locked=False, lock_pending=True))
    assert c.filter_gesture("tap", conn)  # Still consumed; never execute an ordinary gesture while preparing.
    QCoreApplication.processEvents()
    assert proc.writes.count(b"baseline-activity 2 gestures\n") == 1
    proc.send(dict(event="locked"))
    c.filter_gesture("tap", conn)
    QCoreApplication.processEvents()
    assert proc.writes.count(b"baseline-activity 2 gestures\n") == 1


@pytest.mark.parametrize("count,duration", [(5, 2), (5, 100), (11, 10), (20, 60)])
def test_short_sparse_and_previous_auto_captures_remain_valid(proximity, count, duration):
    c, _ = proximity
    prepare(c); c.enabled = True; c.setBaselineOption("enabled", True)
    c._process.send(update(c, samples=[-70] * count, duration_seconds=duration))
    assert c.savedCalibration["baseline"] == -70
    assert c.savedCalibration["duration_seconds"] == duration
    assert c._process is not None


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("xcrun"), reason="Swift macOS toolchain")
def test_native_activity_collection_and_existing_lock_return_policies(tmp_path):
    root = Path(__file__).resolve().parents[1] / "native/ProxiMicPresence"
    for suite in ("ActivityCalibration", "Calibration", "ReturnWake", ""):
        main = root / "Tests" / suite / "main.swift"
        executable = tmp_path / (suite or "policy")
        subprocess.run(["xcrun", "swiftc", "-swift-version", "5", "-module-cache-path", str(tmp_path / "cache"),
                        str(root / "Policy.swift"), str(root / "Calibration.swift"),
                        str(root / "ReturnWakePolicy.swift"), str(root / "UnlockSecret.swift"),
                        str(main), "-o", str(executable)],
                       check=True, capture_output=True, text=True)
        subprocess.run([str(executable)], check=True, capture_output=True, text=True)
