"""Sequential native permission requests, without an application-owned wizard."""
from __future__ import annotations

import sys

from PySide6.QtCore import (
    QObject, Property, QTimer, Qt, Signal, Slot, QBluetoothPermission, QMicrophonePermission,
)
from PySide6.QtGui import QGuiApplication


STEPS = ("bluetooth", "microphone", "accessibility", "screen")


class PermissionSetupController(QObject):
    ATTEMPTED_KEY = "onboarding/nativePermissionsV2Attempted"
    changed = Signal()

    def __init__(self, permissions, settings, parent=None, *, enabled=None,
                 permission_app=None, is_active=None):
        super().__init__(parent)
        self._permissions = permissions
        self._settings = settings
        self._enabled = sys.platform == "darwin" if enabled is None else enabled
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
        if automatic and self._attempted() == set(STEPS):
            return
        self._generation += 1
        self._automatic = automatic
        self._active = True
        self._index = -1
        self._permissions.refresh()
        self._permissions.refreshScreenRecording()
        self._next()

    def _next(self):
        self._waiting = self._advance_pending = False
        self._left_app = self._returned = False
        self._index += 1
        while self._index < len(STEPS):
            kind = self.currentKind
            if not self._permissions.permissionGranted(kind) and not (self._automatic and kind in self._attempted()):
                break
            self._mark_attempted(kind)
            self._index += 1
        self._pending_request = self._index < len(STEPS)
        if not self._pending_request:
            self._active = False
        self.changed.emit()
        self._schedule()

    def _schedule(self):
        if self._closed or not self._active or self._scheduled:
            return
        self._scheduled = True
        QTimer.singleShot(0, self._resume)

    def _resume(self):
        self._scheduled = False
        if not self._active or self._closed or self.busy or not self._is_active():
            return
        # Wait for the initial/passive native status read before choosing a step.
        if self._permissions.checking:
            return
        if self._advance_pending or (self._waiting and (
                self._permissions.permissionGranted(self.currentKind) or self._returned)):
            self._next()
        elif self._pending_request:
            self._request_current()

    def _request_current(self):
        kind = self.currentKind
        self._pending_request = False
        if self._permissions.permissionGranted(kind):
            self._mark_attempted(kind)
            self._next()
            return
        # Record each actual attempt, not the whole sequence. Relaunching midway
        # can request the remaining permissions without repeating denied ones.
        self._mark_attempted(kind)
        self._waiting = True
        try:
            if kind in {"accessibility", "screen"}:
                self._requesting = True
                self.changed.emit()
                if not self._permissions.requestSystemPermission(kind):
                    self._requesting = False
                    self._advance_pending = True
            else:
                permission = {"bluetooth": QBluetoothPermission,
                              "microphone": QMicrophonePermission}[kind]()
                status = self._app.checkPermission(permission)
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
                        self._permissions.refreshDevicePermissions()
                        self.changed.emit()
                        self._schedule()

                    self._callback = completed
                    self._app.requestPermission(permission, self, completed)
        except Exception:
            self._requesting = False
            self._callback = None
            self._advance_pending = True
        self.changed.emit()
        self._schedule()

    @Slot(str, bool)
    def _native_finished(self, kind, success):
        if self._closed or not self._active or kind != self.currentKind or not self._requesting:
            return
        self._requesting = False
        if not success:
            self._advance_pending = True
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
