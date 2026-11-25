"""Versioned component rules, independent of the wearer's trajectory model."""
from functools import lru_cache
import gzip
import hashlib
from importlib.resources import files
import json


@lru_cache(maxsize=1)
def component_rules():
    data = json.loads(files('proximic_ring').joinpath('assets/stroke-joined-rules.json').read_text('utf-8'))
    if data.get('schema') != 'ring-joined-component-rules' or data.get('version') != 1:
        raise ValueError('Unsupported joined-component rules')
    families, identities = set(), set()
    for rule in data['rules']:
        if rule['id'] in identities or rule['family'] in families:
            raise ValueError('Duplicate joined-component rule')
        identities.add(rule['id']); families.add(rule['family'])
        if not rule['codes'] or any(not 2 <= len(code) <= 4 or set(code)-set('hspnz') for code in rule['codes']):
            raise ValueError('Invalid joined-component code')
        for form in rule.get('forms', ()):
            indices = form['indices']
            if (not indices or indices != list(range(indices[0], indices[-1]+1))
                    or indices[0] < 0 or indices[-1] >= form['strokeCount']):
                raise ValueError('Joined contact must cover consecutive component strokes')
    return data


@lru_cache(maxsize=1)
def component_registry():
    path = files('proximic_ring').joinpath('assets/stroke-joined-components.json.gz')
    with path.open('rb') as stream:
        data = json.loads(gzip.decompress(stream.read()))
    rule_bytes = files('proximic_ring').joinpath('assets/stroke-joined-rules.json').read_bytes()
    # Windows Git checkouts can use CRLF for the same rule file.
    rule_hashes = {hashlib.sha256(raw).hexdigest()
                   for raw in (rule_bytes, rule_bytes.replace(b'\r\n', b'\n'))}
    if (data.get('schema') != 'ring-joined-component-positions' or data.get('version') != 1
            or data['source']['rulesSha256'] not in rule_hashes):
        raise ValueError('Joined component positions must be rebuilt for these rules')
    return data
