"""Move a group of native cards without stealing buttons or text input."""
from PySide6.QtCore import QObject, QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QGuiApplication
from shiboken6 import isValid


class AgentStackDrag(QObject):
    def __init__(self, overlay):
        super().__init__(overlay)
        self.overlay = overlay
        self.press = None
        self.press_window = None
        self.active = False
        self.frames = {}

    @staticmethod
    def hit_test(window, point):
        # childItems() converts every native child to a Python wrapper. Chromium
        # owns private QQuickItems that can crash in getWrapperForQObject during
        # that conversion, before Python can catch an exception or check validity.
        # QML tests only its declared regions and returns a scalar across the bridge.
        if not isValid(window): return 0
        root = window.rootObject()
        if root is None or not isValid(root): return 0
        return root.dragHitTest(point.x(), point.y())

    def reset(self):
        self.press = self.press_window = None
        self.active = False
        self.frames.clear()

    def eventFilter(self, window, event):
        kind = event.type()
        if kind in (QEvent.Hide, QEvent.Close, QEvent.WindowDeactivate, QEvent.UngrabMouse):
            if window is self.press_window: self.reset()
            return False
        if kind not in (QEvent.MouseButtonPress, QEvent.MouseMove, QEvent.MouseButtonRelease):
            return False
        key = next((k for k, w in self.overlay.windows.items() if w is window), None)
        if (self.overlay._closed or key is None or key in self.overlay.retiring
                or key in self.overlay.parked):
            self.reset()
            return False
        if kind == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            hit = self.hit_test(window, event.position())
            # Only a real click selects another task; ring input stays bound to
            # the last clicked conversation. A collapsed card expands on release.
            if self.overlay.sessions.focused and hit != 2:
                if key and self.overlay.owners[key] is not self.overlay.sessions.active:
                    self.overlay.owners[key].focusCard(key.split('/')[-1])
            self.press = event.globalPosition().toPoint() if hit == 1 else None
            self.press_window = window if self.press is not None else None
            self.active = False
        elif (kind == QEvent.MouseMove and window is self.press_window
                and self.press is not None and event.buttons() & Qt.LeftButton):
            delta = event.globalPosition().toPoint() - self.press
            if not self.active:
                if delta.manhattanLength() < QGuiApplication.styleHints().startDragDistance():
                    return False
                self.active = True
                grabber = window.mouseGrabberItem()
                if grabber is not None: grabber.ungrabMouse()
                for (key, prop), animation in self.overlay.animations.items():
                    if prop == 'geometry': animation.stop()
                self.frames = {k: QRect(w.geometry()) for k, w in self.overlay.windows.items()
                               if w.isVisible() and k not in self.overlay.retiring}
                self.origin = QPoint(self.overlay.offset)
            bounds = QRect()
            for frame in self.frames.values(): bounds = bounds.united(frame)
            area = self.overlay.screen.availableGeometry().adjusted(8, 8, -8, -8)
            dx = max(area.left()-bounds.left(), min(delta.x(), area.right()-bounds.right()))
            dy = max(area.top()-bounds.top(), min(delta.y(), area.bottom()-bounds.bottom()))
            self.overlay.offset = self.origin + QPoint(dx, dy)
            for key, frame in self.frames.items():
                if key in self.overlay.windows: self.overlay.windows[key].setGeometry(frame.translated(dx, dy))
            return True
        elif kind == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            dragged = self.active
            self.reset()
            if dragged:
                self.overlay.schedule()
                return True
        return False

