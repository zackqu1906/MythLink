"""Binary admission and unified synthesis contracts; historical audit stays intact."""
import json
from proximic_ring.agent_workflow.workflow_content import validate_plain, plain_flow
from proximic_ring.agent_workflow.operations.operation_identity import digest
from proximic_ring.agent_workflow.operations.operation_store import encoded
from proximic_ring.agent_workflow.extraction.workflow_store import now

VERSION = 'workflow-synthesis-1'


def validate_decisions(value, packet):
    if not isinstance(value, dict) or set(value) != {'decisions'}:
        raise ValueError('二分类输出仅需 decisions')
    expected = {c['operation_id'] for c in packet['candidates']}
    items = value['decisions']
    if not isinstance(items, list) or len(items) != len(expected):
        raise ValueError('入组判断必须覆盖全部候选')
    seen = set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {'operation_id', 'relation', 'reason'}:
            raise ValueError('二分类字段错误')
        oid = item['operation_id']
        if not isinstance(oid, str) or oid not in expected or oid in seen or item['relation'] not in ('same', 'different'):
            raise ValueError('候选关系只能是 same 或 different，且不能重复')
        if not isinstance(item['reason'], str) or not item['reason'].strip():
            raise ValueError('分类依据不能为空')
        seen.add(oid)
    return items


def queue(store, sample, admission, decisions, prototypes):
    """All matching groups are jointly synthesized, never merged by pairwise votes."""
    ids = {d['operation_id'] for d in decisions}
    if sample['operation_id']:
        ids.add(store.canonical(sample['operation_id']))
    created = {o['id']: o['created_at'] for o in store.rows('operations')}
    oid = min(ids, key=lambda i: (created[i], i))
    expected = {i: prototypes[i]['id'] for i in sorted(ids)}
    detail = dict(kind='generalization', pipeline=VERSION, expected_heads=expected,
                  sample_id=sample['id'], decisions=decisions)
    uid = digest([VERSION, admission['id'], sample['version_id'], expected])
    store.db.execute('INSERT OR IGNORE INTO operation_updates VALUES (?,?,?,?,?,?,?,?,?)',
        (uid, oid, expected[oid], sample['version_id'], admission['id'], encoded(detail), 'pending', None, now()))
    return uid


def validate_result(value, packet):
    if not isinstance(value, dict) or set(value) != {'workflow', 'parameter_bindings', 'reason'}:
        raise ValueError('统一泛化输出字段错误')
    if not isinstance(value['reason'], str) or not value['reason'].strip():
        raise ValueError('泛化说明不能为空')
    bindings = value['parameter_bindings']
    if value['workflow'] is None:
        if bindings != {}: raise ValueError('未生成流程时参数对应必须为空')
        return value
    validate_plain(value['workflow'])
    sources = {s['version_id']: s['workflow'] for s in packet['sources']}
    if not isinstance(bindings, dict) or set(bindings) != set(sources):
        raise ValueError('参数对应必须覆盖全部来源版本')
    names = {p['name'] for p in value['workflow']['parameters']}
    for vid, source in sources.items():
        mapping = bindings[vid]
        if not isinstance(mapping, dict) or set(mapping) != {p['name'] for p in source['parameters']}:
            raise ValueError('来源参数对应不完整')
        for targets in mapping.values():
            if (not isinstance(targets, list) or any(not isinstance(t, str) or t not in names for t in targets)
                    or len(set(targets)) != len(targets)):
                raise ValueError('参数对应目标必须是现有参数名称数组')
    return value


def inputs(store, update, current):
    """Snapshot all current members plus the incoming task. Old queued work also upgrades."""
    detail = json.loads(update['novelty'])
    admission = next(a for a in store.rows('operation_admissions') if a['id'] == update['admission_id'])
    incoming = next(s for s in store.rows('samples') if s['id'] == admission['sample_id'])
    if incoming['version_id'] != update['source_version']:
        raise ValueError('来源任务已变化，请重新分类')
    expected = detail.get('expected_heads', {update['operation_id']: update['base_revision']})
    # Legacy pending requests may refer to other independent groups.
    incoming_ids = {incoming['id']} | {p['sample_id'] for p in detail.get('proposals', [])}
    samples = store.rows('samples')
    if 'expected_heads' not in detail:
        for sample in samples:
            if sample['id'] in incoming_ids and sample['operation_id']:
                oid = store.canonical(sample['operation_id'])
                expected[oid] = current[oid]['id']
    if any(oid not in current or current[oid]['id'] != rid for oid, rid in expected.items()):
        raise ValueError('候选基准版本已变化，请重新分类')
    selected = [s for s in samples if s['operation_id'] in expected or s['id'] in incoming_ids]
    versions = store.versions()
    vids = sorted({s['version_id'] for s in selected})
    packet = dict(current_workflow=plain_flow(current[update['operation_id']]['flow']),
        sources=[dict(version_id=v, workflow=plain_flow(versions[v])) for v in vids],
        classification_reason='\n'.join(d['reason'] for d in detail.get('decisions', [])) or detail.get('reason', '融合已分类的历史经验'))
    refs = [dict(sample_id=s['id'], version_id=s['version_id'], source=json.loads(s['source'])) for s in selected]
    return packet, expected, refs, incoming
