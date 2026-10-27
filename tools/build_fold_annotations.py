"""Build offline fine fold orders from the pinned cnchar-order npm archive."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from proximic_ring.touchpad.stroke.stroke_folds import FOLD_GROUPS, coarse_order


def build(archive_path, dictionary_path, output):
    with tarfile.open(archive_path) as archive:
        package = json.load(archive.extractfile('package/package.json'))
        if package['version'] != '3.2.6':
            raise ValueError('Expected cnchar-order 3.2.6')
        source = archive.extractfile('package/cnchar.order.min.js').read().decode('utf-8')
    objects = [json.loads(value) for value in re.findall(r"JSON.parse\('([^']*)'\)", source)]
    orders = next(value for value in objects if '宝' in value)
    table = next(value for value in objects if isinstance(value.get('d'), dict))
    for letter, names in FOLD_GROUPS.items():
        if set(table[letter]['name'].split('|')) != set(names):
            raise ValueError(f'Unexpected cnchar fold group: {letter}')
    with gzip.open(dictionary_path, 'rt', encoding='utf-8') as stream:
        entries = {(char, code) for char, code, *_ in json.load(stream)['entries']}
    aligned = {char: order for char, order in orders.items()
               if (char, coarse_order(order)) in entries}
    payload = dict(schema='ring-fold-orders', version=1, source=dict(
        repository='https://github.com/theajack/cnchar', package='cnchar-order', version=package['version'],
        url='https://registry.npmjs.org/cnchar-order/-/cnchar-order-3.2.6.tgz',
        sha256=hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        license='MIT', license_file='stroke-left-dots-cnchar-license.txt'),
        groups=FOLD_GROUPS, orders=aligned)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    output.write_bytes(gzip.compress(raw, mtime=0))
    return payload


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--dictionary', type=Path, default=ROOT/'src/proximic_ring/assets/strokes.json.gz')
    parser.add_argument('--output', type=Path, default=ROOT/'src/proximic_ring/assets/stroke-folds.json.gz')
    args = parser.parse_args()
    data = build(args.archive, args.dictionary, args.output)
    print(f'{len(data["orders"])} aligned character orders')
