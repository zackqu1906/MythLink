"""Window-local Cocoa focus and click-away lifetime for the recommendation Hub.

Monitors cover the visible Hub and its bounded native activation tail. They observe mouse-down events,
never swallow them, and never read other applications' accessibility contents.
"""
import time
from PySide6.QtCore import QPoint, QObject, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication


class HubFocusSession(QObject):
    """Bound a Hub's native activation to its own lifetime.

    A nonactivating panel still needs process activation for the native IME.
    That activation is asynchronous: a fast click-away can resign the panel
    BEFORE the activation arrives, causing AppKit to order the main window
    forward. Keep the previous application and normal-window order in memory.
    Focus/application notifications restore only windows that crossed an
    existing external anchor; no window is shown, unminimized or made key.

    Dismissal watches the short activation tail (750 ms), following a newly
    selected external app rather than forcing the invocation app over it.
    A deliberate click in another host window or submission discards this
    ownership immediately. No app contents or persistent history are read.
    """
    released = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.previous = None
        self.engaged = self.return_pending = False
        self.deadline = 0.
        self.window_order = []
        self.timer = QTimer(self)
        self.timer.setInterval(25)
        self.timer.timeout.connect(self._settle_release)
        self.order_timer = QTimer(self)
        self.order_timer.setSingleShot(True)
        self.order_timer.timeout.connect(self._restore_order)
        app = QGuiApplication.instance()
        app.applicationStateChanged.connect(self._queue_order_check)
        app.focusWindowChanged.connect(self._queue_order_check)

    def begin(self):
        if QGuiApplication.platformName() != 'cocoa':
            return
        import AppKit as A
        front = A.NSWorkspace.sharedWorkspace().frontmostApplication()
        current = A.NSRunningApplication.currentApplication()
        continuing = self.engaged or self.return_pending
        if front and front.processIdentifier() != current.processIdentifier():
            self.previous = front
        elif not continuing:
            self.previous = None
        if not continuing:
            self._capture_order()
        self.timer.stop()
        self.return_pending = False
        self.engaged = True
        self._queue_order_check()

    def release(self, reason='dismiss'):
        if not self.engaged:
            return
        self.engaged = False
        if reason == 'local_click':
            self.discard()
            return
        self.return_pending = True
        self.deadline = time.monotonic() + .75
        # A global mouse-down is delivered before its target finishes becoming
        # active. Allow that click to settle before deciding where focus belongs.
        if reason != 'external_click':
            self._settle_release()
        self.timer.start()

    def discard(self):
        """Explicit local selection / send owns the next focus transition."""
        self.timer.stop()
        self.order_timer.stop()
        self.engaged = self.return_pending = False
        self.previous = None
        self.window_order = []
        self.released.emit()

    def _queue_order_check(self, *_):
        if self.engaged or self.return_pending:
            self.order_timer.start(0)

    def _capture_order(self):
        import AppKit as A
        import Quartz as Q
        listed = Q.CGWindowListCopyWindowInfo(Q.kCGWindowListOptionOnScreenOnly, 0) or []
        ids = [int(w['kCGWindowNumber']) for w in listed]
        pid = A.NSRunningApplication.currentApplication().processIdentifier()
        self.window_order = []
        for native in A.NSApplication.sharedApplication().windows():
            wid = native.windowNumber()
            if native.level() != 0 or wid not in ids or native.isMiniaturized():
                continue
            ahead = [w for w in listed[:ids.index(wid)]
                     if w['kCGWindowOwnerPID'] != pid and w.get('kCGWindowLayer', 0) == 0]
            if ahead:
                self.window_order.append((native, int(ahead[-1]['kCGWindowNumber'])))

    def _restore_order(self):
        if not self.window_order or not (self.engaged or self.return_pending):
            return
        import AppKit as A
        import Quartz as Q
        ids = [int(w['kCGWindowNumber']) for w in
               Q.CGWindowListCopyWindowInfo(Q.kCGWindowListOptionOnScreenOnly, 0) or []]
        for native, anchor in self.window_order:
            wid = native.windowNumber()
            # Missing/hidden/minimized windows are intentionally left alone.
            # Do not lower the main window below a newly selected arbitrary app;
            # only undo promotion past the exact window that used to cover it.
            if (wid in ids and anchor in ids and not native.isMiniaturized()
                    and ids.index(wid) < ids.index(anchor)):
                native.orderWindow_relativeTo_(A.NSWindowBelow, anchor)

    def _settle_release(self):
        if not self.return_pending:
            return
        self._restore_order()
        import AppKit as A
        current = A.NSRunningApplication.currentApplication()
        front = A.NSWorkspace.sharedWorkspace().frontmostApplication()
        if front and front.processIdentifier() != current.processIdentifier():
            self.previous = front
        elif front and self.previous and not self.previous.isTerminated():
            self.previous.activateWithOptions_(A.NSApplicationActivateIgnoringOtherApps)
            if current.isActive():
                A.NSApplication.sharedApplication().deactivate()
        if time.monotonic() >= self.deadline:
            self.discard()


def native_window(widget):
    import ctypes
    import objc
    return objc.objc_object(c_void_p=ctypes.c_void_p(int(widget.winId()))).window()


def has_native_focus(dialog):
    if QGuiApplication.platformName() == 'cocoa':
        return bool(native_window(dialog).isKeyWindow())
    return dialog.windowHandle() is QGuiApplication.focusWindow()


def focus_panel(dialog):
    """Key this panel before activating its input owner; never raise all windows."""
    from .agent_overlay import _set_input_focus_enabled
    if QGuiApplication.platformName() == 'cocoa':
        _set_input_focus_enabled(dialog.windowHandle(), True)
        dialog.guard.refresh()
        native = native_window(dialog)
        native.setIgnoresMouseEvents_(False)
        native.setBecomesKeyOnlyIfNeeded_(False)
        native.makeKeyWindow()
        dialog.windowHandle().requestActivate()
        # Do this only at explicit Hub entry, not on every editor focus event.
        if native.isKeyWindow():
            import AppKit as A
            application = A.NSRunningApplication.currentApplication()
            front = A.NSWorkspace.sharedWorkspace().frontmostApplication()
            # With a nonactivating key panel isActive() can remain true while
            # another process is frontmost. Native IME ownership needs the PID.
            if not front or front.processIdentifier() != application.processIdentifier():
                application.activateWithOptions_(A.NSApplicationActivateIgnoringOtherApps)
    else:
        dialog.raise_(); dialog.activateWindow()


def owned_popup(dialog):
    popup = QApplication.activePopupWidget()
    parent = popup.parentWidget() if popup else None
    if parent and parent.window() is dialog:
        return popup
    window = popup.windowHandle() if popup else None
    while window:
        window = window.transientParent()
        if window is dialog.windowHandle():
            return popup
    return None


class HubClickAway:
    def __init__(self, dialog):
        self.dialog = dialog
        self.monitors = []

    def start(self):
        if self.monitors or QGuiApplication.platformName() != 'cocoa':
            return
        import AppKit as A
        mask = A.NSEventMaskLeftMouseDown | A.NSEventMaskRightMouseDown | A.NSEventMaskOtherMouseDown
        self.monitors = [
            A.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(mask, self._local),
            A.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(mask, self._global),
        ]

    def stop(self):
        if not self.monitors:
            return
        import AppKit as A
        for monitor in self.monitors:
            if monitor is not None:
                A.NSEvent.removeMonitor_(monitor)
        self.monitors = []

    def _point(self, event):
        import AppKit as A
        window = event.window()
        point = window.convertPointToScreen_(event.locationInWindow()) if window else A.NSEvent.mouseLocation()
        return QPoint(round(point.x), round(A.NSScreen.screens()[0].frame().size.height - point.y))

    def _local(self, event):
        dialog = self.dialog
        if dialog.closing:
            # Clicking our main window during the fade is an explicit choice,
            # unlike AppKit's automatic main-window promotion on key resign.
            if event.window() and event.window()!=native_window(dialog):dialog.focus_session.discard()
            return event
        if not dialog.isVisible():
            return event
        popup = owned_popup(dialog)
        if popup and event.window() == native_window(popup):
            return event
        if event.window() == native_window(dialog) and dialog.contains_artwork(self._point(event)):
            if not dialog.has_panel_focus():
                dialog.activate_panel()
        else:
            reason = 'local_click' if event.window()!=native_window(dialog) else 'dismiss'
            dialog.dismiss_animated(focus_reason=reason)
        return event  # The original click still reaches its destination.

    def _global(self, event):
        if not self.dialog.isVisible() or self.dialog.closing:
            return
        point = self._point(event)
        if self._input_palette_at(point):
            return
        self.dialog.dismiss_animated(focus_reason='external_click')

    def _input_palette_at(self, point):
        """Do not mistake a click on the current native IME for click-away.

        Inspect only the first window under this click (bounds and owning PID).
        No titles, AX trees, screenshots or text are collected or persisted.
        """
        if self.dialog.runtime.input.stage != 'parameters':
            return False
        import AppKit as A
        import Quartz as Q
        windows = Q.CGWindowListCopyWindowInfo(Q.kCGWindowListOptionOnScreenOnly | Q.kCGWindowListExcludeDesktopElements, 0) or []
        for window in windows:
            bounds = window.get('kCGWindowBounds', {})
            x, y = bounds.get('X', 0), bounds.get('Y', 0)
            if (window.get('kCGWindowAlpha', 1) <= 0 or not
                    (x <= point.x() < x + bounds.get('Width', 0) and y <= point.y() < y + bounds.get('Height', 0))):
                continue
            app = A.NSRunningApplication.runningApplicationWithProcessIdentifier_(window.get('kCGWindowOwnerPID', 0))
            bundle = str(app.bundleIdentifier() or '') if app else ''
            return bundle in {'com.proximic.inputmethod.ProxiMicVoice', 'com.apple.TextInputMenuAgent'} or bundle.startswith('com.apple.inputmethod.')
        return False


def input_helper_active(dialog):
    owner = dialog.runtime.agent.owner
    sources = (owner._inline_input._source_activation, getattr(owner._touchpad.strokeSession, '_source', None))
    return any(source and source.owns_foreground() for source in sources)
