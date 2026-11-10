"""Bounded, in-memory signal history. Display telemetry never drives locking."""
from collections import deque
import math
import time

from PySide6.QtCore import QObject, Property, Signal, Slot


class ProximityTimeline(QObject):
    changed = Signal()
    WINDOW_MS = 60_000
    MAX_POINTS = 1200
    LABELS = {
        "normal": "未达到远离条件", "away": "弱信号确认",
        "countdown": "远离已确认", "locking": "正在锁屏", "locked": "已锁屏",
        "protected": "操作保护中", "calibrating": "手动校准中",
        "disconnected": "已断连 · 暂停锁屏", "stale": "等待新读数",
        "paused": "监测已暂停", "unknown": "等待判定",
    }

    def __init__(self, parent=None, *, clock=time.monotonic):
        super().__init__(parent)
        self._clock = clock
        self._points = deque(maxlen=self.MAX_POINTS)
        self._context = dict(baseline=None, threshold=None, state="paused", countdown=0,
                             weak_samples=0, required=2, reason="")

    @Property("QVariantList", notify=changed)
    def points(self):
        return [dict(point) for point in self._points]

    @Property(str, notify=changed)
    def state(self):
        return self._context["state"]

    @Property(str, notify=changed)
    def stateLabel(self):
        state = self.state
        if state == "away":
            return f"弱信号确认 {self._context['weak_samples']} / {self._context['required']}"
        if state == "countdown":
            return f"远离已确认 · {self._context['countdown']} 秒后锁屏"
        if state == "locked":
            return "已锁屏 · 远离触发" if self._context["reason"] == "weak_signal" else "已锁屏 · 系统或手动"
        return self.LABELS[state]

    @Slot(result=float)
    def clockNow(self):
        return self._clock() * 1000

    def reset(self):
        self._points.clear()
        self._context = dict(baseline=None, threshold=None, state="paused", countdown=0,
                             weak_samples=0, required=2, reason="")
        self.changed.emit()

    @staticmethod
    def valid_number(value):
        return type(value) in (int, float) and math.isfinite(value)

    def record(self, values, *, sample=False, at=None):
        context = dict(self._context)
        for key in ("baseline", "threshold"):
            if key in values:
                value = values[key]
                context[key] = value if self.valid_number(value) and -110 <= value <= -20 else None
        if values.get("state") in self.LABELS:
            context["state"] = values["state"]
        for key, upper in (("countdown", 5), ("weak_samples", 60), ("required", 60)):
            value = values.get(key)
            if type(value) is int and 0 <= value <= upper:
                context[key] = value
        if "reason" in values:
            context["reason"] = values["reason"] if values["reason"] == "weak_signal" else ""
        raw, smoothed = values.get("raw"), values.get("smoothed")
        sample = bool(sample and all(self.valid_number(v) and -110 <= v < 0 for v in (raw, smoothed)))
        if not sample and context == self._context and self._points:
            return
        self._context = context
        now = self.clockNow()
        recorded_at = min(now, at) if self.valid_number(at) else now
        if self._points:
            recorded_at = max(self._points[-1]["time"], recorded_at)
        # Retain one predecessor for step boundaries at the left edge.
        while len(self._points) > 1 and self._points[1]["time"] < now - self.WINDOW_MS:
            self._points.popleft()
        self._points.append(dict(context, time=recorded_at, sample=sample,
                                 raw=raw if sample else None, smoothed=smoothed if sample else None))
        self.changed.emit()
