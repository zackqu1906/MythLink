"""Offline, per-character/per-order left-dot annotations (zero-based positions)."""
from functools import lru_cache
from importlib.resources import files
import json

LEFT_DOT_COST = .45


def validate_annotations(rows):
    result = {}
    for row in rows:
        character, code, positions = row['character'], row['code'], row['positions']
        if (len(character) != 1 or not code or any(letter not in 'hspnz' for letter in code)
                or not positions or any(type(index) is not int or not 0 <= index < len(code)
                                        or code[index] != 'n' for index in positions)
                or len(set(positions)) != len(positions) or (character, code) in result):
            raise ValueError('Invalid or duplicate left-dot annotation')
        result[(character, code)] = tuple(sorted(positions))
    return result


@lru_cache(maxsize=1)
def _annotation_data():
    data = json.loads(files('proximic_ring').joinpath('assets/stroke-left-dots.json').read_text(encoding='utf-8'))
    if (data['schema'] != 'ring-left-dot-annotations' or data['version'] != 1
            or data['index_base'] != 0 or data['substitution_cost'] != LEFT_DOT_COST):
        raise ValueError('Unsupported left-dot annotations')
    return data


@lru_cache(maxsize=1)
def left_dot_annotations():
    return validate_annotations(_annotation_data()['annotations'])


def annotation_snapshot():
    data = _annotation_data()
    return dict(schema=data['schema'], version=data['version'], cost=LEFT_DOT_COST,
                compatibleObservations=data['compatible_observations'],
                statistics=dict(data['statistics']), sources=data['sources'])
