"""Compact pointer-anchored panel, with a native Cocoa surface on macOS."""
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QRect, Qt, QUrl
from PySide6.QtGui import QColor, QCursor, QGuiApplication
from PySide6.QtQuick import QQuickView

from ..history.history_source import HUD_TITLE


def placement(pointer, available, width=300, height=140):
    width, height = min(width, available.width()), min(height, available.height())
    x, y = pointer.x() + 18, pointer.y() + 18
    if x + width > available.right() + 1:
        x = pointer.x() - width - 18
    if y + height > available.bottom() + 1:
        y = pointer.y() - height - 18
    return QRect(max(available.left(), min(x, available.right() + 1 - width)),
                 max(available.top(), min(y, available.bottom() + 1 - height)), width, height)


class HistoryRecommendationOverlay(QObject):
    def __init__(self, controller):
        super().__init__(controller)
        self.controller = controller
        self.native = None
        self.window = None
        if QGuiApplication.platformName() == "cocoa":
            from .history_recommendation_native import NativeHistoryRecommendation
            self.native = NativeHistoryRecommendation(controller)
            return
        self.window = QQuickView()
        self.window.setObjectName("historyRecommendationHud")
        self.window.setTitle(HUD_TITLE)
        self.window.setFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                             | Qt.WindowDoesNotAcceptFocus)
        self.window.setColor(QColor("transparent"))
        self.window.setResizeMode(QQuickView.SizeRootObjectToView)
        self.window.rootContext().setContextProperty("recommendation", controller)
        self.window.setSource(QUrl.fromLocalFile(str(Path(__file__).parent / "qml/HistoryRecommendation.qml")))
        if self.window.status() == QQuickView.Error:
            raise RuntimeError("推荐浮窗加载失败：" + "\n".join(str(e) for e in self.window.errors()))
        self.window.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.window and event.type() in (
                QEvent.MouseMove, QEvent.MouseButtonPress, QEvent.MouseButtonRelease,
                QEvent.Wheel, QEvent.Enter):
            self.controller.interact()
        return super().eventFilter(watched, event)

    def show(self):
        pointer = QCursor.pos()
        screen = QGuiApplication.screenAt(pointer) or QGuiApplication.primaryScreen()
        if screen is None:
            self.controller.dismiss()
            return
        geometry = placement(pointer, screen.availableGeometry())
        if self.native:
            self.native.show(geometry)
            return
        self.window.setScreen(screen)
        self.window.setMinimumSize(geometry.size())
        self.window.setMaximumSize(geometry.size())
        self.window.setGeometry(geometry)
        self.window.showNormal()
        self.window.raise_()

    def hide(self):
        if self.native:
            self.native.hide()
        elif self.window:
            self.window.hide()

    def close(self):
        self.hide()
        if self.native:
            self.native.close()
        elif self.window:
            self.window.removeEventFilter(self)
            self.window.setSource(QUrl())
            self.window.close()
