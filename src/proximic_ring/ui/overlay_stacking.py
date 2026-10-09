"""Shared macOS floating-window policy. Never activates the process or takes focus."""
from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QGuiApplication


def content_window(windows, pid):
    """Frontmost substantial content window, including elevated fullscreen canvases.

    WindowServer metadata only. Small toolbar windows and transparent helpers
    must not select the wrong monitor for a slideshow.
    """
    candidates = [w for w in windows if w.get("kCGWindowOwnerPID") == pid
                  and w.get("kCGWindowAlpha", 1) > 0
                  and w.get("kCGWindowLayer", 0) >= 0]
    for window in candidates:
        bounds = window.get("kCGWindowBounds", {})
        if bounds.get("Width", 0) >= 180 and bounds.get("Height", 0) >= 120:
            return window
    return None


def foreground_content_window():
    import Quartz
    from ..mac_workspace import frontmost_application
    front = frontmost_application()
    if front is None:
        return None
    windows = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID) or []
    return content_window(windows, int(front.processIdentifier()))


def configure_native_overlay(native, appkit, *, content_level=0, level_offset=0):
    def flag(name):
        return int(getattr(appkit, "NSWindowCollectionBehavior" + name, 0))
    # FullScreenNone/Primary and Stage Manager Primary/Auxiliary conflict with
    # joining another application's fullscreen space. Do not set them together.
    conflicts = ("MoveToActiveSpace", "Managed", "Stationary", "ParticipatesInCycle",
                 "FullScreenPrimary", "FullScreenNone", "FullScreenAllowsTiling",
                 "Primary", "Auxiliary")
    behavior = int(native.collectionBehavior())
    for name in conflicts:
        behavior &= ~flag(name)
    for name in ("CanJoinAllSpaces", "FullScreenAuxiliary", "Transient", "IgnoresCycle",
                 "FullScreenDisallowsTiling", "CanJoinAllApplications"):
        behavior |= flag(name)
    # NSPanel must be nonactivating to join another app's full-screen Space.
    # Qt's DoesNotAcceptFocus prevents key focus but does not set this style bit.
    native.setStyleMask_(int(native.styleMask()) | int(getattr(appkit, "NSWindowStyleMaskNonactivatingPanel", 128)))
    native.setCollectionBehavior_(behavior)
    # Stay above presentation/video canvases, below OS screensavers/secure UI.
    level = min(int(getattr(appkit, "NSScreenSaverWindowLevel", 1000)) - 1,
                max(int(getattr(appkit, "NSPopUpMenuWindowLevel", 101)) + 1,
                    int(content_level) + 1) + level_offset)
    native.setLevel_(level)
    native.setHidesOnDeactivate_(False)
    native.orderFrontRegardless()


class OverlayVisibilityGuard(QObject):
    """Reassert native ordering during Space/canvas transitions, only while shown.

    Owns no display lifetime: hide/close stops it, and it never calls show().
    The normal HUD/toast timers remain the only owners of their duration.
    """
    def __init__(self, window, parent=None, *, diagnostic=None, level_offset=0):
        super().__init__(parent)
        self.window = window
        self._level_offset = level_offset
        self._diagnostic = diagnostic or (lambda _exc: None)
        self._last_error = None
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.refresh)
        window.visibleChanged.connect(self._visibility_changed)

    def _visibility_changed(self, visible):
        if visible and QGuiApplication.platformName() == "cocoa":
            self._timer.start()
        else:
            self._timer.stop()
            self._last_error = None

    def refresh(self):
        if not self.window.isVisible() or QGuiApplication.platformName() != "cocoa":
            self._timer.stop()
            return
        try:
            from .notifications import _show_on_macos_spaces
            if self._level_offset:
                _show_on_macos_spaces(self.window, level_offset=self._level_offset)
            else:
                _show_on_macos_spaces(self.window)
            self._last_error = None
        except Exception as exc:
            signature = (type(exc).__name__, str(exc))
            if signature != self._last_error:
                self._diagnostic(exc)
                self._last_error = signature
