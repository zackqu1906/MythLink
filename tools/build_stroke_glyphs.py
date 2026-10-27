"""Build offline Kaishu outlines from the same data used by cnchar-draw.

Inputs are downloaded explicitly, never at application runtime. Example:
python tools/build_stroke_glyphs.py tmp/hanzi-writer-data-2.0.1.tgz tmp/left-dot-research/cnchar-order.tgz
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import re
import tarfile

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'src/proximic_ring/assets'


def build(glyph_archive, order_archive):
    with tarfile.open(order_archive) as archive:
        source = archive.extractfile('package/cnchar.order.min.js').read().decode('utf-8')
        version = json.load(archive.extractfile('package/package.json'))['version']
    if version != '3.2.6':
        raise ValueError('Expected cnchar-order 3.2.6')
    objects = [json.loads(value) for value in re.findall(r"JSON.parse\('([^']*)'\)", source)]
    orders = next(value for value in objects if '宝' in value)
    translate = str.maketrans({c: ('h' if c in 'ji' else 's' if c in 'fg' else
                                  'p' if c == 's' else 'n' if c in 'dkl' else 'z')
                              for c in 'abcdefghijklmnopqrstuvwxyz'})
    characters = {}
    with tarfile.open(glyph_archive) as archive:
        if json.load(archive.extractfile('package/package.json'))['version'] != '2.0.1':
            raise ValueError('Expected hanzi-writer-data 2.0.1')
        for member in archive.getmembers():
            name = Path(member.name).stem
            if not member.isfile() or not member.name.endswith('.json') or len(name) != 1:
                continue
            data = json.load(archive.extractfile(member))
            paths = data['strokes']
            if not paths or any(not isinstance(p, str) or not p.startswith('M ') for p in paths):
                raise ValueError(f'Invalid outlines for {name}')
            code = orders.get(name, '').translate(translate)
            characters[name] = dict(paths=paths, code=code if len(code) == len(paths) else '')
        license_text = archive.extractfile('package/ARPHICPL.TXT').read()
    payload = dict(schema=1, sources=[
        dict(package='hanzi-writer-data', version='2.0.1',
             url='https://registry.npmjs.org/hanzi-writer-data/-/hanzi-writer-data-2.0.1.tgz',
             sha256=hashlib.sha256(glyph_archive.read_bytes()).hexdigest()),
        dict(package='cnchar-order', version=version,
             url='https://registry.npmjs.org/cnchar-order/-/cnchar-order-3.2.6.tgz',
             sha256=hashlib.sha256(order_archive.read_bytes()).hexdigest())],
        characters=characters)
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode('utf-8')
    (ASSETS / 'stroke-glyphs.json.gz').write_bytes(gzip.compress(raw, mtime=0))
    (ASSETS / 'stroke-glyphs-arphic-license.txt').write_bytes(license_text)
    print(f'{len(characters)} offline glyphs, {sum(bool(g["code"]) for g in characters.values())} with aligned order')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('glyph_archive', type=Path)
    parser.add_argument('order_archive', type=Path)
    args = parser.parse_args()
    build(args.glyph_archive, args.order_archive)
