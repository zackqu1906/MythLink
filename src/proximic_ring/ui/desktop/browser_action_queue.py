"""Serialize browser IO off Qt; revalidate queued work before starting it."""
from collections import deque
import threading

from PySide6.QtCore import QObject, Qt, Signal, Slot

from ...diagnostics.scene_diagnostics import SceneActionError


class BrowserActionQueue(QObject):
    _finished = Signal(object, object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pending = deque()
        self._active = None
        self._closed = False
        self._finished.connect(self._complete, Qt.QueuedConnection)

    def submit(self, work, valid, complete):
        # The GUI owns the queue. Bound bursts rather than replaying a backlog
        # after a browser hang; native actions also retain their own deadlines.
        if self._closed or len(self._pending) >= 4:
            return False
        self._pending.append((work, valid, complete))
        if self._active is None:
            self._start_next()
        return True

    def _start_next(self):
        while self._pending and not self._closed:
            job = self._pending.popleft()
            work, valid, complete = job
            reason = valid()
            if reason:
                complete(None, SceneActionError(reason))
                continue
            self._active = job

            def run(job=job, work=work):
                result, error = None, None
                try:
                    result = work()
                except Exception as exc:
                    error = exc
                if not self._closed:
                    self._finished.emit(job, result, error)

            threading.Thread(target=run, name="BrowserGestureAction", daemon=True).start()
            return

    @Slot(object, object, object)
    def _complete(self, job, result, error):
        if job is not self._active:
            return
        self._active = None
        if self._closed:
            return
        try:
            job[2](result, error)
        finally:
            self._start_next()

    def close(self):
        self._closed = True
        self._pending.clear()
