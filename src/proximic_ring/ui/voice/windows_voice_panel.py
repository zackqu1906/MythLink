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
        self._last_caret = None
        inline.changed.connect(self.refresh)
        parent.aboutToQuit.connect(self.window.close)

    def refresh(self):
        inline = self.inline
        view = inline._view
        phase = view.get("phase", "idle")
        preview = str(view.get("voice_preview") or "")
        settled = phase in {"dictated", "edited", "undone", "interrupted", "error"} and not preview
        if (not inline._utterance_id and not (phase == "error" and self._state)) or phase == "idle":
            self.dismiss.stop()
            self.window.hide()
            self._state = None
            self._last_caret = None
            return
        state = (inline._utterance_id, phase, view.get("revision"), inline.error, preview)
        if settled and state == self._state and not self.window.isVisible():
            return
        content = self.window.rootObject()
        actions = []
        def add(action, label, gestures=None):
            assigned = [g for g in (gestures if gestures is not None else inline._gesture_hints.get(action, []))
                        if g in GESTURE_LABELS]
            if assigned:
                names = " / ".join(GESTURE_LABELS[g].split("（")[0] for g in assigned)
                actions.append(dict(label=label, gestures=assigned, description=names + " · " + label))
        pending = phase in {"listening", "finishing"} and not view.get("edit_requested")
        writing = view.get("awaiting_readback")
        if inline.canUndo and view.get("can_cancel", True):
            add("undo", "取消" if inline.cancelLabel == "取消" else "撤销")
        if inline.canConvert:
            add("switch_mode", "修改")
        elif inline.canHistoryForward:
            add("switch_mode", "重做" if inline.canRetryEdit else "下一步")
        if preview:
            add("polish", "采纳", ["swipe-down"])
        elif not inline.session_automatic and not inline.automatic_polish and (
                (pending and "can_polish" in view) or inline.canPolish):
            add("polish", "润色")
        if not writing and (pending or (phase in {"dictated", "edited"} and view.get("raw") and not inline.error)):
            add("send", "发送")
        if phase == "listening":
            add("end", "结束")
        busy = bool(not inline.error and (phase in {"starting", "finishing", "editing", "polishing"} or writing
                                          or (phase == "listening" and view.get("automatic_stage"))))
        icon = ("status-error" if inline.error else "processing-spinner" if busy else
                "listening-waveform" if phase == "listening" else "status-done")
        title = inline.error or inline.status
        if phase == "listening" and inline.session_basic_dictation and not inline.error:
            title = "基础听写 · 当前窗口使用旧版输入法"
        elif phase == "listening" and not (inline.session_automatic or inline.error or view.get("raw")):
            title = "听写 · 可以说话"
        elif phase == "dictated" and not (inline.error or view.get("raw")):
            title = "未识别到语音"
        content.setProperty("statusText", title)
        content.setProperty("statusIcon", icon)
        content.setProperty("processing", busy)
        content.setProperty("actions", actions)
        content.setProperty("suggestion", preview)
        content.setProperty("uiScale", inline._hud_scale_percent / 100)
        caret = view.get("caret") or []
        target = screen_caret(caret) if len(caret) == 4 and caret[3] > caret[1] else None
        same_sentence = self._state and self._state[0] == inline._utterance_id
        if same_sentence and (not target or busy) and self._last_caret:
            target = self._last_caret
        self._last_caret = target
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
