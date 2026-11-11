"""Shared local writing menu: five candidates, three punctuation cells, one delete.

Selection is a two-finger swipe toward a cell: the eight directions around the
centre map to the cells below; a two-finger swipe left deletes one stroke (or,
with no pending strokes, one character). The centre cell (first candidate) is
confirmed with an ordinary tap.
"""
import math

from .stroke_contact_intent import CONTACT_INTENT, classify_contact

PAGE_SIZE = 5
CANDIDATE_CELLS = (
    ('center', 1, 1, '首选'), ('right', 1, 2, '→'),
    ('down-left', 2, 0, '↙'), ('down', 2, 1, '↓'), ('down-right', 2, 2, '↘'),
)
PUNCTUATION_CELLS = (
    ('up-left', 0, '↖', '，', '逗号'), ('up', 1, '↑', '。', '句号'),
    ('up-right', 2, '↗', '？', '问号'),
)
# (direction, row, column, arrow, symbol, title, action)
ACTION_CELLS = (('left', 1, 0, '←', '⌫', '删除', 'delete-one'),)
# Two-finger travel is measured in the same pointer units as stroke points.
TWO_FINGER_SELECTION = dict(policy='two-finger-swipe-v1', selectDistance=6.)
TRIAL_SELECTION = dict(CONTACT_INTENT, policy='two-finger-five-candidates-v1',
    sourcePolicy=TWO_FINGER_SELECTION['policy'], pageSize=PAGE_SIZE, center='cancel',
    selectDistance=TWO_FINGER_SELECTION['selectDistance'],
    candidateDirections={direction: index for index, (direction, _, _, _) in enumerate(CANDIDATE_CELLS)
                         if direction != 'center'},
    punctuationDirections={direction: symbol for direction, _, _, symbol, _ in PUNCTUATION_CELLS},
    actionDirections={direction: action for direction, *_, action in ACTION_CELLS})
_SECTORS = ('right', 'down-right', 'down', 'down-left', 'left', 'up-left', 'up', 'up-right')


def _display(points):
    stride = max(1, (len(points) + 199) // 200)
    view = list(points[::stride])
    if view[-1] != points[-1]:
        view.append(points[-1])
    return dict(origin=list(points[0]), points=view)


def trial_contact(points, *, finished=False):
    """Single-finger contacts only write or tap; selection belongs to two fingers."""
    return classify_contact(points, finished=finished, hold_select=False)


def two_finger_selection(points, *, finished=False):
    """Live/final state of one two-finger contact (screen space, +y down).

    The direction is the contact's net displacement; below selectDistance it
    stays in the centre, and releasing there cancels.
    """
    threshold = TWO_FINGER_SELECTION['selectDistance']
    result = dict(policy=TWO_FINGER_SELECTION['policy'], source='two-finger', phase='selecting',
                  progress=0, direction=None, distance=0., threshold=threshold)
    try:
        valid = bool(points) and all(len(p) == 2 and all(math.isfinite(v) for v in p) for p in points)
    except (TypeError, ValueError):
        valid = False
    if valid:
        dx, dy = points[-1][0] - points[0][0], points[-1][1] - points[0][1]
        distance = math.hypot(dx, dy)
        result.update(distance=distance, progress=min(100, distance * 100 / threshold),
                      display=_display(points))
        if distance >= threshold:
            result['direction'] = _SECTORS[int((math.degrees(math.atan2(dy, dx)) + 22.5) % 360 // 45)]
    if finished:
        result.pop('display', None)
        result.update(trial_selection(result))
    return result


def trial_selection(decision):
    """Keep SDK timing/direction, translate only the trial's final menu action."""
    if decision.get('phase') != 'selecting':
        return dict(decision)
    result = dict(decision)
    direction = result.get('direction') or 'center'
    if direction in TRIAL_SELECTION['candidateDirections']:
        result.update(action='select', target=TRIAL_SELECTION['candidateDirections'][direction])
    elif direction in TRIAL_SELECTION['punctuationDirections']:
        result.update(action='punctuation', target=TRIAL_SELECTION['punctuationDirections'][direction])
    elif direction in TRIAL_SELECTION['actionDirections']:
        result.update(action=TRIAL_SELECTION['actionDirections'][direction], target=None)
    else:
        result.update(action='cancel-selection', target=None)
    return result
