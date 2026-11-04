"""Nonactivating pre-lock feedback, centered above the active screen's Dock."""
from pathlib import Path

from PySide6.QtCore import QObject, QPoint, Qt, QUrl
from PySide6.QtGui import QColor, QCursor, QGuiApplication
from PySide6.QtQuick import QQuickView

from ..gestures.gesture_hud import foreground_window_bounds, screen_for_window
from ..shared.overlay_stacking import OverlayVisibilityGuard


def notice_position(area, width, height):
    # availableGeometry excludes the menu bar and a visible Dock on any edge.
    # The QML window includes 24 px of transparent space below the card.
    return QPoint(area.x() + max(0, (area.width() - width) // 2),
                  max(area.y(), area.y() + area.height() - height))


class ProximityLockOverlay(QObject):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self._screen = None
        self._material = None
        self._closed = False
        self.window = QQuickView()
        self.window.setTitle("MythLink · 自动锁屏提示")
        self.window.setFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                             | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.window.setColor(QColor("transparent"))
        self.window.setResizeMode(QQuickView.SizeRootObjectToView)
        self.window.rootContext().setContextProperty("proximityLock", controller)
        self.window.setSource(QUrl.fromLocalFile(str(Path(__file__).parents[1] / "qml/ProximityLockNotice.qml")))
        if self.window.status() == QQuickView.Error:
            raise RuntimeError("锁屏提示加载失败：" + "\n".join(str(e) for e in self.window.errors()))
        root = self.window.rootObject()
        root.implicitWidthChanged.connect(self._place)
        root.implicitHeightChanged.connect(self._place)
        self._visibility_guard = OverlayVisibilityGuard(self.window, self, diagnostic=self._failed)
        controller.lockNoticeChanged.connect(self.refresh)
        app = QGuiApplication.instance()
        app.screenRemoved.connect(self._screen_removed)
        app.aboutToQuit.connect(self.close)

    def _failed(self, exc):
        self.controller.diagnostic.emit({"action": "lock_notice_failed", "error_type": type(exc).__name__})

    def _select_screen(self, screen):
        if self._screen is screen:
            return
        if self._screen is not None:
            self._screen.availableGeometryChanged.disconnect(self._place)
        self._screen = screen
        if screen is not None:
            screen.availableGeometryChanged.connect(self._place)
            self.window.setScreen(screen)

    def _screen_removed(self, screen):
        if self._screen is screen:
            self._select_screen(None)
            self.refresh()

    def _place(self):
        if self._closed or self._screen is None:
            return
        root = self.window.rootObject()
        self.window.resize(int(root.implicitWidth()), int(root.implicitHeight()))
        self.window.setPosition(notice_position(self._screen.availableGeometry(),
                                                self.window.width(), self.window.height()))
        if self._material is not None:
            self._material.resize(corner_radius=14 if self.controller.lockNoticeState == "cancelled" else 16)

    def refresh(self):
        if self._closed:
            return
        if not self.controller.lockNoticeState:
            self.window.hide()
            self._select_screen(None)
            return
        # Keep the same screen throughout a countdown and its cancellation toast.
        if self._screen is None:
            try:
                bounds = foreground_window_bounds()
            except Exception:
                bounds = None
            self._select_screen(screen_for_window(QGuiApplication.screens(), bounds)
                                or QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen())
        if self._screen is None:
            return
        self._place()
        self.window.show()
        if QGuiApplication.platformName() == "cocoa":
            try:
                if self._material is None:
                    from ..shared.window_material import WindowMaterial
                    self._material = WindowMaterial(self.window, light=True, insets=(18, 12, 18, 24),
                                                    corner_radius=16)
                self._place()
                from ..shared.notifications import _show_on_macos_spaces
                _show_on_macos_spaces(self.window)
            except Exception as exc:
                self._failed(exc)

    def close(self):
        self._closed = True
        self.window.hide()
        self._select_screen(None)
        if self._material is not None:
            self._material.close()
            self._material = None
        self.window.close()
