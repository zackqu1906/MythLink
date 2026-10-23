"""Two-part stroke evidence on a completed path; no sensor or contact decisions."""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Split:
    shape: str
    first: tuple
    second: tuple
    fraction: float
    fit: float


def clean_path(points):
    clean = []
    for point in points:
        if len(point) != 2 or not all(math.isfinite(float(v)) for v in point):
            return []
        point = tuple(map(float, point))
        if not clean or point != clean[-1]:
            clean.append(point)
    return clean


def _lengths(points):
    lengths = [0.]
    for a, b in zip(points, points[1:]):
        lengths.append(lengths[-1] + math.dist(a, b))
    return lengths


def resample(points, count=64):
    lengths = _lengths(points)
    if not lengths or lengths[-1] <= 0:
        return ()
    total = lengths[-1]
    result = []
    for i in range(count):
        position = total * i / (count - 1)
        j = min(max(1, bisect_left(lengths, position)), len(points)-1)
        ratio = (position-lengths[j-1])/(lengths[j]-lengths[j-1])
        a, b = points[j-1], points[j]
        result.append(((a[0]+ratio*(b[0]-a[0])-points[0][0])/total,
                       (a[1]+ratio*(b[1]-a[1])-points[0][1])/total))
    return tuple(result)


def _line_fit(points):
    a, b = points[0], points[-1]
    dx, dy = b[0]-a[0], b[1]-a[1]
    squared = dx*dx+dy*dy
    if squared <= 1e-12:
        return float('inf')
    return sum(((p[0]-a[0])*dy-(p[1]-a[1])*dx)**2/squared
               for p in points)/len(points)/squared


def splits(points):
    """Find sustained left/down->right/down and right->left/down paths.

    Fit both pieces separately. Arc-length sampling makes the short piece count,
    while original-point support prevents one noisy endpoint becoming a hook.
    """
    clean = clean_path(points)
    if len(clean) < 7:
        return {}
    raw_lengths = _lengths(clean)
    total = raw_lengths[-1]
    trace = resample(clean)
    lengths = _lengths(trace)
    best = {}
    for k in range(8, len(trace)-2):
        fraction = k/(len(trace)-1)
        tail = 1-fraction
        if tail < .035 or fraction < .25:
            continue
        raw_split = bisect_left(raw_lengths, total*fraction)
        if raw_split < 3 or len(clean)-raw_split < 4:
            continue
        u = (trace[k][0]-trace[0][0], trace[k][1]-trace[0][1])
        v = (trace[-1][0]-trace[k][0], trace[-1][1]-trace[k][1])
        lu, lv = math.hypot(*u), math.hypot(*v)
        if lu < 1e-8 or lv < 1e-8:
            continue
        if lu/lengths[k] < .90 or lv/(lengths[-1]-lengths[k]) < .90:
            continue
        first, second = trace[:k+1], trace[k:]
        fit1, fit2 = _line_fit(first), _line_fit(second)
        if max(fit1, fit2) > .006:
            continue
        x1, y1, x2, y2 = u[0]/lu, u[1]/lu, v[0]/lv, v[1]/lv
        angle = math.degrees(math.acos(max(-1., min(1., x1*x2+y1*y2))))
        shape = None
        if x1 < -.20 and y1 > .50 and x2 > .25 and y2 > .10 and angle >= 35:
            shape = '撇点'
        elif x1 > .80 and abs(y1) < .35 and x2 < -.20 and y2 > .10 and angle >= 95 and tail <= .40:
            shape = '横钩'
        if shape:
            split = Split(shape, resample(first, 16), resample(second, 16), fraction, fit1+fit2)
            if shape not in best or split.fit < best[shape].fit:
                best[shape] = split
    return best


def piece_distance(a, b):
    return sum((x-tx)**2+(y-ty)**2 for (x,y),(tx,ty) in zip(a,b))/len(a)


def compare_parts(actual, template, whole_distance):
    return (.25*whole_distance + .375*piece_distance(actual.first, template.first)
            + .375*piece_distance(actual.second, template.second)
            + .02*(actual.fraction-template.fraction)**2)


def nearly_straight(points):
    clean = clean_path(points)
    if len(clean) < 2:
        return False
    length = _lengths(clean)[-1]
    return length > 0 and math.dist(clean[0], clean[-1])/length >= .985 and _line_fit(clean) <= .0006
