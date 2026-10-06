"""Sequential native permission requests, without an application-owned wizard."""
from __future__ import annotations

import sys

from PySide6.QtCore import (
    QObject, Property, QTimer, Qt, Signal, Slot, QBluetoothPermission, QMicrophonePermission,
)
from PySide6.QtGui import QGuiApplication
from ..mac_permissions import permission_request_scope


STEPS = ("bluetooth", "microphone", "accessibility", "screen")


class PermissionSetupController(QObject):
    ATTEMPTED_PREFIX = "onboarding/nativePermissionsV3/"
    changed = Signal()
    diagnostic = Signal(object)

    def __init__(self, permissions, settings, parent=None, *, enabled=None,
                 permission_app=None, is_active=None, request_scope=None):
        super().__init__(parent)
        self._permissions = permissions
        self._settings = settings
        self._enabled = sys.platform == "darwin" if enabled is None else enabled
        self.ATTEMPTED_KEY = self.ATTEMPTED_PREFIX + (request_scope or permission_request_scope())
        self._app = permission_app or QGuiApplication.instance()
        self._gui_app = self._app if isinstance(self._app, QGuiApplication) else None
        self._is_active = is_active or (
            lambda: self._gui_app is not None
            and self._gui_app.applicationState() == Qt.ApplicationActive
            and self._gui_app.focusWindow() is not None
        )
        self._active = False
        self._closed = False
        self._index = -1
        self._requesting = False
        self._waiting = False
        self._pending_request = False
        self._advance_pending = False
        self._left_app = False
        self._returned = False
        self._automatic = True
        self._scheduled = False
        self._generation = 0
        self._callback = None
        permissions.changed.connect(self._permissions_changed)
        permissions.systemPermissionRequestFinished.connect(self._native_finished)
        if self._gui_app is not None:
            self._gui_app.applicationStateChanged.connect(self._application_state_changed)
            self._gui_app.focusWindowChanged.connect(self._window_focus_changed)

    @Property(bool, constant=True)
    def supported(self):
        return bool(self._enabled)

    @Property(bool, notify=changed)
    def active(self):
        return self._active

    @Property(bool, notify=changed)
    def busy(self):
        return self._requesting or self._permissions.systemPermissionRequesting

    @Property(bool, notify=changed)
    def blocksFeedback(self):
        # Pending onboarding is not a visible prompt. The user may already be
        # working in another app while the sequence waits for main-window focus.
        # Only an actual in-flight/unanswered system prompt suppresses feedback.
        return not self._closed and bool(self.busy or (self._active and self._waiting
            and not self._returned and not self._permissions.permissionGranted(self.currentKind)))

    @Property(str, notify=changed)
    def currentKind(self):
        return STEPS[self._index] if 0 <= self._index < len(STEPS) else ""

    def _attempted(self):
        saved = self._settings.value(self.ATTEMPTED_KEY, [])
        return set(saved if isinstance(saved, list) else [saved]) & set(STEPS)

    def _mark_attempted(self, kind):
        self._settings.setValue(self.ATTEMPTED_KEY, sorted(self._attempted() | {kind}))
        self._settings.sync()

    @Slot()
    def startIfNeeded(self):
        self._start(automatic=True)

    @Slot()
    def open(self):
        # The compact Settings button is also a manual continuation if macOS
        # did not send an activation event after dismissing an asynchronous alert.
        if self._active:
            if self._waiting and not self.busy and self._is_active():
                self._advance_pending = True
                self._schedule()
            return
        self._start(automatic=False)

    def _start(self, *, automatic):
        if not self._enabled or self._closed or self._active:
            return
        self._generation += 1
        self._automatic = automatic
        self._active = True
        self._index = -1
        self.diagnostic.emit(dict(action="start", automatic=automatic,
                                  scope=self.ATTEMPTED_KEY.rsplit("/", 1)[-1]))
        self._permissions.refresh()
        self._permissions.refreshScreenRecording()
        self._next()

    def _next(self):
        self._waiting = self._advance_pending = False
        self._left_app = self._returned = False
        self._index += 1
        while self._index < len(STEPS):
            kind = self.currentKind
            granted = self._permissions.permissionGranted(kind)
            # Qt's OS status is authoritative for Bluetooth/microphone. A
            # historic attempt must never hide an Undetermined permission.
            attempted = self._automatic and kind in {"accessibility", "screen"} and kind in self._attempted()
            if not granted and not attempted:
                break
            self.diagnostic.emit(dict(action="skip", kind=kind,
                                      reason="granted" if granted else "already_requested_for_this_app"))
            self._index += 1
        self._pending_request = self._index < len(STEPS)
        if not self._pending_request:
            self._active = False
            self.diagnostic.emit(dict(action="complete"))
        self.changed.emit()
        self._schedule()

    def _schedule(self):
        if self._closed or not self._active or self._scheduled:
            return
        self._scheduled = True
        QTimer.singleShot(0, self._resume)

    def _resume(self):
        self._scheduled = False
        if not self._active or self._closed or self.busy:
            return
        # Settle already-granted or answered steps even in the background. An
        # asynchronous initial read must not leave active=True indefinitely.
        if self._permissions.checking:
            return
        if self._advance_pending or ((self._pending_request or self._waiting)
                and self._permissions.permissionGranted(self.currentKind)):
            self._next()
            return
        # Opening the next OS prompt still requires an active app and key window.
        if not self._is_active():
            return
        if self._waiting and self._returned:
            self._next()
        elif self._pending_request:
            self._request_current()

    def _request_current(self):
        kind = self.currentKind
        self._pending_request = False
        if self._permissions.permissionGranted(kind):
            self.diagnostic.emit(dict(action="skip", kind=kind, reason="granted"))
            self._next()
            return
        self.diagnostic.emit(dict(action="request", kind=kind))
        self._waiting = True
        try:
            if kind in {"accessibility", "screen"}:
                self._requesting = True
                self.changed.emit()
                if not self._permissions.requestSystemPermission(kind):
                    self._requesting = False
                    self._advance_pending = True
                    self.diagnostic.emit(dict(action="request_unavailable", kind=kind))
            else:
                permission = {"bluetooth": QBluetoothPermission,
                              "microphone": QMicrophonePermission}[kind]()
                status = self._app.checkPermission(permission)
                self.diagnostic.emit(dict(action="status", kind=kind, status=status.name))
                if status != Qt.PermissionStatus.Undetermined:
                    # A refusal is a completed choice, not a reason to force
                    # System Settings open. Existing yellow notices provide it.
                    self._permissions.refreshDevicePermissions()
                    self._advance_pending = True
                else:
                    self._requesting = True
                    generation = self._generation
                    self.changed.emit()

                    def completed(_permission=None):
                        if self._closed or generation != self._generation:
                            return
                        self._callback = None
                        self._requesting = False
                        self._advance_pending = True
                        self._mark_attempted(kind)
                        self.diagnostic.emit(dict(action="answered", kind=kind))
                        self._permissions.refreshDevicePermissions()
                        self.changed.emit()
                        self._schedule()

                    self._callback = completed
                    self._app.requestPermission(permission, self, completed)
        except Exception as exc:
            self._requesting = False
            self._callback = None
            self._advance_pending = True
            self.diagnostic.emit(dict(action="request_error", kind=kind, error=type(exc).__name__))
        self.changed.emit()
        self._schedule()

    @Slot(str, bool)
    def _native_finished(self, kind, success):
        if self._closed or not self._active or kind != self.currentKind or not self._requesting:
            return
        self._requesting = False
        if success:
            self._mark_attempted(kind)
        if not success:
            self._advance_pending = True
        self.diagnostic.emit(dict(action="native_request_returned", kind=kind, success=success))
        # AX prompt APIs return before the user responds. An API return or a
        # timer is not dismissal: wait for permission or return from system UI.
        self.changed.emit()
        self._schedule()

    def _permissions_changed(self):
        if self._active and not self._closed:
            self.changed.emit()
            self._schedule()

    def _application_state_changed(self, state):
        if not self._active or self._closed:
            return
        if self._waiting and state != Qt.ApplicationActive:
            self._left_app = True
        elif state == Qt.ApplicationActive:
            if self._waiting and self._left_app:
                self._returned = True
            self._schedule()

    def _window_focus_changed(self, window):
        # Some native alerts leave the application active but take key-window
        # focus. Use that return signal too, without inspecting system windows.
        if not self._active or self._closed:
            return
        if self._waiting and window is None:
            self._left_app = True
        elif window is not None:
            if self._waiting and self._left_app:
                self._returned = True
            self._schedule()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._generation += 1
        self._active = False
        self._callback = None
        self._permissions.changed.disconnect(self._permissions_changed)
        self._permissions.systemPermissionRequestFinished.disconnect(self._native_finished)
        if self._gui_app is not None:
            self._gui_app.applicationStateChanged.disconnect(self._application_state_changed)
            self._gui_app.focusWindowChanged.disconnect(self._window_focus_changed)
