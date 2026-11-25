"""Keep order changes and explicitly accepted handwriting variants as literal input."""
from collections import Counter, defaultdict
from functools import lru_cache
from importlib.resources import files
import json


# The last 撇 in 男 can also serve as the vertical through 田.
ACCEPTED_WRITING_VARIANTS = {'男': ('szhhzp',)}


@lru_cache(maxsize=1)
def grass_components():
    data = json.loads(files('proximic_ring').joinpath(
        'assets/stroke-order-components.json').read_text('utf-8'))
    if data.get('schema') != 'ring-stroke-order-components' or data.get('version') != 1:
        raise ValueError('Unsupported stroke-order component annotations')
    return data['grassStarts']


def is_order_variant(canonical, variant, grass_starts=()):
    if Counter(canonical) == Counter(variant):
        return True
    normalized = {variant}
    for start in sorted(grass_starts):
        if canonical[start:start+3] != 'hss':
            continue
        normalized |= {code[:start]+'hss'+code[start+4:]
                       for code in normalized if code[start:start+4] in ('hshs', 'shhs')}
    expected = Counter(canonical)
    return any(Counter(code) == expected for code in normalized)


@lru_cache(maxsize=1)
def reference_orders():
    from .stroke_folds import fold_orders
    from .stroke_glyphs import glyph_library
    result = {character: code for character, (code, _) in fold_orders().items()}
    result.update({character: glyph['code'] for character, glyph in glyph_library().items()
                   if glyph['code']})
    return result


def filter_order_variants(entries):
    by_character = defaultdict(set)
    for character, code, *_ in entries:
        by_character[character].add(code)
    references, grass = reference_orders(), grass_components()
    reviewed = {character: code for character, code in references.items()
                if code in by_character.get(character, ())}
    accepted = [entry for entry in entries
                if entry[0] not in reviewed
                or entry[1] in ACCEPTED_WRITING_VARIANTS.get(entry[0], ())
                or is_order_variant(reviewed[entry[0]], entry[1], grass.get(entry[0], ()))]
    for character, codes in ACCEPTED_WRITING_VARIANTS.items():
        if character in by_character:
            accepted.extend([character, code, 0] for code in codes if code not in by_character[character])
    return accepted
