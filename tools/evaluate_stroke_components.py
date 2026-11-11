"""Offline coverage/alignment audit; synthetic schedules are not user accuracy."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from proximic_ring.touchpad.stroke.stroke_components import RULES
from proximic_ring.touchpad.stroke.stroke_fuzzy import stroke_evidence
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary


def apply_order(order, occurrence, variant):
    component, indices, scope = occurrence['component'], occurrence['indices'], occurrence['scope']
    selected = set(scope)
    slots = [i for i, index in enumerate(order) if index in selected]
    if component in '辶廴匚囗':
        reordered = list(indices)+[index for index in order if index in selected and index not in indices]
    else:
        reordered = [indices[i] for i in RULES[component][1][variant]]
    result = list(order)
    for slot, index in zip(slots, reordered):
        result[slot] = index
    return tuple(result)


def audit(dictionary, ranking_samples=40):
    decoder = dictionary.component_decoder
    counts, failures, timing, checked = Counter(), [], [], 0
    corpus = []
    for char, annotation in decoder.annotations.items():
        canonical = annotation['code']
        if not any(e[1] == canonical for e in dictionary.by_character.get(char, ())):
            continue
        identity = tuple(range(len(canonical)))
        cases = [('reference', identity)]
        combined = identity
        for occurrence in reversed(annotation['components']):
            component = occurrence['component']
            variants = range(1, len(RULES[component][1])) if component not in '辶廴匚囗' else (1,)
            for variant in variants:
                order = apply_order(identity, occurrence, variant)
                if order != identity:
                    cases.append((component, order))
            if len(RULES[component][1]) > 1 or component in '辶廴匚囗':
                combined = apply_order(combined, occurrence, 1)
        if combined != identity:
            cases.append(('combined', combined))
        for component, order in cases:
            observed = ''.join(canonical[i] for i in order)
            ink = [stroke_evidence(c) for c in observed]
            rows = decoder.match(ink, allowed={char})
            checked += 1
            counts[component] += 1
            if not any(r['observed_to_expected'] == order and r['corrections'] == [] and r['is_complete'] for r in rows):
                failures.append(dict(character=char, component=component, code=observed, mapping=order))
            if component not in ('reference', 'combined'):
                corpus.append((char, observed))
    ranking_failures, top1, top5 = [], 0, 0
    samples = corpus[::max(1, len(corpus)//max(1, ranking_samples))][:ranking_samples]
    for char, observed in samples:
        ink = [stroke_evidence(c) for c in observed]
        start = time.perf_counter()
        rows = dictionary.encoding_candidates(ink, limit=30)
        timing.append((time.perf_counter()-start)*1000)
        chars = [r['char'] for r in rows]
        top1 += bool(chars and chars[0] == char)
        top5 += char in chars[:5]
        if char not in chars:
            ranking_failures.append(dict(character=char, code=observed, reason='outside top-30'))
    return dict(schema='ring-stroke-component-audit', version=1,
                method='Synthetic role-order coverage and alignment, not real handwriting accuracy.',
                decoder=decoder.snapshot(), alignmentCases=checked, byComponent=dict(counts),
                alignmentFailures=failures, rankingSamples=len(samples), top1=top1, top5=top5,
                outsideTop30=ranking_failures,
                latencyMs=dict(median=statistics.median(timing) if timing else None,
                               p95=sorted(timing)[min(len(timing)-1, int(.95*len(timing)))] if timing else None))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'data/diagnostics/stroke-components/report.json')
    parser.add_argument('--ranking-samples', type=int, default=40)
    args = parser.parse_args()
    report = audit(StrokeDictionary(), ranking_samples=args.ranking_samples)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k not in ('alignmentFailures', 'outsideTop30')}, ensure_ascii=False))
    print(f"Alignment failures: {len(report['alignmentFailures'])}; outside top-30: {len(report['outsideTop30'])}")
    return bool(report['alignmentFailures'])


if __name__ == '__main__':
    raise SystemExit(main())
