"""Host-owned hotword feedback, independent of the native composition window."""
import math
from pathlib import Path

from PySide6.QtCore import QObject, QRect, Qt, QTimer, QUrl, Slot
from PySide6.QtGui import QColor, QCursor, QGuiApplication
from PySide6.QtQuick import QQuickView

from ..shared.overlay_stacking import OverlayVisibilityGuard


class HotwordNotice(QObject):
    def __init__(self, hotwords, inline, parent):
        super().__init__(parent)
        self._anchor = QRect()
        self._use_voice_panel = True
        self.window = QQuickView()
        self.window.setTitle("Mythlink · 已添加热词")
        self.window.setFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                             | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        self.window.setColor(QColor("transparent"))
        self.window.setResizeMode(QQuickView.SizeRootObjectToView)
        self.window.setSource(QUrl.fromLocalFile(str(Path(__file__).parents[1] / "qml/HotwordNotice.qml")))
        if self.window.status() == QQuickView.Error:
            raise RuntimeError("热词提示加载失败：" + "\n".join(map(str, self.window.errors())))
        self._visibility = OverlayVisibilityGuard(self.window, self)
        self._dismiss = QTimer(self)
        self._dismiss.setSingleShot(True)
        self._dismiss.setTimerType(Qt.PreciseTimer)
        self._dismiss.setInterval(5000)
        self._dismiss.timeout.connect(self.window.hide)
        inline.voicePanelPositioned.connect(self._positioned)
        hotwords.learned.connect(self.show_words)
        parent.aboutToQuit.connect(self.close)

    @Slot(object)
    def _positioned(self, frame):
        if not isinstance(frame, (list, tuple)) or len(frame) != 4:
            return
        if not all(isinstance(n, (int, float)) and abs(n) <= 2**31 - 1 for n in frame):
            return
        rect = QRect(*(round(n) for n in frame))
        if rect.isEmpty():
            return
        self._anchor = rect
        if self.window.isVisible() and self._use_voice_panel:
            self._place()

    def _place(self):
        anchor = self._anchor if self._use_voice_panel else QRect()
        screen = QGuiApplication.screenAt(anchor.center()) if not anchor.isEmpty() else None
        if screen is None:
            anchor = QRect()
            screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
            if screen is None:
                return False
        area = screen.availableGeometry().adjusted(6, 6, -6, -6)
        self.window.setScreen(screen)
        root = self.window.rootObject()
        root.setProperty("widthLimit", area.width())
        width, height = math.ceil(root.implicitWidth()), math.ceil(root.implicitHeight())
        if anchor.isEmpty():
            # Match the native fallback's bottom margin and default voice panel height.
            anchor = QRect(area.center().x() - width // 2, screen.availableGeometry().bottom() + 1 - 40 - 32,
                           width, 32)
        x = anchor.left()
        y = anchor.top() - height - 6
        if y < area.top():
            y = anchor.bottom() + 1 + 6
        self.window.setGeometry(
            max(area.left(), min(x, area.right() + 1 - width)),
            max(area.top(), min(y, area.bottom() + 1 - height)), width, height)
        return True

    @Slot(object, object)
    def show_words(self, key, words):
        if not words:
            return
        self._use_voice_panel = key[1] != "desktop"
        self.window.rootObject().setProperty("message", "已添加热词 · " + "、".join(words))
        if self._place():
            self.window.show()
            if QGuiApplication.platformName() == "cocoa":
                self._visibility.refresh()
            self._dismiss.start()

    @Slot()
    def close(self):
        self._dismiss.stop()
        self.window.close()
