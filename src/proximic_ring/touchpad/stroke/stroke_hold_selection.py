"""Selection from confirmed, contiguous 200 Hz contact samples.

Only the initial stationary portion can arm the menu. Once handwriting starts,
a pause within that stroke cannot change its meaning. Replay the release-trimmed
points for the final decision so delayed SDK lift edges cannot select a target.
"""
import math


HOLD_SELECTION = dict(policy='contact-hold-eight-v1', holdMs=500, sampleMs=5,
                      stationaryRadius=1.5, stationaryPath=6., selectDistance=3.)
TARGETS = (('right', 4), ('down-right', 2), ('down', 0), ('down-left', 1),
           ('left', 3), ('up-left', '，'), ('up', 'next-page'), ('up-right', '。'))


def hold_selection(points, *, finished=False):
    """Return UI state and, only for an armed release, one consumed action."""
    idle = dict(phase='idle', progress=0, direction=None)
    if not points or any(len(p) != 2 or not all(math.isfinite(v) for v in p) for p in points):
        return idle
    origin = points[0]
    length = 0.
    pivot = None
    for index, point in enumerate(points):
        if index:
            length += math.dist(points[index - 1], point)
        if (math.dist(origin, point) > HOLD_SELECTION['stationaryRadius']
                or length > HOLD_SELECTION['stationaryPath']):
            return dict(phase='writing', progress=0, direction=None)
        if index * HOLD_SELECTION['sampleMs'] >= HOLD_SELECTION['holdMs']:
            pivot = point
            break
    if pivot is None:
        return idle if finished else dict(phase='holding', direction=None,
            progress=min(100, (len(points)-1)*HOLD_SELECTION['sampleMs']*100/HOLD_SELECTION['holdMs']))
    dx, dy = points[-1][0]-pivot[0], points[-1][1]-pivot[1]
    result = dict(phase='selecting', progress=100, direction=None,
                  hold_ms=index*HOLD_SELECTION['sampleMs'], distance=math.hypot(dx, dy))
    if result['distance'] >= HOLD_SELECTION['selectDistance']:
        sector = int((math.degrees(math.atan2(dy, dx)) + 22.5) % 360 // 45)
        direction, target = TARGETS[sector]
        result.update(direction=direction, target=target)
    if finished:
        target = result.get('target')
        result['action'] = ('select' if isinstance(target, int) else
                            'page' if target == 'next-page' else
                            'punctuation' if target is not None else 'cancel-selection')
    return result
