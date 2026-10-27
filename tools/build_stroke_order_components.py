"""Record verified grass-component positions for the retained component orders."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from build_left_dot_annotations import parse_ids
from proximic_ring.touchpad.stroke.stroke_glyphs import glyph_library


def component_positions(character, dictionary, seen=()):
    if character in seen:
        return []
    data = dictionary.get(character, {})
    matches = data.get('matches', [])
    result = []
    for leaf, path in parse_ids(data.get('decomposition', '')):
        if leaf in ('？', character):
            continue
        indices = [index for index, match in enumerate(matches)
                   if match is not None and tuple(match[:len(path)]) == path]
        if not indices:
            continue
        if leaf in ('艹', '艸') and len(indices) == 3:
            result.append(tuple(indices))
        else:
            for child in component_positions(leaf, dictionary, seen + (character,)):
                if child and max(child) < len(indices):
                    result.append(tuple(indices[index] for index in child))
    return result


def build(source):
    dictionary = {row['character']: row for line in source.read_text('utf-8').splitlines()
                  if (row := json.loads(line))}
    positions = {}
    for character, glyph in glyph_library().items():
        if len(dictionary.get(character, {}).get('matches', [])) != len(glyph['paths']):
            continue
        starts = sorted({indices[0] for indices in component_positions(character, dictionary)
                         if indices == tuple(range(indices[0], indices[0]+3))
                         and glyph['code'][indices[0]:indices[0]+3] == 'hss'})
        if starts:
            positions[character] = starts
    return dict(schema='ring-stroke-order-components', version=1,
                source=dict(url='https://raw.githubusercontent.com/skishore/makemeahanzi/master/dictionary.txt',
                            sha256=hashlib.sha256(source.read_bytes()).hexdigest()),
                grassStarts=positions)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT/'tmp/left-dot-research/dictionary.txt')
    parser.add_argument('--output', type=Path,
                        default=ROOT/'src/proximic_ring/assets/stroke-order-components.json')
    args = parser.parse_args()
    data = build(args.source)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(f"{len(data['grassStarts'])} characters with verified grass-component positions")


if __name__ == '__main__':
    main()
