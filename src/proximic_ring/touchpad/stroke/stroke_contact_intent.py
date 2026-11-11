"""Application-only arbitration of one complete, contiguous 200 Hz contact.

Replay the same prefixes for live feedback and release decisions. Both landing
and a pause after movement can arm selection. A selected contact is consumed in
full; its preceding motion is never added to the pending handwriting.
"""
import math

from .stroke_hold_selection import TARGETS


CONTACT_INTENT = dict(policy='contact-intent-v2', sampleMs=5, holdMs=333,
    settleMs=80, settleRadius=4., settlePath=6., stationaryRadius=1.5,
    motionWindowMs=100, stationaryWindowPath=6., motionEpsilon=.025,
    tapQuietMs=15, tapPath=10., tapRadius=5., tapCompactRadius=2.,
    tapTailMs=20, tapTailRadius=.35, tapTailPath=1.2, minStrokePath=1.5,
    selectDistance=3.)


def classify_contact(points, *, finished=False, hold_select=True):
    """Return a UI phase; only finished contacts have an action.

    Compact contacts favor tapping; larger motion needs a quiet release tail.
    A stationary pause anywhere in a contact may arm selection. Identical
    observations cannot reveal intent, so these limits remain explicit policy.
    The caller must verify continuity and trim at the confirmed release frame.
    hold_select=False disables pause/hold arming: contacts only tap or write.
    """
    cfg = CONTACT_INTENT
    result = dict(policy=cfg['policy'], phase='idle', progress=0, direction=None)
    try:
        valid = bool(points) and all(len(p) == 2 and all(math.isfinite(v) for v in p) for p in points)
    except (TypeError, ValueError):
        valid = False
    if not valid:
        result.update(reason='invalid-geometry')
        if finished:
            result['action'] = 'reject'
        return result

    sample_ms = cfg['sampleMs']
    distances = [0.] + [math.dist(a, b) for a, b in zip(points, points[1:])]
    cumulative = [0.]
    for distance in distances[1:]:
        cumulative.append(cumulative[-1] + distance)
    radius = max(math.dist(points[0], point) for point in points)
    if not all(math.isfinite(value) for value in (*cumulative, radius)):
        result.update(reason='invalid-geometry')
        if finished:
            result['action'] = 'reject'
        return result
    result.update(path_length=cumulative[-1], radius=radius,
                  duration_ms=len(points)*sample_ms)
    anchor = last_motion = 0
    settling = False
    window_frames = cfg['motionWindowMs']//sample_ms
    writing_at = armed_at = None
    for index, point in enumerate(points):
        elapsed = index*sample_ms
        window_path = cumulative[index] - cumulative[max(anchor, index-window_frames)]
        if writing_at is None and elapsed <= cfg['settleMs']:
            if (math.dist(points[0], point) > cfg['settleRadius']
                    or cumulative[index] > cfg['settlePath']):
                writing_at = index
                anchor = index
            # Contacts already inside the original stationary envelope keep
            # their hold timing. Only an actual landing excursion resets it.
            settling = settling or math.dist(points[0], point) > cfg['stationaryRadius']
            if settling and distances[index] >= cfg['motionEpsilon']:
                anchor = index
        elif (math.dist(points[anchor], point) > cfg['stationaryRadius']
              or window_path > cfg['stationaryWindowPath']):
            if writing_at is None:
                writing_at = index
            # Rebase on motion rather than locking this contact as handwriting.
            # This also excludes earlier ink from the stationary path window.
            anchor = index
        if distances[index] >= cfg['motionEpsilon']:
            last_motion = index
        if hold_select and (index-anchor)*sample_ms >= cfg['holdMs']:
            armed_at = index
            break

    result['settle_ms'] = anchor*sample_ms
    if armed_at is not None:
        dx, dy = points[-1][0]-points[armed_at][0], points[-1][1]-points[armed_at][1]
        distance = math.hypot(dx, dy)
        result.update(phase='selecting', progress=100,
                      reason='pause-hold' if writing_at is not None else 'stationary-hold',
                      hold_ms=armed_at*sample_ms, distance=distance)
        if distance >= cfg['selectDistance']:
            sector = int((math.degrees(math.atan2(dy, dx)) + 22.5) % 360 // 45)
            direction, target = TARGETS[sector]
            result.update(direction=direction, target=target)
        if finished:
            target = result.get('target')
            result['action'] = ('select' if isinstance(target, int) else
                                'page' if target == 'next-page' else
                                'punctuation' if target is not None else 'cancel-selection')
        return result

    quiet_ms = (len(points)-1-last_motion)*sample_ms
    paused = quiet_ms >= cfg['tapQuietMs']
    result.update(phase='holding' if writing_at is None or paused else 'writing',
                  reason='awaiting-release-or-hold' if writing_at is None else 'motion-pause' if paused else 'sustained-motion', quiet_ms=quiet_ms,
                  progress=min(100, (len(points)-1-anchor)*sample_ms*100/cfg['holdMs']) if hold_select else 0)
    if finished:
        path = cumulative[-1]
        compact = radius < cfg['tapCompactRadius']
        tail_start = max(0, len(points)-1-cfg['tapTailMs']//sample_ms)
        tail = points[tail_start:]
        quiet_tail = ((len(points)-1)*sample_ms >= cfg['tapTailMs']
                      and cumulative[-1]-cumulative[tail_start] <= cfg['tapTailPath']
                      and max(math.dist(tail[0], point) for point in tail) <= cfg['tapTailRadius'])
        if (compact or (path <= cfg['tapPath'] and radius <= cfg['tapRadius']
                and (path < cfg['minStrokePath'] or quiet_ms >= cfg['tapQuietMs'] or quiet_tail))):
            result.update(action='tap', reason='stationary-release')
        elif len(points) >= 5 and path >= cfg['minStrokePath']:
            result.update(phase='writing', progress=0, action='stroke', reason='moving-release')
        else:
            result.update(action='reject', reason='uncertain-motion')
    return result
