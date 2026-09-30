"""Native first-launch sequence without changing this machine's TCC grants."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QCoreApplication, QSettings, Signal, Qt, QBluetoothPermission
from PySide6.QtTest import QTest

from proximic_ring.ui.permission_setup_controller import PermissionSetupController, STEPS


class Permissions(QObject):
    changed = Signal()
    systemPermissionRequestFinished = Signal(str, bool)

    def __init__(self):
        super().__init__()
        self.states = {kind: False for kind in STEPS}
        self.requests = []
        self.systemPermissionRequesting = False
        self.checking = False

    def permissionGranted(self, kind):
        return self.states.get(kind, False)

    def refresh(self):
        self.changed.emit()

    refreshScreenRecording = refresh
    refreshDevicePermissions = refresh

    def requestSystemPermission(self, kind):
        assert not self.systemPermissionRequesting
        self.requests.append(kind)
        self.systemPermissionRequesting = True
        self.changed.emit()
        return True

    def complete_native(self, kind, *, granted=False, success=True):
        self.states[kind] = granted
        self.systemPermissionRequesting = False
        self.changed.emit()
        self.systemPermissionRequestFinished.emit(kind, success)

    def grant(self, kind):
        self.states[kind] = True
        self.changed.emit()


class PermissionApp:
    def __init__(self, permissions):
        self.permissions = permissions
        self.status = {kind: Qt.PermissionStatus.Undetermined for kind in ("bluetooth", "microphone")}
        self.callback = None
        self.inflight = None

    def checkPermission(self, permission):
        return self.status["bluetooth" if isinstance(permission, QBluetoothPermission) else "microphone"]

    def requestPermission(self, permission, context, callback):
        assert self.callback is None, "must never overlap system prompts"
        self.inflight = "bluetooth" if isinstance(permission, QBluetoothPermission) else "microphone"
        self.permissions.requests.append(self.inflight)
        self.callback = callback

    def complete(self, granted):
        self.status[self.inflight] = Qt.PermissionStatus.Granted if granted else Qt.PermissionStatus.Denied
        self.permissions.states[self.inflight] = granted
        callback, self.callback = self.callback, None
        callback(SimpleNamespace(status=lambda: self.status[self.inflight]))


@pytest.fixture
def flow(tmp_path):
    app = QCoreApplication.instance() or QCoreApplication([])
    permissions = Permissions()
    permission_app = PermissionApp(permissions)
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    foreground = [True]
    setup = PermissionSetupController(permissions, settings, enabled=True,
                                      permission_app=permission_app, is_active=lambda: foreground[0])
    yield setup, permissions, permission_app, settings, foreground
    setup.close()
    app.processEvents()


def settle():
    QTest.qWait(20)


def return_to_app(setup, foreground):
    foreground[0] = False
    setup._application_state_changed(Qt.ApplicationInactive)
    settle()
    foreground[0] = True
    setup._application_state_changed(Qt.ApplicationActive)
    settle()


def test_first_launch_requests_directly_without_intro_or_begin(flow):
    setup, permissions, _, settings, _ = flow
    settings.setValue("onboarding/permissionsV1Shown", True)  # Old wizard does not suppress the new flow.
    setup.startIfNeeded()
    settle()
    assert setup.active and setup.currentKind == "bluetooth" and setup.busy
    assert permissions.requests == ["bluetooth"]
    assert not settings.contains(setup.ATTEMPTED_KEY)
    setup.startIfNeeded()
    setup.open()
    settle()
    assert permissions.requests == ["bluetooth"]


def test_grants_proceed_in_order_never_stack_and_finish_without_ui(flow):
    setup, permissions, native, _, _ = flow
    setup.startIfNeeded()
    settle()
    native.complete(True)
    settle()
    assert permissions.requests == ["bluetooth", "microphone"]
    native.complete(True)
    settle()
    assert permissions.requests == ["bluetooth", "microphone", "accessibility"]
    permissions.grant("accessibility")
    settle()
    assert setup.busy and setup.currentKind == "accessibility"
    permissions.complete_native("accessibility", granted=True)
    settle()
    assert permissions.requests == list(STEPS)
    permissions.complete_native("screen", granted=True)
    settle()
    assert not setup.active and not setup.busy
    setup.startIfNeeded()
    setup.open()  # All granted: no requests or completion dialog.
    settle()
    assert not setup.active and permissions.requests == list(STEPS)


def test_denial_is_a_completed_choice_and_next_permission_still_requests(flow):
    setup, permissions, native, _, _ = flow
    setup.startIfNeeded()
    settle()
    native.complete(False)
    settle()
    assert permissions.requests == ["bluetooth", "microphone"]
    native.complete(False)
    settle()
    assert permissions.requests[-1] == "accessibility"
    assert not permissions.states["bluetooth"] and not permissions.states["microphone"]


def test_already_denied_qt_permissions_do_not_reprompt_or_force_settings(flow):
    setup, permissions, native, _, _ = flow
    native.status = {kind: Qt.PermissionStatus.Denied for kind in native.status}
    permissions.grant("accessibility")
    permissions.grant("screen")
    setup.startIfNeeded()
    settle()
    assert not setup.active and permissions.requests == []
    assert setup._attempted() == set()


def test_async_native_api_return_is_not_dismissal_or_grant(flow):
    setup, permissions, _, _, foreground = flow
    permissions.grant("bluetooth")
    permissions.grant("microphone")
    setup.startIfNeeded()
    settle()
    permissions.complete_native("accessibility")
    for _ in range(5):
        permissions.changed.emit()
        settle()
    assert permissions.requests == ["accessibility"] and setup.active
    # macOS's asynchronous AX prompt has no dismissal callback. Wait until the
    # user returns from its alert/settings, even when permission was declined.
    return_to_app(setup, foreground)
    assert permissions.requests == ["accessibility", "screen"]
    permissions.complete_native("screen")
    return_to_app(setup, foreground)
    assert not setup.active and not permissions.states["screen"]


def test_return_before_native_api_finishes_still_cannot_stack(flow):
    setup, permissions, _, _, foreground = flow
    permissions.grant("bluetooth")
    permissions.grant("microphone")
    setup.open()
    settle()
    return_to_app(setup, foreground)
    assert permissions.requests == ["accessibility"]
    permissions.complete_native("accessibility")
    settle()
    assert permissions.requests == ["accessibility", "screen"]


def test_wait_for_activation_and_initial_status_check_before_request(flow):
    setup, permissions, _, settings, foreground = flow
    foreground[0] = False
    setup.startIfNeeded()
    settle()
    assert not settings.contains(setup.ATTEMPTED_KEY) and not permissions.requests
    permissions.checking = True
    foreground[0] = True
    setup._application_state_changed(Qt.ApplicationActive)
    settle()
    assert not permissions.requests
    permissions.checking = False
    permissions.grant("bluetooth")
    settle()
    assert permissions.requests == ["microphone"]


def test_grant_in_settings_waits_for_return_before_next_prompt(flow):
    setup, permissions, _, _, foreground = flow
    permissions.grant("bluetooth")
    permissions.grant("microphone")
    setup.open()
    settle()
    foreground[0] = False
    setup._application_state_changed(Qt.ApplicationInactive)
    permissions.complete_native("accessibility", granted=True)
    settle()
    assert permissions.requests == ["accessibility"]
    foreground[0] = True
    setup._application_state_changed(Qt.ApplicationActive)
    settle()
    assert permissions.requests == ["accessibility", "screen"]


def test_relaunch_uses_os_response_and_ignores_old_callback(flow):
    setup, permissions, native, settings, _ = flow
    setup.startIfNeeded()
    settle()
    setup.close()
    next_native = PermissionApp(permissions)
    next_native.status["bluetooth"] = Qt.PermissionStatus.Denied
    other = PermissionSetupController(permissions, settings, enabled=True,
                                      permission_app=next_native, is_active=lambda: True)
    try:
        other.startIfNeeded()
        settle()
        assert permissions.requests == ["bluetooth", "microphone"]
        native.complete(True)
        settle()
        assert permissions.requests == ["bluetooth", "microphone"]
    finally:
        other.close()


def test_completed_requests_do_not_nag_but_manual_native_retry_is_allowed(flow):
    setup, permissions, native, settings, _ = flow
    settings.setValue(setup.ATTEMPTED_KEY, list(STEPS))
    native.status = {kind: Qt.PermissionStatus.Denied for kind in native.status}
    setup.startIfNeeded()
    settle()
    assert not setup.active and not permissions.requests
    setup.open()
    settle()
    assert permissions.requests == ["accessibility"]


def test_manual_continuation_handles_missing_os_activation_event(flow):
    setup, permissions, _, _, _ = flow
    permissions.grant("bluetooth")
    permissions.grant("microphone")
    setup.open()
    settle()
    setup.open()  # Pending native call: cannot overlap.
    assert permissions.requests == ["accessibility"]
    permissions.complete_native("accessibility")
    settle()
    setup.open()  # Explicit click after returning to the Settings page.
    settle()
    assert permissions.requests == ["accessibility", "screen"]


def test_errors_continue_without_settings_popups_or_false_grants(flow, monkeypatch):
    setup, permissions, native, _, _ = flow
    monkeypatch.setattr(native, "requestPermission", lambda *args: (_ for _ in ()).throw(RuntimeError("unavailable")))
    setup.open()
    settle()
    assert permissions.requests == ["accessibility"]
    permissions.complete_native("accessibility", success=False)
    settle()
    assert permissions.requests == ["accessibility", "screen"]
    permissions.complete_native("screen", success=False)
    settle()
    assert not setup.active and not any(permissions.states.values())


def test_all_granted_and_unsupported_platform_do_not_request(flow):
    setup, permissions, _, settings, _ = flow
    permissions.states = {kind: True for kind in STEPS}
    setup.startIfNeeded()
    settle()
    assert not setup.active and not permissions.requests
    settings.remove(setup.ATTEMPTED_KEY)
    setup._enabled = False
    setup.startIfNeeded()
    setup.open()
    assert not setup.active and not settings.contains(setup.ATTEMPTED_KEY)


def test_close_ignores_late_callback_and_is_idempotent(flow):
    setup, permissions, native, _, _ = flow
    setup.open()
    settle()
    setup.close()
    setup.close()
    native.complete(True)
    settle()
    assert not setup.active and permissions.requests == ["bluetooth"]


def test_native_alert_key_window_return_without_app_activation(flow):
    setup, permissions, _, _, _ = flow
    permissions.grant("bluetooth")
    permissions.grant("microphone")
    setup.open()
    settle()
    permissions.complete_native("accessibility")
    settle()
    assert permissions.requests == ["accessibility"]
    setup._window_focus_changed(None)
    settle()
    assert permissions.requests == ["accessibility"]
    setup._window_focus_changed(object())
    settle()
    assert permissions.requests == ["accessibility", "screen"]


def test_old_shared_source_marker_cannot_suppress_installed_permissions(flow):
    setup, permissions, _, settings, _ = flow
    settings.setValue("onboarding/nativePermissionsV2Attempted", list(STEPS))
    settings.setValue("onboarding/permissionsV1Shown", True)
    setup.startIfNeeded()
    settle()
    assert permissions.requests == ["bluetooth"]


def test_past_grant_is_not_a_permanent_skip_after_permission_is_missing(flow):
    setup, permissions, _, settings, _ = flow
    permissions.states = dict.fromkeys(STEPS, True)
    setup.startIfNeeded()
    settle()
    assert not settings.contains(setup.ATTEMPTED_KEY)
    permissions.states["accessibility"] = False
    setup.startIfNeeded()
    settle()
    assert permissions.requests == ["accessibility"]


def test_undetermined_os_permission_overrides_any_historic_attempt_record(flow):
    setup, permissions, _, settings, _ = flow
    settings.setValue(setup.ATTEMPTED_KEY, list(STEPS))
    setup.startIfNeeded()
    settle()
    assert permissions.requests == ["bluetooth"]


def test_request_error_is_not_persisted_as_a_completed_request(flow, monkeypatch):
    setup, permissions, native, settings, _ = flow
    monkeypatch.setattr(native, "requestPermission", lambda *args: (_ for _ in ()).throw(RuntimeError()))
    setup.startIfNeeded()
    settle()
    permissions.complete_native("accessibility", success=False)
    settle()
    permissions.complete_native("screen", success=False)
    settle()
    assert not setup.active and not settings.contains(setup.ATTEMPTED_KEY)


def test_source_and_installed_scopes_do_not_share_request_markers(flow):
    setup, permissions, native, settings, _ = flow
    settings.setValue(PermissionSetupController.ATTEMPTED_PREFIX + "source", list(STEPS))
    native.status = dict.fromkeys(native.status, Qt.PermissionStatus.Denied)
    installed = PermissionSetupController(permissions, settings, enabled=True,
        permission_app=native, is_active=lambda: True, request_scope="installed")
    try:
        installed.startIfNeeded()
        settle()
        assert permissions.requests == ["accessibility"]
    finally:
        installed.close()
