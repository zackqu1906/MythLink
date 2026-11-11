"""Compile component membership and logical spans; no handwriting labels here."""
from __future__ import annotations

import argparse
from functools import lru_cache
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from proximic_ring.touchpad.stroke.stroke_joined_components import component_rules
from proximic_ring.touchpad.stroke.stroke_dtw import sequence
from build_left_dot_annotations import parse_ids


def build(cache, dictionary=None):
    dictionary = dictionary or StrokeDictionary()
    rules = component_rules()['rules']
    forms = {}
    for rule in rules:
        for form in rule['forms']:
            forms.setdefault(form['component'], []).append((rule, form))
    orders = json.loads((cache/'cnchar-orders.json').read_text('utf-8'))
    table = json.loads((cache/'cnchar-stroke-table.json').read_text('utf-8'))
    mapping = {c: ('h' if c in 'ji' else 's' if c in 'fg' else 'p' if c == 's' else 'n' if c in 'dkl' else 'z') for c in table}
    decompositions = {row['character']: row for line in (cache/'dictionary.txt').read_text('utf-8').splitlines() if (row := json.loads(line))}
    by_id = {rule['id']: rule for rule in rules}

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
            for rule_id, component, local in spans(leaf, len(indices), seen+(char,)):
                global_indices = tuple(indices[i] for i in local)
                if global_indices == tuple(range(global_indices[0],global_indices[-1]+1)):
                    value = (rule_id, component, global_indices)
                    if value not in found: found.append(value)
        return tuple(found)

    positions, review = {}, []
    for char, entries in dictionary.by_character.items():
        order = orders.get(char)
        if order:
            base = ''.join(mapping[c] for c in order)
        elif char in forms:
            # Standalone components absent from cnchar still have a verified
            # seed count and an existing dictionary code; never substring-guess.
            base = next((e[1] for e in entries if any(len(e[1]) == f['strokeCount'] for _, f in forms[char])), '')
        else:
            continue
        if base not in {e[1] for e in entries}: continue
        found = spans(char, len(base))
        if not found: continue
        variants = {}
        for _, full, _ in entries:
            annotations = []
            for rule_id, component, indices in found:
                rule = by_id[rule_id]
                a, b = indices[0], indices[-1]+1
                for code in rule['codes']:
                    # Only the registered local span may change. The rest of
                    # this dictionary variant must retain its reference order.
                    if full != base[:a]+code+base[b:]: continue
                    value = dict(ruleId=rule_id, component=component, family=rule['family'],
                        start=a, length=len(code), indices=list(range(a,a+len(code))))
                    if value not in annotations: annotations.append(value)
            if annotations: variants[full] = annotations
        if variants: positions[char] = variants
        else: review.append(dict(character=char, reason='No dictionary variant matches registered component span'))

    graphics = {row['character']: row for line in (cache/'graphics.txt').read_text('utf-8').splitlines() if (row := json.loads(line))}
    references = []
    for char in ('过', '近', '这', '进', '远', '返'):
        for full, annotations in positions.get(char, {}).items():
            medians = graphics.get(char, {}).get('medians', [])
            if len(medians) != len(full): continue
            for annotation in annotations:
                if annotation['family'] != 'walk': continue
                points = [[x, -y] for i in annotation['indices'] for x,y in medians[i]]
                features = sequence(points)
                if features is not None:
                    references.append(dict(family='walk', features=features.tolist(), referenceCharacter=char,
                        referenceIndices=annotation['indices'], referenceCode=full[annotation['start']:annotation['start']+annotation['length']],
                        source='reference-component', calibration='not-ring-samples'))
            break
    source_files = ('dictionary.txt', 'cnchar-orders.json', 'cnchar-stroke-table.json', 'graphics.txt')
    rules_path = ROOT/'src/proximic_ring/assets/stroke-joined-rules.json'
    return dict(schema='ring-joined-component-positions', version=1,
        source=dict(rulesSha256=hashlib.sha256(rules_path.read_bytes()).hexdigest(),
            dictionarySha256=hashlib.sha256((ROOT/'src/proximic_ring/assets/strokes.json.gz').read_bytes()).hexdigest(),
            urls=dict(decomposition='https://github.com/skishore/makemeahanzi',
                      fineOrder='https://registry.npmjs.org/cnchar-order/-/cnchar-order-3.2.6.tgz'),
            licenses=['stroke-left-dots-source-notice.txt','stroke-left-dots-lgpl.txt',
                      'stroke-left-dots-cnchar-license.txt','stroke-glyphs-arphic-license.txt'],
            files={name:hashlib.sha256((cache/name).read_bytes()).hexdigest() for name in source_files}),
        rules=rules, positions=positions, referenceTemplates=references, review=review)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',type=Path,default=ROOT/'tmp/left-dot-research')
    parser.add_argument('--output',type=Path,default=ROOT/'src/proximic_ring/assets/stroke-joined-components.json.gz')
    args=parser.parse_args()
    result=build(args.cache)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_bytes(gzip.compress(json.dumps(result,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8'),mtime=0))
    print(f"{len(result['positions'])} component-annotated characters; {len(result['referenceTemplates'])} reference walk traces")


if __name__=='__main__': main()
