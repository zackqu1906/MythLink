"""One-shot, QObject-bound callback for Qt's native permission requests."""
from PySide6.QtCore import QObject


class PermissionCallback(QObject):
    def __init__(self, finished, parent=None):
        super().__init__(parent)
        self._finished = finished

    def completed(self, permission):
        # PySide 6.11 accesses functor.__func__ after dispatching the native
        # request. A closure raises AttributeError *after* opening the prompt;
        # a bound method works and gives Qt a per-request lifetime context.
        finished, self._finished = self._finished, None
        if finished is None:
            return
        try:
            finished(permission)
        finally:
            self.deleteLater()

    def cancel(self):
        self._finished = None
        self.deleteLater()
