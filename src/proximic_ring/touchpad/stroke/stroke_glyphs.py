"""Offline, per-candidate Kaishu stroke states, independent of the UI toolkit."""
from __future__ import annotations

import base64
from functools import lru_cache
import gzip
from html import escape
from importlib.resources import files
import json

INK = '#171C28'
OUTLINE = '#98A1B2'
FUZZY = '#EAB308'
CORRECTION = '#DC2626'
NEXT = '#2563EB'


@lru_cache(maxsize=1)
def glyph_library():
    with files('proximic_ring').joinpath('assets/stroke-glyphs.json.gz').open('rb') as stream:
        return json.loads(gzip.decompress(stream.read()))['characters']


@lru_cache(maxsize=1)
def _heart_components():
    data = json.loads(files('proximic_ring').joinpath('assets/stroke-left-dots.json').read_text('utf-8'))
    result = {}
    for row in data['annotations']:
        for index, origins in row.get('provenance', {}).items():
            index = int(index)
            if (row['code'][index:index+3] == 'nns'
                    and any(origin.startswith('component:') and origin.endswith('/忄') for origin in origins)):
                result.setdefault(row['character'], set()).add(index)
    return result


def stroke_mapping(canonical, variant, heart_starts=()):
    """Only map verified equal orders or the established three-stroke 忄 variant.

    Equal counts alone do not prove equal order. Unsupported versions retain the
    whole glyph with an explicit fallback rather than color the wrong outlines.
    """
    if canonical == variant:
        return tuple(range(len(canonical)))
    # The recognition dictionary accepts nns/nsn/snn for 忄. Map the vertical
    # stroke and the two dots separately, retaining their left-to-right order.
    if len(canonical) == len(variant):
        for start in heart_starts:
            if (canonical[start:start+3] == 'nns' and variant[start:start+3] in ('nsn', 'snn')
                    and canonical[:start] == variant[:start]
                    and canonical[start+3:] == variant[start+3:]):
                slots = {'n': [start, start+1], 's': [start+2]}
                mapped = list(range(len(canonical)))
                for index, letter in enumerate(variant[start:start+3], start):
                    mapped[index] = slots[letter].pop(0)
                return tuple(mapped)
    return None


@lru_cache(maxsize=512)
def candidate_glyph(character, variant='', entered=0, fuzzy=(), prediction=False, corrections=(), stroke_indices=None):
    glyph = glyph_library().get(character)
    if not glyph:
        return dict(character=character, available=False, source='', states=[], total=0,
                    entered=entered, next='', status='暂缺逐笔字形')
    paths, canonical = glyph['paths'], glyph['code']
    mapping = (stroke_mapping(canonical, variant,
               _heart_components().get(character, ()) if canonical != variant else ())
               if canonical and variant else None)
    if stroke_indices is not None:
        mapping = (stroke_indices if len(stroke_indices) == len(canonical) == len(variant)
                   and all(type(i) is int for i in stroke_indices)
                   and sorted(stroke_indices) == list(range(len(canonical)))
                   and ''.join(canonical[i] for i in stroke_indices) == variant else None)
    available = prediction or (mapping is not None and 0 <= entered <= len(mapping))
    states = ['entered' if prediction else 'remaining'] * len(paths)
    next_symbol = ''
    if available and not prediction:
        for index in range(entered):
            states[mapping[index]] = ('correction' if index in corrections else
                                      'fuzzy' if index in fuzzy else 'entered')
        if entered < len(mapping):
            states[mapping[entered]] = 'next'
            from .stroke_input import SYMBOLS
            next_symbol = SYMBOLS[variant[entered]].replace('𠃍', '乛')
    elif not available:
        states = ['entered'] * len(paths)
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">',
             '<g transform="translate(0 900) scale(1 -1)">']
    for path, state in zip(paths, states):
        style = {'entered': f'fill="{INK}"',
                 'remaining': f'fill="none" stroke="{OUTLINE}" stroke-width="9" stroke-linejoin="round"',
                 'fuzzy': f'fill="{FUZZY}"',
                 'correction': f'fill="{CORRECTION}"',
                 'next': f'fill="{NEXT}" fill-opacity="0.38" stroke="{NEXT}" stroke-opacity="0.55" stroke-width="5"'}[state]
        parts.append(f'<path d="{escape(path, quote=True)}" {style}/>')
    parts.append('</g></svg>')
    svg = ''.join(parts)
    status = ('联想' if prediction else '笔顺版本不同' if not available else
              '已写完' if entered == len(paths) else f'{entered} / {len(paths)} 笔')
    if corrections and not prediction:
        positions = '、'.join(str(index+1) for index in corrections)
        status = f'疑似错笔 第{positions}笔 · {status}'
    return dict(character=character, available=available,
                source='data:image/svg+xml;base64,' + base64.b64encode(svg.encode()).decode(),
                states=states, total=len(paths), entered=entered, next=next_symbol, status=status)
