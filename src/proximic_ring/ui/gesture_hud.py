"""Non-activating global gesture hints. Presentation only; no gesture routing."""
from __future__ import annotations

from pathlib import Path
import sys

from PySide6.QtCore import QMetaObject, QObject, QRect, QTimer, Qt, QUrl
from PySide6.QtGui import QColor, QCursor, QGuiApplication
from PySide6.QtQuick import QQuickView


HUD_SIZE = 280
HUD_MARGIN = 24
HUD_DURATION_MS = 5000


def screen_for_window(screens, bounds):
    """Prefer the screen containing most of the foreground window, in points."""
    if bounds is None:
        return None
    intersections = [(screen.geometry().intersected(bounds), screen) for screen in screens]
    intersections = [(rect.width() * rect.height(), screen) for rect, screen in intersections if not rect.isEmpty()]
    return max(intersections, key=lambda item: item[0])[1] if intersections else None


def hud_geometry(area: QRect) -> QRect:
    size = max(1, min(HUD_SIZE, area.width() - 2 * HUD_MARGIN, area.height() - 2 * HUD_MARGIN))
    margin_x = min(HUD_MARGIN, max(0, area.width() - size))
    margin_y = min(HUD_MARGIN, max(0, area.height() - size))
    return QRect(area.x() + area.width() - size - margin_x,
                 area.y() + area.height() - size - margin_y, size, size)


def foreground_window_bounds() -> QRect | None:
    if sys.platform != "darwin":
        return None
    import Quartz

    from ..mac_workspace import frontmost_application
    front = frontmost_application()
    if front is None:
        return None
    pid = int(front.processIdentifier())
    windows = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
        Quartz.kCGNullWindowID,
    ) or []
    # WindowServer returns front-to-back order. Only geometry is read: no window
    # image, document text, AX traversal or permission prompt is needed here.
    for window in windows:
        if window.get(Quartz.kCGWindowOwnerPID) != pid or window.get(Quartz.kCGWindowLayer) != 0:
            continue
        bounds = window.get(Quartz.kCGWindowBounds, {})
        if bounds.get("Width", 0) > 1 and bounds.get("Height", 0) > 1:
            return QRect(round(bounds["X"]), round(bounds["Y"]),
                         round(bounds["Width"]), round(bounds["Height"]))
    return None


class GestureHud(QObject):
    def __init__(self, parent=None, *, diagnostic=None):
        super().__init__(parent)
        self._diagnostic = diagnostic or (lambda message: print(message, file=sys.stderr))
        self.window = QQuickView()
        self.window.setObjectName("ringGestureHudWindow")
        self.window.setTitle("Ring 手势提示")
        self.window.setFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                             | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.window.setColor(QColor("transparent"))
        self.window.setResizeMode(QQuickView.SizeRootObjectToView)
        self.window.setSource(QUrl.fromLocalFile(str(Path(__file__).parent / "qml/GestureHud.qml")))
        if self.window.status() == QQuickView.Error:
            raise RuntimeError("手势提示层加载失败：" + "\n".join(str(error) for error in self.window.errors()))
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.setSingleShot(True)
        self._timer.setInterval(HUD_DURATION_MS)
        self._timer.timeout.connect(self.hide)
        QGuiApplication.instance().aboutToQuit.connect(self.close)

    def _screen(self):
        bounds = None
        if QGuiApplication.platformName() == "cocoa":
            try:
                bounds = foreground_window_bounds()
            except Exception as exc:
                self._diagnostic(f"[GESTURE HUD] 获取前台窗口屏幕失败：{exc}")
        return (screen_for_window(QGuiApplication.screens(), bounds)
                or QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen())

    def show_mode(self, mode: str, *, preview: bool = False, input_fields_available: bool = True,
                  message: str = "", input_fields_hint: str = "", scene_actions=None):
        if mode not in ("input", "operation"):
            raise ValueError("未知手势提示模式")
        root = self.window.rootObject()
        root.setProperty("mode", mode)
        root.setProperty("preview", preview)
        root.setProperty("sceneActions", scene_actions or [])
        root.setProperty("notice", message)
        root.setProperty("inputFieldsAvailable", input_fields_available)
        root.setProperty("inputFieldsHint", input_fields_hint or
                         ("" if input_fields_available else "当前窗口未提供文本框"))
        screen = self._screen()
        if screen is not None:
            self.window.setScreen(screen)
            self.window.setGeometry(hud_geometry(screen.availableGeometry()))
        QMetaObject.invokeMethod(root, "replayEntrance", Qt.DirectConnection)
        self.window.show()
        if QGuiApplication.platformName() == "cocoa":
            try:
                # QWindow.raise() on Cocoa also activates NSApp, even for a
                # tooltip that cannot take focus. Order only this native window
                # so a gesture never brings the main UI to the foreground.
                from .notifications import _show_on_macos_spaces
                _show_on_macos_spaces(self.window)
            except Exception as exc:
                self._diagnostic(f"[GESTURE HUD] macOS Space 显示失败：{exc}")
        else:
            self.window.raise_()
        # Repeated requests always replace the current content and renew the
        # same timer; an earlier request cannot hide a newly shown mode.
        self._timer.start()

    def preview(self, mode: str):
        self.show_mode(mode, preview=True)

    def update_input_fields(self, available: bool, hint: str):
        root = self.window.rootObject()
        if not root.property("preview"):
            root.setProperty("inputFieldsAvailable", available)
            root.setProperty("inputFieldsHint", hint)

    def hide(self):
        self._timer.stop()
        QMetaObject.invokeMethod(self.window.rootObject(), "stopEntrance", Qt.DirectConnection)
        self.window.hide()

    def close(self):
        self.hide()
        self.window.close()


def main():
    """Standalone preview, with no BLE, microphone, models or user settings."""
    import argparse
    from PySide6.QtWidgets import QApplication

    parser = argparse.ArgumentParser(description="预览 Ring 全局手势提示层")
    parser.add_argument("--mode", choices=("input", "operation"), default="input")
    args = parser.parse_args()
    app = QApplication([sys.argv[0]])
    app.setQuitOnLastWindowClosed(False)
    hud = GestureHud(app)
    hud.preview(args.mode)
    QTimer.singleShot(HUD_DURATION_MS + 100, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
