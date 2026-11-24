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
MORE_PUNCTUATION_CELLS = (
    ('、',), ('“', '”'), ('；',),
    ('《', '》'), ('！',), ('（', '）'),
    ('……',), ('——',), ('：',),
)
PUNCTUATION = (*[cell[3] for cell in PUNCTUATION_CELLS],
               *[symbol for cell in MORE_PUNCTUATION_CELLS for symbol in cell])
# (direction, row, column, arrow, symbol, title, action)
ACTION_CELLS = (('left', 1, 0, '←', '⌫', '删除', 'delete-one'),)
# Two-finger travel is measured in the same pointer units as stroke points.
TWO_FINGER_SELECTION = dict(policy='two-finger-swipe-v2', selectDistance=6., clearDistance=30.)
TRIAL_SELECTION = dict(CONTACT_INTENT, policy='two-finger-five-candidates-v1',
    sourcePolicy=TWO_FINGER_SELECTION['policy'], pageSize=PAGE_SIZE, center='cancel',
    selectDistance=TWO_FINGER_SELECTION['selectDistance'],
    clearDistance=TWO_FINGER_SELECTION['clearDistance'],
    candidateDirections={direction: index for index, (direction, _, _, _) in enumerate(CANDIDATE_CELLS)
                         if direction != 'center'},
    punctuationDirections={direction: symbol for direction, _, _, symbol, _ in PUNCTUATION_CELLS},
    actionDirections={direction: action for direction, *_, action in ACTION_CELLS})
_SECTORS = ('right', 'down-right', 'down', 'down-left', 'left', 'up-left', 'up', 'up-right')


def _direction(dx, dy):
    return _SECTORS[int((math.degrees(math.atan2(dy, dx)) + 22.5) % 360 // 45)]


def _punctuation_pull(points, threshold):
    """Replay the submenu and return the current primary selection origin."""
    origin_x, origin_y = points[0]
    origin_index = 0
    entry = None
    opened = None
    crossed_other_cell = False
    cell_travel = 1.5 * threshold
    cancel_travel = 2 * threshold
    cancel_entry_y = None
    for index, (x, y) in enumerate(points[1:], 1):
        if opened is not None:
            in_cancel_cell = (-cell_travel / 2 <= x - opened[0] < cell_travel / 2
                              and y - opened[1] >= cell_travel / 2)
            if not in_cancel_cell:
                cancel_entry_y = None
            elif cancel_entry_y is None:
                cancel_entry_y = y
            elif y - cancel_entry_y >= cancel_travel:
                opened = entry = None
                cancel_entry_y = None
                crossed_other_cell = False
                origin_x, origin_y = x, y
                origin_index = index
            continue
        dx, dy = x - origin_x, y - origin_y
        distance = math.hypot(dx, dy)
        direction = _direction(dx, dy) if distance >= threshold else None
        if direction != 'up':
            entry = None
            crossed_other_cell = direction is not None
            continue
        if entry is None:
            # A fast first packet can already be beyond the selection boundary.
            # After another cell, start the pull from where it actually reached the period.
            fraction = 1. if crossed_other_cell else threshold / distance
            entry = (origin_x + dx * fraction, origin_y + dy * fraction)
        if abs(x - entry[0]) >= threshold:
            entry = (x, y)
        start_y = min(origin_y - 3 * threshold, entry[1] - threshold)
        end_y = start_y - 2 * threshold
        if y <= end_y:
            opened = (x, y)

    x, y = points[-1]
    if opened is not None:
        px = (x - opened[0]) / cell_travel
        py = (y - opened[1]) / cell_travel
        col = max(0, min(2, math.floor(px + 1.5)))
        row = max(0, min(2, math.floor(py + 1.5)))
        symbols = MORE_PUNCTUATION_CELLS[row * 3 + col]
        symbol = symbols[int(px >= col - 1)] if len(symbols) == 2 else symbols[0]
        pull = dict(direction='up', progress=1., opened=True, symbol=symbol)
        if cancel_entry_y is not None and y > cancel_entry_y:
            pull['cancel_progress'] = (y - cancel_entry_y) / cancel_travel
        return pull, origin_index
    if entry is not None:
        start_y = min(origin_y - 3 * threshold, entry[1] - threshold)
        progress = max(0., min(1., (start_y - y) / (2 * threshold)))
        return dict(direction='up', progress=progress, opened=False), origin_index
    return None, origin_index


def _display(points):
    stride = max(1, (len(points) + 199) // 200)
    view = list(points[::stride])
    if view[-1] != points[-1]:
        view.append(points[-1])
    return dict(origin=list(points[0]), points=view)


def trial_contact(points, *, finished=False):
    """Single-finger contacts only write or tap; selection belongs to two fingers."""
    return classify_contact(points, finished=finished, hold_select=False)


def two_finger_selection(points, *, finished=False, correction_enabled=False):
    """Live/final state of one two-finger contact (screen space, +y down).

    Net displacement selects the primary direction. An upward extension
    opens punctuation and resets the selection origin to that sample.
    """
    threshold = TWO_FINGER_SELECTION['selectDistance']
    result = dict(policy=TWO_FINGER_SELECTION['policy'], source='two-finger', phase='selecting',
                  progress=0, direction=None, distance=0., threshold=threshold,
                  clear_distance=TWO_FINGER_SELECTION['clearDistance'], clear_armed=False, clear_progress=0.,
                  correction_armed=False, correction_progress=0.)
    try:
        valid = bool(points) and all(len(p) == 2 and all(math.isfinite(v) for v in p) for p in points)
    except (TypeError, ValueError):
        valid = False
    if valid:
        pull, origin_index = _punctuation_pull(points, threshold)
        active_points = points[origin_index:]
        dx, dy = active_points[-1][0] - active_points[0][0], active_points[-1][1] - active_points[0][1]
        distance = math.hypot(dx, dy)
        result.update(distance=distance, progress=min(100, distance * 100 / threshold),
                      display=_display(active_points))
        if pull is not None:
            result.update(punctuation_pull=pull, direction=pull['direction'])
        elif distance >= threshold:
            result['direction'] = _direction(dx, dy)
        if result['direction'] == 'left':
            result['clear_armed'] = -dx >= result['clear_distance']
            result['clear_progress'] = max(0., min(1., (-dx - 3 * threshold) / (result['clear_distance'] - 3 * threshold)))
        elif correction_enabled and result['direction'] == 'right':
            result['correction_armed'] = dx >= result['clear_distance']
            result['correction_progress'] = max(0., min(1., (dx - 3 * threshold) / (result['clear_distance'] - 3 * threshold)))
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
    pull = result.get('punctuation_pull') or {}
    if pull.get('opened') and pull.get('symbol') in PUNCTUATION:
        result.update(action='punctuation', target=pull['symbol'])
    elif direction == 'right' and result.get('correction_armed'):
        result.update(action='correct-text', target=None)
    elif direction in TRIAL_SELECTION['candidateDirections']:
        result.update(action='select', target=TRIAL_SELECTION['candidateDirections'][direction])
    elif direction in TRIAL_SELECTION['punctuationDirections']:
        result.update(action='punctuation', target=TRIAL_SELECTION['punctuationDirections'][direction])
    elif direction == 'left' and result.get('clear_armed'):
        result.update(action='clear-all', target=None)
    elif direction in TRIAL_SELECTION['actionDirections']:
        result.update(action=TRIAL_SELECTION['actionDirections'][direction], target=None)
    else:
        result.update(action='cancel-selection', target=None)
    return result
