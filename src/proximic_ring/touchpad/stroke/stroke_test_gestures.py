"""Exploratory contact gestures with local review of tentative clear gestures.

Selection parameters are a conservative geometric starting point, not a trained
probability model. Deletion uses one continuous left-then-right contact.
"""
import math

from .stroke_clear_review import review_clear

DELETE = dict(minPartLength=5., minPartFraction=.20, maxHorizontalError=30.,
              maxRetraceAngleError=10., minStraightness=.85,
              minDirectionConsistency=.90, horizontalShare=.90)
CLEAR = dict(reversalFraction=.25, minReversal=1., minSpan=5., horizontalShare=.75)
SELECTION = dict(minPartLength=3., minPartFraction=.10, maxUpError=20.,
                 minStraightness=.80, minTurn=25., minChordGain=.025)
DELETION_SOURCE = 'left-right-geometric-v3'
TARGETS = (('right', 0, 4), ('down-right', 45, 2), ('down', 90, 0),
           ('down-left', 135, 1), ('left', 180, 3),
           ('up-left', -135, '，'), ('up-right', -45, '。'))


def angle_error(a, b):
    return (a-b+180) % 360-180


def horizontal_segments(values, fraction=.25, minimum=1.):
    threshold = max(minimum, (max(values)-min(values))*fraction)
    start = extreme = values[0]
    direction = count = 0
    for value in values[1:]:
        if direction == 0:
            if abs(value-start) >= threshold:
                direction = 1 if value > start else -1
                extreme, count = value, 1
        elif direction > 0:
            if value > extreme: extreme = value
            elif extreme-value >= threshold:
                count += 1
                direction, extreme = -1, value
        else:
            if value < extreme: extreme = value
            elif value-extreme >= threshold:
                count += 1
                direction, extreme = 1, value
    return count


def geometry(points):
    lengths = [0.]
    horizontal = 0.
    for a, b in zip(points, points[1:]):
        lengths.append(lengths[-1]+math.dist(a, b))
        horizontal += abs(b[0]-a[0])
    dx, dy = points[-1][0]-points[0][0], points[-1][1]-points[0][1]
    net = math.hypot(dx, dy)
    return dict(dx=dx, dy=dy, length=lengths[-1], cumulative=lengths,
                netAngle=math.degrees(math.atan2(dy, dx)) if net else None,
                leftAngle=math.degrees(math.atan2(dy, -dx)) if net else None,
                straightness=net/lengths[-1] if lengths[-1] else 0.,
                horizontalShare=horizontal/lengths[-1] if lengths[-1] else 0.,
                leftConsistency=-dx/horizontal if horizontal else 0.,
                xSpan=max(p[0] for p in points)-min(p[0] for p in points),
                segments=horizontal_segments([p[0] for p in points]))


def selection(points, metrics):
    length, cumulative = metrics['length'], metrics['cumulative']
    if length <= 0: return None
    best = None
    for i in range(1, len(points)-1):
        a, pivot, b = points[0], points[i], points[-1]
        first, second = cumulative[i], length-cumulative[i]
        if min(first, second) < max(SELECTION['minPartLength'], length*SELECTION['minPartFraction']): continue
        chord1, chord2 = math.dist(a, pivot), math.dist(pivot, b)
        if min(chord1/first, chord2/second) < SELECTION['minStraightness']: continue
        up = math.degrees(math.atan2(pivot[1]-a[1], pivot[0]-a[0]))
        direction = math.degrees(math.atan2(b[1]-pivot[1], b[0]-pivot[0]))
        if abs(angle_error(up, -90)) > SELECTION['maxUpError']: continue
        turn = abs(angle_error(direction, up))
        gain = (chord1+chord2-math.hypot(metrics['dx'], metrics['dy']))/length
        if turn < SELECTION['minTurn'] or gain < SELECTION['minChordGain']: continue
        # The explicitly upward first run is this contact's direction reference.
        # Correct only the menu direction, never rotate handwriting or SDK data.
        reference_offset = angle_error(up, -90)
        selection_angle = angle_error(direction-reference_offset, 0)
        # Up is reserved. Reject its sector instead of assigning its nearest other direction.
        if abs(angle_error(selection_angle, -90)) <= 22.5: continue
        name, target_angle, target = min(TARGETS, key=lambda t: abs(angle_error(selection_angle, t[1])))
        if abs(angle_error(selection_angle, target_angle)) > 22.5: continue
        row = dict(action='select' if isinstance(target, int) else 'punctuation',
                   direction=name, target=target, splitIndex=i, firstAngle=up,
                   secondAngle=direction, selectionAngle=selection_angle,
                   referenceOffset=reference_offset, turn=turn, chordGain=gain,
                   firstLength=first, secondLength=second)
        if best is None or gain > best['chordGain']: best = row
    return best


def deletion(points, metrics):
    """One left run then one right run; pauses and minor jitter are allowed."""
    # Compare both runs along the same axis, reversing the first vector.
    # An inclined out-and-back can delete; a pie-fold's changed axis cannot.
    if metrics['segments'] != 2 or metrics['horizontalShare'] < DELETE['horizontalShare']:
        return None
    split = min(range(len(points)), key=lambda i: points[i][0])
    if split == 0 or split == len(points)-1:
        return None
    parts = []
    for run, direction in ((points[:split+1], -1), (points[split:], 1)):
        part = geometry(run)
        travel = direction*part['dx']
        if travel < max(DELETE['minPartLength'], metrics['length']*DELETE['minPartFraction']):
            return None
        error = abs(math.degrees(math.atan2(part['dy'], travel)))
        horizontal = sum(abs(b[0]-a[0]) for a, b in zip(run, run[1:]))
        consistency = travel/horizontal if horizontal else 0.
        if (error > DELETE['maxHorizontalError']
                or part['straightness'] < DELETE['minStraightness']
                or consistency < DELETE['minDirectionConsistency']):
            return None
        parts.append(dict(length=part['length'], horizontalTravel=travel,
                          angleError=error, straightness=part['straightness'],
                          directionConsistency=consistency,
                          axisAngle=math.degrees(math.atan2(direction*part['dy'], travel))))
    retrace_error = abs(angle_error(parts[0]['axisAngle'], parts[1]['axisAngle']))
    if retrace_error > DELETE['maxRetraceAngleError']:
        return None
    return dict(direction='left-right', splitIndex=split, parts=parts,
                retraceAngleError=retrace_error, policy=DELETION_SOURCE)


def recognize(points, *, context_assist=None, before_text='', stroke_code='',
              stroke_dictionary=None, clear_review_enabled=True, legacy_selection=True):
    # Legacy language-assist arguments remain accepted for replay callers, but
    # cannot authorize single-left deletion under the left-right policy.
    if len(points) < 2 or any(len(p) != 2 or not all(math.isfinite(v) for v in p) for p in points):
        return dict(action='ignore', reason='invalid-trajectory')
    metrics = geometry(points)
    selected = selection(points, metrics) if legacy_selection else None
    if selected: return selected
    if (legacy_selection and metrics['netAngle'] is not None and abs(angle_error(metrics['netAngle'], -90)) <= 20
            and metrics['straightness'] >= .8):
        return dict(action='ignore', reason='up-reserved')
    delete_gesture = deletion(points, metrics)
    if (metrics['xSpan'] >= CLEAR['minSpan'] and metrics['segments'] >= 3
            and metrics['horizontalShare'] >= CLEAR['horizontalShare']):
        action = 'clear-all'
    elif delete_gesture is not None:
        action = 'delete-one'
    else:
        action = 'stroke'
    baseline = action
    clear_review = None
    if action == 'clear-all' and clear_review_enabled:
        clear_review = review_clear(points, stroke_dictionary)
        if clear_review['protect']:
            action = 'stroke'
    return dict(action=action, baselineAction=baseline, context=None,
                segments=metrics['segments'], leftAngle=metrics['leftAngle'],
                clearReview=clear_review, deleteGesture=delete_gesture)
