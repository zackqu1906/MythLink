import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtTest import QTest

from proximic_ring import screen_preview_check as check
from proximic_ring.mac_permissions import PermissionState
from proximic_ring.ui import mac_permissions_controller as permissions


class Capture:
    def __init__(self, mode):
        self.mode, self.started, self.stopped, self.closed = mode, [], False, False
    def permission(self): return self.mode != "permission"
    def start(self, cards, frame, status):
        self.started = cards
        if self.mode == "verified": frame(cards[0]["id"], SimpleNamespace(isNull=lambda: False))
        elif self.mode == "null": frame(cards[0]["id"], SimpleNamespace(isNull=lambda: True))
        elif self.mode == "unavailable": status("unavailable")
    def stop(self): self.stopped = True
    def close(self): self.stop(); self.closed = True


@pytest.mark.parametrize("mode,expected", [("verified","verified"), ("permission","permission"),
                                          ("null","timeout"), ("unavailable","unavailable")])
def test_setup_requires_an_actual_frame_and_always_closes_capture(mode, expected):
    engine = Capture(mode)
    targets = []
    def target():
        targets.append(True)
        return dict(id="test", number=41, pid=12, frame=(0,0,500,400))
    result = check.verify_screen_preview(threading.Event(), factory=lambda: engine,
                                         target_reader=target, timeout=.01)
    assert result == expected and engine.stopped and engine.closed
    assert len(engine.started) == (0 if mode == "permission" else 1)
    if mode == "permission": assert not targets


def test_cancelled_setup_does_not_start_capture():
    cancel = threading.Event(); cancel.set()
    assert check.verify_screen_preview(cancel, factory=lambda: pytest.fail("must not start")) == "cancelled"


def test_cancelling_a_waiting_capture_closes_the_stream():
    cancel = threading.Event()
    engine = Capture("waiting")
    engine.start = lambda *args: cancel.set()
    result = check.verify_screen_preview(cancel, factory=lambda:engine, target_reader=lambda:{"id":"fixture"})
    assert result == "cancelled" and engine.closed


def test_setup_target_excludes_own_app_and_other_screens(monkeypatch):
    own = dict(pid=check.os.getpid(), number=1, frame=(0,0,600,400))
    other_screen = dict(pid=12, number=2, frame=(1500,100,500,400))
    current = dict(pid=13, number=3, frame=(20,20,500,400))
    system = dict(pid=14, number=4, frame=(20,20,500,400))
    screens = [dict(id=1,frame=(0,0,1200,800)),dict(id=2,frame=(1200,0,1200,800))]
    monkeypatch.setattr(check, "MacWindowAX", lambda: SimpleNamespace(
        on_screen=lambda:[own,system,other_screen,current], apps=lambda:{12:object(),13:object()},
        screens=lambda:screens))
    assert check.preview_check_target()["number"] == 3


def wait_for(predicate):
    for _ in range(150):
        QTest.qWait(10)
        if predicate(): return
    assert predicate()


def test_preflight_is_not_full_success_and_only_explicit_setup_checks_capture(monkeypatch):
    app = QCoreApplication.instance() or QCoreApplication([])
    monkeypatch.setattr(permissions.sys, "platform", "darwin")
    monkeypatch.setattr(permissions, "read_screen_capture_access", lambda: True)
    requests, captures = [], []
    monkeypatch.setattr(permissions, "request_screen_capture_access", lambda: requests.append(True) or True)
    monkeypatch.setattr(permissions.QDesktopServices, "openUrl", lambda url: True)
    monkeypatch.setattr(check, "verify_screen_preview", lambda cancel: captures.append(True) or "verified")
    monitor = permissions.MacPermissionsController(reader=lambda: PermissionState(True,True))
    try:
        monitor.refreshScreenRecording()
        monitor._on_application_state(Qt.ApplicationActive)
        monitor.cancelScreenPreview()
        assert not captures and not requests and not monitor._screen_preview_verified
        assert "继续完成" in monitor.screenRecordingMessage
        monitor.openScreenRecordingSettings()
        wait_for(lambda: not monitor.screenRecordingRequesting)
        assert requests == [True] and not captures
        monitor._on_application_state(Qt.ApplicationActive)
        wait_for(lambda: not monitor.screenPreviewBusy)
        assert captures == [True] and monitor._screen_preview_verified
        monitor._on_application_state(Qt.ApplicationActive)
        assert captures == [True]  # Returning focus never continuously captures.
    finally:
        monitor.close()


def test_cancel_or_close_discards_a_late_success(monkeypatch):
    app = QCoreApplication.instance() or QCoreApplication([])
    monkeypatch.setattr(permissions.sys, "platform", "darwin")
    monkeypatch.setattr(permissions, "read_screen_capture_access", lambda: True)
    started, release = threading.Event(), threading.Event()
    def verify(cancel):
        started.set(); release.wait(2)
        return "verified"
    monkeypatch.setattr(check, "verify_screen_preview", verify)
    monitor = permissions.MacPermissionsController(reader=lambda: PermissionState(True,True))
    try:
        monitor.verifyScreenPreview()
        wait_for(started.is_set)
        pending = monitor._screen_preview_cancel
        monitor.cancelScreenPreview()
        assert pending.is_set() and not monitor.screenPreviewBusy
        release.set(); QTest.qWait(50)
        assert not monitor._screen_preview_verified and "取消" in monitor.screenPreviewMessage
    finally:
        release.set(); monitor.close()
