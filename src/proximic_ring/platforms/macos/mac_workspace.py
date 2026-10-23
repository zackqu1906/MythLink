"""Fresh workspace metadata for GUI threads and persistent pipe workers."""
from __future__ import annotations

import sys


def _on_qt_event_thread():
    # Native pipe workers do not load Qt. Do not import it just to check this.
    core = sys.modules.get("PySide6.QtCore")
    if core is None:
        return False
    app = core.QCoreApplication.instance()
    return app is not None and core.QThread.currentThread() == app.thread()


def refresh_workspace(*, interval: float = .005):
    from AppKit import NSWorkspace

    workspace = NSWorkspace.sharedWorkspace()
    # Qt already services AppKit notifications on its event thread. Pumping a
    # nested run loop here can deliver BEGIN acknowledgments/cancel callbacks
    # halfway through a caller's focus check and invalidate its transaction.
    # Pipe workers still need to drain notifications to avoid stale NSWorkspace
    # metadata; their Python main thread has no Qt event loop.
    if not _on_qt_event_thread():
        from Foundation import NSDate, NSRunLoop
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(interval))
    return workspace


def frontmost_application():
    # Never keep the application or PID here: callers pin their own target and
    # must revalidate it before applying a queued operation.
    return refresh_workspace().frontmostApplication()


def running_applications():
    return refresh_workspace().runningApplications()
