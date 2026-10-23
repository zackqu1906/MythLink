"""Targeted console actions, persistence, stale versions and real HTTP controls."""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from urllib.request import urlopen

import pytest

# Preserve legacy behavior tests behind an explicit test-only opt-in.
pytestmark = pytest.mark.usefixtures('legacy_workflow_pipeline')

from test_agent_recording import recorder
from test_agent_workflows import finished
from test_workflow_simple import answer
from test_agent_records_server import server, post
from proximic_ring.agent_workflow.recording.workbench_data import snapshot
from proximic_ring.agent_workflow.recording.workbench_actions import enqueue, process_one, recover, adopt
from proximic_ring.agent_workflow.extraction.workflow_store import WorkflowStore
from proximic_ring.agent_workflow.extraction.workflows import process_one as extract
from proximic_ring.agent_workflow.operations.operation_store import OperationStore
from proximic_ring.agent_workflow.operations.operation_prototypes import heads


@pytest.fixture
def ready(recorder, monkeypatch):
    from proximic_ring.agent_workflow.operations import operation_retrieval
    monkeypatch.setattr(operation_retrieval, 'ensure_embeddings', lambda directory, flows, **kw:
        ({v: dict(goal=[1.], steps=[1.]) for v in flows}, {}))
    episode, packet = finished(recorder)
    jobs = WorkflowStore(recorder.root)
    jid = jobs.enqueue(packet)
    assert extract(jobs, None, model=lambda p, s: (answer(p), {}))
    assert jobs.jobs()[0]['status'] == 'completed'
    yield jobs, dict(session_id=recorder.session_id, episode_id=episode, job_id=jid, workflow_index=0)
    jobs.close()


def action(queue, identifier):
    return dict(queue.db.execute('SELECT * FROM workbench_actions WHERE id=?', (identifier,)).fetchone())


def group(ready):
    queue, payload = ready
    identifier = enqueue(queue.root, dict(payload, step='group'))['id']
    assert process_one(queue, None)
    assert action(queue, identifier)['status'] == 'completed'
    return snapshot(queue.root)['tasks'][0]['flows'][0]


def missing(packet, *args, **kwargs):
    return dict(parameters=[dict(name=p['name'], status='missing') for p in packet['general_workflow']['parameters']]), {}


def test_seed_parameters_force_rerun_and_raw_records_preserved(ready, recorder):
    queue, payload = ready
    raw = (recorder.directory/'events.jsonl').read_bytes()
    f = group(ready)
    assert f['members'] == 1 and f['general_flow'] == f['flow']
    request = dict(payload, step='parameters', revision_id=f['revision_id'])
    first = enqueue(queue.root, request)['id']
    assert enqueue(queue.root, request)['id'] == first
    assert process_one(queue, None, model=missing)
    assert action(queue, first)['status'] == 'completed'
    first_record = snapshot(queue.root)['tasks'][0]['flows'][0]['parameters'][0]
    second = enqueue(queue.root, request)['id']
    assert second != first and process_one(queue, None, model=missing)
    records = snapshot(queue.root)['tasks'][0]['flows'][0]['parameters']
    assert len(records) == 2 and records[0]['id'] != first_record['id']
    assert records[0]['revision_id'] == f['revision_id']
    assert raw == (recorder.directory/'events.jsonl').read_bytes()


def test_stale_extraction_cannot_be_submitted_or_consumed(ready):
    queue, payload = ready
    identifier = enqueue(queue.root, dict(payload, step='group'))['id']
    packet = json.loads((queue.directory/(payload['job_id']+'.input.json')).read_text())
    queue.enqueue(packet, manual=True)
    with pytest.raises(ValueError, match='版本已变化'):
        enqueue(queue.root, dict(payload, step='group'))
    assert process_one(queue, None, model=lambda *a, **kw: pytest.fail('stale request must not call model'))
    assert action(queue, identifier)['status'] == 'failed'
    assert snapshot(queue.root)['tasks'][0]['flows'] == []


def test_compatible_prompt_upgrade_does_not_masquerade_as_changed_evidence(ready):
    from proximic_ring.agent_workflow.recording.workbench_data import same_source
    queue, payload = ready
    before = json.loads((queue.directory/(payload['job_id']+'.input.json')).read_text())
    after = copy.deepcopy(before)
    after.update(prompt_version='next-compatible-prompt', fingerprint='new-prompt-hash')
    after['source']['episode']['updated_at'] = '2099-01-01'
    assert same_source(before, after)
    after['source']['episode']['goal'] = 'Different task'
    assert not same_source(before, after)


def test_targeted_group_never_processes_other_pending_samples(ready):
    queue, payload = ready
    group(ready)
    operations = OperationStore(queue.root)
    try:
        from test_operation_prototypes import sample
        from proximic_ring.agent_workflow.operations.operation_prototypes import prepare
        operations.ingest(sample('unrelated'))
        prepare(operations)
        prior = copy.deepcopy(operations.rows('operation_admissions'))
        identifier = enqueue(queue.root, dict(payload, step='group'))['id']
        calls = []
        def same(packet, *args, **kwargs):
            calls.append(packet)
            if 'new_workflow' in packet:
                assert packet['new_workflow']['name'] == '打开指定目标'
                return dict(decisions=[dict(operation_id=c['operation_id'], relation='same', reason='同类')
                                       for c in packet['candidates']]), {}
            return dict(workflow=packet['current_workflow'], reason='无需修改',
                parameter_bindings={s['version_id']: {'target': ['target']} for s in packet['sources']}), {}
        assert process_one(queue, None, model=same)
        assert action(queue, identifier)['status'] == 'completed'
        assert len(calls) == 2
        for before in prior:
            after = next(r for r in operations.rows('operation_admissions') if r['id'] == before['id'])
            assert after == before
    finally:
        operations.close()


def test_reextracted_member_candidate_is_reviewed_then_adopted(ready):
    queue, payload = ready
    before = group(ready)
    packet = json.loads((queue.directory/(payload['job_id']+'.input.json')).read_text())
    jid = queue.enqueue(packet, manual=True)
    value = answer(packet)
    value['workflows'][0]['steps'].append('打开后确认目标名称与 {{target}} 一致。')
    assert extract(queue, None, model=lambda *a: (value, {}))
    payload = dict(payload, job_id=jid)
    identifier = enqueue(queue.root, dict(payload, step='group'))['id']
    def model(packet, *a, **kw):
        if 'new_workflow' in packet:
            return dict(decisions=[dict(operation_id=c['operation_id'], relation='same', reason='同类')
                                   for c in packet['candidates']]), {}
        flow = copy.deepcopy(packet['current_workflow'])
        flow['steps'].append('打开后确认目标名称与 {{target}} 一致。')
        return dict(workflow=flow, reason='保留新增核对步骤',
                    parameter_bindings={s['version_id']: {'target': ['target']} for s in packet['sources']}), {}
    assert process_one(queue, None, model=model)
    assert action(queue, identifier)['status'] == 'review'
    f = snapshot(queue.root)['tasks'][0]['flows'][0]
    assert f['revision_id'] is None  # old membership is never presented as current
    candidate = f['candidates'][0]
    with pytest.raises(ValueError, match='核对说明'):
        adopt(queue.root, dict(payload, candidate_id=candidate['id'], reviews={}))
    result = adopt(queue.root, dict(payload, candidate_id=candidate['id'],
        reviews={v: '已核对目标名称检查及输入角色，保留原任务要求。' for v in candidate['bindings']}))
    f = snapshot(queue.root)['tasks'][0]['flows'][0]
    assert f['revision_id'] == candidate['id'] != before['revision_id']
    assert f['members'] == 1 and not f['candidates']
    assert action(queue, identifier)['status'] == 'completed' and result['merge_id']
    with pytest.raises(ValueError, match='当前使用'):
        enqueue(queue.root, dict(payload, step='parameters', revision_id=before['revision_id']))


def test_worker_failure_and_restart_never_silently_repeat_model(ready):
    queue, payload = ready
    group(ready)
    identifier = enqueue(queue.root, dict(payload, step='group'))['id']
    assert process_one(queue, None, model=lambda *a, **k: (_ for _ in ()).throw(RuntimeError('secret body')))
    failed = action(queue, identifier)
    assert failed['status'] == 'failed' and 'secret body' not in failed['error']
    assert not process_one(queue, None, model=lambda *a, **k: pytest.fail('automatic paid retry'))
    second = enqueue(queue.root, dict(payload, step='group'))['id']
    with queue.db:
        queue.db.execute("UPDATE workbench_actions SET status='running' WHERE id=?", (second,))
    recover(queue)
    assert action(queue, second)['status'] == 'failed'
    assert '结果未知' in action(queue, second)['error']


def test_console_http_routes_dedup_and_offline_export(ready, server):
    queue, payload = ready
    with urlopen(server.origin+server.prefix+'workbench.html') as response:
        page = response.read().decode()
    assert 'workbench.js' in page and 'controls.js' not in page
    assert '<script' not in (queue.root/'workbench.html').read_text()
    with urlopen(server.origin+server.prefix+'api/workbench') as response:
        data = json.load(response)
    assert len(data['tasks']) == 1 and data['tasks'][0]['flows']
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: post(server, 'workbench/run', dict(payload, step='group')), range(4)))
    assert len({r['id'] for r in results}) == 1
    assert len(list(queue.db.execute('SELECT * FROM workbench_actions'))) == 1
    assert not (queue.root/'operations/catalog.sqlite3').exists()  # enqueue did not run the model/pipeline


def test_existing_worker_consumes_console_action_without_automatic_mode(ready, monkeypatch):
    import queue as queue_module
    import threading
    from dataclasses import asdict
    from proximic_ring.agent_workflow.extraction import workflows, workflow_model
    from proximic_ring.agent_workflow.operations import operation_embeddings
    from proximic_ring.agent_workflow.recording import workbench_actions
    from proximic_ring.text_processing.model import LLMSettings
    queue, payload = ready
    identifier = enqueue(queue.root, dict(payload, step='group'))['id']
    incoming = queue_module.Queue()
    incoming.put(json.dumps(dict(type='configure', automatic=False, busy=False,
                                settings=asdict(LLMSettings()))))
    def stream():
        while True:
            item = incoming.get(timeout=5)
            if item is None:
                break
            yield item
    monkeypatch.setattr(workflow_model, 'configuration_error', lambda config: '')
    monkeypatch.setattr(operation_embeddings, 'publish_activity', lambda *a: None)
    monkeypatch.setattr(operation_embeddings, 'agent_busy', lambda: False)
    monkeypatch.setattr(operation_embeddings, 'activity_directory', lambda: queue.directory)
    monkeypatch.setattr(workflows.os, 'nice', lambda _: None)
    original = workbench_actions.process_one
    def consume(*args, **kwargs):
        result = original(*args, **kwargs)
        if result:
            incoming.put(None)
        return result
    monkeypatch.setattr(workbench_actions, 'process_one', consume)
    # Watchdog stops a broken integration without ever sending a paid request.
    timer = threading.Timer(5, lambda: incoming.put(None))
    timer.start()
    try:
        workflows.serve(queue.root, '2099-01-01T00:00:00+00:00', input_stream=stream())
    finally:
        timer.cancel()
    assert action(queue, identifier)['status'] == 'completed'
