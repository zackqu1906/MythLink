"""Explicit main-window entry, separate from temporary input-panel activation."""
from PySide6.QtCore import QObject, Qt, Slot
from PySide6.QtGui import QGuiApplication


class MainWindowActivation(QObject):
    def __init__(self, window, agent_overlay, parent=None):
        super().__init__(parent)
        self.window = window
        self.agent_overlay = agent_overlay
        self._delegate = None
        if QGuiApplication.platformName() == 'cocoa':
            from proximic_ring.platforms.macos.mac_main_window import install_reopen_delegate
            self._delegate = install_reopen_delegate(self.show)
        QGuiApplication.instance().aboutToQuit.connect(self.close)

    @Slot()
    def show(self):
        # A Dock/menu command is an explicit user selection. Clear panel focus
        # ownership before AppKit raises or restores the normal window.
        self.agent_overlay.prepare_local_focus()
        if self.window.windowState() == Qt.WindowMinimized:
            self.window.showNormal()
        else:
            self.window.show()
        if QGuiApplication.platformName() == 'cocoa':
            from proximic_ring.platforms.macos.mac_window_focus import focus_window
            focus_window(self.window)
        else:
            self.window.raise_()
            self.window.requestActivate()

    def close(self):
        if self._delegate is not None:
            from proximic_ring.platforms.macos.mac_main_window import remove_reopen_delegate
            remove_reopen_delegate(self._delegate)
            self._delegate = None
