"""Retry budget, durable scheduling and provenance; no paid model requests."""
import copy
import io
import json
from datetime import datetime, timezone, timedelta
from urllib.error import HTTPError

import pytest

# Preserve legacy behavior tests behind an explicit test-only opt-in.
pytestmark = pytest.mark.usefixtures('legacy_workflow_pipeline')
from test_agent_recording import recorder
from test_agent_workflows import finished, answer
from proximic_ring.agent_workflow.extraction import workflow_store, workflows
from proximic_ring.agent_workflow.extraction.workflow_store import WorkflowStore
from proximic_ring.agent_workflow.extraction.workflow_model import extract, ModelOutputError
from proximic_ring.agent_workflow.extraction.workflow_retry import RetryableModelError
from proximic_ring.text_processing.model import LLMSettings


@pytest.fixture
def clock(monkeypatch):
    current=[datetime.now(timezone.utc)]
    monkeypatch.setattr(workflow_store,'now',lambda:current[0].isoformat())
    def advance(seconds):current[0]+=timedelta(seconds=seconds)
    return advance


def malformed(packet):
    value=answer(packet);value['workflows'][0]['steps'][0]['evidence_ids']=['missing']
    return value,{}


def test_validation_retry_is_delayed_deduplicated_and_durable(recorder,clock):
    _,packet=finished(recorder);raw=(recorder.directory/'events.jsonl').read_bytes()
    store=WorkflowStore(recorder.root)
    first=store.enqueue(packet)
    workflows.process_one(store,None,model=lambda p,s:malformed(p))
    retry=store.jobs()[0]
    assert retry['parent_job_id']==retry['root_job_id']==first
    assert retry['retry_number']==1 and retry['status']=='queued'
    assert store.retry_delay()==10
    assert store.enqueue(packet)==store.enqueue(packet,manual=True)==retry['id']
    assert store.next_job() is None
    clock(9);assert store.next_job() is None
    store.close();store=WorkflowStore(recorder.root)
    try:
        store.recover();assert store.jobs()[0]['status']=='queued'
        clock(1)
        assert workflows.process_one(store,None,model=lambda p,s:(answer(p),{}))
        assert not workflows.process_one(store,None,model=lambda *a:pytest.fail('success must stop retries'))
        assert store.jobs()[0]['status']=='completed' and len(store.jobs())==2
        assert (store.directory/(first+'.rejected.json')).exists()
        assert (store.directory/(first+'.input.json')).read_bytes()==(store.directory/(retry['id']+'.input.json')).read_bytes()
        library=json.loads((store.directory/'library.json').read_text())
        assert [entry['job_id'] for entry in library['entries']]==[retry['id']]
        assert '查看触发本次重试的失败记录' in (store.directory/'library.html').read_text()
        assert (recorder.directory/'events.jsonl').read_bytes()==raw
    finally:store.close()


@pytest.mark.parametrize('fault',['validation','invalid_json','network'])
def test_three_extra_attempts_are_a_hard_limit_and_manual_retry_starts_new_chain(recorder,clock,fault):
    _,packet=finished(recorder);store=WorkflowStore(recorder.root);calls=[]
    def model(p,s):
        calls.append(1)
        if fault=='invalid_json':raise ModelOutputError('模型未返回完整 JSON','broken final',{})
        if fault=='network':raise RetryableModelError('文本模型网络失败或超时')
        return malformed(p)
    try:
        root=store.enqueue(packet)
        for number in range(4):
            assert workflows.process_one(store,None,model=model)
            if number<3:
                retry=store.jobs()[0]
                assert retry['retry_number']==number+1 and retry['root_job_id']==root
                assert store.retry_delay()==(10,30,60)[number]
                clock((10,30,60)[number])
        assert len(calls)==len(store.jobs())==4
        assert all(j['status']=='failed' for j in store.jobs())
        assert store.retry_delay() is None and store.enqueue(packet)==store.jobs()[0]['id']
        assert not workflows.process_one(store,None,model=model)
        assert '自动重试 3 次仍失败' in (store.directory/'library.html').read_text()
        manual=store.enqueue(packet,manual=True)
        assert store.jobs()[0]['retry_number']==0 and manual!=root
        workflows.process_one(store,None,model=lambda p,s:(answer(p),{}))
        assert store.jobs()[0]['status']=='completed'
    finally:store.close()


def test_newer_source_supersedes_retry_and_missing_source_never_calls_model(recorder):
    _,packet=finished(recorder);store=WorkflowStore(recorder.root)
    try:
        first=store.enqueue(packet)
        def changed(p,s):
            newer=copy.deepcopy(packet);newer['fingerprint']='new-source'
            store.enqueue(newer)
            return malformed(p)
        workflows.process_one(store,None,model=changed)
        assert len(store.jobs())==2 and all(j['retry_number']==0 for j in store.jobs())
        latest=store.jobs()[0];assert latest['id']!=first
        (store.directory/(latest['id']+'.input.json')).unlink()
        workflows.process_one(store,None,model=lambda *a:pytest.fail('missing source sent'))
        assert all(j['status']=='failed' for j in store.jobs())
    finally:store.close()


def test_upgrade_does_not_requeue_historical_failures(recorder):
    _,packet=finished(recorder);store=WorkflowStore(recorder.root)
    old=store.enqueue(packet);store.finish(old,error='历史失败')
    store.db.execute('DROP TABLE job_retries');store.db.commit();store.close()
    store=WorkflowStore(recorder.root)
    try:
        assert len(store.jobs())==1 and store.jobs()[0]['retry_number']==0
        assert store.jobs()[0]['root_job_id']==old and store.next_job() is None
    finally:store.close()


@pytest.mark.parametrize('code,retryable',[(400,False),(401,False),(403,False),(408,True),(429,True),(503,True)])
def test_http_retry_classification_preserves_permanent_failures(code,retryable):
    settings=LLMSettings(provider='volcengine',model='test',api_key='secret')
    def http(*a,**k):raise HTTPError('https://example.test',code,'secret body',{},None)
    with pytest.raises(ValueError) as error:extract({},settings,urlopen=http)
    assert isinstance(error.value,RetryableModelError) is retryable
    assert 'secret' not in str(error.value)


def test_retry_prompt_carries_error_without_mutating_evidence_or_disabling_thinking(recorder):
    _,packet=finished(recorder);original=copy.deepcopy(packet);requests=[]
    settings=LLMSettings(provider='volcengine',model='test',api_key='secret')
    def http(req,**kw):
        requests.append(json.loads(req.data))
        return io.BytesIO(json.dumps(dict(status='completed',output=[dict(type='message',role='assistant',
            content=[dict(type='output_text',text=json.dumps(answer(packet)))])])).encode())
    error='Workflow 参数声明与引用不一致；声明后未使用：school_type'
    extract(packet,settings,urlopen=http,retry_error=error)
    body=requests[0]
    assert error in body['input'][0]['content'][0]['text']
    assert body['thinking']=={'type':'enabled'} and packet==original


def test_configuration_failure_has_no_paid_retry(recorder):
    _,packet=finished(recorder);store=WorkflowStore(recorder.root)
    try:
        store.enqueue(packet)
        workflows.process_one(store,LLMSettings(provider='local'))
        assert len(store.jobs())==1 and store.jobs()[0]['status']=='failed'
        assert '思考适配' in store.jobs()[0]['error']
    finally:store.close()


def test_cli_waits_for_retry_then_continues_downstream(recorder,monkeypatch):
    _,packet=finished(recorder);store=WorkflowStore(recorder.root)
    store.enqueue(packet);store.close();calls=[]
    from proximic_ring.agent_workflow.operations import operation_service, operation_embeddings
    monkeypatch.setattr(operation_embeddings,'agent_busy',lambda:False)
    monkeypatch.setattr(workflow_store,'RETRY_DELAYS',(.01,.01,.01))
    original=workflows.process_one
    def model(p,s):
        calls.append('extract')
        return malformed(p) if calls.count('extract')==1 else (answer(p),{})
    monkeypatch.setattr(workflows,'process_one',lambda st,s:original(st,s,model=model))
    monkeypatch.setattr(workflows,'saved_settings',lambda:None)
    monkeypatch.setattr(operation_service,'update_catalog',lambda root:calls.append('import'))
    def review(root,settings):
        calls.append('review')
        return calls.count('review')==1
    monkeypatch.setattr(operation_service,'review_catalog',review)
    workflows.main(['--root',str(recorder.root),'--run'])
    assert calls==['extract','extract','import','import','review','import','review']
