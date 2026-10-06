"""Click-through scene-entry feedback in the active screen's upper-right corner."""
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl
from PySide6.QtGui import QColor, QCursor, QGuiApplication
from PySide6.QtQuick import QQuickView

from .gesture_hud import foreground_window_bounds, screen_for_window


class SceneNoticeOverlay(QObject):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.window = QQuickView()
        self.window.setTitle('MythLink · 场景提示')
        self.window.setFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                             | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.window.setColor(QColor('transparent'))
        self.window.setResizeMode(QQuickView.SizeRootObjectToView)
        self.window.rootContext().setContextProperty('sceneNotice', controller)
        self.window.setSource(QUrl.fromLocalFile(str(Path(__file__).parent / 'qml/SceneNotice.qml')))
        if self.window.status() == QQuickView.Error:
            raise RuntimeError('场景提示加载失败：' + '\n'.join(str(error) for error in self.window.errors()))
        root = self.window.rootObject()
        self.window.resize(int(root.implicitWidth()), int(root.implicitHeight()))
        from .overlay_stacking import OverlayVisibilityGuard
        self._visibility_guard = OverlayVisibilityGuard(self.window, self,
            diagnostic=lambda exc: self.controller._log('notice_surface_failed', error_type=type(exc).__name__))
        controller.changed.connect(self.refresh)
        QGuiApplication.instance().aboutToQuit.connect(self.close)

    def refresh(self):
        if not self.controller.visible:
            self.window.hide()
            return
        try:
            bounds = foreground_window_bounds()
        except Exception:
            bounds = None
        screen = (screen_for_window(QGuiApplication.screens(), bounds)
                  or QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen())
        if screen is None:
            self.controller._log('notice_surface_failed', reason_code='screen_unavailable')
            return
        area = screen.availableGeometry()
        self.window.setScreen(screen)
        self.window.setPosition(max(area.x(), area.x() + area.width() - self.window.width() - 24), area.y() + 24)
        self.window.show()
        if QGuiApplication.platformName() == 'cocoa':
            try:
                from .notifications import _show_on_macos_spaces
                _show_on_macos_spaces(self.window)
            except Exception as exc:
                self.controller._log('notice_surface_failed', error_type=type(exc).__name__)
                return
        self.controller._log('notice_surface_shown', scene=self.controller.notice.get('scene', ''),
                             position=[self.window.x(), self.window.y()],
                             size=[self.window.width(), self.window.height()])

    def close(self):
        self.controller.close()
        self.window.hide()
        self.window.close()
