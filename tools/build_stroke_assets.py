"""Convert the local ring-stroke-ime-demo data into a compact UI asset.

Usage: python tools/build_stroke_assets.py PATH_TO_DEMO/data
"""
import gzip
import json
from pathlib import Path
import sys
import importlib.util


def read_assignment(path):
    return json.loads(path.read_text(encoding="utf-8").split("=", 1)[1].rstrip(";\n"))


def main():
    source = Path(sys.argv[1])
    target = Path(__file__).resolve().parents[1] / "src/proximic_ring/assets/strokes.json.gz"
    data = read_assignment(source / "stroke-data.js")
    model = read_assignment(source / "stroke-model.js")
    payload = {"ranks": data["ranks"], "entries": data["entries"],
               "templates": model["templates"], "sources": data["sources"]}
    # Retain original recordings, rather than invent detail by upsampling 32 points.
    spec = importlib.util.spec_from_file_location('demo_builder', source.parent/'tools/build_recognizer.py')
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    raw_templates = []
    for path in sorted((source/'recordings').glob('ring-strokes-*.json')):
        for sample in json.loads(path.read_text(encoding='utf-8'))['samples']:
            raw = sample.get('points', [])
            if len(raw) < 2:
                continue
            trace, length = builder.resample(raw)
            if length < 10 or raw[-1]['t']-raw[0]['t'] < 30:
                continue
            raw_templates.append((sample, trace, [[p['x'], p['y']] for p in raw]))
    if len(raw_templates) != len(payload['templates']):
        raise ValueError('Raw recordings do not match template count')
    for template, (sample, trace, raw) in zip(payload['templates'], raw_templates):
        if template['shape'] != sample['shape'] or template['category'] != sample['category'] or template['trace'] != trace:
            raise ValueError('Raw recording order or geometry differs from shipped templates')
        template['raw_trace'] = raw
    payload['sampling'] = 'Original recordings retained; legacy 32-point traces unchanged'
    with gzip.open(target, "wt", encoding="utf-8", compresslevel=9) as stream:
        json.dump(payload, stream, ensure_ascii=False, separators=(",", ":"))
    print(f"{target}: {target.stat().st_size} bytes")


if __name__ == "__main__":
    main()
