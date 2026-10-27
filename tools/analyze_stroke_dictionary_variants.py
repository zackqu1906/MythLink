"""Audit dictionary variants against the current glyph labels without changing assets."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from dataclasses import replace
import gzip
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from proximic_ring.touchpad.stroke.stroke_common import common_characters
from proximic_ring.touchpad.stroke.stroke_glyphs import _heart_components, glyph_library, stroke_mapping
from proximic_ring.touchpad.stroke.stroke_input import StrokeDictionary
from proximic_ring.touchpad.stroke.stroke_word_correction import (
    DEFAULT_CORRECTION_POLICY, WholeCharacterCorrector, align_strokes, merge_candidates,
)


def repair_eligibility(observed, expected, distance):
    policy = DEFAULT_CORRECTION_POLICY
    if len(observed) < policy.min_strokes:
        return 'too_short'
    if len(observed) > policy.max_strokes:
        return 'too_long'
    budget = policy.max_edits if len(observed) >= policy.two_edit_min_strokes else 1
    if distance > budget:
        return 'over_budget'
    if expected.startswith(observed):
        return 'ordinary_prefix_or_equal'
    return 'reachable'


def summarize(records):
    affected = {row['character'] for row in records}
    return dict(
        entries=len(records), characters=len(affected),
        distanceHistogram=dict(sorted(Counter(row['distance'] for row in records).items())),
        sourceHistogram=dict(sorted(Counter(row['source'] for row in records).items())),
        strokeCounts=dict(Counter(
            'same' if len(row['code']) == len(row['reference']) else 'different' for row in records)),
        operationGroups=dict(sorted(Counter(
            '+'.join(sorted({edit['kind'] for edit in row['edits']})) for row in records).items())),
        singleEditOperations=dict(Counter(
            row['edits'][0]['kind'] for row in records if row['distance'] == 1)),
        eligibility=dict(Counter(row['eligibility'] for row in records)),
        replay=dict(Counter(row['replay'] for row in records if 'replay' in row)),
    )


def inspect_entries(dictionary, library, hearts, allowed=None):
    records, coverage = [], Counter()
    for character, code, source in dictionary.entries:
        if allowed is not None and character not in allowed:
            continue
        coverage['entries'] += 1
        glyph = library.get(character)
        if not glyph:
            coverage['missingGlyphEntries'] += 1
            continue
        reference = glyph['code']
        if not reference:
            coverage['missingLabelEntries'] += 1
            continue
        if code == reference:
            coverage['referenceEntries'] += 1
            continue
        mapped = stroke_mapping(reference, code, hearts.get(character, ())) is not None
        alignment = align_strokes(code, reference)
        records.append(dict(
            character=character, code=code, reference=reference, source=source,
            mapped=mapped, distance=alignment['distance'], edits=alignment['edits'],
            referenceInDictionary=any(row[1] == reference for row in dictionary.by_character[character]),
            eligibility=repair_eligibility(code, reference, alignment['distance']),
        ))
    return records, dict(coverage)


def replacement_dictionary(dictionary, library, allowed):
    references = {character: library[character]['code'] for character in allowed
                  if library.get(character, {}).get('code')
                  and any(row[1] == library[character]['code']
                          for row in dictionary.by_character[character])}
    entries = [row for row in dictionary.entries
               if row[0] not in references or row[1] == references[row[0]]]
    assert {row[0] for row in entries} == set(dictionary.by_character)
    return StrokeDictionary(dict(entries=entries, ranks=dictionary.ranks, templates=[]))


def replay_records(records, dictionary, allowed):
    policy = replace(DEFAULT_CORRECTION_POLICY, limit=len(allowed))
    corrector = WholeCharacterCorrector(dictionary.entries, dictionary.ranks,
                                      policy=policy, allowed_characters=allowed)
    observations = {}
    eligible = [row for row in records if row['referenceInDictionary']]
    started = time.monotonic()
    for index, row in enumerate(eligible, 1):
        observed, character = row['code'], row['character']
        if observed not in observations:
            normal = dictionary.candidates(observed, allowed_characters=allowed)
            repairs = corrector.candidates(observed, excluded_characters=normal)
            positions = {repair['char']: position for position, repair in enumerate(repairs, 1)}
            merged = merge_candidates(normal, repairs[:DEFAULT_CORRECTION_POLICY.limit])
            observations[observed] = normal, positions, merged
        normal, positions, merged = observations[observed]
        rank = positions.get(character)
        row['repairRank'] = rank
        row['mergedRank'] = merged.index(character) + 1 if character in merged else None
        if character in normal:
            row['replay'] = 'ordinary_candidate'
        elif rank is not None and rank <= DEFAULT_CORRECTION_POLICY.limit:
            row['replay'] = 'repair_first_page' if row['mergedRank'] <= 6 else 'repair_later_page'
        elif rank is not None:
            row['replay'] = 'repair_truncated_after_18'
        else:
            row['replay'] = 'outside_search'
        if index % 1000 == 0:
            print(f'Replayed {index}/{len(eligible)} variants in {time.monotonic()-started:.1f}s', flush=True)
    return corrector


def case_replay(original, replacement, corrector, allowed, code, target):
    before = original.candidates(code, allowed_characters=allowed)
    normal = replacement.candidates(code, allowed_characters=allowed)
    repairs = corrector.candidates(code, excluded_characters=normal)
    visible_repairs = repairs[:DEFAULT_CORRECTION_POLICY.limit]
    merged = merge_candidates(normal, visible_repairs)
    repair = next((row for row in repairs if row['char'] == target), None)
    return dict(
        observed=code, target=target, beforeFirstSix=before[:6],
        afterNormalFirstSix=normal[:6], afterMergedFirstSix=merged[:6],
        beforeRank=before.index(target)+1 if target in before else None,
        afterNormalRank=normal.index(target)+1 if target in normal else None,
        repairRank=next((index for index, row in enumerate(repairs, 1) if row['char'] == target), None),
        afterMergedRank=merged.index(target)+1 if target in merged else None,
        correction=repair['correction_summary'] if repair else '',
        prefixDistance=align_strokes(code, glyph_library()[target]['code'])['distance'],
    )


def audit():
    raw = json.loads(gzip.decompress((ROOT/'src/proximic_ring/assets/strokes.json.gz').read_bytes()))
    dictionary, library = StrokeDictionary(raw), glyph_library()
    hearts, allowed = _heart_components(), common_characters()
    records, common_coverage = inspect_entries(dictionary, library, hearts, allowed)
    full_records, full_coverage = inspect_entries(dictionary, library, hearts)
    replacement = replacement_dictionary(dictionary, library, allowed)
    corrector = replay_records(records, replacement, allowed)
    unsupported = [row for row in records if not row['mapped']]
    missing_references = [dict(character=character, reference=library[character]['code'],
                               dictionaryCodes=[row[1] for row in dictionary.by_character[character]])
                          for character in sorted(allowed)
                          if library.get(character, {}).get('code')
                          and not any(row[1] == library[character]['code']
                                      for row in dictionary.by_character[character])]
    cases = [case_replay(dictionary, replacement, corrector, allowed, code, character)
             for code, character in [('hhss', '舌'), ('hhsszh', '舌'), ('phsszh', '舌'),
                                     ('hhsszhz', '乱'), ('hhsszhhsshh', '甜'),
                                     ('hhsszhnzn', '适'), ('hhsszhphpn', '敌'),
                                     ('hz', '丁'), ('pshnzshhhhhpszss', '儒')]]
    she_prefixes = [case_replay(dictionary, replacement, corrector, allowed,
                               'hhsszh'[:length], '舌') for length in range(1, 7)]
    pure_one_replacement = [row for row in unsupported
                           if row['distance'] == 1 and row['edits'][0]['kind'] == 'substitute']
    report = dict(
        schema='ring-stroke-dictionary-variant-audit', version=1,
        method=dict(
            reference='Current cnchar-order labels attached to Hanzi Writer glyphs, not an independently verified normative standard.',
            comparison='Five-category optimal string alignment; equal codes and verified heart-component mappings are distinguished.',
            replay='One reference per character where already present; preserve all entries for missing labels or absent references. No changes to runtime assets.',
            ranking='Strict/manual input, no context or recognition alternatives; current top-30 normal, top-18 repair and six-slot merge.',
            wholeCorrectionPolicy=DEFAULT_CORRECTION_POLICY.snapshot(),
            sourceBits={'1': 'Rime', '2': 'Conway', '3': 'both'},
        ),
        assetSha256={name: hashlib.sha256((ROOT/'src/proximic_ring/assets'/name).read_bytes()).hexdigest()
                     for name in ('strokes.json.gz', 'stroke-glyphs.json.gz',
                                  'stroke-common-characters.json', 'stroke-left-dots.json')},
        common=dict(characters=len(allowed), coverage=common_coverage,
                    nonReference=summarize(records), unsupported=summarize(unsupported),
                    verifiedMappings=summarize([row for row in records if row['mapped']]),
                    singleReplacement=summarize(pure_one_replacement),
                    missingGlyphCharacters=sum(character not in library for character in allowed),
                    missingLabelCharacters=sum(character in library and not library[character]['code']
                                               for character in allowed),
                    absentReferences=missing_references,
                    removedInSimulation=sum(character in allowed for character, *_ in dictionary.entries)
                    - sum(character in allowed for character, *_ in replacement.entries)),
        full=dict(characters=len(dictionary.by_character), coverage=full_coverage,
                  nonReference=summarize(full_records),
                  unsupported=summarize([row for row in full_records if not row['mapped']])),
        cases=cases, shePrefixes=she_prefixes, variants=records,
    )
    assert sum(value for key, value in common_coverage.items() if key != 'entries') + len(records) == common_coverage['entries']
    assert report['common']['unsupported']['entries'] + report['common']['verifiedMappings']['entries'] == len(records)
    assert sum(report['common']['nonReference']['replay'].values()) == report['common']['removedInSimulation']
    assert next(row for row in cases if row['observed'] == 'hhsszh')['correction'] == '输入第1笔一→丿'
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path,
                        default=ROOT/'data/diagnostics/dictionary-variants/audit.json')
    args = parser.parse_args()
    report = audit()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    with args.output.with_suffix('.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        columns = ['character', 'code', 'reference', 'source', 'mapped', 'referenceInDictionary',
                   'distance', 'eligibility', 'replay', 'repairRank', 'mergedRank', 'edits']
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in report['variants']:
            writer.writerow(dict(row, edits=json.dumps(row['edits'], ensure_ascii=False)))
    print(json.dumps({key: value for key, value in report.items()
                      if key not in ('variants', 'assetSha256')}, ensure_ascii=False, indent=2))
    print(args.output)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
