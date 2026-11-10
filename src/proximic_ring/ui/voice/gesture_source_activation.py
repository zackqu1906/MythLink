"""Bridge a gesture worker to the existing asynchronous native IME selector."""
from dataclasses import dataclass, field
import threading
import time

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot

from ...voice.ime.input_method_install import BUNDLE_ID
from .input_source_activation import InputSourceActivation


@dataclass(eq=False)
class _Request:
    origin: tuple
    deadline: float
    done: threading.Event = field(default_factory=threading.Event)
    cancelled: threading.Event = field(default_factory=threading.Event)
    error: str = ""
    source: object = None
    recovering: bool = False
    previous_source: str | None = None
    restore_source: str = ""
    restored: bool = False
    expected_source: str = ""
    on_borrow: object = None


class GestureSourceActivation(QObject):
    _requested = Signal(object)
    _cancelled = Signal(object)

    def __init__(self, parent=None, *, foreground, factory=InputSourceActivation):
        super().__init__(parent)
        self._foreground = foreground
        self._factory = factory
        self._pending = set()
        self._restore_lock = threading.Lock()
        self._restore_ticket = None
        self._restore_request = None
        self._closed = False
        self._requested.connect(self._start, Qt.QueuedConnection)
        self._cancelled.connect(self._cancel, Qt.QueuedConnection)

    def activate(self, origin, *, timeout, expected_source="", on_borrow=None):
        request = _Request(origin, time.monotonic() + timeout,
                           expected_source=expected_source, on_borrow=on_borrow)
        self._wait(request)
        return request.previous_source

    def invalidate_restore(self):
        """New input takes ownership, even if an older focus probe is still running."""
        ticket = object()
        with self._restore_lock:
            self._restore_ticket = ticket
            request, self._restore_request = self._restore_request, None
            if request is not None and not request.done.is_set():
                request.cancelled.set()
        if request is not None and request.cancelled.is_set():
            if QThread.currentThread() == self.thread():
                self._cancel(request)
            else:
                self._cancelled.emit(request)
        return ticket

    def owns_restore(self, ticket):
        with self._restore_lock:
            return not self._closed and ticket is not None and ticket is self._restore_ticket

    def restore_if_unused(self, origin, previous, ticket, *, timeout=.5):
        """Return a failed probe's loan only while its original request owns it."""
        if not isinstance(previous, str) or not previous or previous in {BUNDLE_ID, BUNDLE_ID + '.dictation'}:
            return False
        request = _Request(origin, time.monotonic() + timeout, restore_source=previous)
        with self._restore_lock:
            if ticket is None or ticket is not self._restore_ticket:
                return False
            self._restore_ticket = None
            self._restore_request = request
        try:
            self._wait(request)
            return request.restored
        except (RuntimeError, TimeoutError):
            return False

    def _wait(self, request):
        # Only the gesture worker may wait. The GUI must keep servicing native
        # focus changes, helper output and Agent-panel restoration throughout.
        if QThread.currentThread() == self.thread():
            raise RuntimeError('输入法激活必须在手势线程发起')
        if self._closed:
            raise RuntimeError('输入法已关闭')
        if time.monotonic() >= request.deadline:
            raise TimeoutError('输入法切换超时')
        self._requested.emit(request)
        if (not request.done.wait(max(0, request.deadline - time.monotonic()))
                or time.monotonic() >= request.deadline):
            request.cancelled.set()
            self._cancelled.emit(request)
            raise TimeoutError('输入法切换超时')
        if request.error:
            raise RuntimeError(request.error)

    @Slot(object)
    def _start(self, request):
        if self._closed or request.cancelled.is_set() or time.monotonic() >= request.deadline:
            self._finish(request, '输入法激活已取消')
            return
        self._pending.add(request)
        self._launch(request)

    def _launch(self, request):
        try:
            remaining = request.deadline - time.monotonic()
            if request.cancelled.is_set() or remaining <= 0:
                self._finish(request, '输入法切换超时')
                return
            if self._foreground() != request.origin:
                self._finish(request, '前台应用已变化')
                return
            source = self._factory(self, timeout_ms=max(1, int(remaining * 1000)))
            request.source = source
            source.event.connect(lambda message: self._event(request, source, message))
            if request.restore_source:
                source.restore(request.origin, request.restore_source, BUNDLE_ID + '.dictation')
            elif request.expected_source:
                def borrow(previous):
                    if previous is not None and (request.cancelled.is_set() or time.monotonic() >= request.deadline
                                                 or self._foreground() != request.origin):
                        raise RuntimeError('输入法激活已取消')
                    request.on_borrow(previous)
                source.start_probe(request.origin, request.expected_source, borrow)
            else:
                (source.recover if request.recovering else source.start)(request.origin)
        except Exception as exc:
            self._finish(request, str(exc))

    def _event(self, request, source, message):
        if request not in self._pending or request.source is not source:
            return
        if request.cancelled.is_set() or time.monotonic() >= request.deadline:
            self._finish(request, '输入法切换超时')
        elif request.restore_source:
            request.restored = message.get('event') == 'restored' and message.get('restored') is True
            self._finish(request)
        elif message.get('event') != 'selected':
            self._finish(request, str(message.get('error') or '输入法激活失败'))
        elif self._foreground() != request.origin:
            self._finish(request, '前台应用已变化')
        elif not request.recovering and message.get('already_selected') is True and not message.get('focus_restored'):
            # The focus probe reached us because no current IMK client exists.
            # Re-selecting an already selected source alone cannot repair it.
            source.cancel()
            request.source = None
            request.recovering = True
            self._launch(request)
        elif request.recovering and message.get('focus_restored') is not True:
            self._finish(request, '输入法焦点恢复未完成')
        else:
            request.previous_source = message.get('previous_source')
            self._finish(request)

    def _finish(self, request, error=''):
        source, request.source = request.source, None
        self._pending.discard(request)
        if source is not None:
            source.cancel()
        request.error = error
        request.done.set()

    @Slot(object)
    def _cancel(self, request):
        self._finish(request, '输入法激活已取消')

    def close(self):
        self._closed = True
        for request in tuple(self._pending):
            self._finish(request, '输入法已关闭')
