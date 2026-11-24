"""Compile component membership and logical spans; no handwriting labels here."""
from __future__ import annotations

import argparse
from functools import lru_cache
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from proximic_ring.touchpad.stroke.stroke_joined_components import component_rules
from proximic_ring.touchpad.stroke.stroke_dtw import sequence
from build_left_dot_annotations import aligned_positions, parse_ids


# The supplementary IDS data identifies components but has no stroke alignment.
# Match the full encoding of each confirmed component before granting a join.
SUPPLEMENTAL_CODES = {
    '水': ('szpn', 'zzpn'),
    '氺': ('snhpn', 'znnpn', 'snnpn'),
    '永': ('nzzpn',),
    '函': ('zsnhpnzs', 'zznnpnzs', 'hznhpnzs'),
    '承': ('zshhhzpn',),
    '而': ('hpszss',),
    '面': ('hpszsshhh',),
}


def supplemental_spans(code, memberships, forms):
    for component, count in memberships:
        offsets = [i for i in range(len(code)) if code.startswith(SUPPLEMENTAL_CODES[component], i)]
        if len(offsets) != count:
            continue
        for rule, form in forms[component]:
            for offset in offsets:
                indices = tuple(offset+i for i in form['indices'])
                if ''.join(code[i] for i in indices) in rule['codes']:
                    yield rule['id'], component, indices


def pie_horizontal_spans(code, medians):
    """Require a falling-left stroke and a nearby rightward horizontal stroke."""
    if len(medians) != len(code):
        return
    for start in range(len(code)-1):
        if code[start:start+2] != 'ph':
            continue
        (a, b), (c, d) = [(median[0], median[-1]) for median in medians[start:start+2]]
        px, py = a[0]-b[0], a[1]-b[1]
        hx, hy = d[0]-c[0], d[1]-c[1]
        if px <= 0 or py < .08*px or hx <= 0 or abs(hy) > .32*hx:
            continue
        # Some horizontals intersect the 撇 higher up in the printed form.
        # Allow a short retrace, but reject a large jump to a separate stroke.
        width = max(px, hx)
        dx, dy = c[0]-b[0], b[1]-c[1]
        if (c[1] <= b[1]+.60*py and -.65*hx <= dx <= .75*width
                and math.hypot(dx, dy) <= .75*max(width, py)):
            yield start


def reference_paths(family, medians):
    paths = [[[x, -y] for x, y in median] for median in medians]
    first, second = paths[:2]
    if family == 'pie-horizontal':
        # Cursive 撇折 shape. Join the end of the left fall directly to
        # the rightward stroke, using the glyph's length and slope.
        start, corner = first[0], first[-1]
        dx, dy = (second[-1][axis]-second[0][axis] for axis in (0, 1))
        yield [start, corner, [corner[0]+dx, corner[1]+dy]]
        yield [start, corner, [corner[0]+dx, corner[1]]]
    elif family == 'mouth-tail':
        # Top horizontal, down-left to the bottom start, then right.
        top = max(first, key=lambda point: point[0])
        yield [first[0], top, second[0], second[-1]]
        # Also cover a narrower diagonal where the writer retains the right edge.
        bottom = [second[0][0]+.24*(top[0]-second[0][0]), second[0][1]]
        yield [first[0], top, bottom, second[-1]]
    elif family == 'silk-whole':
        tail = paths[2]
        yield first + second + tail
        folds = [first[0], min(first, key=lambda point: point[0]), first[-1],
                 min(second, key=lambda point: point[0]), second[-1]]
        yield folds + tail
        # A compact handwritten 纟 can finish at the second fold.
        yield first + second
        yield folds
    elif family == 'water-tail':
        yield first + second
        # In fast ink the second stroke may begin at the end of the first.
        yield first + [[first[-1][0] + x - second[0][0], first[-1][1] + y - second[0][1]]
                       for x, y in second[1:]]
    else:
        yield first + second


def build(cache, dictionary=None):
    dictionary = dictionary or StrokeDictionary()
    rules = component_rules()['rules']
    forms = {}
    for rule in rules:
        for form in rule.get('forms', ()):
            forms.setdefault(form['component'], []).append((rule, form))
    orders = json.loads((cache/'cnchar-orders.json').read_text('utf-8'))
    table = json.loads((cache/'cnchar-stroke-table.json').read_text('utf-8'))
    mapping = {c: ('h' if c in 'ji' else 's' if c in 'fg' else 'p' if c == 's' else 'n' if c in 'dkl' else 'z') for c in table}
    decompositions = {row['character']: row for line in (cache/'dictionary.txt').read_text('utf-8').splitlines() if (row := json.loads(line))}
    decompositions.setdefault('沝', dict(decomposition='⿰水水', matches=[[0]]*4 + [[1]]*4))
    graphics = {row['character']: row for line in (cache/'graphics.txt').read_text('utf-8').splitlines() if (row := json.loads(line))}
    supplemental = json.loads(Path(__file__).with_name('stroke-joined-component-membership.json').read_text('utf-8'))
    memberships = {}
    for component, by_count in supplemental['members'].items():
        for count, characters in by_count.items():
            for character in characters:
                memberships.setdefault(character, []).append((component, int(count)))
    by_id = {rule['id']: rule for rule in rules}
    spatial_rules = [rule for rule in rules if rule.get('geometry') == 'pie-horizontal']

    @lru_cache(None)
    def spans(char, count, seen=()):
        if char in seen: return ()
        found = []
        for rule, form in forms.get(char, []):
            if count == form['strokeCount']:
                found.append((rule['id'], char, tuple(form['indices'])))
        data = decompositions.get(char, {})
        matches = data.get('matches', [])
        if len(matches) != count: return tuple(found)
        for leaf, path in parse_ids(data.get('decomposition', '')):
            if leaf in ('？', char): continue
            indices = [i for i, match in enumerate(matches) if match is not None and tuple(match[:len(path)]) == path]
            if not indices: continue
            # This five-stroke 水 label denotes 氺 in the supplied glyph of 暴.
            if char == '暴' and leaf == '水' and len(indices) == 5:
                leaf = '氺'
            for rule_id, component, local in spans(leaf, len(indices), seen+(char,)):
                global_indices = tuple(indices[i] for i in local)
                if global_indices == tuple(range(global_indices[0],global_indices[-1]+1)):
                    value = (rule_id, component, global_indices)
                    if value not in found: found.append(value)
        return tuple(found)

    positions, review, bases = {}, [], {}
    for char, entries in dictionary.by_character.items():
        codes = {code: sources for _, code, sources in entries}
        base = ''.join(mapping[c] for c in orders.get(char, ''))
        if base not in codes:
            counts = {len(decompositions.get(char, {}).get('matches', ()))}
            counts.update(form['strokeCount'] for _, form in forms.get(char, ()))
            eligible = sorted((code for code in codes if len(code) in counts), key=codes.get, reverse=True)
            base = (eligible[0] if eligible and
                    (len(eligible) == 1 or codes[eligible[0]] > codes[eligible[1]]) else '')
        found = list(spans(char, len(base)))
        medians = graphics.get(char, {}).get('medians', [])
        hou_middle = {indices[1] for _, component, indices in found if component == '后'}
        for rule in spatial_rules:
            found.extend((rule['id'], char, (i, i+1)) for i in pie_horizontal_spans(base, medians)
                         if i not in hou_middle)
        supplemental_memberships = memberships.get(char, ())
        if not found and not supplemental_memberships:
            continue
        variants = {}
        for _, full, _ in entries:
            annotations = []
            for rule_id, component, indices in found:
                rule = by_id[rule_id]
                a, b = indices[0], indices[-1]+1
                for code in rule['codes']:
                    reference = base[:a]+code+base[b:]
                    start = a
                    if full != reference:
                        target = tuple(range(a, a+len(code)))
                        shifted = aligned_positions(reference, full, target, same_category=True)
                        if len(shifted) != len(target):
                            continue
                        start = shifted[a]
                        if tuple(shifted.values()) != tuple(range(start, start+len(code))):
                            continue
                    value = dict(ruleId=rule_id, component=component, family=rule['family'],
                        start=start, length=len(code), indices=list(range(start,start+len(code))))
                    if value not in annotations: annotations.append(value)
            occupied = {(row['ruleId'], row['start'], row['length']) for row in annotations}
            for rule_id, component, indices in supplemental_spans(full, supplemental_memberships, forms):
                key = rule_id, indices[0], len(indices)
                if key not in occupied:
                    occupied.add(key)
                    annotations.append(dict(ruleId=rule_id, component=component, family=by_id[rule_id]['family'],
                                            start=indices[0], length=len(indices), indices=list(indices)))
            if annotations: variants[full] = annotations
        if variants:
            positions[char] = variants
            bases[char] = base
        else: review.append(dict(character=char, reason='No dictionary variant matches registered component span'))

    references = []
    for rule in rules:
        family = rule['family']
        examples = rule['examples'] if rule['templateSource'] == 'reference-component' else rule.get('referenceExamples', ())
        for char in examples:
            medians = graphics.get(char, {}).get('medians', [])
            variants = positions.get(char, {})
            base = bases.get(char, '')
            for annotation in variants.get(base, ()):
                indices = annotation['indices']
                if annotation['family'] != family or len(medians) != len(base):
                    continue
                for points in reference_paths(family, [medians[i] for i in indices]):
                    features = sequence(points)
                    if features is not None:
                        references.append(dict(family=family, features=features.tolist(), referenceCharacter=char,
                            referenceIndices=indices, referenceCode=base[indices[0]:indices[-1]+1],
                            source='reference-component', calibration='not-ring-samples'))
    source_files = ('dictionary.txt', 'cnchar-orders.json', 'cnchar-stroke-table.json', 'graphics.txt')
    rules_path = ROOT/'src/proximic_ring/assets/stroke-joined-rules.json'
    unique_references = {(
        row['family'], row['referenceCharacter'], tuple(tuple(column) for column in row['features'])
    ): row for row in references}
    return dict(schema='ring-joined-component-positions', version=1,
        source=dict(rulesSha256=hashlib.sha256(rules_path.read_bytes().replace(b'\r\n', b'\n')).hexdigest(),
            dictionarySha256=hashlib.sha256((ROOT/'src/proximic_ring/assets/strokes.json.gz').read_bytes()).hexdigest(),
            urls=dict(decomposition='https://github.com/skishore/makemeahanzi',
                      fineOrder='https://registry.npmjs.org/cnchar-order/-/cnchar-order-3.2.6.tgz'),
            supplementalMembership=supplemental['source'],
            licenses=['stroke-left-dots-source-notice.txt','stroke-left-dots-lgpl.txt',
                      'stroke-left-dots-cnchar-license.txt','stroke-glyphs-arphic-license.txt'],
            files={name:hashlib.sha256((cache/name).read_bytes()).hexdigest() for name in source_files}),
        rules=rules, positions=positions, referenceTemplates=list(unique_references.values()), review=review)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',type=Path,default=ROOT/'tmp/left-dot-research')
    parser.add_argument('--output',type=Path,default=ROOT/'src/proximic_ring/assets/stroke-joined-components.json.gz')
    args=parser.parse_args()
    result=build(args.cache)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_bytes(gzip.compress(json.dumps(result,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8'),mtime=0))
    print(f"{len(result['positions'])} annotated characters; {len(result['referenceTemplates'])} reference traces")


if __name__=='__main__': main()
