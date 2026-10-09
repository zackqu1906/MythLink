"""Shared display geometry for the Qt trial editor and native stroke palette.

Recognition always receives the original sensor path. Only these display copies
are normalized; both surfaces draw the same five canonical shapes.
"""
from __future__ import annotations

import math

TRAIL_STYLE = {"color": "#082ACB", "line_width": 3.2, "hold_ms": 400, "fade_ms": 280}
STANDARD_STROKES = {
    "h": [[.18, .50], [.82, .50]],
    "s": [[.50, .18], [.50, .82]],
    "p": [[.70, .18], [.68, .31], [.64, .44], [.57, .57], [.47, .69], [.33, .81]],
    "n": [[.35, .28], [.42, .37], [.50, .48], [.58, .61], [.63, .72]],
    "z": [[.20, .28], [.80, .28], [.51, .76]],
}


def standard_stroke(category):
    return [list(point) for point in STANDARD_STROKES.get(category, [])]


def normalized_trace(points):
    points = [(float(x), float(y)) for x, y in points]
    if len(points) < 2 or any(not math.isfinite(v) for p in points for v in p):
        return []
    x0, y0 = points[0]
    extent = max(1., max(max(abs(x - x0), abs(y - y0)) for x, y in points))
    scale = min(1.6 / 140., .43 / extent)
    return [[.5 + (x - x0) * scale, .5 + (y - y0) * scale] for x, y in points]
