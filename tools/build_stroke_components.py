"""Build verified component occurrences, without enumerating whole-word orders."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from build_left_dot_annotations import parse_ids
from proximic_ring.touchpad.stroke.stroke_components import RULES, ENCLOSURES
from proximic_ring.touchpad.stroke.stroke_glyphs import glyph_library


def occurrences(character, code, dictionary, diagnostics):
    found = []

    def walk(char, indices, parent_scope, provenance, seen=()):
        if char in seen:
            return
        # 匸 is explicitly an enclosure form of the same scheduling rule.
        component = '匚' if char == '匸' else char
        if component in RULES:
            expected = RULES[component][0]
            if ''.join(code[i] for i in indices) != expected:
                diagnostics['code_mismatch:'+component] += 1
                return
            scope = parent_scope if component in ENCLOSURES else indices
            if (not indices or not scope or tuple(scope) != tuple(range(min(scope), max(scope)+1))
                    or (component not in ENCLOSURES and tuple(indices) != tuple(scope))):
                diagnostics['noncontiguous:'+component] += 1
                return
            inside = [i for i in scope if i not in indices]
            if inside and tuple(inside) != tuple(range(min(inside), max(inside)+1)):
                diagnostics['interleaved:'+component] += 1
                return
            # Only known enclosing schedules are licensed. Identity-only roots
            # remain useful as a token even when they have no internal child.
            if inside:
                valid = (tuple(indices) == tuple(scope[-len(indices):]) if component in '辶廴' else
                         tuple(indices) == (scope[0], scope[-1]) if component == '匚' else
                         tuple(indices) == (scope[0], scope[1], scope[-1]))
                if not valid:
                    diagnostics['unsupported_schedule:'+component] += 1
                    return
            found.append(dict(component=component, form=char, indices=list(indices),
                              scope=list(scope), provenance=provenance))
            return
        data = dictionary.get(char, {})
        matches = data.get('matches', [])
        if len(matches) != len(indices):
            diagnostics['missing_mapping'] += 1
            return
        for leaf, path in parse_ids(data.get('decomposition', '')):
            if leaf in ('？', char):
                continue
            child = tuple(indices[i] for i, match in enumerate(matches)
                          if match is not None and tuple(match[:len(path)]) == path)
            if not child:
                continue
            parent_path = path[:-1]
            scope = (indices if not parent_path else
                     tuple(indices[i] for i, match in enumerate(matches)
                           if match is not None and tuple(match[:len(parent_path)]) == parent_path))
            walk(leaf, child, scope, provenance+'/'+leaf, seen+(char,))

    walk(character, tuple(range(len(code))), tuple(range(len(code))), character)
    unique = {(row['component'], tuple(row['indices']), tuple(row['scope'])): row for row in found}
    return sorted(unique.values(), key=lambda row: (row['scope'][0], -len(row['scope']), row['component']))


def build(source):
    raw = source.read_bytes()
    dictionary = {row['character']: row for line in raw.decode('utf-8').splitlines()
                  if (row := json.loads(line))}
    diagnostics, counts, characters = Counter(), Counter(), {}
    for char, glyph in glyph_library().items():
        code = glyph.get('code', '')
        if not code or len(code) != len(glyph['paths']):
            continue
        components = occurrences(char, code, dictionary, diagnostics)
        if components:
            characters[char] = dict(code=code, components=components)
            counts.update(row['component'] for row in components)
    return dict(schema='ring-stroke-components', version=1,
                rules={key: [code, [list(p) for p in orders]] for key, (code, orders) in RULES.items()},
                source=dict(url='https://raw.githubusercontent.com/skishore/makemeahanzi/master/dictionary.txt',
                            sha256=hashlib.sha256(raw).hexdigest(),
                            glyphsSha256=hashlib.sha256((ROOT/'src/proximic_ring/assets/stroke-glyphs.json.gz').read_bytes()).hexdigest()),
                coverage=dict(characters=len(characters), occurrences=dict(sorted(counts.items())),
                              skipped=dict(sorted(diagnostics.items()))), characters=characters)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ROOT/'src/proximic_ring/assets/stroke-components.json')
    args = parser.parse_args()
    payload = build(args.source)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(payload['coverage'], ensure_ascii=False))


if __name__ == '__main__':
    main()
