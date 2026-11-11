"""Build per-stroke start→end vectors from the hanzi-writer medians behind cnchar-draw.

Only characters whose glyph stroke order already aligns with a five-class code
in stroke-glyphs.json.gz are kept, so vector i always belongs to code letter i.
Inputs are explicit local archives; the application never downloads them:
python tools/build_stroke_vectors.py tmp/hanzi-writer-data-2.0.1.tgz
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / 'src/proximic_ring/assets'


def build(glyph_archive, output=ASSETS / 'stroke-vectors.json.gz'):
    with gzip.open(ASSETS / 'stroke-glyphs.json.gz', 'rt', encoding='utf-8') as stream:
        codes = {name: value['code'] for name, value in json.load(stream)['characters'].items() if value['code']}
    characters = {}
    with tarfile.open(glyph_archive) as archive:
        if json.load(archive.extractfile('package/package.json'))['version'] != '2.0.1':
            raise ValueError('Expected hanzi-writer-data 2.0.1')
        for name, code in codes.items():
            medians = json.load(archive.extractfile(f'package/{name}.json'))['medians']
            if len(medians) != len(code) or any(len(m) < 2 for m in medians):
                continue
            # hanzi-writer y grows upward; SDK displacement y grows downward.
            characters[name] = dict(code=code, vectors=[[m[-1][0]-m[0][0], m[0][1]-m[-1][1]] for m in medians])
    payload = dict(schema=1, frame='x right, y down, 1024-unit glyph box', sources=[
        dict(package='hanzi-writer-data', version='2.0.1',
             url='https://registry.npmjs.org/hanzi-writer-data/-/hanzi-writer-data-2.0.1.tgz',
             sha256=hashlib.sha256(Path(glyph_archive).read_bytes()).hexdigest())],
        characters=characters)
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode('utf-8')
    Path(output).write_bytes(gzip.compress(raw, mtime=0))
    print(f'{len(characters)} characters with aligned stroke vectors')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('glyph_archive', type=Path)
    build(parser.parse_args().glyph_archive)
