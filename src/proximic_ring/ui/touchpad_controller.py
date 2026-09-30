"""GUI lifecycle for the existing Ring session; high-rate events never enter Qt."""
from __future__ import annotations

import math
import sys
import time
from types import SimpleNamespace

from PySide6.QtCore import QObject, Property, Qt, Signal, Slot, QTimer

from ..touchpad_mouse import TouchpadMouseOutput


class TouchpadController(QObject):
    changed = Signal()
    sourceReady = Signal(object, object)
    _event = Signal(object, str, object)

    def __init__(self, owner, *, output_factory=TouchpadMouseOutput):
        super().__init__(owner)
        self.owner, self.output_factory = owner, output_factory
        self._source = self._connection = self._run = None
        self._closed = False
        self._state = "idle"
        self._message = "开启后，用 Ring 移动系统指针并轻触点击。"
        self._gain = self._number("gain", 1., .25, 3.)
        self._seconds = int(self._number("seconds", 90, 30, 600))
        if self._seconds not in (30, 90, 180, 300, 600): self._seconds = 90
        self._clicks = owner._settings.value("touchpad/clicks", True, type=bool)
        self._invert_y = owner._settings.value("touchpad/invertY", False, type=bool)
        self._warm = False
        self.sourceReady.connect(self.attach, Qt.QueuedConnection)
        self._event.connect(self._receive, Qt.QueuedConnection)
        owner.connectedChanged.connect(self._connection_changed)
        owner.busyChanged.connect(self.changed)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.changed)

    def _number(self, key, default, low, high):
        try:
            value = float(self.owner._settings.value("touchpad/" + key, default))
            return max(low, min(high, value)) if math.isfinite(value) else default
        except (ValueError, TypeError):
            return default

    @Property(bool, constant=True)
    def supported(self): return sys.platform == "darwin"

    @Property(bool, notify=changed)
    def available(self):
        return (self.supported and not self._closed and self.owner.connected
                and not self.owner.busy and self._source is not None
                and self._connection is self.owner._disconnect_event
                and not self._connection.is_set())

    @Property(bool, notify=changed)
    def active(self): return self._state in {"starting", "running"}

    @Property(bool, notify=changed)
    def busy(self): return self._state in {"starting", "stopping"}

    @Property(str, notify=changed)
    def state(self): return self._state

    @Property(str, notify=changed)
    def title(self):
        return {"starting": "正在开启触摸板", "running": "触摸板已开启",
                "stopping": "正在停止触摸板", "error": "触摸板未能运行"}.get(self._state, "触摸板已关闭")

    @Property(str, notify=changed)
    def message(self): return self._message

    @Property(int, notify=changed)
    def remaining(self):
        run = self._run
        return max(0, math.ceil(run.deadline - time.monotonic())) if run and run.deadline else 0

    @Property(float, notify=changed)
    def gain(self): return self._gain

    @gain.setter
    def gain(self, value):
        if not self._run and math.isfinite(value) and .25 <= value <= 3.:
            self._gain = round(value, 2)
            self.owner._settings.setValue("touchpad/gain", self._gain)
            self.changed.emit()

    @Property(int, notify=changed)
    def seconds(self): return self._seconds

    @seconds.setter
    def seconds(self, value):
        if not self._run and value in (30, 90, 180, 300, 600):
            self._seconds = value
            self.owner._settings.setValue("touchpad/seconds", value)
            self.changed.emit()

    @Property(bool, notify=changed)
    def clicks(self): return self._clicks

    @clicks.setter
    def clicks(self, value):
        if not self._run:
            self._clicks = bool(value)
            self.owner._settings.setValue("touchpad/clicks", self._clicks)
            self.changed.emit()

    @Property(bool, notify=changed)
    def invertY(self): return self._invert_y

    @invertY.setter
    def invertY(self, value):
        if not self._run:
            self._invert_y = bool(value)
            self.owner._settings.setValue("touchpad/invertY", self._invert_y)
            self.changed.emit()

    @Slot(object, object)
    def attach(self, source, connection):
        if self._closed or connection is not self.owner._disconnect_event or connection.is_set():
            return
        self._source, self._connection = source, connection
        self.changed.emit()

    def _connection_changed(self):
        if not self.owner.connected:
            self.stop("设备已断开")
            self._source = self._connection = None
        self.changed.emit()

    @Slot()
    def toggle(self):
        if self.active:
            self.stop()
        elif not self.busy:
            self.start()

    @Slot()
    def start(self):
        if not self.available or self._run is not None:
            return
        run = SimpleNamespace(source=self._source, output=None, deadline=None, reason="", sdk_started=False)
        self._run = run
        self._state, self._warm = "starting", False
        self._message = "正在准备鼠标控制…"
        run.output = self.output_factory(
            connection=self._connection, gain=self._gain, clicks=self._clicks, invert_y=self._invert_y,
            on_ready=lambda: self._post(run, "ready", None),
            on_end=lambda reason, error: self._post(run, "mouse_end", (reason, error)),
        )
        self.changed.emit()
        run.output.start()

    def _post(self, run, kind, value):
        try:
            self._event.emit(run, kind, value)
        except RuntimeError:
            pass  # QObject already destroyed during application shutdown.

    def _watch(self, run, future, kind):
        def done(result):
            try:
                result.result()
                error = None
            except BaseException as exc:
                error = exc
            self._post(run, kind, error)
        future.add_done_callback(done)

    @Slot()
    def stop(self, reason="已停止鼠标控制"):
        run = self._run
        if run is None or self._state == "stopping": return
        run.reason = reason
        run.output.stop()  # Synchronous gate; never wait for Quartz or MNN here.
        self._state, self._message = "stopping", "正在停止鼠标控制…"
        self._timer.stop()
        self.changed.emit()
        if not run.sdk_started:
            self._finish(run, None)
            return
        try:
            self._watch(run, run.source.stop_touchpad(), "stopped")
        except Exception as exc:
            self._finish(run, exc)

    def _finish(self, run, error):
        if run is not self._run: return
        run.output.stop()
        self._run = None
        self._timer.stop()
        error = error or getattr(run, "output_error", None)
        self._state = "error" if error else "idle"
        if isinstance(error, PermissionError):
            self._message = "请在系统设置的「隐私与安全性 → 辅助功能」中允许 MythLink 控制电脑，然后重新开启。"
        else:
            self._message = str(error) if error else run.reason or "已自动停止，可再次开启。"
        self.changed.emit()

    @Slot(object, str, object)
    def _receive(self, run, kind, value):
        if run is not self._run: return
        if kind == "ready" and self._state == "starting":
            try:
                run.sdk_started = True
                def ended(error):
                    run.output.stop()
                    self._post(run, "ended", error)
                self._watch(run, run.source.start_touchpad(
                    on_event=run.output.submit,
                    on_stats=lambda stats: self._post(run, "stats", stats),
                    on_stopped=ended,
                    duration_s=self._seconds), "started")
            except Exception as exc:
                self._finish(run, exc)
        elif kind == "started":
            if value:
                self._finish(run, value)
            elif self._state == "starting":
                run.deadline = time.monotonic() + self._seconds
                run.output.activate(self._seconds)
                self._state, self._message = "running", "准备中，稍后即可移动指针。按 Esc 随时停止。"
                self._timer.start()
                self.changed.emit()
        elif kind == "stats" and self._state == "running":
            warm = value.warmup_frames >= 200
            if warm != self._warm:
                self._warm = warm
                self._message = "用 Ring 移动指针，按 Esc 随时停止。" if warm else "信号恢复中，正在重新准备…"
                self.changed.emit()
        elif kind in {"ended", "stopped"}:
            self._finish(run, value)
        elif kind == "mouse_end" and self._state != "stopping":
            reason, error = value
            if not run.sdk_started:
                run.reason = reason
                self._finish(run, error)
            else:
                run.output_error = error
                self.stop(reason or str(error or "鼠标控制已停止"))

    def close(self):
        self._closed = True
        self.stop("鼠标控制已关闭")
