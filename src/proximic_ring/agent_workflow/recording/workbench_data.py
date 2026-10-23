"""Read-only task/flow/group projection for the records workbench."""
import json
import sqlite3
from pathlib import Path

from .task_list import task_list
from proximic_ring.agent_workflow.extraction.workflow_artifacts import compact_workflows
from proximic_ring.agent_workflow.operations.operation_identity import digest


def read_tables(path, names):
    if not path.exists():
        return {name: [] for name in names}
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=3) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {name: [dict(r) for r in db.execute('SELECT * FROM ' + name + ' ORDER BY rowid')]
                if name in tables else [] for name in names}


def audit_for_job(root, job):
    filename = job.get('result_file')
    if not filename or Path(filename).name != filename:
        raise ValueError('提取结果路径无效')
    path = (Path(root) / 'workflows' / filename).resolve()
    if not path.is_relative_to((Path(root) / 'workflows').resolve()):
        raise ValueError('提取结果路径无效')
    value = json.loads(path.read_text())
    src = value['source']
    if (src['session_id'], src['episode_id']) != (job['session_id'], job['episode_id']):
        raise ValueError('提取结果的任务标识不一致')
    return value


def same_source(left, right):
    """Prompt upgrades and diagnostic timestamps do not change recorded evidence."""
    def content(packet):
        value = {k: v for k, v in packet.items()
                 if k not in ('prompt_version', 'fingerprint', 'fingerprint_version')}
        value['source'] = dict(value['source'], episode={
            k: v for k, v in value['source']['episode'].items() if k != 'updated_at'})
        return value
    return content(left) == content(right)


def snapshot(root):
    root = Path(root)
    queue = read_tables(root / 'workflows/queue.sqlite3', ['jobs', 'workbench_actions'])
    data = read_tables(root / 'operations/catalog.sqlite3', [
        'samples', 'operation_memberships', 'operation_heads', 'operation_revisions',
        'operation_admissions', 'operation_updates', 'operation_parameter_history', 'operation_jobs'])
    heads = {r['operation_id']: r['active_revision'] for r in data['operation_heads']}
    revisions = {r['id']: r for r in data['operation_revisions']}
    samples = {r['id']: r for r in data['samples']}
    memberships = {(r['sample_id'], r['version_id']): r for r in data['operation_memberships']}
    tasks = sorted(task_list(root), key=lambda t: (t['created_at'], t['session_id'], t['id']))
    for task in tasks:
        key = (task['session_id'], task['id'])
        jobs = [j for j in reversed(queue['jobs']) if (j['session_id'], j['episode_id']) == key]
        actions = [dict(a, target=json.loads(a['target']), result=json.loads(a['result']))
                   for a in reversed(queue['workbench_actions'])
                   if (a['session_id'], a['episode_id']) == key]
        task.update(jobs=jobs, actions=actions, flows=[], warning='')
        latest = jobs[0] if jobs else None
        task['latest_job'] = latest
        # An unsuccessful latest attempt must not silently expose an old flow as current.
        if not latest or latest['status'] != 'completed':
            continue
        try:
            audit = audit_for_job(root, latest)
            for index, flow in enumerate(compact_workflows(audit)['workflows']):
                sid = digest([*key, index])[:32]
                vid = digest(flow)
                sample = samples.get(sid)
                member = memberships.get((sid, vid))
                current = sample and sample['version_id'] == vid
                oid = member['operation_id'] if current and member else None
                rid = heads.get(oid)
                rev = revisions.get(rid)
                admissions = [a for a in reversed(data['operation_admissions'])
                              if a['sample_id'] == sid and a['version_id'] == vid]
                aids = {a['id'] for a in admissions}
                updates = [u for u in reversed(data['operation_updates']) if u['admission_id'] in aids]
                candidates = []
                for update in updates:
                    candidate = revisions.get(update['revision_id'])
                    if candidate and candidate['status'] == 'candidate':
                        generation = json.loads(candidate['generation'])
                        candidates.append(dict(id=candidate['id'], flow=json.loads(candidate['flow']),
                            reason=candidate['reason'], sources=json.loads(candidate['sources']),
                            bindings=generation.get('parameter_bindings', {})))
                records = [dict(r, parameters=json.loads(r['parameters']), generation=json.loads(r['generation']))
                           for r in reversed(data['operation_parameter_history'])
                           if current and r['sample_id'] == sid and r['version_id'] == vid
                           and r['revision_id'] == rid and r['source_key'] == digest(json.loads(sample['source']))]
                task['flows'].append(dict(index=index, sample_id=sid, version_id=vid, flow=flow,
                    operation_id=oid, revision_id=rid, general_flow=json.loads(rev['flow']) if rev else None,
                    members=sum(1 for m in data['operation_memberships']
                                if m['operation_id'] == oid and samples.get(m['sample_id'], {}).get('version_id') == m['version_id']) if oid else 0,
                    admissions=[dict(a, detail=json.loads(a['detail'])) for a in admissions],
                    updates=updates, candidates=candidates, parameters=records,
                    scope=sample['kind'] if current else None))
            task['extraction_notes'] = audit.get('notes', '')
        except (OSError, ValueError, KeyError, TypeError):
            task['warning'] = '当前提取结果暂不可读取；请查看提取历史或重新提取。'
    # Public metadata only: large model inputs/results remain separately linked artifacts.
    operations = [{k: j[k] for k in ('id', 'kind', 'subject_id', 'status', 'generation', 'error', 'at')}
                  for j in data['operation_jobs']]
    versions = {}
    for row in data['operation_revisions']:
        for ref in json.loads(row['sources']):
            versions.setdefault(ref['version_id'], ref)
    version_data = read_tables(root / 'operations/catalog.sqlite3', ['versions'])['versions']
    return dict(tasks=tasks, operation_jobs=operations,
                source_flows={v['id']: json.loads(v['flow']) for v in version_data if v['id'] in versions})


def selected(root, payload):
    if not isinstance(payload.get('session_id'), str) or not isinstance(payload.get('episode_id'), str):
        raise ValueError('请选择一个任务')
    task = next((t for t in snapshot(root)['tasks']
                 if (t['session_id'], t['id']) == (payload['session_id'], payload['episode_id'])), None)
    if not task:
        raise ValueError('任务不存在')
    job = task['latest_job']
    if not job or job['status'] != 'completed' or payload.get('job_id') != job['id']:
        raise ValueError('提取版本已变化或尚未完成，请刷新后选择当前结果')
    index = payload.get('workflow_index')
    if type(index) is not int or not 0 <= index < len(task['flows']):
        raise ValueError('请选择一个已提取的 Workflow')
    return task, task['flows'][index]
