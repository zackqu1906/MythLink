import json
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QCoreApplication, QSettings, Signal

from proximic_ring.ui import proximity_controller as module


class Process(QObject):
    readyReadStandardOutput = Signal()
    readyReadStandardError = Signal()
    finished = Signal(int, int)
    errorOccurred = Signal(int)

    def __init__(self, parent):
        super().__init__(parent); self.output = b""; self.writes = []; self.killed = False
    def setProcessChannelMode(self, _): pass
    def start(self, path, args): self.path = path; self.args = args
    def readAllStandardOutput(self):
        output, self.output = self.output, b""; return output
    def readAllStandardError(self): return b""
    def write(self, data): self.writes.append(data)
    def closeWriteChannel(self): pass
    def kill(self): self.killed = True
    def send(self, data):
        self.output += json.dumps(data).encode() + b"\n"; self.readyReadStandardOutput.emit()


@pytest.fixture
def proximity(tmp_path, monkeypatch):
    app = QCoreApplication.instance() or QCoreApplication([])
    helper = tmp_path / "helper"; helper.touch()
    monkeypatch.setattr(module, "helper_path", lambda: helper)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    c = module.ProximityController(settings=settings, process_factory=Process)
    yield c, settings
    c.close()


def prepare(c):
    c.useDevice("a44fbefd-c2d7-46ce-a9a2-731f1d5b2cf3", "Ringo test")
    c.refresh(); proc = c._process
    proc.send(dict(event="status", permission=True, password=True, password_access=True, error=""))
    proc.finished.emit(0, 0)
    calibrate(c)


def calibrate(c, baseline=-64):
    c.startCalibration()
    proc = c._process
    proc.send(dict(event="calibration_complete", device=c._target, baseline=baseline,
                   samples=[baseline] * 20, duration_seconds=10))
    proc.finished.emit(0, 0)


def test_explicit_opt_in_and_prerequisites(proximity):
    c, settings = proximity
    assert not c.enabled and c._process is None
    c.enabled = True
    assert not c.enabled and "连接戒指" in c.error
    c.useDevice("not-a-uuid", "fake")
    assert not c._target
    prepare(c)
    assert not c.enabled
    c.enabled = True
    assert c.enabled and settings.value("proximity/enabled", type=bool)
    assert c._process.args == ["monitor", c._target, "2", "15", "-64"]
    assert "password" not in settings.allKeys()


def test_lock_ack_only_after_voice_pause_and_ignore_late_messages(proximity):
    c, _ = proximity; prepare(c); c.enabled = True
    process = c._process; order = []
    c.lockPreparing.connect(lambda: order.append(("pause", len(process.writes))))
    process.send(dict(event="prepare_lock"))
    assert order == [("pause", 0)] and process.writes == [b"lock-ready\n"]
    c.enabled = False
    assert process.killed
    process.send(dict(event="prepare_lock"))
    process.send(dict(event="monitor", state="old", permission=True))
    assert len(process.writes) == 1 and c.status == "已关闭"


def test_password_dialog_is_native_no_secret_in_python(proximity):
    c, settings = proximity; prepare(c); c.enabled = True
    old = c._process
    c.configurePassword()
    assert not c.enabled and old.killed
    assert c._process.args == ["password"] and c.busy
    assert all("password" not in key.lower() for key in settings.allKeys())
    c._process.send(dict(event="status", permission=True, password=True, password_access=True))
    c._process.finished.emit(0, 0)
    assert c.passwordConfigured and not c.enabled


def test_monitor_hang_stops_without_retrying_unlock(proximity):
    c, _ = proximity; prepare(c); c.enabled = True
    old = c._process
    c._timeout()
    assert old.killed and c._process is None and "未响应" in c.error
    old.finished.emit(0, 0)
    assert c._process is None


def test_protocol_handles_partial_lines_and_bounds_buffer(proximity):
    c, _ = proximity; c.refresh(); proc = c._process
    proc.output = b'{"event":"status",'; proc.readyReadStandardOutput.emit()
    assert not c.passwordConfigured
    proc.output = b'"password":true,"permission":true}\n'; proc.readyReadStandardOutput.emit()
    assert c.passwordConfigured and c.permissionReady
    proc.output = b"x" * 33000; proc.readyReadStandardOutput.emit()
    assert proc.killed and "通信异常" in c.error


def test_threshold_validation_and_follow_main_window_device(proximity):
    c, _ = proximity
    c.setOption("unlockRSSI", -80)
    assert "unlockRSSI" not in c.options
    c.setOption("awaySamples", 12)
    assert c.options["awaySamples"] == 12
    prepare(c); c.enabled = True
    target = c._target
    c.useDevice("023cb503-2538-4c12-80a2-e4ed8f214fc1", "other")
    assert c._target != target
    assert c._process is None and "校准" in c.error
    c.setOption("awaySamples", 1)
    assert c.options["awaySamples"] == 12


def test_process_failure_and_status_refresh_never_launch_lock_action(proximity):
    c, _ = proximity; c.refresh()
    proc = c._process
    assert proc.args == ["status"]
    proc.errorOccurred.emit(0)
    assert c._process is None and not c.enabled
    assert "无法运行" in c.error


def test_monitor_error_is_visible_and_does_not_restart(proximity):
    c, _ = proximity; prepare(c); c.enabled = True
    proc = c._process
    proc.send(dict(event="error", error="无法读取钥匙串"))
    proc.finished.emit(1, 0)
    assert c.error == "无法读取钥匙串" and c._process is None


def test_same_ring_reconnect_keeps_monitor_and_lock_ownership(proximity):
    c, settings = proximity; prepare(c); c.enabled = True
    proc = c._process
    c.useDevice(c._target, "same ring")
    assert c._process is proc and not proc.killed
    assert not any(key in settings.allKeys() for key in ("proximity/device", "proximity/name"))


def test_settings_corruption_does_not_break_main_ui(tmp_path, monkeypatch):
    app = QCoreApplication.instance() or QCoreApplication([])
    settings = QSettings(str(tmp_path / "bad.ini"), QSettings.IniFormat)
    settings.setValue("proximity/awaySamples", "invalid")
    settings.setValue("proximity/lockRSSI", -30)
    c = module.ProximityController(settings=settings, process_factory=Process)
    assert c.options == dict(awaySamples=2, lostDelay=15)
    c.close()


def test_departure_count_replaces_old_seconds_without_changing_calibration(proximity):
    c, settings = proximity
    prepare(c)
    target = c._target
    original = settings.value("proximity/calibrations/" + target)
    c.close()
    settings.setValue("proximity/awayDelay", 8)
    settings.setValue("proximity/lostDelay", 20)
    migrated = module.ProximityController(settings=settings, process_factory=Process)
    try:
        assert migrated.options == dict(awaySamples=2, lostDelay=20)
        assert not settings.contains("proximity/awayDelay")
        migrated.useDevice(target, "same ring")
        assert settings.value("proximity/calibrations/" + target) == original
        assert migrated.savedCalibration["baseline"] == -64
        migrated.setOption("awaySamples", 5)
        migrated.setOption("awaySamples", 0)
        assert migrated.options["awaySamples"] == 5
        migrated.setOption("awaySamples", 61)
        assert migrated.options["awaySamples"] == 5
    finally:
        migrated.close()
    restored = module.ProximityController(settings=settings, process_factory=Process)
    try:
        assert restored.options == dict(awaySamples=5, lostDelay=20)
    finally:
        restored.close()


def test_packaging_probe_does_not_start_saved_enabled_monitor(tmp_path, monkeypatch):
    app = QCoreApplication.instance() or QCoreApplication([])
    settings = QSettings(str(tmp_path / "probe.ini"), QSettings.IniFormat)
    settings.setValue("proximity/enabled", True)
    monkeypatch.setenv("PROXIMIC_STARTUP_PROBE", "1")
    c = module.ProximityController(settings=settings, process_factory=Process)
    app.processEvents()
    assert not c.enabled and c._process is None
    assert settings.value("proximity/enabled", type=bool)  # Probe does not alter user preference.
    c.close()


def test_saved_password_without_keychain_access_cannot_start_monitor(proximity):
    c, _ = proximity
    c.useDevice("a44fbefd-c2d7-46ce-a9a2-731f1d5b2cf3", "Ringo test")
    c.refresh(); c._process.send(dict(event="status", permission=True, password=True, password_access=False))
    c._process.finished.emit(0, 0)
    c.enabled = True
    assert not c.enabled and "允许访问已存密码" in c.error and c._process is None
    c.authorizePassword()
    assert c._process.args == ["authorize-password"] and c.busy
    c._process.send(dict(event="status", permission=True, password=True, password_access=True))
    c._process.finished.emit(0, 0)
    assert c.passwordAccessReady and not c.enabled
    calibrate(c)
    c.enabled = True
    assert c._process.args[0] == "monitor"


def test_automatic_thresholds_are_live_read_only_and_clear_on_disconnect(proximity):
    c, settings = proximity
    assert "lockRSSI" not in c.options and "unlockRSSI" not in c.options
    prepare(c); c.enabled = True
    c._process.send(dict(event="monitor", permission=True, thresholds_ready=True,
                         baseline=-68, lock=-80, unlock=-72, calibration_samples=40))
    assert c.learnedThresholds["thresholds_ready"] is True
    assert c.learnedThresholds["lock"] == -80
    c._process.send(dict(event="monitor", permission=True, thresholds_ready=True,
                         baseline=-69, lock=-81, unlock=-73, calibration_samples=41))
    assert c.learnedThresholds["unlock"] == -73
    c.enabled = False
    assert c.learnedThresholds == {}


def test_calibration_trace_preserves_gate_and_effective_boundaries(proximity):
    c, _ = proximity
    prepare(c); c.enabled = True
    events = []
    c.diagnostic.connect(events.append)
    c._process.send(dict(event="calibration_sample", raw_rssi=-65, decision_rssi=-64.5,
                         collecting=False, lock=-80, unlock=-68, sample_interval_seconds=1.05))
    assert events[-1] == dict(action="calibration_sample", raw_rssi=-65, decision_rssi=-64.5,
                             collecting=False, lock=-80, unlock=-68, sample_interval_seconds=1.05)


@pytest.mark.skipif(module.sys.platform != "darwin", reason="macOS startup authorization check")
def test_saved_authorization_is_checked_silently_on_startup(proximity):
    c, settings = proximity
    assert not c.passwordStatusChecked
    QCoreApplication.processEvents()
    assert c._process.args == ["status"]
    c._process.send(dict(event="status", password=True, password_access=True, permission=True))
    c._process.finished.emit(0, 0)
    assert c.passwordStatusChecked and c.passwordAccessReady
    assert not any("password" in key.lower() for key in settings.allKeys())


def test_learning_progress_reaches_settings(proximity):
    c, _ = proximity
    prepare(c); c.enabled = True
    c._process.send(dict(event="monitor", permission=True, baseline=-70.5,
                         calibration_samples=43, calibration_window=23,
                         calibration_active=True, rssi_samples=81, raw_rssi=-72))
    assert c.learnedThresholds["baseline"] == -70.5
    assert c.learnedThresholds["calibration_window"] == 23
    assert c.learnedThresholds["rssi_samples"] == 81
    assert c.learnedThresholds["calibration_active"] is True
    c._process.send(dict(event="monitor", permission=True, calibration_samples=43,
                         calibration_window=23, calibration_active=False, rssi_samples=82))
    assert c.learnedThresholds["calibration_active"] is False
    assert c.learnedThresholds["calibration_window"] == 23
    c.close()

def test_old_unlock_delay_is_removed(tmp_path):
    settings = QSettings(str(tmp_path / "migration.ini"), QSettings.IniFormat)
    settings.setValue("proximity/nearDelay", 3)
    c = module.ProximityController(settings=settings, process_factory=Process)
    assert "nearDelay" not in c.options
    assert "proximity/nearDelay" not in settings.allKeys()
    c.setOption("nearDelay", 4)
    assert "nearDelay" not in c.options
    c.close()


def test_locked_gesture_is_consumed_and_sent_only_once(proximity):
    import threading
    c, _ = proximity; prepare(c); c.enabled = True
    proc = c._process
    conn = threading.Event()
    assert not c.filter_gesture("snap", conn)
    proc.send(dict(event="prepare_lock"))
    assert c.filter_gesture("snap", conn)
    token = "a44fbefd-c2d7-46ce-a9a2-731f1d5b2cf3"
    proc.send(dict(event="monitor", locked=True, unlock_token=token, permission=True))
    for gesture in ["tap", "swipe-down", "clench", "index-pinch", "middle-pinch", "swipe-up"]:
        assert c.filter_gesture(gesture, conn)
    QCoreApplication.processEvents()
    assert not any(w.startswith(b"gesture-unlock") for w in proc.writes)
    for gesture in ["snap", "snap"]:
        assert c.filter_gesture(gesture, conn)
    QCoreApplication.processEvents()
    assert proc.writes.count(("gesture-unlock " + token + "\n").encode()) == 1
    proc.send(dict(event="unlocked"))
    assert not c.filter_gesture("snap", conn)


def test_queued_gesture_cannot_cross_lock_or_connection(proximity):
    import threading
    c, _ = proximity; prepare(c); c.enabled = True
    proc = c._process; conn = threading.Event()
    token = "a44fbefd-c2d7-46ce-a9a2-731f1d5b2cf3"
    proc.send(dict(event="monitor", locked=True, unlock_token=token, permission=True))
    c.filter_gesture("snap", conn)
    proc.send(dict(event="monitor", locked=True, unlock_token="", permission=True))
    QCoreApplication.processEvents()
    assert not any(w.startswith(b"gesture-unlock") for w in proc.writes)
    proc.send(dict(event="monitor", locked=True, unlock_token=token, permission=True))
    c.filter_gesture("snap", conn); conn.set()
    QCoreApplication.processEvents()
    assert not any(w.startswith(b"gesture-unlock") for w in proc.writes)
    assert c.filter_gesture("tap", conn)  # Never fall through to voice on lock screen.


def test_requires_explicit_calibration_and_saves_per_ring_across_restart(proximity):
    c, settings = proximity
    target = "a44fbefd-c2d7-46ce-a9a2-731f1d5b2cf3"
    other = "023cb503-2538-4c12-80a2-e4ed8f214fc1"
    c.useDevice(target, "ring")
    c.refresh(); proc = c._process
    proc.send(dict(event="status", permission=True, password=True, password_access=True))
    proc.finished.emit(0, 0)
    c.enabled = True
    assert not c.enabled and "校准" in c.error
    calibrate(c, -63.5)
    saved = c.savedCalibration
    assert saved["baseline"] == -63.5 and len(saved["samples"]) == 20
    c.enabled = True
    assert c._process.args[-1] == "-63.5"
    # Live signal and a same-ring reconnect cannot overwrite the saved record.
    c._process.send(dict(event="monitor", baseline=-90, connected=True, permission=True))
    c.useDevice(target.upper(), "returned")
    assert c.savedCalibration == saved
    c.enabled = False
    c.useDevice(other, "other")
    assert not c.savedCalibration
    calibrate(c, -72)
    c.useDevice(target, "returned")
    assert c.savedCalibration == saved
    c.close()
    restarted = module.ProximityController(settings=settings, process_factory=Process)
    restarted.useDevice(target, "ring")
    assert restarted.savedCalibration == saved
    restarted.close()


def test_failed_cancelled_or_late_calibration_never_replaces_saved_data(proximity):
    c, settings = proximity; prepare(c)
    original = c.savedCalibration
    key = "proximity/calibrations/" + c._target
    serialized = settings.value(key)
    c.startCalibration(); proc = c._process
    proc.send(dict(event="calibration_progress", elapsed=4.5, samples=9))
    assert c.calibrating and c.calibrationElapsed == 4.5 and c.calibrationSamples == 9
    proc.send(dict(event="error", error="戒指断连，未保存")); proc.finished.emit(2, 0)
    assert not c.calibrating and c.savedCalibration == original
    c.startCalibration(); old = c._process
    c.cancelCalibration()
    old.send(dict(event="calibration_complete", device=c._target, baseline=-80,
                  samples=[-80] * 20, duration_seconds=10))
    assert c.savedCalibration == original and settings.value(key) == serialized
    c.startCalibration(); proc = c._process
    proc.send(dict(event="calibration_complete", device=c._target, baseline=-80,
                   samples=[-80] * 3, duration_seconds=10))
    assert "无效" in c.error and c.savedCalibration == original
    calibrate(c, -70)
    assert c.savedCalibration["baseline"] == -70


def test_calibration_while_monitoring_rejects_wrong_ring_and_resumes_original_baseline(proximity):
    c, _ = proximity; prepare(c); c.enabled = True
    monitor = c._process
    c.startCalibration()
    assert monitor.killed and c.calibrating and c.enabled
    original = c.savedCalibration
    c._process.send(dict(event="calibration_complete", device="wrong-device", baseline=-80,
                        samples=[-80] * 20, duration_seconds=10))
    assert c.savedCalibration == original and "无效" in c.calibrationStatus
    assert c._process.args == ["monitor", c._target, "2", "15", "-64"]


@pytest.mark.parametrize("enabled", [False, True])
def test_calibration_available_with_either_switch_state_and_restores_that_state(proximity, enabled):
    c, settings = proximity; prepare(c); c.enabled = enabled
    monitor = c._process
    preference = settings.value("proximity/enabled")
    c.startCalibration(); capture = c._process
    assert capture.args == ["calibrate", c._target] and c.calibrating
    assert c.enabled == enabled and settings.value("proximity/enabled") == preference
    if monitor:
        assert monitor.killed
        writes = list(monitor.writes)
        monitor.send(dict(event="prepare_lock"))
        assert monitor.writes == writes  # Late old-monitor output cannot lock during capture.
    capture.send(dict(event="calibration_complete", device=c._target, baseline=-70,
                      samples=[-70] * 20, duration_seconds=10))
    capture.finished.emit(0, 0)
    assert not c.calibrating and c.savedCalibration["baseline"] == -70
    assert "已保存" in c.calibrationStatus
    assert c.enabled == enabled and settings.value("proximity/enabled") == preference
    if enabled:
        assert c._process.args == ["monitor", c._target, "2", "15", "-70"]
        resumed = c._process
        capture.finished.emit(0, 0); capture.errorOccurred.emit(0)
        assert c._process is resumed and not resumed.killed
    else:
        assert c._process is None


@pytest.mark.parametrize("outcome", ["cancel", "error", "exit", "timeout", "process_error", "storage", "protocol"])
def test_unsuccessful_calibration_restores_monitor_with_old_baseline(proximity, monkeypatch, outcome):
    c, settings = proximity; prepare(c); c.enabled = True
    original = c.savedCalibration
    c.startCalibration(); capture = c._process
    if outcome == "cancel":
        c.cancelCalibration()
    elif outcome == "error":
        capture.send(dict(event="error", error="戒指断连，校准未保存"))
        capture.finished.emit(2, 0)
    elif outcome == "exit":
        capture.finished.emit(0, 0)
    elif outcome == "timeout":
        c._timeout()
    elif outcome == "process_error":
        capture.errorOccurred.emit(0)
    elif outcome == "storage":
        monkeypatch.setattr(settings, "status", lambda: QSettings.AccessError)
        capture.send(dict(event="calibration_complete", device=c._target, baseline=-70,
                          samples=[-70] * 20, duration_seconds=10))
    else:
        capture.output = b"x" * 33000
        capture.readyReadStandardOutput.emit()
    assert c.enabled and not c.calibrating and c.savedCalibration == original
    assert c.calibrationStatus
    resumed = c._process
    assert resumed.args == ["monitor", c._target, "2", "15", "-64"]
    capture.send(dict(event="calibration_complete", device=c._target, baseline=-80,
                      samples=[-80] * 20, duration_seconds=10))
    capture.finished.emit(0, 0)
    assert c._process is resumed and c.savedCalibration == original


def test_finishing_process_cannot_clear_monitor_started_while_draining_invalid_result(proximity):
    c, _ = proximity; prepare(c); c.enabled = True
    c.startCalibration(); capture = c._process
    capture.output = json.dumps(dict(event="calibration_complete", device="wrong-device",
                                     baseline=-70, samples=[-70] * 20, duration_seconds=10)).encode() + b"\n"
    capture.finished.emit(0, 0)
    assert c.enabled and c._command == "monitor" and c._process is not capture
    assert c._process.args[-1] == "-64" and "无效" in c.calibrationStatus


def test_disabled_or_closed_during_calibration_cannot_restart_monitor(proximity):
    c, settings = proximity; prepare(c); c.enabled = True
    c.startCalibration(); capture = c._process
    c.enabled = False
    capture.finished.emit(0, 0)
    assert c._process is None and not settings.value("proximity/enabled", type=bool)
    c.enabled = True
    c.startCalibration(); capture = c._process
    c.close(); capture.finished.emit(0, 0)
    assert c._process is None


def test_corrupt_saved_calibration_requires_new_capture(proximity):
    c, settings = proximity
    target = "a44fbefd-c2d7-46ce-a9a2-731f1d5b2cf3"
    settings.setValue("proximity/calibrations/" + target,
                      json.dumps(dict(version=1, baseline=-20, samples=[-80] * 20, duration_seconds=10)))
    c.useDevice(target, "ring")
    assert not c.savedCalibration


def test_reconnect_request_requires_owned_locked_live_ready_helper(proximity, monkeypatch):
    c, _ = proximity; prepare(c); c.enabled = True
    requests = []
    c.reconnectRequested.connect(requests.append)
    valid = dict(event="monitor", owned=True, locked=True, connected=True, permission=True,
                 reconnect_allowed=True, return_confirmed=True)
    for override in (dict(owned=False), dict(locked=False), dict(connected=False),
                     dict(reconnect_allowed=False), dict(return_confirmed=False), dict(error="failed")):
        c._process.send({**valid, **override})
        assert not c.reconnect_target
    assert requests == []
    c._process.send(valid)
    assert requests == [c._target] and c.reconnect_target == c._target
    monkeypatch.setattr(module.time, "monotonic", lambda: c._monitor_received_at + 4)
    assert not c.reconnect_target
    c.enabled = False
    assert not c.reconnect_target


def test_return_diagnostics_record_transition_without_waiting_for_log_interval(proximity, monkeypatch):
    c, _ = proximity; prepare(c); c.enabled = True
    events = []
    c.diagnostic.connect(events.append)
    monkeypatch.setattr(module.time, "monotonic", lambda: 100.0)
    pending = dict(event="monitor", owned=True, locked=True, connected=True, permission=True,
                   state="same status", awaiting_return=True, return_confirmed=False,
                   reconnect_allowed=False, departure_samples=2, required_departure_samples=2,
                   rssi_age_seconds=0.2)
    c._process.send(pending)
    c._process.send(pending)  # Unchanged state is still throttled.
    c._process.send({**pending, "awaiting_return": False, "return_confirmed": True,
                     "reconnect_allowed": True, "departure_samples": 0})
    assert len(events) == 2
    assert events[0]["awaiting_return"] is True and events[0]["return_confirmed"] is False
    assert events[0]["departure_samples"] == 2 and events[0]["required_departure_samples"] == 2
    assert events[0]["rssi_age_seconds"] == 0.2
    assert events[1]["owned"] is True and events[1]["locked"] is True
    assert events[1]["return_confirmed"] is True and events[1]["reconnect_allowed"] is True
    assert "unlock_token" not in events[1]


def test_storage_failure_does_not_report_or_keep_new_calibration(proximity, monkeypatch):
    c, settings = proximity; prepare(c)
    original = c.savedCalibration
    key = "proximity/calibrations/" + c._target
    serialized = settings.value(key)
    monkeypatch.setattr(settings, "status", lambda: QSettings.AccessError)
    c.startCalibration()
    c._process.send(dict(event="calibration_complete", device=c._target, baseline=-80,
                        samples=[-80] * 20, duration_seconds=10))
    assert "无法保存" in c.error
    assert c.savedCalibration == original and settings.value(key) == serialized
    assert not c.calibrating


@pytest.mark.parametrize("success", [True, False])
def test_return_display_wake_is_diagnostic_only_and_keeps_lock_and_token(proximity, success):
    c, _ = proximity; prepare(c); c.enabled = True
    proc = c._process
    token = "023cb503-2538-4c12-80a2-e4ed8f214fc1"
    proc.send(dict(event="monitor", locked=True, permission=True, unlock_token=token))
    state, writes = c._gesture_state, list(proc.writes)
    events, unlocks = [], []
    c.diagnostic.connect(events.append)
    c.unlocked.connect(lambda: unlocks.append(True))
    proc.send(dict(event="return_wake", success=success, result=0 if success else -1))
    assert c._gesture_state == state and c.gestures_blocked
    assert proc.writes == writes and not unlocks
    assert events == [dict(action="return_wake", success=success, result=0 if success else -1)]


def test_return_latency_diagnostics_preserve_measured_callback_to_wake_time(proximity):
    c, _ = proximity; prepare(c); c.enabled = True
    events = []
    c.diagnostic.connect(events.append)
    c._process.send(dict(event="return_confirmed", decision_rssi=-68, threshold=-68,
                         signal_source="raw", smoothed_rssi=-75.5))
    c._process.send(dict(event="return_wake", success=True, result=0, owned=True,
                         unlock_ready=True, return_to_wake_ms=812.5))
    assert events == [dict(action="return_confirmed", decision_rssi=-68, threshold=-68,
                           signal_source="raw", smoothed_rssi=-75.5),
                      dict(action="return_wake", success=True, result=0, owned=True,
                           unlock_ready=True, return_to_wake_ms=812.5)]
