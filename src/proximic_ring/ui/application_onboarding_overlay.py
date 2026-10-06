"""Small nonactivating opt-in card above the currently used application's Space."""
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl
from PySide6.QtGui import QColor, QCursor, QGuiApplication
from PySide6.QtQuick import QQuickView

from .application_icons import install_application_icons
from .gesture_hud import foreground_window_bounds, screen_for_window


class ApplicationOnboardingOverlay(QObject):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.window = QQuickView()
        self.window.setTitle('MythLink · 应用手势')
        self.window.setFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.window.setColor(QColor('transparent'))
        self.window.setResizeMode(QQuickView.SizeRootObjectToView)
        install_application_icons(self.window.engine())
        self.window.rootContext().setContextProperty('onboarding', controller)
        self.window.setSource(QUrl.fromLocalFile(str(Path(__file__).parent / 'qml/ApplicationSuggestion.qml')))
        if self.window.status() == QQuickView.Error:
            raise RuntimeError('应用手势提示加载失败：' + '\n'.join(str(error) for error in self.window.errors()))
        root = self.window.rootObject()
        root.implicitHeightChanged.connect(self._resize)
        self._resize()
        controller.changed.connect(self.refresh)
        QGuiApplication.instance().aboutToQuit.connect(self.close)

    def _resize(self):
        root = self.window.rootObject()
        self.window.resize(382, max(1, int(root.implicitHeight() + .5)))

    def refresh(self):
        if not self.controller.visible:
            self.window.hide()
            return
        if self.window.isVisible():
            return
        try:
            bounds = foreground_window_bounds()
        except Exception:
            bounds = None
        screen = (screen_for_window(QGuiApplication.screens(), bounds)
                  or QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen())
        if screen is None:
            return
        self.window.setScreen(screen)
        area = screen.availableGeometry()
        self.window.setPosition(area.right() - self.window.width() - 24, area.y() + 24)
        self.window.show()
        if QGuiApplication.platformName() == 'cocoa':
            try:
                from .notifications import _show_on_macos_spaces
                _show_on_macos_spaces(self.window)
            except Exception as exc:
                self.controller._log('suggestion_surface_failed', error_type=type(exc).__name__)

    def close(self):
        self.controller.close()
        self.window.hide()
        self.window.close()
