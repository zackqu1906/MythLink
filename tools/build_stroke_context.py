"""Package the existing demo's context counts and source attributions unchanged."""
import gzip
import hashlib
import json
from pathlib import Path
import sys

source = Path(sys.argv[1])
target = Path(__file__).resolve().parents[1]/'src/proximic_ring/assets'
data = json.loads((source/'context-model.js').read_text(encoding='utf-8').split('=',1)[1].rstrip(';\n'))
provenance = json.loads((source/'context-model-sources.json').read_text(encoding='utf-8'))
provenance['demo_model_sha256'] = hashlib.sha256((source/'context-model.js').read_bytes()).hexdigest()
for name, value in [('stroke-context.json.gz', data), ('stroke-context-sources.json.gz', provenance)]:
    (target/name).write_bytes(gzip.compress(json.dumps(value,ensure_ascii=False,separators=(',',':')).encode('utf-8'),mtime=0))
print('Local context model and attribution packaged')
