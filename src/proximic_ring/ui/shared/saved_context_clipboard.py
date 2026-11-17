"""Observe new clipboard text, including copy buttons in other applications."""
import sys

from PySide6.QtCore import QObject, QTimer, Slot
from PySide6.QtGui import QGuiApplication

from .clipboard import INTERNAL_CLIPBOARD_TYPE, internal_clipboard_change


class SavedContextClipboardWatcher(QObject):
    def __init__(self, save_text, parent=None):
        super().__init__(parent)
        self._save_text = save_text
        self._clipboard = QGuiApplication.clipboard()
        self._pasteboard = None
        self._closed = False
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        if sys.platform == 'darwin' and QGuiApplication.platformName() == 'cocoa':
            from AppKit import NSPasteboard
            self._pasteboard = NSPasteboard.generalPasteboard()
            # Do not replace the persisted slot with an old clipboard on launch.
            self._revision = int(self._pasteboard.changeCount())
            # Qt may only announce external changes when the app is activated.
            self._timer.timeout.connect(self.check)
            self._timer.start()
        self._clipboard.dataChanged.connect(self.check)

    def flush(self):
        """Observe any native change before an edit pins its context snapshot."""
        if self._pasteboard is not None:
            self.check()

    @Slot()
    def check(self):
        if self._closed:
            return
        if self._pasteboard is not None:
            revision = int(self._pasteboard.changeCount())
            if revision == self._revision:
                return
            if internal_clipboard_change(revision):
                self._revision = revision
                return
            from AppKit import NSPasteboardTypeString
            if self._pasteboard.stringForType_(INTERNAL_CLIPBOARD_TYPE):
                self._revision = revision
                return
            text = self._pasteboard.stringForType_(NSPasteboardTypeString)
            if int(self._pasteboard.changeCount()) != revision:
                return  # Retry a coherent clipboard item at the next tick.
            self._revision = revision
        else:
            mime = self._clipboard.mimeData()
            if internal_clipboard_change() or mime is None or mime.hasFormat(INTERNAL_CLIPBOARD_TYPE):
                return
            text = mime.text() if mime.hasText() else None
        if text and str(text).strip():
            self._save_text(str(text))

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._timer.stop()
        self._clipboard.dataChanged.disconnect(self.check)
