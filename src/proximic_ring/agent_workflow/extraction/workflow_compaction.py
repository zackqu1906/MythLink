"""Deterministic model-input deduplication; never edit the recording or infer facts.

Two layers stay separate:
* <job>.input.json retains every evidence ID and its full event references.
* The model sees the same evidence IDs/kinds/call identities, compact sequence
  ranges, and a shared-content table for exactly equal large JSON values.

Repeated actions are NOT deleted. Even identical observations from two calls
keep separate evidence entries and step IDs. Sharing text does not establish
that the calls have the same before/after state. No fuzzy match, whitespace
normalization, summarization, extra truncation, or model call is used here.
"""
from __future__ import annotations

import copy
import json
from collections import Counter

INPUT_FORMAT = 'deduplicated-evidence-v1'
MIN_SHARED_CHARS = 256
REF = '$content_ref'
LITERAL = '$literal_content'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _projection(packet):
    """Only project audit metadata; all semantic content remains intact.

The short E ID resolves to the complete original refs in the saved snapshot.
seq_range gives chronological bounds, NOT membership of every event between
them. Remove the nested call refs only when they equal the outer refs exactly.
Fingerprint fields are local job diagnostics, not extraction evidence.
"""
    value = copy.deepcopy({k:v for k,v in packet.items()
                           if k not in ('fingerprint', 'fingerprint_version')})
    for entry in value.get('evidence', []):
        refs = entry.pop('event_refs', [])
        seqs = [r['seq'] for r in refs if isinstance(r.get('seq'), int)]
        entry['event_seq_range'] = [min(seqs), max(seqs)] if seqs else None
        content = entry.get('content')
        if (entry.get('kind') == 'call' and isinstance(content, dict)
                and content.get('event_refs') == refs):
            content.pop('event_refs', None)
    return value


def compact_packet(packet):
    """Return (model packet, deterministic size statistics), without mutation.

Intern only repeated *content* values >=256 serialized characters; tiny values
cost more references and make the prompt harder to read. Prefer whole repeated
objects over their leaves. Table values are literal original content, never
recursive references, so reconstruction is unambiguous and cannot form cycles.
Reserved-key collisions in original inline objects are escaped as literals.
If overhead would increase total input, keep the original representation.

Sizes count the exact compact JSON user text, not tokens, HTTP envelope, system
prompt, or hidden thinking. This does not change the configured input ceiling.
"""
    original = {k:v for k,v in packet.items() if k not in ('fingerprint', 'fingerprint_version')}
    before = len(encoded(original))
    projected = _projection(packet)
    projected_chars = len(encoded(projected))
    counts = Counter()

    def count(value):
        key = encoded(value)
        if len(key) >= MIN_SHARED_CHARS:
            counts[key] += 1
        if isinstance(value, dict):
            for item in value.values(): count(item)
        elif isinstance(value, list):
            for item in value: count(item)

    for entry in projected.get('evidence', projected.get('records', [])): count(entry['content'])
    shared, ids = {}, {}
    references = 0

    def pack(value):
        nonlocal references
        key = encoded(value)
        if len(key) >= MIN_SHARED_CHARS and counts[key] > 1:
            if key not in ids:
                content_id = 'C' + str(len(ids) + 1)
                ids[key] = content_id
                shared[content_id] = value
            references += 1
            return {REF:ids[key]}
        if isinstance(value, dict):
            if REF in value or LITERAL in value:
                return {LITERAL:value}
            return {k:pack(v) for k,v in value.items()}
        if isinstance(value, list): return [pack(v) for v in value]
        return value

    for entry in projected.get('evidence', projected.get('records', [])): entry['content'] = pack(entry['content'])
    projected.update(input_format=INPUT_FORMAT, shared_content=shared)
    after = len(encoded(projected))
    mode = INPUT_FORMAT
    if after >= before:
        projected, after, mode = copy.deepcopy(original), before, 'original'
        references, shared = 0, {}
    stats = dict(version=1, mode=mode, original_chars=before,
        metadata_projected_chars=projected_chars, model_input_chars=after,
        saved_chars=before-after, reduction_percent=round(100*(before-after)/before, 2) if before else 0,
        evidence_entries=len(original.get('evidence', original.get('records', []))), shared_values=len(shared),
        content_references=references, extra_model_calls=0)
    return projected, stats


def expand_model_packet(value):
    """Reconstruct the metadata projection for tests/offline integrity checks.

Full event refs intentionally live in the original snapshot; they are not
fabricated from event_seq_range. Shared table values are returned literally.
"""
    if value.get('input_format') != INPUT_FORMAT:
        return copy.deepcopy(value)
    shared = value['shared_content']

    def expand(node):
        if isinstance(node, dict):
            if set(node) == {REF}: return copy.deepcopy(shared[node[REF]])
            if set(node) == {LITERAL}: return copy.deepcopy(node[LITERAL])
            return {k:expand(v) for k,v in node.items()}
        if isinstance(node, list): return [expand(v) for v in node]
        return node

    result = copy.deepcopy({k:v for k,v in value.items() if k not in ('input_format', 'shared_content')})
    for entry in result.get('evidence', result.get('records', [])): entry['content'] = expand(entry['content'])
    return result


def ensure_input_budget(stats, max_chars):
    if stats['model_input_chars'] > max_chars:
        stage = '轨迹精简与去重后' if 'trajectory_selection' in stats else '去重后'
        raise ValueError(f"证据包{stage}仍超过 {max_chars} 字符："
            f"{stats['original_chars']} → {stats['model_input_chars']}，"
            f"减少 {stats['reduction_percent']}%；未发送模型，需分段提取支持")
