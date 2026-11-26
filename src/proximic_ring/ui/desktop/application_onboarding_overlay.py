"""Small nonactivating opt-in card above the currently used application's Space."""
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtQuick import QQuickView

from ..shared.notifications import show_corner_notice


class ApplicationOnboardingOverlay(QObject):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.window = QQuickView()
        self.window.setTitle('MythLink · 应用手势')
        self.window.setFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.window.setColor(QColor('transparent'))
        self.window.setResizeMode(QQuickView.SizeRootObjectToView)
        self.window.rootContext().setContextProperty('onboarding', controller)
        self.window.setSource(QUrl.fromLocalFile(str(Path(__file__).parents[1] / 'qml/ApplicationSuggestion.qml')))
        if self.window.status() == QQuickView.Error:
            raise RuntimeError('应用手势提示加载失败：' + '\n'.join(str(error) for error in self.window.errors()))
        root = self.window.rootObject()
        self.window.resize(round(root.implicitWidth()), round(root.implicitHeight()))
        controller.changed.connect(self.refresh)
        controller.catalog.service.noticeChanged.connect(self._notice_changed)
        QGuiApplication.instance().aboutToQuit.connect(self.close)

    def _notice_changed(self):
        if self.controller.visible and not self.controller.catalog.service.notice:
            self.controller.poll()  # The foreground may have changed while the question was hidden.
        self.refresh()

    def refresh(self):
        if not self.controller.visible or self.controller.catalog.service.notice:
            self.window.hide()
            return
        if not self.window.isVisible():
            show_corner_notice(self.window, self.controller.catalog.service.owner._append_background_diagnostic)

    def close(self):
        self.controller.close()
        self.window.hide()
        self.window.close()
