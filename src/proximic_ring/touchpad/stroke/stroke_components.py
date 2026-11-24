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
    '与': ('hzh', ((0, 1, 2), (1, 0, 2))),
    '比': ('hzpz', ((0, 1, 2, 3), (1, 0, 2, 3), (0, 1, 3, 2), (1, 0, 3, 2))),
    '曲': ('szhssh', ((0, 1, 2, 3, 4, 5), (0, 1, 2, 5, 3, 4))),
    '小': ('spn', ((0, 1, 2), (1, 0, 2), (1, 0, 2))),
    '少': ('spnp', ((0, 1, 2, 3), (1, 0, 2, 3), (1, 0, 2, 3))),
    '步': ('shshspp', ((0, 1, 2, 3, 4, 5, 6), (0, 1, 2, 3, 5, 4, 6),
                       (0, 1, 2, 3, 5, 4, 6), (0, 1, 2, 3, 5, 4, 6))),
    '里': ('szhhshh', ((0, 1, 2, 3, 4, 5, 6), (0, 1, 2, 3, 5, 4, 6))),
    '黑': ('sznphshhnnnn', ((0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11),
                           (0, 1, 2, 3, 4, 6, 5, 7, 8, 9, 10, 11))),
    '为': ('npzn', ((0, 1, 2, 3), (1, 2, 0, 3))),
    '革': ('hsshszhhs', ((0, 1, 2, 3, 4, 5, 6, 7, 8), (0, 1, 2, 3, 4, 5, 6, 8, 7))),
    '昔': ('hsshszhh', ((0, 1, 2, 3, 4, 5, 6, 7), (0, 3, 1, 2, 4, 5, 6, 7))),
    '共': ('hsshpn', ((0, 1, 2, 3, 4, 5), (0, 3, 1, 2, 4, 5))),
    # 甫 can start its middle vertical early, including lifting and continuing
    # it after the fold. Both segments refer to the same reference stroke.
    '甫': ('hszhhsn', ((0, 1, 2, 3, 4, 5, 6), (0, 1, 5, 2, 3, 4, 6),
                      (0, 1, 5, 2, 5, 3, 4, 6))),
    '辶': ('nzn', ((0, 1, 2),)),
    '廴': ('zn', ((0, 1),)),
    '匚': ('hz', ((0, 1),)),
    '囗': ('szh', ((0, 1, 2),)),
}
ENCLOSURES = frozenset('辶廴匚囗')
EMBEDDED_ONLY = frozenset('革')
# Short 左撇 may look like 左点 in quick ink. 步 also accepts the requested
# 点、竖、点 ending. These shapes are offered in the reordered schedules.
REORDERED_POINTS = {('小', 2): (1,), ('少', 2): (1,), ('步', 2): (5,), ('步', 3): (5, 6)}
VARIANT_COST = .12


@lru_cache(maxsize=1)
def component_annotations():
    data = json.loads(files('proximic_ring').joinpath('assets/stroke-components.json').read_text('utf-8'))
    if data.get('schema') != 'ring-stroke-components' or data.get('version') != 1:
        raise ValueError('Unsupported component annotations')
    if data.get('rules') != {key: [code, [list(p) for p in orders]] for key, (code, orders) in RULES.items()}:
        raise ValueError('Component rules and annotations differ')
    return data['characters']


@lru_cache(maxsize=512)
def point_roles(character, code):
    annotation = component_annotations().get(character)
    if annotation is None or annotation['code'] != code:
        return frozenset()
    return frozenset(row['indices'][role] for row in annotation['components']
                     for (component, _), roles in REORDERED_POINTS.items()
                     if row['component'] == component for role in roles)


def split_stroke_roles(character, code):
    annotation = component_annotations().get(character)
    if annotation is None or annotation['code'] != code:
        return frozenset()
    return frozenset(row['indices'][5] for row in annotation['components'] if row['component'] == '甫')
