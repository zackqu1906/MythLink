"""Lock ownership, runtime recovery and gesture readiness without real BLE/locks."""
import threading
from types import SimpleNamespace

import pytest

from test_interaction_controls import _controller, _close
from test_proximity import Process, prepare


@pytest.fixture
def controller(tmp_path, monkeypatch):
    monkeypatch.setenv("PROXIMIC_STARTUP_PROBE", "1")
    from proximic_ring.ui import proximity_controller
    from proximic_ring.ui import controller as controller_module
    from PySide6.QtCore import QSettings
    monkeypatch.setattr(controller_module, "QSettings", lambda *_args:
                        QSettings(str(tmp_path / "app-settings.ini"), QSettings.IniFormat))
    helper = tmp_path / "helper"; helper.touch()
    monkeypatch.setattr(proximity_controller, "helper_path", lambda: helper)
    c = _controller(tmp_path, monkeypatch)
    c._proximity._factory = Process
    prepare(c._proximity)
    c._selector = c._proximity._target
    c._proximity.enabled = True
    yield c
    c._proximity.close()
    _close(c)


def returned(c, **overrides):
    c._proximity._process.send({**dict(event="monitor", permission=True, owned=True,
                                     locked=True, connected=True, reconnect_allowed=True, return_confirmed=True), **overrides})


def test_return_after_disconnect_starts_once_after_cleanup_and_keeps_lock(controller, monkeypatch):
    c = controller; starts = []
    c._runtime_active = True
    monkeypatch.setattr(c, "_start_selected_device", lambda: starts.append(c._selector))
    returned(c)
    assert not starts  # Runtime cleanup must finish before reconnecting.
    c._runtime_active = False
    returned(c)
    assert starts == [c._selector]
    assert c._proximity.gestures_blocked
    assert not c._recognition_event.is_set()
    returned(c)
    assert len(starts) == 1  # Backoff covers repeated monitor reports.
    c._proximity_reconnect_after = 0
    returned(c)
    assert len(starts) == 2


def test_connected_helper_waits_for_shared_return_before_gesture_recovery(controller, monkeypatch):
    c = controller; starts = []
    monkeypatch.setattr(c, "_start_selected_device", lambda: starts.append(True))
    returned(c, return_confirmed=False)
    assert not starts and not c._proximity.reconnect_target
    assert c._proximity.gestures_blocked
    returned(c)
    assert starts == [True]
    c._proximity.enabled = False
    c._reconnect_for_proximity(c._selector)
    assert starts == [True]


@pytest.mark.parametrize("blocker", ["_disconnect_requested_by_user", "_proximity_reconnect_cancelled",
                                    "_quitting", "_audio_input_failed", "_busy", "_scan_busy", "_connected"])
def test_automatic_reconnect_respects_user_and_runtime_state(controller, monkeypatch, blocker):
    c = controller; starts = []
    monkeypatch.setattr(c, "_start_selected_device", lambda: starts.append(True))
    setattr(c, blocker, True)
    returned(c)
    assert not starts


def test_different_ring_and_dead_helper_cannot_reconnect(controller, monkeypatch):
    c = controller; starts = []
    monkeypatch.setattr(c, "_start_selected_device", lambda: starts.append(True))
    c._selector = "other-ring"
    returned(c)
    assert not starts
    c._selector = c._proximity._target
    c._proximity._timeout()
    c._reconnect_for_proximity(c._selector)
    assert not starts


def test_gesture_ready_requires_current_connection_and_fresh_imu_health(controller, monkeypatch):
    from proximic_ring.ui import controller as module
    c = controller
    c._connected = c._runtime_active = True
    conn = c._disconnect_event
    assert not c.proximity_gesture_ready
    c._apply_proximity_gesture_health(threading.Event(), True)
    assert not c.proximity_gesture_ready
    c._apply_proximity_gesture_health(conn, True)
    assert c.proximity_gesture_ready
    assert c._proximity._process.writes[-1] == b"gesture-ready 1\n"  # No monitor poll needed.
    returned(c)
    assert c._proximity._process.writes[-1] == b"gesture-ready 1\n"
    c.startRecognition()
    assert not c._recognition_event.is_set()  # Locked even after the IMU recovers.
    monkeypatch.setattr(module.time, "monotonic", lambda: c._proximity_gesture_healthy_at + 9)
    assert not c.proximity_gesture_ready
    returned(c)
    assert c._proximity._process.writes[-1] == b"gesture-ready 0\n"
    conn.set()
    c._apply_proximity_gesture_health(conn, True)
    assert not c.proximity_gesture_ready


def test_display_ack_is_sent_only_after_snap_route_is_installed(controller, monkeypatch):
    from PySide6.QtCore import QCoreApplication
    c = controller
    c._connected = c._runtime_active = True
    helper = c._proximity._process
    token = "023cb503-2538-4c12-80a2-e4ed8f214fc1"
    c._apply_proximity_gesture_health(c._disconnect_event, True)
    assert not any(w.startswith(b"gesture-installed") for w in helper.writes)
    write = helper.write
    def installed_then_snap(data):
        write(data)
        if data.startswith(b"gesture-installed"):
            assert c._proximity._gesture_state == (True, token)
            # The user snaps immediately when the native side receives the ack/wakes.
            c._proximity.filter_gesture("snap", c._disconnect_event)
    monkeypatch.setattr(helper, "write", installed_then_snap)
    returned(c, unlock_token=token)
    QCoreApplication.processEvents()
    acknowledgement = ("gesture-installed " + token + "\n").encode()
    unlock = ("gesture-unlock " + token + "\n").encode()
    assert helper.writes.index(acknowledgement) < helper.writes.index(unlock)
    assert helper.writes.count(unlock) == 1
    before = len(helper.writes)
    c._apply_proximity_gesture_health(c._disconnect_event, False)
    assert helper.writes[before:] == [b"gesture-ready 0\n"]


def test_explicit_disconnect_cancels_waiting_recovery_even_without_worker(controller, monkeypatch):
    c = controller; starts = []
    monkeypatch.setattr(c, "_start_selected_device", lambda: starts.append(True))
    c.disconnectDevice()
    returned(c)
    assert not starts


def test_return_restarts_runtime_preserves_monitor_and_accepts_new_snap_once(controller, monkeypatch):
    import time
    from PySide6.QtCore import QCoreApplication
    from proximic_ring.ui import controller as module
    c = controller
    helper = c._proximity._process
    saved = c._proximity.savedCalibration
    old_connection = c._disconnect_event
    old_connection.set()
    started = threading.Event()
    class Runtime:
        def __init__(self, *args, **kwargs): pass
        def run(self, stop, recognition, **callbacks):
            assert not recognition.is_set()
            callbacks["on_connected"]()
            callbacks["on_started"]()
            callbacks["on_gesture_health"](True)
            started.set()
            stop.wait(3)
            callbacks["on_stopping"]()
            callbacks["on_disconnected"]()
    monkeypatch.setattr(module, "RecognitionRuntime", Runtime)
    worker = None
    try:
        returned(c)
        worker = c._worker
        assert worker is not None and started.wait(2)
        for _ in range(20):
            QCoreApplication.processEvents()
            if c.proximity_gesture_ready:
                break
            time.sleep(0.01)
        assert c.proximity_gesture_ready and c._proximity._process is helper
        assert c._proximity.savedCalibration == saved and c._proximity.gestures_blocked
        assert not c._recognition_event.is_set()
        token = "023cb503-2538-4c12-80a2-e4ed8f214fc1"
        returned(c, unlock_token=token)
        c._proximity.filter_gesture("snap", old_connection)
        QCoreApplication.processEvents()
        assert not any(w.startswith(b"gesture-unlock") for w in helper.writes)
        c._proximity.filter_gesture("tap", c._disconnect_event)
        c._proximity.filter_gesture("snap", c._disconnect_event)
        c._proximity.filter_gesture("snap", c._disconnect_event)
        QCoreApplication.processEvents()
        assert helper.writes.count(("gesture-unlock " + token + "\n").encode()) == 1
    finally:
        c._disconnect_event.set()
        if worker is not None:
            worker.join(3)
        QCoreApplication.processEvents()
