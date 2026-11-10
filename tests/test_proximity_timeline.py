from proximic_ring.ui.proximity.proximity_timeline import ProximityTimeline
from test_proximity import proximity, prepare


def test_history_keeps_original_thresholds_and_distinct_state_events():
    clock = [0.0]
    timeline = ProximityTimeline(clock=lambda: clock[0])
    timeline.record(dict(baseline=-64, threshold=-72, state="normal", raw=-62, smoothed=-63), sample=True)
    first = timeline.points[0]
    clock[0] = 1
    timeline.record(dict(state="away", weak_samples=1))
    clock[0] = 2
    timeline.record(dict(state="countdown", countdown=5))
    assert timeline.stateLabel == "远离已确认 · 5 秒后锁屏"
    clock[0] = 3
    timeline.record(dict(baseline=-80, threshold=-88, state="protected", raw=-79, smoothed=-78), sample=True)
    assert timeline.points[0] == first and first["baseline"] == -64 and first["threshold"] == -72
    assert [point["sample"] for point in timeline.points] == [True, False, False, True]
    assert timeline.points[-1]["threshold"] == -88
    timeline.record(dict(state="locked", reason="weak_signal"))
    assert timeline.stateLabel == "已锁屏 · 远离触发"
    timeline.record(dict(state="locked", reason=""))
    assert timeline.stateLabel == "已锁屏 · 系统或手动"


def test_history_is_bounded_and_same_state_does_not_manufacture_samples():
    clock = [0.0]
    timeline = ProximityTimeline(clock=lambda: clock[0])
    timeline.record(dict(state="normal"))
    for _ in range(100):
        timeline.record(dict(state="normal"))
    assert len(timeline.points) == 1
    for i in range(3000):
        clock[0] = i / 100
        timeline.record(dict(raw=-60, smoothed=-60), sample=True)
    assert len(timeline.points) == timeline.MAX_POINTS
    clock[0] = 100
    timeline.record(dict(state="stale"))
    assert len(timeline.points) == 2
    assert not timeline.points[-1]["sample"]
    timeline.reset()
    assert timeline.points == [] and timeline.state == "paused"


def test_native_trace_records_actual_lock_counter_and_avoids_duplicate_readings(proximity):
    c, _ = proximity; prepare(c); c.enabled = True
    proc = c._process
    before = len([p for p in c.signalTimeline.points if p["sample"]])
    proc.send(dict(event="calibration_sample", sample_id=1, raw_rssi=-80, decision_rssi=-73.2))
    assert len([p for p in c.signalTimeline.points if p["sample"]]) == before
    trace = dict(event="signal_trace", sample_id=1, raw_rssi=-80, decision_rssi=-73.2,
                 baseline=-64, lock=-72, signal_state="away", lock_departure_samples=1,
                 required_departure_samples=2, lock_countdown_seconds=0)
    proc.send(trace)
    point = c.signalTimeline.points[-1]
    assert point["sample"] and point["smoothed"] == -73.2
    assert c.signalTimeline.stateLabel == "弱信号确认 1 / 2"
    proc.send(trace | dict(signal_state="countdown", lock_departure_samples=2, lock_countdown_seconds=5))
    assert not c.signalTimeline.points[-1]["sample"]
    # An unrelated return-detection counter must not override the native lock trace.
    proc.send(dict(event="monitor", connected=True, departure_samples=0, locked=False,
                   rssi=-73, raw_rssi=-80, rssi_age_seconds=0))
    assert c.signalTimeline.state == "countdown"
    proc.send(trace | dict(sample_id=2, signal_state="protected", baseline=-80, lock=-88))
    assert point["baseline"] == -64 and c.signalTimeline.points[-1]["baseline"] == -80
    proc.send(dict(event="signal_trace", signal_state="disconnected"))
    proc.send(trace | dict(sample_id=2))
    assert c.signalTimeline.points[-1]["sample"]  # Counter can restart at the previous ID.
    c._expire_signal()
    assert c.signalTimeline.state == "stale"
    c.useDevice("023cb503-2538-4c12-80a2-e4ed8f214fc1", "other")
    assert not any(p["sample"] for p in c.signalTimeline.points)


def test_manual_capture_plots_before_calibration_and_disable_preserves_history(proximity):
    c, _ = proximity; prepare(c); c.startCalibration()
    c._process.send(dict(event="calibration_sample", sample_id=1, raw_rssi=-78, decision_rssi=-76))
    point = c.signalTimeline.points[-1]
    assert point["sample"] and point["state"] == "calibrating"
    assert point["baseline"] == -64
    c.cancelCalibration()
    assert point in c.signalTimeline.points and c.signalTimeline.state == "paused"


def test_trace_clock_preserves_event_spacing_when_delivery_is_batched(proximity):
    c, _ = proximity; prepare(c); c.enabled = True
    c.signalTimeline.reset()
    clock = [100.0]
    c.signalTimeline._clock = lambda: clock[0]
    trace = dict(event="signal_trace", signal_state="normal", baseline=-64, lock=-72,
                 raw_rssi=-62, decision_rssi=-63)
    c._process.send(trace | dict(sample_id=1, trace_uptime=10.0))
    clock[0] = 102
    c._process.send(trace | dict(sample_id=2, trace_uptime=10.5))
    c._process.send(trace | dict(sample_id=3, trace_uptime=11.0))
    assert [p["time"] for p in c.signalTimeline.points] == [100000, 100500, 101000]
