"""Freeze one explicit, completed collection as personal DTW templates."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(source):
    rows = [json.loads(line) for line in source.read_text(encoding='utf-8').splitlines() if line.strip()]
    if rows[0].get('version') != 3 or rows[0].get('synthetic'):
        raise ValueError('Require a real post-orientation-fix collection')
    annotations = [r for r in rows if r['kind']=='annotation']
    if len(annotations) != len(rows[0]['plan']):
        raise ValueError('Collection must be completed before freezing')
    correction_path = source.with_suffix('.labels.json')
    correction = json.loads(correction_path.read_text(encoding='utf-8')) if correction_path.exists() else {}
    templates = [dict(shape=correction.get('shape_aliases', {}).get(r['target']['shape'], r['target']['shape']),
        category=r['target']['category'], trace=r['value']['points']) for r in annotations if r['rating'] != 3]
    payload = dict(version=1, source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        labels_sha256=hashlib.sha256(correction_path.read_bytes()).hexdigest() if correction_path.exists() else None,
        note='Frozen prior session; new test paths are never automatically added.', templates=templates)
    target = ROOT/'src/proximic_ring/assets/stroke-personal.json.gz'
    with gzip.open(target, 'wt', encoding='utf-8') as stream:
        json.dump(payload, stream, ensure_ascii=False, separators=(',', ':'))
    print('Frozen templates:', len(templates), 'profile:', payload['source_sha256'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    build(parser.parse_args().source)
