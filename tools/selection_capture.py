"""Lossless SDK contact capture for the eight-direction selection study.

No gesture recognizer or distance thresholds run during collection. Device
coordinates are kept at their original scale; UI scaling is display-only.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import json
import math
from pathlib import Path


SAMPLE_PERIOD_MS = 5  # Current SDK model emits one movement per 200 Hz frame.
SHAPES = [
    ("h", "横", "向右"), ("h", "提", "左下向右上"),
    ("s", "竖", "向下"), ("s", "竖钩", "向下，末端左上钩"),
    ("p", "撇", "右上向左下"), ("n", "点", "短促向右下"), ("n", "捺", "舒展向右下"),
    ("z", "弯钩", "向下弯曲，末端钩起"), ("z", "斜钩", "向右下，末端向上钩"),
    ("z", "卧钩", "平卧弯向右，末端向左上钩"), ("z", "竖弯", "下、弯向右"),
    ("z", "横折折", "右、下、右"), ("z", "横折钩", "右、下，末端钩起"),
    ("z", "横撇", "右、左下"), ("z", "横折弯钩", "右、下、弯右、钩"),
    ("z", "竖折折钩", "下、右、下、钩"), ("z", "横折提", "右、下、右上"),
    ("z", "横折折撇", "右、下、右、左下"), ("z", "横撇弯钩", "右、左下、弯下、钩"),
    ("z", "横折弯", "右、下、弯右"), ("z", "横折折折", "右、下、右、下"),
    ("z", "横折", "右、下"), ("z", "横钩", "右，末端左下钩"),
    ("z", "竖折", "下、右"), ("z", "竖提", "下、右上"),
    ("z", "撇点", "左下、右下"), ("z", "撇折", "左下、右"),
    ("z", "竖折折", "下、右、下"), ("z", "竖弯钩", "下、弯右、钩"),
    ("z", "横折折折钩", "右、下、右、下、钩"),
    ("z", "竖折撇", "下、右、左下"), ("z", "横斜钩", "右、右下、钩"),
]


def now():
    return datetime.now(timezone.utc).isoformat()


def validate_trace(trace):
    points = trace.get("points")
    if not isinstance(points, list) or not points:
        raise ValueError("样本没有轨迹点。")
    last = -1
    for p in points:
        if not all(isinstance(p.get(k), (float, int)) and math.isfinite(p[k]) for k in ("x", "y", "t")) or p["t"] < last:
            raise ValueError("样本坐标或时间无效。")
        last = p["t"]
    if not isinstance(trace.get("rawEvents"), list) or not trace["rawEvents"]:
        raise ValueError("样本缺少原始事件。")


def atomic_save(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


class ContactCapture:
    """Reconstruct delayed SDK contact boundaries without click/shape filtering.

    Preserve early movement until down is confirmed. Release uses its original
    frame, so later lift movement never alters the measured horizontal length.
    """
    def __init__(self):
        self.reset()

    def reset(self):
        self.history = deque(maxlen=128)
        self.start = self.end = self.last_step = None
        self.moves = []
        self.edges = []

    def feed(self, event):
        kind = event.get("kind")
        if kind == "contact" and event.get("state") == "reset":
            self.reset()
            return "信号重置；本笔未保存"
        if kind == "move":
            step = event["step"]
            if not all(math.isfinite(event[k]) for k in ("dx", "dy", "timestamp")):
                self.reset()
                return "无效坐标；本笔未保存"
            if self.last_step is not None and step <= self.last_step:
                return None
            gap = self.last_step is not None and step != self.last_step + 1
            if gap:
                self.reset()
            self.last_step = step
            self.history.append(event.copy())
            if self.start is not None and step >= self.start:
                self.moves.append(event.copy())
                if len(self.moves) > 4000:
                    self.reset()
                    return "接触超过 20 秒；请抬手重采"
            result = self._finish()
            return result or ("轨迹缺帧；本笔未保存" if gap else None)
        if kind == "contact" and event.get("state") == "down":
            self.start, self.end = event["step"], None
            self.edges = [event.copy()]
            self.moves = [e for e in self.history if e["step"] >= self.start]
        elif kind == "contact" and event.get("state") == "up" and self.start is not None:
            self.end = event["step"]
            self.edges.append(event.copy())
            return self._finish()
        return None

    def _finish(self):
        if self.end is None or self.last_step is None or self.last_step < self.end - 1:
            return None
        start, end = self.start, self.end
        moves = [e for e in self.moves if start <= e["step"] < end]
        edges = self.edges
        self.start = self.end = None
        self.moves, self.edges = [], []
        if not moves or moves[0]["step"] != start or len(moves) != end - start:
            return "接触边界不完整；请重采"
        x = y = 0.
        points = []
        length = 0.
        for i, event in enumerate(moves):
            if i:  # Same first-frame convention as the production collector.
                x += event["dx"]
                y += event["dy"]
                length += math.hypot(event["dx"], event["dy"])
            points.append(dict(x=x, y=y, t=(event["step"]-start)*SAMPLE_PERIOD_MS,
                               step=event["step"], arrivalTime=event["timestamp"]))
        duration = (end - start) * SAMPLE_PERIOD_MS
        points.append(dict(x=x, y=y, t=duration, step=end, arrivalTime=edges[-1]["timestamp"]))
        return dict(points=points, rawEvents=edges[:1]+moves+edges[1:], startStep=start, endStep=end,
                    durationMs=duration, pathLength=length, capturedAt=now(),
                    quality={"complete": True, "shortContact": duration < 30,
                             "stationary": length == 0})

    def preview(self):
        x = y = 0.
        points = [(x, y)]
        for e in self.moves[1:]:
            x += e["dx"]
            y += e["dy"]
            points.append((x, y))
        return points


def integration_error(trace):
    moves = [e for e in trace["rawEvents"] if e.get("kind") == "move"]
    if len(trace["points"]) != len(moves)+1:
        raise ValueError("事件数与轨迹点数不一致。")
    x = y = error = 0.
    for i, event in enumerate(moves):
        if i:
            x += event["dx"]
            y += event["dy"]
        point = trace["points"][i]
        error = max(error, abs(point["x"]-x), abs(point["y"]-y))
    return max(error, abs(trace["points"][-1]["x"]-x), abs(trace["points"][-1]["y"]-y))

