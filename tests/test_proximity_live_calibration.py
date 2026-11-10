"""Manual recalibration shares the live monitor and preserves truthful history."""
import json

import pytest

from test_proximity import module, prepare, proximity


def complete(c, process, baseline=-80, **overrides):
    process.send(dict(event="calibration_complete", capture_id=c._live_calibration_id,
                      device=c._target, baseline=baseline, samples=[baseline] * 20,
                      duration_seconds=10) | overrides)


def test_manual_calibration_keeps_monitor_samples_and_both_chart_boundaries_continuous(proximity):
    c, _ = proximity; prepare(c); c.enabled = True
    proc = c._process
    timeline = c.signalTimeline; timeline.reset()
    trace = dict(event="signal_trace", raw_rssi=-70, decision_rssi=-67.2,
                 baseline=-64, lock=-72, signal_state="normal")
    proc.send(trace | dict(sample_id=1))
    c.startCalibration()
    assert c._process is proc and not proc.killed
    token = c._live_calibration_id
    assert proc.writes[-1] == f"calibration-start {token}\n".encode()
    assert c.calibrating and c.busy
    proc.send(dict(event="calibration_sample", sample_id=2, raw_rssi=-80, decision_rssi=-72.32, collecting=True))
    proc.send(trace | dict(sample_id=2, raw_rssi=-80, decision_rssi=-72.32, signal_state="calibrating"))
    complete(c, proc)
    assert proc.writes[-1] == f"calibration-accept {token}\n".encode()
    proc.send(dict(event="calibration_finished", capture_id=token, result="saved"))
    proc.send(trace | dict(sample_id=3, baseline=-80, lock=-88, raw_rssi=-80, decision_rssi=-75.392))
    assert c._process is proc and not proc.killed and not c.calibrating
    assert not any(p["state"] in {"paused", "stale", "disconnected"} for p in timeline.points)
    samples = [p for p in timeline.points if p["sample"]]
    assert [p["smoothed"] for p in samples] == [-67.2, -72.32, -75.392]
    assert [p["baseline"] for p in samples] == [-64, -64, -80]
    assert c.savedCalibration["baseline"] == -80 and c.learnedThresholds["lock"] == -88


def test_cancel_and_late_completion_do_not_replace_baseline_or_stop_monitor(proximity):
    c, settings = proximity; prepare(c); c.enabled = True
    proc = c._process; original = c.savedCalibration
    c.startCalibration(); token = c._live_calibration_id
    c.cancelCalibration()
    assert c._process is proc and not proc.killed
    assert proc.writes[-1] == f"calibration-cancel {token}\n".encode()
    complete(c, proc)
    assert c.savedCalibration == original
    proc.send(dict(event="calibration_finished", capture_id=token, result="cancelled"))
    assert not c.calibrating
    c.startCalibration(); replacement = c._live_calibration_id
    complete(c, proc, capture_id=token)
    proc.send(dict(event="calibration_finished", capture_id=token, result="saved"))
    assert c.calibrating and c._live_calibration_id == replacement
    assert json.loads(settings.value("proximity/calibrations/" + c._target)) == original


@pytest.mark.parametrize("connected", [True, False])
def test_live_capture_renews_actual_host_connection_without_monitor_reports(proximity, monkeypatch, connected):
    # Live capture replaces periodic monitor reports with progress events. The
    # host lease still needs renewal, otherwise it expires and resets the EMA.
    monkeypatch.setattr(module.ProximityController, "lock_connection_ready", property(lambda self: connected))
    c, _ = proximity; prepare(c); c.enabled = True
    proc = c._process
    c.startCalibration()
    for second in range(10):
        previous = len(proc.writes)
        proc.send(dict(event="calibration_progress", capture_id=c._live_calibration_id,
                       elapsed=second, samples=second * 2))
        assert proc.writes[previous:] == [b"host-connected 1\n" if connected else b"host-connected 0\n"]
    assert c._process is proc and not proc.killed


@pytest.mark.parametrize("failure", ["设备断连", "电脑睡眠", "样本不足"])
def test_failed_live_capture_preserves_old_baseline_and_monitor(proximity, failure):
    c, _ = proximity; prepare(c); c.enabled = True
    proc = c._process; original = c.savedCalibration
    c.startCalibration()
    proc.send(dict(event="calibration_finished", capture_id=c._live_calibration_id,
                   result="failed", error=failure))
    assert c._process is proc and not proc.killed and not c.calibrating
    assert c.savedCalibration == original and failure in c.calibrationStatus


def test_lock_request_and_auto_learning_cannot_race_manual_capture(proximity):
    c, _ = proximity; prepare(c); c.enabled = True; c.setBaselineOption("enabled", True)
    proc = c._process; original = c.savedCalibration
    c.startCalibration()
    proc.send(dict(event="monitor", locked=False, permission=True, connected=True, locking_enabled=True,
                   lock_countdown_seconds=4, lock_pending=False))
    assert c.lockNoticeState == ""
    proc.send(dict(event="prepare_lock"))
    assert proc.writes[-1] == b"lock-cancel\n"
    assert b"lock-ready\n" not in proc.writes
    proc.send(dict(event="baseline_updated", device=c._target, revision=c._baseline_revision,
                   baseline=-90, samples=[-90] * 5, duration_seconds=2, sources=["keyboardMouse"]))
    assert c.savedCalibration == original


def test_closing_dialog_after_save_does_not_cancel_pending_acceptance(proximity):
    c, _ = proximity; prepare(c); c.enabled = True
    proc = c._process
    c.startCalibration(); token = c._live_calibration_id
    complete(c, proc)
    writes = list(proc.writes)
    c.cancelCalibration()
    assert proc.writes == writes and c.calibrating
    proc.send(dict(event="calibration_finished", capture_id=token, result="saved"))
    assert c.savedCalibration["baseline"] == -80
    assert not c.calibrating and c._process is proc and not proc.killed


def test_unacknowledged_save_recovers_monitor_using_saved_baseline(proximity):
    c, _ = proximity; prepare(c); c.enabled = True
    proc = c._process
    c.startCalibration(); complete(c, proc)
    c._timeout()
    assert not c.calibrating and proc.killed
    assert c._process.args[-1] == "-80" and c.savedCalibration["baseline"] == -80
