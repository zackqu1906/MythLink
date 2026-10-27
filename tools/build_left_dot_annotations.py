"""Build position-specific left-dot annotations from cached public source data."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import re
import tarfile

ROOT = Path(__file__).resolve().parents[1]
SEEDS = {'宀': (1,), '忄': (0,), '心': (0,), '冖': (0,)}
IDS_ARITY = {chr(value): (3 if value in (0x2FF2, 0x2FF3) else 2)
             for value in range(0x2FF0, 0x2FFC)}


def parse_ids(text):
    """Return leaf characters and their decomposition-tree paths."""
    def read(index, path):
        character = text[index]
        index += 1
        if character not in IDS_ARITY:
            return [(character, path)], index
        leaves = []
        for child in range(IDS_ARITY[character]):
            values, index = read(index, path + (child,))
            leaves.extend(values)
        return leaves, index
    try:
        leaves, consumed = read(0, ())
        return leaves if consumed == len(text) else []
    except IndexError:
        return []


def component_seeds(character, count, dictionary, *, seen=()):
    """Map seed-local strokes through matches; incomplete mappings are skipped."""
    if character in seen:
        return []
    if character in SEEDS:
        expected = {'宀': 3, '忄': 3, '心': 4, '冖': 2}[character]
        if count == expected:
            return [(character, tuple(range(count)), SEEDS[character], character)]
        return []
    data = dictionary.get(character, {})
    matches = data.get('matches', [])
    if len(matches) != count:
        return []
    found = []
    for leaf, path in parse_ids(data.get('decomposition', '')):
        if leaf in ('？', character):
            continue
        indices = tuple(index for index, match in enumerate(matches)
                        if match is not None and tuple(match[:len(path)]) == path)
        if not indices:
            continue
        for seed, child_indices, dots, origin in component_seeds(
                leaf, len(indices), dictionary, seen=seen + (character,)):
            found.append((seed, tuple(indices[index] for index in child_indices), dots,
                          character + '/' + origin))
    return found


def aligned_positions(base, variant, positions):
    """Keep only positions whose mapping agrees across every optimal alignment.

    This deliberately rejects ambiguous repeated strokes and distant variants.
    Known 忄 permutations are handled separately before this alignment.
    """
    m, n = len(base), len(variant)
    forward = [[0] * (n + 1) for _ in range(m + 1)]
    backward = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(m + 1): forward[i][0] = i
    for j in range(n + 1): forward[0][j] = j
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            forward[i][j] = min(forward[i-1][j] + 1, forward[i][j-1] + 1,
                                forward[i-1][j-1] + (base[i-1] != variant[j-1]))
    distance = forward[m][n]
    if distance > 2:
        return {}
    for i in range(m + 1): backward[i][n] = m-i
    for j in range(n + 1): backward[m][j] = n-j
    for i in range(m-1, -1, -1):
        for j in range(n-1, -1, -1):
            backward[i][j] = min(backward[i+1][j] + 1, backward[i][j+1] + 1,
                                 backward[i+1][j+1] + (base[i] != variant[j]))
    result = {}
    for index in positions:
        possible = set()
        for j in range(n + 1):
            if forward[index][j] + 1 + backward[index+1][j] == distance:
                possible.add(None)
            if j < n and forward[index][j] + (base[index] != variant[j]) + backward[index+1][j+1] == distance:
                possible.add(j if variant[j] == 'n' else None)
        if len(possible) == 1 and None not in possible:
            result[index] = possible.pop()
    return result


def build(cache):
    with tarfile.open(cache / 'cnchar-order.tgz') as archive:
        source = archive.extractfile('package/cnchar.order.min.js').read().decode('utf-8')
        version = json.load(archive.extractfile('package/package.json'))['version']
    objects = [json.loads(value) for value in re.findall(r"JSON.parse\('([^']*)'\)", source)]
    orders = next(value for value in objects if '宝' in value)
    table = next(value for value in objects if isinstance(value.get('d'), dict))
    if version != '3.2.6' or table['d']['name'] != '点2':
        raise ValueError('Expected cnchar-order 3.2.6 point2 dictionary')
    mapping = {letter: ('h' if letter in 'ji' else 's' if letter in 'fg' else 'p' if letter == 's'
                        else 'n' if letter in 'dkl' else 'z') for letter in table}
    common = json.loads((ROOT / 'src/proximic_ring/assets/stroke-common-characters.json').read_text(encoding='utf-8'))['characters']
    with gzip.open(ROOT / 'src/proximic_ring/assets/strokes.json.gz', 'rt', encoding='utf-8') as stream:
        entries = json.load(stream)['entries']
    codes = defaultdict(set)
    for character, code, _ in entries: codes[character].add(code)
    sources = {}
    for filename in ('dictionary.txt', 'graphics.txt'):
        with (cache / filename).open(encoding='utf-8') as stream:
            sources[filename] = {row['character']: row for line in stream if (row := json.loads(line))}
    dictionary, graphics = sources['dictionary.txt'], sources['graphics.txt']
    annotations, review = [], []
    counts = Counter()
    for character in common:
        order = orders.get(character, '')
        base = ''.join(mapping[letter] for letter in order)
        medians = graphics.get(character, {}).get('medians', [])
        if not order or len(order) != len(medians) or base not in codes[character]:
            if 'd' in order:
                review.append(dict(character=character, reason='canonical order cannot be aligned to existing code'))
            continue
        origins = defaultdict(set)
        for index, letter in enumerate(order):
            if letter != 'd': continue
            dx, dy = (medians[index][-1][axis] - medians[index][0][axis] for axis in (0, 1))
            if dx < 0 and dy < 0:
                origins[index].add('cnchar-point2+glyph-left-down')
            else:
                review.append(dict(character=character, stroke_number=index+1,
                                   reason='point2 glyph is not left-down'))
        components = component_seeds(character, len(order), dictionary)
        for seed, indices, dots, path in components:
            for local_index in dots:
                index = indices[local_index]
                dx, dy = (medians[index][-1][axis] - medians[index][0][axis] for axis in (0, 1))
                if order[index] in 'dk' and dx < 0 and dy < 0:
                    origins[index].add('component:' + path)
        if not origins: continue
        for variant in sorted(codes[character]):
            adjusted = base
            remapped = {index: index for index in origins}
            # Explicit, verified three-stroke 忄 orders, with left/right dots distinct.
            if len(variant) == len(base):
                for seed, indices, _, _ in components:
                    if seed != '忄' or indices != tuple(range(indices[0], indices[0]+3)):
                        continue
                    start = indices[0]
                    alternative = variant[start:start+3]
                    if start in origins and base[start:start+3] == 'nns' and alternative in ('nsn', 'snn'):
                        adjusted = adjusted[:start] + alternative + adjusted[start+3:]
                        remapped[start] = start + (alternative == 'snn')
            mapped = aligned_positions(adjusted, variant, remapped.values())
            enabled = {}
            for original, adjusted_index in remapped.items():
                if adjusted_index in mapped:
                    enabled.setdefault(mapped[adjusted_index], set()).update(origins[original])
                else:
                    counts['skipped_ambiguous_variant_positions'] += 1
            if enabled:
                annotations.append(dict(character=character, code=variant, positions=sorted(enabled),
                                        provenance={str(i): sorted(value) for i, value in sorted(enabled.items())}))
    counts.update(characters=len({row['character'] for row in annotations}), variants=len(annotations),
                  positions=sum(len(row['positions']) for row in annotations),
                  component_positions=sum(any(origin.startswith('component:') for origin in value)
                                          for row in annotations for value in row['provenance'].values()))
    asset = dict(schema='ring-left-dot-annotations', version=1, scope=f'common-{len(common)}',
                 index_base=0, compatible_observations=['p', 's'], substitution_cost=.45,
                 cnchar_version=version, seeds={key: list(value) for key, value in SEEDS.items()},
                 sources=[dict(file=name, url=url,
                               sha256=hashlib.sha256((cache/name).read_bytes()).hexdigest())
                          for name, url in [
                              ('cnchar-order.tgz', 'https://registry.npmjs.org/cnchar-order/-/cnchar-order-3.2.6.tgz'),
                              ('dictionary.txt', 'https://raw.githubusercontent.com/skishore/makemeahanzi/master/dictionary.txt'),
                              ('graphics.txt', 'https://raw.githubusercontent.com/skishore/makemeahanzi/master/graphics.txt')]],
                 licenses=['stroke-left-dots-cnchar-license.txt', 'stroke-left-dots-source-notice.txt',
                           'stroke-left-dots-lgpl.txt', 'stroke-left-dots-arphic-license.txt'],
                 statistics=dict(counts), annotations=annotations)
    return asset, review


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=ROOT / 'tmp/left-dot-research')
    parser.add_argument('--output', type=Path, default=ROOT / 'src/proximic_ring/assets/stroke-left-dots.json')
    parser.add_argument('--review', type=Path, default=ROOT / 'data/diagnostics/left-dot-review/build-exceptions.json')
    args = parser.parse_args()
    result, exceptions = build(args.cache)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    args.review.parent.mkdir(parents=True, exist_ok=True)
    args.review.write_text(json.dumps(exceptions, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    for source, target in [('cnchar-LICENSE','cnchar-license'), ('COPYING','source-notice'),
                           ('LGPL','lgpl'), ('ARPHICPL.TXT','arphic-license')]:
        (args.output.parent / ('stroke-left-dots-'+target+'.txt')).write_bytes((args.cache/source).read_bytes())
    print(json.dumps(result['statistics'], ensure_ascii=False, indent=2))
