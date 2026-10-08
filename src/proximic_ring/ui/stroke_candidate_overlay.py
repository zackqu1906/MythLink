"""Non-activating Windows stroke candidates beside the target caret."""
from pathlib import Path

from PySide6.QtCore import QObject, QRect, Qt, QUrl
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtQuick import QQuickView

from .gesture_hud import screen_for_window


class StrokeCandidateOverlay(QObject):
    def __init__(self, touchpad, parent=None):
        super().__init__(parent)
        self.touchpad = touchpad
        self.window = QQuickView()
        self.window.setObjectName("strokeCandidateWindow")
        self.window.setFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                             | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.window.setColor(QColor("transparent"))
        self.window.setResizeMode(QQuickView.SizeRootObjectToView)
        self.window.setSource(QUrl.fromLocalFile(str(Path(__file__).parent / "qml/StrokeCandidates.qml")))
        if self.window.status() == QQuickView.Error:
            raise RuntimeError("笔画候选窗加载失败：" + "\n".join(map(str, self.window.errors())))
        touchpad.changed.connect(self.update)
        QGuiApplication.instance().aboutToQuit.connect(self.close)

    def update(self):
        target = self.touchpad._stroke_target
        if not (self.touchpad.active and self.touchpad.inputMode == "stroke"
                and self.touchpad.strokeCode and target):
            self.window.hide()
            return
        try:
            bounds = self.touchpad.owner._desktop_target_adapter().caret_bounds(target)
        except Exception:
            self.window.hide()
            return
        if bounds[3] <= 0:
            self.window.hide()
            return
        root = self.window.rootObject()
        root.setProperty("code", self.touchpad.strokeCode)
        root.setProperty("candidates", self.touchpad.strokeCandidates)
        root.setProperty("selected", self.touchpad.selectedCandidate)
        width, height = 410, 76
        caret = QRect(*bounds)
        screen = screen_for_window(QGuiApplication.screens(), caret)
        if screen is None:
            self.window.hide()
            return
        available = screen.availableGeometry()
        x = max(available.left(), min(caret.left(), available.right() - width))
        y = caret.bottom() + 8
        if y + height > available.bottom():
            y = caret.top() - height - 8
        self.window.setScreen(screen)
        self.window.setGeometry(x, max(available.top(), y), width, height)
        self.window.show()

    def close(self):
        self.window.hide()
        self.window.close()
