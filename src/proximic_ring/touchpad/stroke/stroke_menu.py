"""Shared local writing menu: six candidates and three punctuation directions."""
from .stroke_contact_intent import CONTACT_INTENT, classify_contact

PAGE_SIZE = 6
CANDIDATE_CELLS = (
    ('center', 1, 1, '首选'), ('left', 1, 0, '←'), ('right', 1, 2, '→'),
    ('down-left', 2, 0, '↙'), ('down', 2, 1, '↓'), ('down-right', 2, 2, '↘'),
)
PUNCTUATION_CELLS = (
    ('up-left', 0, '↖', '，', '逗号'), ('up', 1, '↑', '。', '句号'),
    ('up-right', 2, '↗', '？', '问号'),
)
TRIAL_SELECTION = dict(CONTACT_INTENT, policy='contact-hold-six-candidates-v4',
    sourcePolicy=CONTACT_INTENT['policy'], pageSize=PAGE_SIZE, center='select-first',
    candidateDirections={direction: index for index, (direction, _, _, _) in enumerate(CANDIDATE_CELLS)},
    punctuationDirections={direction: symbol for direction, _, _, symbol, _ in PUNCTUATION_CELLS})


def trial_contact(points, *, finished=False):
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
    else:
        result.update(action='cancel-selection', target=None)
    return result
