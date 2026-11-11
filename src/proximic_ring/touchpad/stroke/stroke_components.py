"""Verified component identities and their accepted encoding schedules.

Orders refer to stroke roles, not category permutations. Costs are engineering
priors, not error penalties or calibrated probabilities.
"""
from functools import lru_cache
from importlib.resources import files
import json


RULES = {
    '忄': ('nns', ((0, 1, 2), (0, 2, 1), (2, 0, 1))),
    '方': ('nhzp', ((0, 1, 2, 3), (0, 1, 3, 2))),
    '万': ('hzp', ((0, 1, 2), (0, 2, 1))),
    '刀': ('zp', ((0, 1), (1, 0))),
    '力': ('zp', ((0, 1), (1, 0))),
    '乃': ('zp', ((0, 1), (1, 0))),
    '匕': ('pz', ((0, 1), (1, 0))),
    '及': ('pzn', ((0, 1, 2), (1, 0, 2))),
    '火': ('nppn', ((0, 1, 2, 3), (2, 3, 0, 1))),
    '母': ('zznhn', ((0, 1, 2, 3, 4), (0, 1, 3, 2, 4))),
    '辶': ('nzn', ((0, 1, 2),)),
    '廴': ('zn', ((0, 1),)),
    '匚': ('hz', ((0, 1),)),
    '囗': ('szh', ((0, 1, 2),)),
}
ENCLOSURES = frozenset('辶廴匚囗')
VARIANT_COST = .12


@lru_cache(maxsize=1)
def component_annotations():
    data = json.loads(files('proximic_ring').joinpath('assets/stroke-components.json').read_text('utf-8'))
    if data.get('schema') != 'ring-stroke-components' or data.get('version') != 1:
        raise ValueError('Unsupported component annotations')
    if data.get('rules') != {key: [code, [list(p) for p in orders]] for key, (code, orders) in RULES.items()}:
        raise ValueError('Component rules and annotations differ')
    return data['characters']
