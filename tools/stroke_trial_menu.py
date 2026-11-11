"""Standalone trial menu: two-finger (product) or the original hold menu.

Two modes share one module so every trial helper reads the active layout:

* ``two-finger`` (the trial default on Raw2 hosts): the product menu from
  proximic_ring.touchpad.stroke.stroke_menu -- five candidates, three
  punctuation cells and a delete cell, selected by a two-finger swipe.
* ``hold``: six candidates selected by a single-finger hold-then-slide. The
  legacy touchpad protocol has no two-finger tag, and recorded hold sessions
  replay against it, so this stays the import-time default.

Consumers must read these names through the module (``stroke_trial_menu.X``)
so ``use()`` can switch the mode before the trial window is built.
"""
from proximic_ring.touchpad.stroke import stroke_menu as product
from proximic_ring.touchpad.stroke.stroke_contact_intent import CONTACT_INTENT, classify_contact

HOLD_CANDIDATE_CELLS = (
    ('center', 1, 1, '首选'), ('left', 1, 0, '←'), ('right', 1, 2, '→'),
    ('down-left', 2, 0, '↙'), ('down', 2, 1, '↓'), ('down-right', 2, 2, '↘'),
)
HOLD_PUNCTUATION_CELLS = (
    ('up-left', 0, '↖', '，', '逗号'), ('up', 1, '↑', '。', '句号'),
    ('up-right', 2, '↗', '？', '问号'),
)
HOLD_SELECTION = dict(CONTACT_INTENT, policy='contact-hold-six-candidates-v4',
    sourcePolicy=CONTACT_INTENT['policy'], pageSize=len(HOLD_CANDIDATE_CELLS), center='select-first',
    candidateDirections={direction: index for index, (direction, _, _, _) in enumerate(HOLD_CANDIDATE_CELLS)},
    punctuationDirections={direction: symbol for direction, _, _, symbol, _ in HOLD_PUNCTUATION_CELLS})


def hold_contact(points, *, finished=False):
    """Attach a single synchronized display snapshot to live selection feedback."""
    result = classify_contact(points, finished=finished)
    if not finished and result['phase'] == 'selecting':
        # This is the classifier's actual arming frame, including landing settle.
        start = round(result['hold_ms'] / CONTACT_INTENT['sampleMs'])
        path = points[start:]
        stride = max(1, (len(path) + 199) // 200)
        view = list(path[::stride])
        if view[-1] != path[-1]:
            view.append(path[-1])
        result['display'] = dict(origin=list(points[start]), points=view)
    return result


def hold_selection(decision):
    """Keep SDK timing/direction, translate only the trial's final menu action."""
    if decision.get('phase') != 'selecting':
        return dict(decision)
    result = dict(decision)
    direction = result.get('direction') or 'center'
    if direction in HOLD_SELECTION['candidateDirections']:
        result.update(action='select', target=HOLD_SELECTION['candidateDirections'][direction])
    elif direction in HOLD_SELECTION['punctuationDirections']:
        result.update(action='punctuation', target=HOLD_SELECTION['punctuationDirections'][direction])
    else:
        result.update(action='cancel-selection', target=None)
    return result


MODES = {
    'hold': dict(CANDIDATE_CELLS=HOLD_CANDIDATE_CELLS, PUNCTUATION_CELLS=HOLD_PUNCTUATION_CELLS,
                 ACTION_CELLS=(), TRIAL_SELECTION=HOLD_SELECTION, trial_contact=hold_contact,
                 trial_selection=hold_selection, two_finger_selection=None),
    'two-finger': dict(CANDIDATE_CELLS=product.CANDIDATE_CELLS, PUNCTUATION_CELLS=product.PUNCTUATION_CELLS,
                       ACTION_CELLS=product.ACTION_CELLS, TRIAL_SELECTION=product.TRIAL_SELECTION,
                       trial_contact=product.trial_contact, trial_selection=product.trial_selection,
                       two_finger_selection=product.two_finger_selection),
}


def use(mode):
    """Switch the active menu; call before the trial window is created."""
    globals().update(MODES[mode], MODE=mode, TWO_FINGER=mode == 'two-finger',
                     PAGE_SIZE=len(MODES[mode]['CANDIDATE_CELLS']))


use('hold')
