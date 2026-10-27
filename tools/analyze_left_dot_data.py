"""Build an offline left-dot review list; this does not change runtime matching.

Inputs: cached cnchar-order npm tarball and Make Me a Hanzi graphics.txt.
Point2 is treated as a review label, not a verified universal left-dot label.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import gzip
import hashlib
import json
from pathlib import Path
import re
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def analyze(cache, output):
    with tarfile.open(cache / 'cnchar-order.tgz') as archive:
        source = archive.extractfile('package/cnchar.order.min.js').read().decode('utf-8')
        package = json.load(archive.extractfile('package/package.json'))
    # Read static embedded JSON, without executing downloaded JavaScript.
    objects = [json.loads(value) for value in re.findall(r"JSON.parse\('([^']*)'\)", source)]
    orders = next(value for value in objects if '\u5b9d' in value)
    table = next(value for value in objects if isinstance(value.get('d'), dict))
    assert table['d']['name'] == '\u70b92', 'Unexpected cnchar point2 encoding'
    mapping = {letter: ('h' if letter in 'ji' else 's' if letter in 'fg'
                       else 'p' if letter == 's' else 'n' if letter in 'dkl' else 'z')
               for letter in table}
    common_path = ROOT / 'src/proximic_ring/assets/stroke-common-characters.json'
    common = json.loads(common_path.read_text(encoding='utf-8'))['characters']
    with gzip.open(ROOT / 'src/proximic_ring/assets/strokes.json.gz', 'rt', encoding='utf-8') as stream:
        entries = json.load(stream)['entries']
    by_character = defaultdict(list)
    for character, code, _ in entries:
        by_character[character].append(code)
    graphics = {}
    with (cache / 'graphics.txt').open(encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            if row['character'] in common:
                graphics[row['character']] = row

    rows, extra = [], []
    for character in common:
        order = orders.get(character)
        if order is None:
            continue
        code = ''.join(mapping[letter] for letter in order)
        medians = graphics.get(character, {}).get('medians', [])
        count_agrees = len(medians) == len(order)
        dots = []
        for index, letter in enumerate(order):
            if letter not in 'dk':
                continue
            delta = None
            if count_agrees and len(medians[index]) >= 2:
                delta = [medians[index][-1][axis] - medians[index][0][axis] for axis in (0, 1)]
            direction = ('unavailable' if delta is None else
                         'left-down' if delta[0] < 0 and delta[1] < 0 else 'other')
            dot = dict(stroke_number=index + 1, delta=delta, direction=direction)
            if letter == 'd':
                dots.append(dot)
            elif direction == 'left-down':
                extra.append(dict(character=character, **dot,
                                  status='review ordinary point; equal counts do not prove stroke alignment'))
        if dots:
            rows.append(dict(character=character, cnchar_order=order, canonical_code=code,
                             existing_codes=by_character[character],
                             exact_code_present=code in by_character[character],
                             glyph_count_agrees=count_agrees, point2=dots,
                             status='review candidate; not enabled in runtime'))

    # Snapshot the completed reports, including retries, rather than the live raw log.
    trials = sorted((ROOT / 'data/stroke-test').glob('20261013_193210*-trial-*.json'))
    events, trial_sources = [], []
    for path in trials:
        trial = json.loads(path.read_text(encoding='utf-8'))
        actual = ''
        trial_sources.append(dict(file=path.name, sha256=sha256(path), target=trial['target'],
                                  actual=trial['actual'], manual=trial['contains_manual_strokes']))
        for event in trial['events']:
            if event['kind'] == 'commit' and event['delivered']:
                actual += event['character']
            elif event['kind'] == 'delete_character':
                actual = actual[:-1]
            elif event['kind'] == 'stroke':
                character, index = event.get('character'), event['stroke_index']
                order = orders.get(character, '')
                codes = by_character.get(character, [])
                if (codes and len(order) == len(codes[0]) and index < len(order)
                        and order[index] == 'd' and event.get('expected') == 'n'):
                    events.append(dict(file=path.name, **event,
                                       correct_text_prefix=actual == trial['target'][:event['position']]))
    summary = dict(
        schema='left-dot-review-v1', runtime_changed=False,
        common_characters=len(common), cnchar_covered=sum(c in orders for c in common),
        point2_characters=len(rows), point2_strokes=sum(len(row['point2']) for row in rows),
        characters_with_exact_existing_code=sum(row['exact_code_present'] for row in rows),
        glyph_count_agrees=sum(row['glyph_count_agrees'] for row in rows),
        direction_counts=dict(Counter(dot['direction'] for row in rows for dot in row['point2'])),
        extra_ordinary_points=extra,
        cnchar_version=package['version'],
        sources=[dict(file=name, sha256=sha256(cache / name)) for name in
                 ('cnchar-order.tgz', 'graphics.txt', 'dictionary.txt', 'COPYING')],
        trial_sources=trial_sources,
        trial_point2_events=len(events),
        trial_recognized_counts=dict(Counter(event['recognized'] for event in events)),
        trial_valid_prefix_events=sum(event['correct_text_prefix'] for event in events),
        limitations=['Point2 is not always a leftward path.',
                     'Equal stroke counts alone do not establish per-stroke alignment.',
                     'Trial labels follow the current target and first dictionary order; no manual intent labels.',
                     'Compatibility at one position is not character recall, ranking, or input accuracy.'],
    )
    output.mkdir(parents=True, exist_ok=True)
    for name, value in [('summary.json', summary), ('characters.json', rows), ('trial-events.json', events)]:
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    with (output / 'characters.csv').open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.writer(stream)
        writer.writerow(['character', 'canonical_code', 'stroke_number', 'dx', 'dy',
                         'direction', 'exact_code_present', 'review_status'])
        for row in rows:
            for dot in row['point2']:
                writer.writerow([row['character'], row['canonical_code'], dot['stroke_number'],
                                 *(dot['delta'] or ['', '']), dot['direction'],
                                 row['exact_code_present'], row['status']])
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=ROOT / 'tmp/left-dot-research')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/diagnostics/left-dot-review')
    args = parser.parse_args()
    result = analyze(args.cache, args.output)
    print(json.dumps({key: value for key, value in result.items()
                      if key not in ('sources', 'trial_sources', 'limitations')}, ensure_ascii=False, indent=2))
