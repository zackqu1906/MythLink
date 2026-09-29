"""Bounded live QML images. Frames never go through JSON, disk, logs or ASR."""
import os
import sys
import threading

from PySide6.QtCore import QObject, Property, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QImage
from PySide6.QtQuick import QQuickImageProvider

from ..window_capture import MAX_STREAMS, PREVIEW_FPS


class PreviewImages(QQuickImageProvider):
    def __init__(self):
        super().__init__(QQuickImageProvider.Image)
        self.images = {}
    def requestImage(self, identifier, size, requestedSize):
        image = self.images.get(identifier.split("/")[0], QImage())
        size.setWidth(image.width())
        size.setHeight(image.height())
        return image


class WindowPreviews(QObject):
    changed = Signal()
    frameReady = Signal(str, str)
    cleared = Signal()
    permissionRequested = Signal()
    _prepared = Signal(object)
    _notice = Signal(int, str)
    _authorization = Signal(bool)

    def __init__(self, parent=None, *, factory=None, enabled=None):
        super().__init__(parent)
        self._enabled = (sys.platform == "darwin" and os.environ.get("PROXIMIC_STARTUP_PROBE") != "1"
                         if enabled is None else enabled)
        self._factory = factory
        self.provider = PreviewImages()
        self._engine = None
        self._preparing = False
        self._active = self._closed = False
        self._epoch = 0
        self._cards = []
        self._first, self._last, self._selected = 0, 8, 0
        self._targets = []
        self._state = "loading"
        self._lock = threading.Lock()
        self._pending = {}
        self._sequence = 0
        self._timer = QTimer(self)
        self._timer.setInterval(round(1000 / PREVIEW_FPS))
        self._timer.timeout.connect(self.flush)
        self._prepared.connect(self._receive_engine, Qt.QueuedConnection)
        self._notice.connect(self._set_status, Qt.QueuedConnection)
        self._authorization.connect(self._authorized, Qt.QueuedConnection)
        if self._enabled: self.prepare()

    @Property(str, notify=changed)
    def status(self): return self._state
    @Property(str, notify=changed)
    def hint(self):
        return {"loading": "正在连接窗口预览", "permission": "开启屏幕录制权限后，可查看窗口实时内容",
                "unavailable": "部分窗口暂不能预览，仍可正常选择", "live": "实时窗口预览 · 仅在此界面显示期间更新"}.get(self._state, "")

    def prepare(self):
        if self._closed or self._preparing or self._engine is not None or not self._enabled: return
        self._preparing = True
        def work():
            try:
                from ..window_capture import WindowCapture
                engine = (self._factory or WindowCapture)()
            except Exception:
                engine = None
            if self._closed:
                if engine: engine.close()
                return
            self._prepared.emit(engine)
        threading.Thread(target=work, name="RingPreviewWarmup", daemon=True).start()

    @Slot(object)
    def _receive_engine(self, engine):
        self._preparing = False
        if self._closed:
            if engine: engine.close()
            return
        self._engine = engine
        if self._active: self._begin()

    def start(self, cards, selected):
        self.stop()
        self._active = True
        self._cards = list(cards)
        self._selected = selected
        self._state = "loading"
        self.changed.emit()
        self._timer.start()
        self.prepare()
        if not self._preparing: self._begin()

    def _wanted(self):
        indices = [self._selected, *range(self._first, min(len(self._cards), self._last))]
        indices = list(dict.fromkeys(i for i in indices if 0 <= i < len(self._cards)))[:MAX_STREAMS]
        return [self._cards[i] for i in indices if self._cards[i].get("number")]

    def _begin(self):
        if not self._active: return
        if self._engine is None:
            self._set_status(self._epoch, "unavailable")
            return
        epoch = self._epoch
        self._targets = self._wanted()
        self._engine.start(self._targets,
                           lambda key, image: self._offer(epoch, key, image),
                           lambda status: self._notice.emit(epoch, status))

    @Slot(int, int)
    def setVisibleRange(self, first, last):
        self._first, self._last = max(0, first), max(first, last)
        self._update()

    def select(self, index):
        self._selected = index
        self._update()

    def _update(self):
        if not self._active or not self._engine: return
        targets = self._wanted()
        if targets == self._targets: return
        self._targets = targets
        keep = {c["id"] for c in targets}
        with self._lock:
            self._pending = {key: image for key, image in self._pending.items() if key in keep}
        self.provider.images = {key: image for key, image in self.provider.images.items() if key in keep}
        self._engine.update(targets)

    def _offer(self, epoch, key, image):
        with self._lock:
            if (self._active and self._state != "permission" and epoch == self._epoch
                    and any(c["id"] == key for c in self._targets)):
                self._pending[key] = image  # One newest frame per visible card, never a FIFO.

    @Slot()
    def flush(self):
        with self._lock:
            frames, self._pending = self._pending, {}
        if not self._active: return
        for key, image in frames.items():
            self.provider.images[key] = image
            self._sequence += 1
            self.frameReady.emit(key, f"image://windowPreviews/{key}/{self._sequence}")
        if frames and self._state not in {"live", "permission"}:
            self._set_status(self._epoch, "live")

    @Slot(int, str)
    def _set_status(self, epoch, state):
        if not self._active or epoch != self._epoch: return
        if state != self._state:
            self._state = state
            if state == "permission":
                if self._engine: self._engine.stop()
                with self._lock: self._pending.clear()
                self.provider.images.clear()
                self.cleared.emit()
            self.changed.emit()

    @Slot()
    def requestPermission(self):
        if not self._enabled: return
        self.permissionRequested.emit()  # Dismiss before the system dialog/settings gains focus.
        def request():
            try:
                from ..mac_permissions import request_screen_capture_access
                granted = request_screen_capture_access()
            except Exception:
                granted = False
            if not self._closed: self._authorization.emit(granted)
        threading.Thread(target=request, name="RingScreenPermission", daemon=True).start()

    @Slot(bool)
    def _authorized(self, granted):
        if not granted:
            QDesktopServices.openUrl(QUrl("x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"))

    def stop(self):
        with self._lock:
            self._active = False
            self._epoch += 1
            self._pending.clear()
        self._timer.stop()
        if self._engine: self._engine.stop()
        self._cards = self._targets = []
        self.provider.images.clear()
        self.cleared.emit()

    def close(self):
        self._closed = True
        self.stop()
        if self._engine: self._engine.close()
