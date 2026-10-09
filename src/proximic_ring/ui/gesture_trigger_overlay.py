"""Stacked recognition toasts on one non-activating Gesture HUD canvas."""
import math
from pathlib import Path
import time

from PySide6.QtCore import QObject, QRect, QTimer, Qt, QUrl, Slot
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtQuick import QQuickView

from ..gesture_settings import GESTURE_LABELS
from .gesture_hud import hud_screen, HUD_MARGIN
from .overlay_stacking import OverlayVisibilityGuard


TRIGGER_DURATION_MS = 1000
TRIGGER_WIDTH, TRIGGER_HEIGHT = 248, 72
TRIGGER_GAP = 8
TRIGGER_LABELS = {**GESTURE_LABELS, "click": "单击", "double-click": "双击"}


def trigger_geometry(area, count=1):
    width = max(1, min(TRIGGER_WIDTH, area.width() - 2 * HUD_MARGIN))
    stack_height = max(1, count) * (TRIGGER_HEIGHT + TRIGGER_GAP) - TRIGGER_GAP
    height = max(1, min(stack_height, area.height() - 2 * HUD_MARGIN))
    return QRect(area.right() + 1 - width - min(HUD_MARGIN, max(0, area.width() - width)),
                 area.bottom() + 1 - height - min(HUD_MARGIN, max(0, area.height() - height)),
                 width, height)


class GestureTriggerOverlay(QObject):
    def __init__(self, controller, parent=None, *, diagnostic=None):
        super().__init__(parent)
        self.controller = controller
        self._diagnostic = diagnostic or (lambda _message: None)
        self.window = None
        self._closed = False
        self._entries = []
        self._next_serial = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setTimerType(Qt.PreciseTimer)
        self._timer.timeout.connect(self._expire)
        controller.enabledChanged.connect(self._prepare)
        controller.triggered.connect(self.show_gestures)
        controller.hideRequested.connect(self.hide)
        QGuiApplication.instance().aboutToQuit.connect(self.close)
        self._prepare()

    @Slot()
    def _prepare(self):
        # Load once on enabling, never on a firmware/output worker or per gesture.
        if self._closed or not self.controller.enabled or self.window is not None:
            return
        window = QQuickView()
        window.setObjectName("gestureTriggerWindow")
        window.setTitle("手势触发提示")
        window.setFlags(Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                        | Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput)
        window.setColor(QColor("transparent"))
        window.setResizeMode(QQuickView.SizeRootObjectToView)
        window.setSource(QUrl.fromLocalFile(str(Path(__file__).parent / "qml/GestureTriggerToast.qml")))
        if window.status() == QQuickView.Error:
            self._diagnostic("[GESTURE TRIGGER] 浮窗加载失败：" + "\n".join(map(str, window.errors())))
            window.close()
            window.deleteLater()
            return
        self.window = window
        window.rootObject().setProperty("cardHeight", TRIGGER_HEIGHT)
        window.rootObject().setProperty("cardGap", TRIGGER_GAP)
        self._visibility_guard = OverlayVisibilityGuard(window, self, level_offset=1,
            diagnostic=lambda exc: self._diagnostic(f"[GESTURE TRIGGER] 浮窗显示失败：{exc}"))

    @Slot(list)
    def show_gestures(self, names):
        if self._closed or not self.controller.enabled or self.window is None or not names:
            return
        try:
            now = time.monotonic()
            self._entries = [(row, deadline) for row, deadline in self._entries if deadline > now]
            for name in names:
                self._next_serial += 1
                self._entries.append((dict(serial=self._next_serial,
                    label=TRIGGER_LABELS.get(name, name).split("（")[0], name=name),
                    now + TRIGGER_DURATION_MS / 1000))
            screen = hud_screen(self._diagnostic)
            if screen is not None:
                self.window.setScreen(screen)
            self._render()
            self.window.show()
            # Same fullscreen/Space handling as Gesture HUD, one level above
            # sibling overlays so their visibility guards cannot cover this toast.
            if QGuiApplication.platformName() == "cocoa":
                self._visibility_guard.refresh()
            else:
                self.window.raise_()
        except Exception as exc:
            self.hide()
            self._diagnostic(f"[GESTURE TRIGGER] 浮窗显示失败：{exc}")

    def _render(self):
        if not self._entries:
            self.hide()
            return
        screen = self.window.screen()
        if screen is not None:
            self.window.setGeometry(trigger_geometry(screen.availableGeometry(), len(self._entries)))
        now = time.monotonic()
        # Stable identities let QML retain cards and their animations. Only new
        # cards consume lifetimeMs; later arrivals cannot restart an old fade.
        self.window.rootObject().setProperty("gestures", [
            dict(row, lifetimeMs=max(1, math.ceil((deadline - now) * 1000)))
            for row, deadline in self._entries])
        self._schedule_expiry()

    def _schedule_expiry(self):
        # One timer tracks the oldest deadline. New arrivals never extend it.
        remaining = self._entries[0][1] - time.monotonic()
        self._timer.start(max(1, math.ceil(remaining * 1000)))

    @Slot()
    def _expire(self):
        now = time.monotonic()
        self._entries = [(row, deadline) for row, deadline in self._entries if deadline > now]
        self._render()

    @Slot()
    def hide(self):
        self._timer.stop()
        self._entries.clear()
        if self.window is not None:
            self.window.hide()
            self.window.rootObject().setProperty("gestures", [])

    @Slot()
    def close(self):
        self._closed = True
        self.hide()
        if self.window is not None:
            self.window.close()
