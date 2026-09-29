"""Transparent, non-activating field-selection overlay in AX screen coordinates."""
from pathlib import Path
import math

from PySide6.QtCore import QObject, QMetaObject, QRect, Qt, QUrl
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtQuick import QQuickView

from .gesture_hud import screen_for_window


class FocusBand(QObject):
    def __init__(self, picker, parent=None, *, diagnostic=None):
        super().__init__(parent)
        self.picker = picker
        self.diagnostic = diagnostic or (lambda _: None)
        self.window = QQuickView()
        self.window.setObjectName("ringFieldSelectionWindow")
        self.window.setTitle("Ring 输入框选择")
        self.window.setFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                             | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.window.setColor(QColor("transparent"))
        self.window.setResizeMode(QQuickView.SizeRootObjectToView)
        self.window.setSource(QUrl.fromLocalFile(str(Path(__file__).parent / "qml/FocusBand.qml")))
        if self.window.status() == QQuickView.Error:
            raise RuntimeError("输入框高亮加载失败：" + "\n".join(str(e) for e in self.window.errors()))
        root = self.window.rootObject()
        root.landed.connect(picker.landed)
        root.exitDone.connect(self._hide)
        picker.shown.connect(self.show_snapshot)
        picker.exited.connect(self.exit_selection)
        picker.progress.connect(self.update_progress)
        QGuiApplication.instance().aboutToQuit.connect(self.close)

    def show_snapshot(self, data):
        x, y, width, height = data["frame"]
        margin = 40
        frame = QRect(math.floor(x)-margin, math.floor(y)-margin,
                      math.ceil(width)+margin*2, math.ceil(height)+margin*2)
        screen = screen_for_window(QGuiApplication.screens(), frame)
        if screen is not None:
            self.window.setScreen(screen)
        self.window.setGeometry(frame)
        fields = [{**field, "rect": [field["rect"][0]-frame.x(), field["rect"][1]-frame.y(),
                                      field["rect"][2], field["rect"][3]]} for field in data["fields"]]
        self.window.rootObject().setProperty("request", {**data, "fields": fields})
        was_visible = self.window.isVisible()
        self.window.show()
        if not was_visible:
            if QGuiApplication.platformName() == "cocoa":
                try:
                    from .notifications import _show_on_macos_spaces
                    _show_on_macos_spaces(self.window)
                except Exception as exc:
                    self.diagnostic(f"[FOCUS BAND] 原生显示失败：{exc}")
            else:
                self.window.raise_()

    def update_progress(self, ratio, warn):
        root = self.window.rootObject()
        root.setProperty("remainingRatio", ratio)
        root.setProperty("warning", warn)

    def exit_selection(self, reason):
        root = self.window.rootObject()
        root.setProperty("exitKind", reason)
        QMetaObject.invokeMethod(root, "beginExit", Qt.DirectConnection)

    def _hide(self):
        self.window.hide()
        self.window.rootObject().setProperty("displayed", False)

    def close(self):
        self._hide()
        self.window.close()
