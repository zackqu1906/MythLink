"""Drive native protocol fixtures and headless QML; never lock the desktop."""
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QRect, QSettings, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from test_proximity import Process, prepare
from proximic_ring.ui.proximity import proximity_controller
from proximic_ring.ui.proximity.proximity_lock_overlay import ProximityLockOverlay, notice_position


@pytest.fixture(autouse=True)
def no_queued_startup_probe(monkeypatch):
    # The fixture drives status explicitly; no startup callback should outlive
    # a short protocol test and run inside a later test's Qt event loop.
    monkeypatch.setenv("PROXIMIC_STARTUP_PROBE", "1")


@pytest.fixture
def proximity(tmp_path, monkeypatch):
    helper = tmp_path / "helper"
    helper.touch()
    monkeypatch.setattr(proximity_controller, "helper_path", lambda: helper)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    controller = proximity_controller.ProximityController(settings=settings, process_factory=Process)
    yield controller, settings
    controller.close()
    # Drain Qt-owned objects while their Python owners are still alive, before
    # another test constructs a QML engine or enters the event loop.
    controller.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def report(process, seconds=5, **changes):
    process.send(dict(event="monitor", locked=False, permission=True, connected=True,
                      locking_enabled=True, lock_pending=False, lock_countdown_seconds=seconds) | changes)


def test_countdown_tracks_helper_and_return_toast_expires(proximity):
    c, _ = proximity
    prepare(c); c.enabled = True
    for seconds in range(5, 0, -1):
        report(c._process, seconds)
        assert (c.lockNoticeState, c.lockCountdownSeconds) == ("countdown", seconds)
        assert not c._lock_preparing and not c.gestures_blocked
    report(c._process, 0)
    assert c.lockNoticeState == "cancelled" and c.lockCountdownSeconds == 0
    assert c._lock_notice_timer.interval() == 1800
    c._lock_notice_timer.timeout.emit()
    assert not c.lockNoticeState
    report(c._process, 0)
    assert not c.lockNoticeState  # Regular status must not replay a toast.


@pytest.mark.parametrize("event", ["prepare_lock", "locked", "unlocked", "error", "disconnect", "disable", "exit", "timeout"])
def test_lifecycle_clears_hud_and_late_old_process_cannot_restore_it(proximity, event):
    c, _ = proximity
    prepare(c); c.enabled = True
    proc = c._process
    report(proc)
    if event == "disconnect": c.pauseForDisconnect()
    elif event == "disable": c.enabled = False
    elif event == "exit": proc.finished.emit(1, 0)
    elif event == "timeout": c._timeout()
    else: proc.send(dict(event=event, error="fixture" if event == "error" else ""))
    assert not c.lockNoticeState
    if c._process is not proc:
        report(proc)
        assert not c.lockNoticeState


@pytest.mark.parametrize("changes", [dict(permission=False), dict(connected=False), dict(locking_enabled=False),
                                     dict(locked=True), dict(error="paused"), dict(lock_pending=True),
                                     dict(lock_countdown_seconds=6), dict(lock_countdown_seconds="5")])
def test_invalid_or_unavailable_state_hides_without_claiming_a_return(proximity, changes):
    c, _ = proximity
    prepare(c); c.enabled = True
    report(c._process)
    report(c._process, **changes)
    assert not c.lockNoticeState


def test_stalled_status_and_new_departure_cannot_leave_stale_toast(proximity):
    c, _ = proximity
    prepare(c); c.enabled = True
    report(c._process); report(c._process, 0)
    assert c.lockNoticeState == "cancelled"
    report(c._process)
    assert c.lockNoticeState == "countdown" and c._lock_notice_timer.interval() == 2500
    c._lock_notice_timer.timeout.emit()
    assert not c.lockNoticeState


@pytest.mark.parametrize("area", [QRect(0, 25, 1440, 775), QRect(-1920, 25, 1920, 975), QRect(80, 25, 1360, 875)])
def test_placement_stays_above_dock_on_any_screen(area):
    for width, height in [(336, 102), (266, 86)]:
        point = notice_position(area, width, height)
        assert area.contains(QRect(point.x(), point.y(), width, height))
        assert abs(point.x() + width / 2 - (area.x() + area.width() / 2)) <= 0.5
        assert area.y() + area.height() - (point.y() + height - 24) == 24


def test_qml_countdown_cancel_and_hidden_lifecycle():
    import os
    import subprocess
    import sys
    # Isolate QML/font rendering from deferred deletion of the many short-lived
    # fake QProcesses above (PySide's offscreen engine shares global Qt state).
    subprocess.run([sys.executable, __file__], check=True, capture_output=True, text=True,
                   env={**os.environ, "PROXIMIC_STARTUP_PROBE": "1"})


def check_qml():
    import tempfile
    from pathlib import Path
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from test_proximity import Process
    from proximic_ring.ui.proximity import proximity_controller
    import proximic_ring.ui.proximity.proximity_lock_overlay as module
    app = QApplication.instance() or QApplication([])
    temp = tempfile.TemporaryDirectory()
    helper = Path(temp.name) / "helper"
    helper.touch()
    proximity_controller.helper_path = lambda: helper
    module.foreground_window_bounds = lambda: None
    c = proximity_controller.ProximityController(
        settings=QSettings(str(Path(temp.name) / "settings.ini"), QSettings.IniFormat), process_factory=Process)
    prepare(c); c.enabled = True
    overlay = ProximityLockOverlay(c)
    warnings = []
    overlay.window.engine().warnings.connect(lambda values: warnings.extend(map(str, values)))
    try:
        assert not overlay.window.isVisible()
        assert overlay.window.flags() & Qt.WindowTransparentForInput
        assert overlay.window.flags() & Qt.WindowDoesNotAcceptFocus
        for seconds in [5, 4, 3, 2, 1]:
            report(c._process, seconds)
            QGuiApplication.processEvents()
            assert overlay.window.isVisible() and overlay.window.size().width() == 336
            assert overlay.window.rootObject().property("seconds") == seconds
            QTest.qWait(25)
            rendered = overlay.window.grabWindow()
            assert not rendered.isNull()
            assert rendered.pixelColor(rendered.width() // 2, rendered.height() // 2).alpha() > 128
        report(c._process, 0)
        QGuiApplication.processEvents()
        assert overlay.window.width() == 266 and overlay.window.height() == 86
        assert overlay.window.rootObject().property("cancelled")
        c._lock_notice_timer.start(10)
        QTest.qWait(30)
        assert not overlay.window.isVisible()
        report(c._process)
        assert overlay.window.isVisible()
        c.enabled = False
        assert not overlay.window.isVisible()
        assert not warnings
    finally:
        overlay.close()
        c.close()
        app.processEvents()
        temp.cleanup()


if __name__ == "__main__":
    check_qml()
