import sys
from types import SimpleNamespace

import pytest

from proximic_ring.mac_permissions import (MacPermissionError, PermissionState,
    read_permission_state, require_post_event_access, running_identity)


@pytest.mark.parametrize("ax,post", [(False, False), (True, False), (False, True), (True, True)])
def test_actual_process_permissions_are_distinguished_without_prompting(monkeypatch, ax, post):
    monkeypatch.setitem(sys.modules, "ApplicationServices", SimpleNamespace(AXIsProcessTrusted=lambda: ax))
    monkeypatch.setitem(sys.modules, "Quartz", SimpleNamespace(CGPreflightPostEventAccess=lambda: post))
    state = read_permission_state()
    assert state.accessibility is ax and state.post_events is post
    assert state.ready is (ax and post)
    if post:
        require_post_event_access()
    else:
        with pytest.raises(MacPermissionError, match="自动重新检测") as error:
            require_post_event_access()
        assert error.value.state == state
    if ax and not post:
        assert "按键控制" in state.title


def test_api_failure_is_not_mislabeled_as_user_denial(monkeypatch):
    monkeypatch.setitem(sys.modules, "ApplicationServices", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "Quartz", SimpleNamespace())
    state = read_permission_state()
    assert state.accessibility is None and state.post_events is None
    assert not state.ready and "无法确认" in state.title and state.error


def test_permission_changes_are_not_cached_by_key_delivery(monkeypatch):
    calls = iter([False, True, False])
    monkeypatch.setitem(sys.modules, "ApplicationServices", SimpleNamespace(AXIsProcessTrusted=lambda: True))
    monkeypatch.setitem(sys.modules, "Quartz", SimpleNamespace(CGPreflightPostEventAccess=lambda: next(calls)))
    with pytest.raises(MacPermissionError):
        require_post_event_access()
    require_post_event_access()
    with pytest.raises(MacPermissionError):
        require_post_event_access()


@pytest.mark.parametrize("path,temporary", [
    ("/Applications/Proximic Voice.app/Contents/MacOS/ProximicVoice", False),
    ("/Volumes/Proximic Voice/Proximic Voice.app/Contents/MacOS/ProximicVoice", True),
    ("/private/tmp/AppTranslocation/ABC/d/Proximic Voice.app/Contents/MacOS/ProximicVoice", True),
])
def test_diagnostics_identify_the_running_app_not_an_installed_namesake(monkeypatch, path, temporary):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", path)
    identity = running_identity()
    assert identity["app_path"] == path.split("/Contents/")[0]
    assert identity["temporary_location"] is temporary


def test_source_run_does_not_claim_the_packaged_app_is_responsible(monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert running_identity()["app_path"] == ""


def test_monitor_updates_and_rejects_older_success_after_key_failure(monkeypatch):
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtTest import QTest
    from proximic_ring.ui.mac_permissions_controller import MacPermissionsController
    monkeypatch.setattr(sys, "platform", "darwin")
    app = QCoreApplication.instance() or QCoreApplication([])
    values = iter([PermissionState(False, False), PermissionState(True, True)])
    monitor = MacPermissionsController(reader=lambda: next(values))
    events = []
    monitor.diagnostic.connect(events.append)
    def wait_checked():
        for _ in range(100):
            app.processEvents()
            if monitor._checked and not monitor.checking:
                return
            QTest.qWait(5)
        pytest.fail("permission monitor did not complete")
    wait_checked()
    assert monitor.warning and "自动检测" in monitor.instructions
    monitor.refresh()
    wait_checked()
    assert not monitor.warning
    old_generation = monitor._generation
    monitor.report_error(MacPermissionError(PermissionState(True, False)))
    monitor._apply(old_generation, PermissionState(True, True))
    assert monitor.warning and "按键控制" in monitor.title
    assert events[-1]["post_events"] is False
    monitor.close()
    monitor._apply(monitor._generation, PermissionState(True, True))
    assert monitor.warning


def test_background_timer_observes_grant_and_revocation_without_activation(monkeypatch):
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtTest import QTest
    from proximic_ring.ui import mac_permissions_controller as module
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(module.MacPermissionsController, "WAITING_INTERVAL_MS", 20)
    monkeypatch.setattr(module.MacPermissionsController, "READY_INTERVAL_MS", 60)
    monkeypatch.setattr(module, "request_post_event_access", lambda: pytest.fail("passive checks must not prompt"))
    app = QCoreApplication.instance() or QCoreApplication([])
    current = [PermissionState(False, False)]
    monitor = module.MacPermissionsController(reader=lambda: current[0])
    events = []
    monitor.diagnostic.connect(events.append)

    def wait_for(predicate):
        for _ in range(200):
            app.processEvents()
            if predicate():
                return
            QTest.qWait(5)
        pytest.fail("background permission state did not update")

    try:
        wait_for(lambda: monitor.warning)
        assert monitor._timer.isActive()
        assert monitor._timer.interval() == 20
        # No refresh(), activation event, or controller recreation after grant.
        current[0] = PermissionState(True, True)
        wait_for(lambda: not monitor.warning)
        assert monitor._timer.interval() == 60
        assert "可以继续使用" in monitor.actionMessage
        current[0] = PermissionState(True, False)
        wait_for(lambda: monitor.warning)
        assert monitor._timer.interval() == 20
        assert "权限已失效" in monitor.actionMessage
        assert [event["post_events"] for event in events] == [False, True, False]
    finally:
        monitor.close()
    assert not monitor._timer.isActive()


def test_slow_permission_check_does_not_spawn_overlapping_checks(monkeypatch):
    import threading
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtTest import QTest
    from proximic_ring.ui.mac_permissions_controller import MacPermissionsController
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(MacPermissionsController, "WAITING_INTERVAL_MS", 10)
    app = QCoreApplication.instance() or QCoreApplication([])
    release = threading.Event()
    calls = []
    def reader():
        calls.append(True)
        release.wait(2)
        return PermissionState(True, True)
    monitor = MacPermissionsController(reader=reader)
    try:
        for _ in range(15):
            app.processEvents()
            QTest.qWait(5)
        assert calls == [True] and monitor.checking
    finally:
        monitor.close()
        release.set()


def test_disabled_monitor_never_requests_macos_permissions():
    from PySide6.QtCore import QCoreApplication
    from proximic_ring.ui.mac_permissions_controller import MacPermissionsController
    app = QCoreApplication.instance() or QCoreApplication([])
    monitor = MacPermissionsController(enabled=False, reader=lambda: pytest.fail("no macOS check"))
    monitor.refresh()
    monitor.openSettings()
    app.processEvents()
    assert not monitor.warning
    monitor.close()


def test_device_permission_notices_are_passive_and_unknown_is_not_denied(monkeypatch):
    from PySide6.QtCore import QCoreApplication, Qt
    from proximic_ring.ui.mac_permissions_controller import MacPermissionsController
    app = QCoreApplication.instance() or QCoreApplication([])
    status = Qt.PermissionStatus.Denied
    monkeypatch.setattr(app, "checkPermission", lambda permission: status)
    monitor = MacPermissionsController(enabled=False)
    monitor._enabled = True
    try:
        monitor._refresh_device_permissions()
        assert {n['kind'] for n in monitor.essentialWarnings} == {'bluetooth', 'microphone'}
        for status in (Qt.PermissionStatus.Granted, Qt.PermissionStatus.Undetermined):
            monitor._refresh_device_permissions()
            assert monitor.essentialWarnings == []
    finally:
        monitor._enabled = False
        monitor.close()


@pytest.mark.parametrize("trusted,post", [(False, False), (True, False), (True, True)])
def test_accessibility_request_uses_native_prompt_and_skips_granted(monkeypatch, trusted, post):
    from proximic_ring.mac_permissions import request_accessibility_access
    calls = []
    monkeypatch.setitem(sys.modules, "ApplicationServices", SimpleNamespace(
        AXIsProcessTrusted=lambda: trusted, kAXTrustedCheckOptionPrompt="prompt",
        AXIsProcessTrustedWithOptions=lambda options: calls.append(("ax", options)) or False))
    monkeypatch.setitem(sys.modules, "Quartz", SimpleNamespace(
        CGPreflightPostEventAccess=lambda: post,
        CGRequestPostEventAccess=lambda: calls.append(("post",)) or False))
    assert request_accessibility_access() is (trusted and post)
    assert calls == ([] if trusted and post else [("post",)] if trusted else [("ax", {"prompt": True})])


@pytest.mark.parametrize("kind,raises", [("accessibility", False), ("screen", False), ("screen", True)])
def test_native_request_is_separate_from_opening_settings_and_never_grants_from_api_return(monkeypatch, kind, raises):
    import threading
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtTest import QTest
    from proximic_ring.ui import mac_permissions_controller as module
    app = QCoreApplication.instance() or QCoreApplication([])
    release = threading.Event()
    calls, finished = [], []
    def request():
        calls.append(kind)
        release.wait(2)
        if raises:
            raise RuntimeError("unavailable")
        return False  # Asynchronous system request: not a denial callback.
    monkeypatch.setattr(module, "request_accessibility_access", request)
    monkeypatch.setattr(module, "request_screen_capture_access", request)
    monkeypatch.setattr(module, "read_screen_capture_access", lambda: False)
    monkeypatch.setattr(module.QDesktopServices, "openUrl", lambda url: pytest.fail("native onboarding must not force System Settings open"))
    monitor = module.MacPermissionsController(enabled=False, reader=lambda: PermissionState(False, False))
    monitor._enabled = True
    monitor.systemPermissionRequestFinished.connect(lambda *args: finished.append(args))
    try:
        assert monitor.requestSystemPermission(kind)
        assert monitor.systemPermissionRequesting
        assert not monitor.requestSystemPermission("accessibility")
        monitor.openSettings()
        monitor.openScreenRecordingSettings()
        monitor.verifyScreenPreview()
        assert not monitor.screenPreviewBusy
        release.set()
        for _ in range(100):
            QTest.qWait(5)
            if finished and not monitor.checking:
                break
        assert finished == [(kind, not raises)] and calls == [kind]
        assert not monitor.systemPermissionRequesting and not monitor.permissionGranted(kind)
    finally:
        release.set()
        monitor._enabled = False
        monitor.close()


def test_closed_monitor_discards_native_request_completion(monkeypatch):
    import threading
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtTest import QTest
    from proximic_ring.ui import mac_permissions_controller as module
    app = QCoreApplication.instance() or QCoreApplication([])
    release = threading.Event()
    finished = []
    monkeypatch.setattr(module, "request_accessibility_access", lambda: release.wait(2))
    monitor = module.MacPermissionsController(enabled=False)
    assert not monitor.requestSystemPermission("accessibility")
    monitor._enabled = True
    monitor.systemPermissionRequestFinished.connect(lambda *args: finished.append(args))
    try:
        assert monitor.requestSystemPermission("accessibility")
        monitor._enabled = False
        monitor.close()
        release.set()
        QTest.qWait(30)
        assert not finished
        assert not monitor.requestSystemPermission("screen")
    finally:
        release.set()
