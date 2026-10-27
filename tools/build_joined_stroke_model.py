"""Audit captures, cross-check rounds, and build a local experimental model."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from functools import lru_cache
import gzip
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from proximic_ring.touchpad.stroke.stroke_dtw import sequence
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from proximic_ring.touchpad.stroke.stroke_common import common_characters
from proximic_ring.touchpad.stroke.stroke_fuzzy import stroke_evidence
from proximic_ring.touchpad.stroke.stroke_joined import FAMILIES, POLICY, JoinedStrokeModel, sample_family, merge_joined_candidates
from selection_capture import integration_error, atomic_save
from build_left_dot_annotations import parse_ids
import joined_stroke_collection as profile

SEEDS = {'反': (4, 'fan', 0), '白': (5, 'de', 0), '了': (2, 'le-zi', 0),
         '子': (3, 'le-zi', 0), '衤': (5, 'yi-pie-dian', 3)}
EXAMPLES = {'fan': ('反', 'ppzn', 0), 'de': ('的', 'pszhhpzn', 0),
            'le': ('了', 'zs', 0), 'zi': ('子', 'zsh', 0),
            'yi-pie-dian': ('补', 'nzspnsn', 3)}


def aligned_pair(base, variant, start):
    """Keep only a consecutive pair with a unique optimal edit alignment."""
    @lru_cache(None)
    def walk(i, j):
        if i == len(base): return len(variant)-j, {()}
        if j == len(variant): return len(base)-i, {(None,)*(len(base)-i)}
        candidates = []
        cost, paths = walk(i+1, j+1)
        candidates.append((cost+(base[i] != variant[j]), {(j,)+p for p in paths}))
        cost, paths = walk(i+1, j)
        candidates.append((cost+1, {(None,)+p for p in paths}))
        cost, paths = walk(i, j+1)
        candidates.append((cost+1, paths))
        best = min(c[0] for c in candidates)
        return best, set().union(*(paths for cost, paths in candidates if cost == best))
    # Far variants are deliberately left on the unchanged single-stroke path.
    if abs(len(base)-len(variant)) > 2: return None
    cost, paths = walk(0, 0)
    if cost > 2: return None
    pairs = {(p[start], p[start+1]) for p in paths}
    if len(pairs) != 1: return None
    a, b = pairs.pop()
    return a if a is not None and b == a+1 else None


def build_positions(dictionary, cache):
    orders = json.loads((cache/'cnchar-orders.json').read_text(encoding='utf-8'))
    table = json.loads((cache/'cnchar-stroke-table.json').read_text(encoding='utf-8'))
    if table.get('m', {}).get('name') != '撇点': raise ValueError('Unexpected fine-stroke data')
    mapping = {letter: ('h' if letter in 'ji' else 's' if letter in 'fg' else 'p' if letter == 's'
                        else 'n' if letter in 'dkl' else 'z') for letter in table}
    decompositions = {}
    for line in (cache/'dictionary.txt').read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        decompositions[row['character']] = row

    @lru_cache(None)
    def components(char, count, seen=()):
        if char in seen: return ()
        if char in SEEDS:
            expected, family, start = SEEDS[char]
            return ((family, start, start+1),) if count == expected else ()
        data = decompositions.get(char, {})
        matches = data.get('matches', [])
        if len(matches) != count: return ()
        found = []
        for leaf, path in parse_ids(data.get('decomposition', '')):
            indices = [i for i, match in enumerate(matches) if match is not None and tuple(match[:len(path)]) == path]
            if not indices: continue
            for family, a, b in components(leaf, len(indices), seen+(char,)):
                if indices[b] == indices[a]+1:
                    found.append((family, indices[a], indices[b]))
        return tuple(found)

    result = {}
    for char, entries in dictionary.by_character.items():
        order = orders.get(char)
        if not order: continue
        base = ''.join(mapping[t] for t in order)
        if base not in {e[1] for e in entries}: continue
        seeds = components(char, len(base))
        if not seeds: continue
        variants = {}
        for _, code, _ in entries:
            annotations = []
            for family, a, b in seeds:
                start = a if base == code else aligned_pair(base, code, a)
                if start is not None and code[start:start+2] in FAMILIES[family]:
                    value = dict(family=family, start=start)
                    if value not in annotations: annotations.append(value)
            if annotations: variants[code] = annotations
        if variants: result[char] = variants
    return result


def load_samples(paths):
    sessions, duplicate_paths, samples = {}, [], []
    for path in paths:
        data = profile.validate_session(json.loads(path.read_text(encoding='utf-8')))
        key = data['sessionId']
        if key in sessions:
            previous = sessions[key]
            if data['tasks'] != previous['tasks'] or data['samples'] != previous['samples']:
                raise ValueError('同一session的记录与导出不一致，请明确选择要使用的文件')
            duplicate_paths.append(str(path))
            continue
        if data['phase'] != 'train': continue
        if len(data['samples']) != len(data['tasks']) or data.get('draft') is not None:
            raise ValueError(f'{path}: 采集尚未完成')
        for sample in data['samples']:
            if (integration_error(sample) > 1e-6 or sample['pathLength'] <= 0
                    or not sample.get('quality', {}).get('complete') or sequence([(p['x'],p['y']) for p in sample['points']]) is None):
                raise ValueError(f'{path}: 不完整或无效轨迹，不能直接建模')
            samples.append(dict(sample, sessionId=key))
        sessions[key] = data
    if not samples: raise ValueError('没有完整的训练采集记录')
    return samples, sessions, duplicate_paths


def payload_for(samples, positions, source):
    templates = []
    for s in samples:
        points = [(p['x'], p['y']) for p in s['points']]
        templates.append(dict(family=sample_family(s), features=sequence(points).tolist(),
            sampleId=f"{s['sessionId']}:{s['taskIndex']}"))
    return dict(schema='ring-joined-stroke-model', version=1, policy=POLICY,
        source=source, templates=templates, positions=positions)


def evaluate(samples, positions, dictionary):
    details = []
    folds = sorted({(s['sessionId'], s['task']['round']) for s in samples})
    if len(folds) < 2: raise ValueError('至少需要两轮数据才能做逐轮留出检查')
    for session, rnd in folds:
        training = [s for s in samples if (s['sessionId'],s['task']['round']) != (session,rnd)]
        model = JoinedStrokeModel(payload_for(training, positions, dict(evaluation='held-out-round')))
        for sample in samples:
            if (sample['sessionId'],sample['task']['round']) != (session,rnd): continue
            options = model.recognize([(p['x'],p['y']) for p in sample['points']])
            truth = sample_family(sample)
            row = dict(sessionId=session, round=rnd, label=sample['task']['label'], family=truth,
                options=options, correctFamilyAvailable=any(o['family'] == truth for o in options))
            if truth != 'single':
                char, code, start = EXAMPLES[sample['task']['target']]
                original = sample['singleStrokeBaseline']
                ink = stroke_evidence(original['category'], original)
                ink['joined_options'] = options
                evidence = [stroke_evidence(c) for c in code[:start]]+[ink]+[stroke_evidence(c) for c in code[start+2:]]
                normal = dictionary.fuzzy_candidates(evidence, allowed_characters=common_characters())
                chars = [r['char'] for r in normal]
                merged, matches = merge_joined_candidates(dictionary,evidence,chars,normal,model,allowed=common_characters())
                row.update(replayTarget=char, normalTop5=chars[:5], joinedTop5=merged[:5],
                    top5Before=char in chars[:5], top5After=char in merged[:5],
                    logicalComplete=any(m['char'] == char and m.get('logical_entered') == len(code) for m in matches))
            details.append(row)
    joined = [r for r in details if r['family'] != 'single']
    controls = [r for r in details if r['family'] == 'single']
    return dict(method='Leave one complete round out; same wearer/session, not independent validation. Whole-character replay uses the real joined contact and ideal manual remaining strokes; no language context.',
        joined=len(joined), familyAvailable=sum(r['correctFamilyAvailable'] for r in joined),
        wholeCharacterTop5Before=sum(r['top5Before'] for r in joined),
        wholeCharacterTop5After=sum(r['top5After'] for r in joined),
        controls=len(controls), controlsWithOptionalAlternatives=sum(bool(r['options']) for r in controls),
        forcedSplits=0, details=details)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paths', nargs='*', type=Path)
    parser.add_argument('--cache', type=Path, default=ROOT/'tmp'/'left-dot-research')
    parser.add_argument('--output', type=Path, default=ROOT/'data'/'models'/'joined-strokes'/'model.json.gz')
    parser.add_argument('--report', type=Path, default=ROOT/'data'/'diagnostics'/'joined-strokes'/'model-review.json')
    args = parser.parse_args()
    paths = args.paths or sorted((ROOT/'data'/'recordings'/'joined-strokes').glob('*.json'))
    samples, sessions, duplicates = load_samples(paths)
    dictionary = StrokeDictionary()
    positions = build_positions(dictionary, args.cache)
    digest = hashlib.sha256(json.dumps(samples, sort_keys=True).encode()).hexdigest()
    source = dict(fingerprint=digest, sessions=list(sessions), rounds=len({(s['sessionId'],s['task']['round']) for s in samples}),
        sampleCount=len(samples), familyCounts=dict(Counter(sample_family(s) for s in samples)),
        dictionarySha256=hashlib.sha256((ROOT/'src/proximic_ring/assets/strokes.json.gz').read_bytes()).hexdigest(),
        positionSources={f:hashlib.sha256((args.cache/f).read_bytes()).hexdigest()
                         for f in ('dictionary.txt','cnchar-orders.json','cnchar-stroke-table.json')})
    report = dict(source=source, duplicatesIgnored=duplicates,
        quality=dict(integrationMaxError=max(integration_error(s) for s in samples),
            saved=len(samples), rejected=sum(len(s['rejected']) for s in sessions.values()),
            interruptions=sum(len(s['interruptions']) for s in sessions.values())),
        annotatedCharacters=len(positions), evaluation=evaluate(samples, positions, dictionary))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(gzip.compress(json.dumps(payload_for(samples, positions, source),ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8'),mtime=0))
    atomic_save(args.report, report)
    print(json.dumps(dict(source=source, quality=report['quality'], annotatedCharacters=len(positions),
        evaluation={k:v for k,v in report['evaluation'].items() if k != 'details'}), ensure_ascii=True))


if __name__ == '__main__': main()
