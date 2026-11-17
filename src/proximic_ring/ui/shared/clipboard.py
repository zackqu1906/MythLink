"""Clipboard bridge used by the desktop text adapters."""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager, nullcontext
import sys

from PySide6.QtCore import QCoreApplication, QMimeData
from PySide6.QtGui import QGuiApplication


INTERNAL_CLIPBOARD_TYPE = 'application/x-mythlink-internal-clipboard'
_internal_writes = 0
_native_internal_revision = None


def internal_clipboard_change(revision=None):
    return _internal_writes > 0 or (revision is not None and revision == _native_internal_revision)


@contextmanager
def internal_clipboard_write():
    """Exclude temporary text delivery/restoration from user-saved context."""
    global _internal_writes, _native_internal_revision
    _internal_writes += 1
    try:
        yield
    finally:
        try:
            if sys.platform == 'darwin' and QGuiApplication.platformName() == 'cocoa':
                from AppKit import NSPasteboard
                _native_internal_revision = int(NSPasteboard.generalPasteboard().changeCount())
        except ImportError:
            pass  # The bridge also supports installations without PyObjC.
        finally:
            _internal_writes -= 1


@dataclass(frozen=True)
class QtClipboardSnapshot:
    payloads: tuple[tuple[str, bytes], ...]
    image: object | None = None


class QtClipboardBridge:
    """Clone all clipboard MIME payloads before temporary Select-All/Copy."""

    def __init__(self, *, internal=True):
        self.internal = internal

    @staticmethod
    def _clipboard():
        application = QGuiApplication.instance()
        if application is None:
            raise RuntimeError("图形界面尚未初始化，无法读取文本框")
        return application.clipboard()

    def snapshot(self) -> QtClipboardSnapshot:
        mime = self._clipboard().mimeData()
        payloads = tuple(
            (str(fmt), bytes(mime.data(fmt))) for fmt in mime.formats()
        )
        image = mime.imageData() if mime.hasImage() else None
        return QtClipboardSnapshot(payloads, image)

    def restore(self, snapshot: object) -> None:
        if not isinstance(snapshot, QtClipboardSnapshot):
            return
        mime = QMimeData()
        for fmt, data in snapshot.payloads:
            mime.setData(fmt, data)
        if snapshot.image is not None:
            mime.setImageData(snapshot.image)
        mime.setData(INTERNAL_CLIPBOARD_TYPE, b'1')
        with internal_clipboard_write():
            self._clipboard().setMimeData(mime)
            QCoreApplication.processEvents()

    def set_text(self, text: str) -> None:
        with internal_clipboard_write() if self.internal else nullcontext():
            self._set_text(text)

    def _set_text(self, text: str) -> None:
        value = str(text)
        # QClipboard can advertise data lazily.  On macOS that means changing
        # focus immediately after setText() may let the destination process
        # observe the previous pasteboard item.  Writing NSPasteboard directly
        # makes the new string available before Command+V is posted.
        if sys.platform == "darwin":
            try:
                from AppKit import NSPasteboard, NSPasteboardTypeString

                pasteboard = NSPasteboard.generalPasteboard()
                pasteboard.clearContents()
                if self.internal:
                    pasteboard.setString_forType_('1', INTERNAL_CLIPBOARD_TYPE)
                if not pasteboard.setString_forType_(
                    value, NSPasteboardTypeString
                ):
                    raise RuntimeError("macOS 剪贴板拒绝写入听写内容")
                QCoreApplication.processEvents()
                return
            except ImportError:
                # Source-only environments may not bundle PyObjC.  Qt remains
                # a functional fallback and the adapter verifies the value
                # before it sends the paste shortcut.
                pass
        mime = QMimeData()
        mime.setText(value)
        if self.internal:
            mime.setData(INTERNAL_CLIPBOARD_TYPE, b'1')
        self._clipboard().setMimeData(mime)
        QCoreApplication.processEvents()

    def text(self) -> str:
        QCoreApplication.processEvents()
        if sys.platform == "darwin":
            try:
                from AppKit import NSPasteboard, NSPasteboardTypeString

                value = NSPasteboard.generalPasteboard().stringForType_(
                    NSPasteboardTypeString
                )
                return str(value or "")
            except ImportError:
                pass
        return str(self._clipboard().text() or "")
