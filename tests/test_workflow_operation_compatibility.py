"""New extraction → existing groups → revisions → recommendation, offline fixtures only."""
import copy
import hashlib
import io
import json
import queue
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone, timedelta

import pytest

# Preserve legacy behavior tests behind an explicit test-only opt-in.
pytestmark = pytest.mark.usefixtures('legacy_workflow_pipeline')
from test_agent_recording import recorder
from test_agent_workflows import finished
from test_workflow_trace import lean_answer
from proximic_ring.agent_workflow.extraction import workflows
from proximic_ring.agent_workflow.extraction.workflow_artifacts import compact_workflows, workflow_steps
from proximic_ring.agent_workflow.operations import operation_admission, operation_retrieval
from proximic_ring.agent_workflow.operations.operation_store import OperationStore
from proximic_ring.agent_workflow.operations.operation_flow import validate_flow
from proximic_ring.agent_workflow.operations.operation_catalog import regroup, review_one, sync_catalog
from proximic_ring.agent_workflow.operations.operation_prototypes import heads
from proximic_ring.agent_workflow.operations.operation_novelty import material_information
from proximic_ring.agent_workflow.operations.operation_embeddings import workflow_texts
from proximic_ring.agent_workflow.operations.operation_library import library
from proximic_ring.agent_workflow.operations.operation_view import export_operations
from proximic_ring.agent_workflow.recommendation.recommendation_catalog import catalog_state
from proximic_ring.agent_workflow.recommendation.recommendation_context import invocation_context
from proximic_ring.agent_workflow.recommendation.operation_recommendations import recommend
from proximic_ring.agent_workflow.recommendation.recommendation_service import request_recommendations
from proximic_ring.agent_workflow.execution.recommendation_execution import prepare_execution, dispatch_command
from proximic_ring.text_processing.model import LLMSettings


def flow():
    return dict(name='打开目标', goal='打开指定应用目标', parameters=[], conditions=[], fallbacks=[],
                steps=[dict(action='绑定应用并读取窗口。'), dict(action='按名称选择目标。', check='目标可定位')],
                completion='目标页面可见。')


def sample(sid, value, *, kind='user', context=None):
    return dict(id=sid, flow=value, kind=kind, source=dict(session_id='s', episode_id=sid),
                occurred_at=datetime.now(timezone.utc).isoformat(), context=context or {})


@pytest.fixture
def store(tmp_path, monkeypatch):
    def vectors(directory, flows, **kw):
        return {key: dict(goal=[1.]+[0.]*1023, steps=[1.]+[0.]*1023) for key in flows}, {}
    monkeypatch.setattr(operation_retrieval, 'ensure_embeddings', vectors)
    value=OperationStore(tmp_path)
    yield value
    value.close()


def decision(packet, novelty=None):
    return dict(decisions=[dict(operation_id=c['operation_id'],relation='same',reason='共同目标与方法') for c in packet['candidates']])

from test_operation_prototypes import unchanged, synthesized
from proximic_ring.agent_workflow.workflow_content import plain_flow


@pytest.mark.parametrize('kind', ['admission','update'])
def test_real_operation_transport_keeps_workflows_candidates_and_source_pointers(monkeypatch,kind):
    from proximic_ring.agent_workflow.extraction import workflow_model
    from proximic_ring.agent_workflow.operations.operation_model import call
    from proximic_ring.agent_workflow.extraction.workflow_compaction import encoded
    packet=(dict(new_workflow=flow(),source_version='v1',candidates=[dict(operation_id='a',workflow=flow())])
            if kind=='admission' else dict(current_workflow=flow(),parameter_map={},
                material_information=dict(kind='verification',evidence=[dict(path='/completion',quote=flow()['completion'])]),
                sources=[dict(version_id='v1',workflow=flow())]))
    seen=[]
    def http(req,timeout):
        body=json.loads(req.data);seen.append(body)
        return io.BytesIO(json.dumps(dict(status='completed',thinking=dict(type='enabled'),output=[dict(
            type='message',role='assistant',content=[dict(type='output_text',text='{}')])])).encode())
    original=workflow_model.request_json
    monkeypatch.setattr(workflow_model,'request_json',lambda *a,**kw:original(*a,**kw,urlopen=http))
    monkeypatch.setattr(workflow_model,'prepare_model_input',lambda *a:pytest.fail('structured workflow was projected as trajectory'))
    _,generation=call(packet,LLMSettings(provider='volcengine',model='fixture',api_key='fixture'),prompt='fixture')
    text=seen[0]['input'][1]['content'][0]['text']
    assert json.loads(text)==packet and text==encoded(packet)
    assert generation['input_sha256']==hashlib.sha256(text.encode()).hexdigest()
    assert generation['input_compaction']['saved_chars']==0
    assert seen[0]['thinking']['type']=='enabled'
    monkeypatch.setattr(workflow_model,'MAX_INPUT_CHARS',10)
    with pytest.raises(ValueError,match='未发送模型'):call(packet,LLMSettings(),prompt='fixture')
    assert len(seen)==1


@pytest.mark.parametrize('fault', ['missing_completion', 'empty_completion', 'empty_action', 'null_check', 'mixed_method', 'string_step'])
def test_invalid_new_shapes_are_not_silently_imported(store, fault):
    value=flow()
    if fault=='missing_completion':del value['completion']
    elif fault=='empty_completion':value['completion']=''
    elif fault=='empty_action':value['steps'][0]['action']=''
    elif fault=='null_check':value['steps'][0]['check']=None
    elif fault=='mixed_method':value['steps'][0]['method']='invented'
    else:value['steps'][0]='not an object'
    with pytest.raises(ValueError):store.ingest(sample('invalid', value))
    assert store.rows('samples')==[] and store.versions()=={}


def test_new_flow_preserves_content_context_and_executes_as_natural_language(store):
    stamp=datetime.now(timezone.utc)
    context=invocation_context(dict(bundle='com.example.source', captured_at=stamp.isoformat()), trigger='test', at=stamp)
    value=flow();before=copy.deepcopy(value)
    assert store.ingest(sample('new',value,context=context));regroup(store)
    assert value==before and next(iter(store.versions().values()))==before
    assert value['completion'] in workflow_texts(value)['steps']
    result=request_recommendations(store,context,scope='user')
    item=result['items'][0]
    assert result['context']['app']=='com.example.source'
    assert item['statistics']['levels'][0]['operation_mass']>0
    assert item['workflow']==before
    exposure=store.expose(result['request_id'],item['operation_id'])
    command=dispatch_command(store,prepare_execution(store,exposure,{}))
    assert command['context']['before_agent_focus']['bundle']=='com.example.source'
    assert workflow_steps(before) in command['prompt']
    assert command['prompt'].count('最终完成判断：')==1 and '执行方法：' not in command['prompt']
    assert export_operations(store).exists()


@pytest.mark.parametrize('legacy_first', [True, False])
def test_mixed_format_admission_keeps_stable_group_and_all_versions(store, legacy_first):
    new=flow();old=copy.deepcopy(new);old.pop('completion')
    old['steps']=[dict(action=s['action'],method=s['action'],check='已完成') for s in new['steps']]
    first,second=(old,new) if legacy_first else (new,old)
    store.ingest(sample('same-task',first));regroup(store);before=heads(store)
    token=catalog_state(store)['token']
    store.ingest(sample('same-task',second));regroup(store)
    assert review_one(store,None,model=lambda p,*a,**kw:(decision(p),{}))
    assert review_one(store,None,model=unchanged)
    assert heads(store)==before and len(library(store))==1
    assert len(store.versions())==2 and len(store.rows('operation_membership_events'))==2
    assert len(library(store)[0]['samples'])==1  # re-extraction is not a second use
    assert catalog_state(store)['token']!=token
    assert store.rows('operation_updates')[0]['status']=='no_change'


def test_final_completion_can_support_candidate_update_but_never_auto_activates(store):
    base=flow();store.ingest(sample('first',base));regroup(store);before=heads(store)
    incoming=copy.deepcopy(base);incoming['completion']+=' 核对目标页标题与请求一致。'
    store.ingest(sample('next',incoming));regroup(store)
    novelty=dict(kind='verification',reason='来源补充按标题核对',
                 evidence=[dict(path='/completion',quote=incoming['completion'])])
    assert review_one(store,None,model=lambda p,*a,**k:(decision(p,novelty),{}))
    token=catalog_state(store)['token']
    def update(packet,*a,**kw):
        return synthesized(packet,plain_flow(incoming)),{}
    assert review_one(store,None,model=update)
    assert heads(store)==before and catalog_state(store)['token']==token
    candidate=next(r for r in store.rows('operation_revisions') if r['status']=='candidate')
    assert json.loads(candidate['flow'])==plain_flow(incoming)
    assert recommend(store)['items'][0]['workflow']==base
    assert export_operations(store).exists()
    assert not material_information(novelty,incoming,incoming)['material']
    bad=dict(novelty,evidence=[dict(path='/steps/0/action',quote=incoming['steps'][0]['action'])])
    with pytest.raises(ValueError):material_information(bad,incoming,base)


def test_successful_retry_clears_current_failure_but_keeps_paid_failure_history(store):
    store.ingest(sample('one',flow()));regroup(store)
    other=flow();other['steps'][0]['action']+=' 使用已绑定应用。'
    store.ingest(sample('two',other));regroup(store)
    review_one(store,None,model=lambda *a,**k:({'decisions':[]},{}))
    assert catalog_state(store)['failed']==1
    review_one(store,None,retry=True,model=lambda p,*a,**k:(decision(p),{}))
    review_one(store,None,model=unchanged)
    state=catalog_state(store)
    assert state['failed']==0 and state['pending']==0 and state['historical_failed']==1
    assert any(j['status']=='failed' for j in store.rows('operation_jobs'))


@pytest.mark.parametrize('failures',[0,1,3])
def test_background_serve_automatically_extracts_imports_reviews_and_updates_ranking(recorder, store, monkeypatch,failures):
    """Exercise the actual idle loop, including periodic scan and review callbacks.

    Only the paid extractor/LLM and BGE encoder are fixtures. No explicit sync or
    review calls drive this test; the app's serve loop must discover and finish it.
    """
    from proximic_ring.agent_workflow.operations import embedding_install
    monkeypatch.setattr(embedding_install,'installed',lambda:True)
    captured=datetime.now(timezone.utc)
    context=invocation_context(dict(bundle='com.example.foreground',captured_at=captured.isoformat()),trigger='test',at=captured)
    original_new=recorder.new_episode
    monkeypatch.setattr(recorder,'new_episode',lambda goal,source,ctx:original_new(goal,source,context))
    episode,packet=finished(recorder)
    journal=(recorder.directory/'events.jsonl').read_bytes()
    old=flow();old.pop('completion');old['steps']=[dict(action='打开目标',method='绑定应用再选择目标',check='目标可见')]
    store.ingest(sample('prior',old,kind='test'));regroup(store);old_heads=heads(store)
    original_list=workflows.task_list
    monkeypatch.setattr(workflows,'task_list',lambda root:[dict(t,updated_at=(captured-timedelta(seconds=5)).isoformat()) for t in original_list(root)])
    original_process=workflows.process_one;calls=[]
    from proximic_ring.agent_workflow.extraction import workflow_store
    monkeypatch.setattr(workflow_store,'RETRY_DELAYS',(.01,.01,.01))
    def extract(p,s):
        calls.append('extract');result=lean_answer(p)
        if calls.count('extract')<=failures:result['workflows'][0]['steps'][0]['sources']=['missing']
        return result,{}
    monkeypatch.setattr(workflows,'process_one',lambda st,s:original_process(st,s,model=extract))
    def review(p,*a,**kw):calls.append('review');return decision(p),{}
    monkeypatch.setattr(operation_admission,'call',review)
    from proximic_ring.agent_workflow.operations import operation_update, operation_catalog
    def synthesize(p,*a,**kw):calls.append('synthesize');return unchanged(p)
    monkeypatch.setattr(operation_update,'call',synthesize)
    monkeypatch.setattr(operation_catalog,'process_display',lambda *a,**k:False)
    monkeypatch.setattr(workflows.os,'nice',lambda n:None)
    commands=queue.Queue();errors=[]
    def stream():
        while True:
            command=commands.get()
            if command is None:return
            yield json.dumps(command)+'\n'
    def run():
        try:workflows.serve(recorder.root,'2000-01-01T00:00:00+00:00',input_stream=stream())
        except Exception as exc:errors.append(exc)
    thread=threading.Thread(target=run,daemon=True);thread.start()
    commands.put(dict(type='configure',settings=asdict(LLMSettings(provider='volcengine',model='fixture',api_key='fixture')),automatic=True,busy=False))
    try:
        deadline=time.monotonic()+15
        while time.monotonic()<deadline and not errors:
            path=store.directory/'catalog.json'
            if path.exists():
                data=json.loads(path.read_text())
                if any(s['source'].get('episode_id')==episode for g in data['operations'] for s in g['samples']):break
            time.sleep(.05)
        else:pytest.fail('automatic pipeline did not finish: '+repr(errors))
    finally:
        commands.put(None);thread.join(timeout=5)
    assert not thread.is_alive() and not errors and calls==['extract']*(failures+1)+['review','synthesize']
    assert heads(store)==old_heads
    assert len(library(store)[0]['samples'])==2
    assert sync_catalog(recorder.root)==[]  # idempotent, no paid re-extraction
    result=recommend(store,context)
    assert result['evidence']['tasks']==1  # test seed does not inflate real habits
    assert result['context']['app']=='com.example.foreground'
    assert result['items'][0]['statistics']['levels'][0]['operation_mass']>0
    assert json.loads((recorder.root/'workflows/status.json').read_text())['jobs'][0]['status']=='completed'
    status=json.loads((store.directory/'status.json').read_text())
    assert not status.get('warnings') and not status.get('error')
    assert (recorder.directory/'events.jsonl').read_bytes()==journal
