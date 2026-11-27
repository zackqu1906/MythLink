"""Nonactivating status panel for dictation in an external Windows editor."""
from pathlib import Path

from PySide6.QtCore import QObject, QRect, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QCursor, QGuiApplication
from PySide6.QtQuick import QQuickView

from ...gestures.gesture_settings import GESTURE_LABELS
from ..shared.windows_caret import screen_caret


class WindowsVoicePanel(QObject):
    def __init__(self, inline, parent):
        super().__init__(parent)
        self.inline = inline
        self.window = QQuickView()
        self.window.setObjectName("windowsVoicePanel")
        self.window.setFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
                             Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.window.setColor(QColor("transparent"))
        self.window.setResizeMode(QQuickView.SizeRootObjectToView)
        self.window.setSource(QUrl.fromLocalFile(str(Path(__file__).parents[1] / "qml/WindowsVoicePanel.qml")))
        if self.window.status() == QQuickView.Error:
            raise RuntimeError("听写候选窗加载失败：" + "\n".join(str(e) for e in self.window.errors()))
        self.dismiss = QTimer(self, singleShot=True, interval=5000)
        self.dismiss.timeout.connect(self.window.hide)
        self._state = None
        inline.changed.connect(self.refresh)
        parent.aboutToQuit.connect(self.window.close)

    def refresh(self):
        inline = self.inline
        view = inline._view
        phase = view.get("phase", "idle")
        settled = phase in {"dictated", "edited", "undone", "interrupted", "error"}
        if (not inline._utterance_id and not (phase == "error" and self._state)) or phase == "idle":
            self.dismiss.stop()
            self.window.hide()
            self._state = None
            return
        state = (inline._utterance_id, phase, view.get("revision"), inline.error)
        if settled and state == self._state and not self.window.isVisible():
            return
        content = self.window.rootObject()
        def hint(action, label):
            names = [GESTURE_LABELS.get(g, g).split("（")[0] for g in inline._gesture_hints.get(action, []) if g]
            return " / ".join(names) + " " + label if names else ""
        hints = []
        if inline.canUndo and view.get("can_cancel", True):
            hints.append(hint("undo", inline.cancelLabel))
        if phase == "listening":
            hints.append(hint("end", "结束"))
        if inline.canRedo:
            hints.append(hint("switch_mode", "下一步"))
        content.setProperty("statusText", inline.error or inline.status)
        content.setProperty("partialText", str(view.get("raw") or "") if not inline.error else "")
        content.setProperty("hintText", "    ".join(filter(None, hints)))
        content.setProperty("listening", phase == "listening")
        content.setProperty("uiScale", inline._hud_scale_percent / 100)
        caret = view.get("caret") or []
        target = screen_caret(caret) if len(caret) == 4 and caret[3] > caret[1] else None
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if not target and not screen:
            return
        area = (target[1] if target else screen.availableGeometry()).adjusted(6, 6, -6, -6)
        width = min(area.width(), round(content.implicitWidth()))
        height = round(content.implicitHeight())
        x, y = area.center().x() - width // 2, area.bottom() - height - 40
        if target:
            left, top, _, bottom = target[0]
            x, y = round(left), round(bottom) + 6
            if y + height > area.bottom():
                y = round(top) - height - 6
        rect = QRect(max(area.left(), min(x, area.right() + 1 - width)),
                     max(area.top(), min(y, area.bottom() + 1 - height)), width, height)
        self.window.setGeometry(rect)
        self.window.show()
        inline.voicePanelPositioned.emit(rect.getRect())
        if settled and state != self._state:
            self.dismiss.start()
        elif not settled:
            self.dismiss.stop()
        self._state = state
