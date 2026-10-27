"""Measure z versus pn ambiguity against the actual local stroke dictionary.

This is a strict dictionary audit, not a handwriting-recognition accuracy test.
Only cnchar's explicit 撇点 shape is expanded in the genuine-single-stroke study.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
from collections import Counter, defaultdict
from functools import lru_cache
import hashlib
import itertools
import json
from pathlib import Path
import re
import statistics
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from proximic_ring.touchpad.stroke.stroke_common import common_characters


class PrefixIndex:
    def __init__(self, entries, ranks, allowed=None):
        self.rows = sorted({(code, char) for char, code, *_ in entries if allowed is None or char in allowed})
        self.codes = [code for code, char in self.rows]
        self.ranks = ranks

    @lru_cache(maxsize=None)
    def candidates(self, prefix):
        lo, hi = bisect_left(self.codes, prefix), bisect_left(self.codes, prefix+'{')
        return frozenset(char for code, char in self.rows[lo:hi])

    @lru_cache(maxsize=None)
    def exact(self, code):
        lo, hi = bisect_left(self.codes, code), bisect_left(self.codes, code+'\0')
        return frozenset(char for value, char in self.rows[lo:hi])

    def ranked(self, chars):
        return sorted(chars, key=lambda c: (self.ranks.get(c, 100000), c))


def interpretations(tokens, max_splits=None):
    ambiguous = [i for i, token in enumerate(tokens) if token == 'M']
    result = set()
    for parts in itertools.product(*[('z', 'pn') if t == 'M' else (t,) for t in tokens]):
        if max_splits is None or sum(parts[i] == 'pn' for i in ambiguous) <= max_splits:
            result.add(''.join(parts))
    return sorted(result)


def compare(index, tokens, max_splits=None):
    primary = tokens.replace('M', 'z')
    prefixes = interpretations(tokens, max_splits)
    base = index.candidates(primary)
    union = frozenset().union(*(index.candidates(p) for p in prefixes))
    base_exact = index.exact(primary)
    union_exact = frozenset().union(*(index.exact(p) for p in prefixes))
    added = union-base
    return dict(observed=tokens, primary=primary, interpretations=prefixes,
        primaryCount=len(base), unionCount=len(union), addedCount=len(added),
        primaryExactCount=len(base_exact), unionExactCount=len(union_exact),
        addedExact=index.ranked(union_exact-base_exact),
        addedExamples=index.ranked(added)[:20])


def distribution(rows):
    def stats(values):
        ordered = sorted(values)
        return dict(median=statistics.median(ordered), p90=ordered[max(0, (9*len(ordered)+9)//10-1)], max=max(ordered))
    if not rows:
        return dict(count=0)
    return dict(count=len(rows), primary=stats([r['primaryCount'] for r in rows]),
        union=stats([r['unionCount'] for r in rows]), added=stats([r['addedCount'] for r in rows]),
        noAdditional=sum(r['addedCount'] == 0 for r in rows),
        maxInterpretations=max(len(r['interpretations']) for r in rows),
        withAdditionalExact=sum(bool(r['addedExact']) for r in rows))


def analyze(cache):
    dictionary = StrokeDictionary()
    common = common_characters()
    with tarfile.open(cache/'cnchar-order.tgz') as archive:
        package = json.load(archive.extractfile('package/package.json'))
        source = archive.extractfile('package/cnchar.order.min.js').read().decode('utf-8')
    objects = [json.loads(v) for v in re.findall(r"JSON.parse\('([^']*)'\)", source)]
    orders = next(o for o in objects if '宝' in o)
    table = next(o for o in objects if isinstance(o.get('m'), dict))
    if package['version'] != '3.2.6' or table['m']['name'] != '撇点':
        raise ValueError('Unexpected fine-stroke source')
    mapping = {letter: ('h' if letter in 'ji' else 's' if letter in 'fg' else 'p' if letter == 's'
                        else 'n' if letter in 'dkl' else 'z') for letter in table}
    typed = {c: order for c, order in orders.items() if c in common and 'm' in order}
    supported, skipped = {}, []
    for char, order in typed.items():
        code = ''.join(mapping[t] for t in order)
        if code in {row[1] for row in dictionary.by_character.get(char, [])}:
            supported[char] = ''.join('M' if t == 'm' else mapping[t] for t in order)
        else:
            skipped.append(char)
    indexes = dict(common6500=PrefixIndex(dictionary.entries, dictionary.ranks, common),
                   fullDictionary=PrefixIndex(dictionary.entries, dictionary.ranks))
    report = dict(schema='ring-pie-dian-dictionary-audit', version=1,
        source=dict(strokeDictionarySha256=hashlib.sha256((ROOT/'src/proximic_ring/assets/strokes.json.gz').read_bytes()).hexdigest(),
            fineOrderSha256=hashlib.sha256((cache/'cnchar-order.tgz').read_bytes()).hexdigest(),
            fineOrderVersion=package['version']),
        population=dict(dictionaryCharacters=len(dictionary.by_character), dictionaryEntries=len(dictionary.entries),
            commonCharacters=len(common), fineOrderCommonCovered=sum(c in orders for c in common),
            explicitPieDianCommon=len(typed), alignedPieDianCommon=len(supported),
            skippedUnaligned=sorted(skipped),
            pieDianCountHistogram=dict(Counter(t.count('M') for t in supported.values()))),
        method='Strict prefix matching; character deduplication across all dictionary variants; all previous explicit pie-dian contacts retain z/pn choices. Other strokes are correct and unambiguous. No fuzzy substitutions, language context, split penalty or geometry gate. Counts precede UI truncation. Stage distributions deduplicate identical physical-input prefixes and are not usage-frequency weighted. Targets are aligned canonical orders of 223 common characters, even when the candidate pool is the full dictionary.',
        scopes={})
    for name, index in indexes.items():
        stages = defaultdict(dict)
        for char, tokens in supported.items():
            first = tokens.index('M')
            for stage, end in [('atFirst', first+1), ('oneLater', first+2), ('twoLater', first+3), ('complete', len(tokens))]:
                if end > len(tokens):
                    continue
                signature = tokens[:end]
                if signature not in stages[stage]:
                    stages[stage][signature] = dict(compare(index, signature), targets=[])
                stages[stage][signature]['targets'].append(char)
        cases = {stage: list(rows.values()) for stage, rows in stages.items()}
        # These are concrete physical input prefixes; M is one 撇点-shaped contact.
        examples = {tokens: compare(index, tokens) for tokens in
                    ['M', 'Mp', 'Mph', 'Mphszh', 'Mphzsh', 'nnzMph', 'MMM',
                     'nzsM', 'nzsMz', 'nzsMsn', 'nzsMzp', 'nzsMzpszn']}
        report['scopes'][name] = dict(
            summaries={stage: distribution(rows) for stage, rows in cases.items()},
            completeCollisions=sorted([r for r in cases['complete'] if r['addedExact']],
                                      key=lambda r: (-len(r['addedExact']), r['observed'])),
            threePieDianWithOneSplit=compare(index, 'MMM', max_splits=1),
            examples=examples, cases=cases)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=ROOT/'tmp'/'left-dot-research')
    parser.add_argument('--output', type=Path, default=ROOT/'data'/'diagnostics'/'joined-strokes'/'pie-dian-dictionary-audit.json')
    args = parser.parse_args()
    report = analyze(args.cache)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(population=report['population'], scopes={name: value['summaries'] for name, value in report['scopes'].items()}), ensure_ascii=True))


if __name__ == '__main__':
    main()
