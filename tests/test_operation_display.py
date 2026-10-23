"""Model-authored UI copy is version-bound, background-only and execution-neutral."""
import copy
import json
from datetime import datetime, timezone
import pytest

# Preserve legacy behavior tests behind an explicit test-only opt-in.
pytestmark = pytest.mark.usefixtures('legacy_workflow_pipeline')
from proximic_ring.agent_workflow.operations.operation_store import OperationStore
from proximic_ring.agent_workflow.operations.operation_prototypes import prepare, heads, add_revision
from proximic_ring.agent_workflow.operations.operation_display import validate_display, save_display, read_display, presentation
from proximic_ring.agent_workflow.operations.operation_display_jobs import process_display
from proximic_ring.agent_workflow.operations.operation_jobs import recover
from proximic_ring.agent_workflow.operations.operation_catalog import review_one
from proximic_ring.agent_workflow.recommendation.operation_recommendations import recommend
from proximic_ring.agent_workflow.recommendation.recommendation_catalog import catalog_state
from proximic_ring.agent_workflow.recommendation.recommendation_service import request_recommendations
from proximic_ring.agent_workflow.execution.recommendation_execution import prepare_execution, dispatch_command
from test_operation_prototypes import flow, sample, decision, update_model


def display(flow):
    return dict(title='新建备忘录', summary='把标题和内容保存到新的笔记', parameters={
        p['name']: dict(label='笔记标题' if p['name']=='title' else '要记什么？',
                       placeholder='输入标题' if p['name']=='title' else '输入或说出要记录的内容') for p in flow['parameters']},sentence=[dict(text='创建笔记')]+[dict(parameter=p['name']) for p in flow['parameters']])


@pytest.fixture
def store(tmp_path, monkeypatch):
    from proximic_ring.agent_workflow.operations import operation_retrieval as retrieval
    monkeypatch.setattr(retrieval, 'ensure_embeddings', lambda directory, flows, **kw:
        ({v:dict(goal=[1.]+[0.]*1023,steps=[1.]+[0.]*1023) for v in flows}, {}))
    s=OperationStore(tmp_path);s.ingest(sample());prepare(s)
    yield s
    s.close()


@pytest.mark.parametrize('change', ['key','missing','extra','long','markup','newline'])
def test_invalid_copy_cannot_change_parameter_contract(change):
    f=flow();d=display(f)
    if change=='key':d['parameters']['renamed']=d['parameters'].pop('title')
    elif change=='missing':d['parameters'].pop('body')
    elif change=='extra':d['parameters']['hidden']=dict(label='密码',placeholder='请输入')
    elif change=='long':d['title']='字'*17
    elif change=='markup':d['summary']='<script>run()</script>'
    else:d['parameters']['body']['placeholder']='第一行\n第二行'
    with pytest.raises(ValueError):validate_display(d,f)
    assert presentation(f,d)['title']==f['name']


def test_background_backfill_preserves_all_definitions_and_changes_only_catalog_copy(store):
    versions=store.versions()
    before={t:store.rows(t) for t in ('operation_revisions','operation_heads','samples','operation_memberships')}
    token=catalog_state(store)['token'];scores=[i['score'] for i in recommend(store,scope='test')['items']]
    calls=[]
    def model(packet,settings,**kw):
        assert set(packet)=={'revision_id','workflow','prompt_version'} and packet['workflow']==flow()
        assert '不得操作电脑' in kw['system_prompt']
        second=OperationStore(store.root)
        try:
            with second.db:second.db.execute('BEGIN IMMEDIATE')
            assert not process_display(second,None,model=lambda *a,**k:pytest.fail('duplicate call'))
        finally:second.close()
        calls.append(packet)
        return {'display':display(packet['workflow'])},{'thinking_requested':'enabled'}
    assert review_one(store,None,model=model)
    assert len(calls)==1 and not process_display(store,None,model=model)
    assert before=={t:store.rows(t) for t in before} and store.versions()==versions
    assert catalog_state(store)['token']!=token
    item=recommend(store,scope='test')['items'][0]
    assert item['name']==flow()['name'] and item['display']['title']=='新建备忘录'
    assert item['display_status']=='ready' and item['workflow']==flow()
    assert [i['score'] for i in recommend(store,scope='test')['items']]==scores
    record=json.loads(next((store.directory/'displays').glob('*.json')).read_text())
    assert record['generation']['job_id']==store.rows('operation_jobs')[0]['id']
    from proximic_ring.agent_workflow.operations.operation_view import export_operations
    page=export_operations(store).read_text()
    assert all(t in page for t in ('Hub 简洁标题','原参数名','Hub 标签','输入提示','要记什么？'))


def test_two_requests_have_identical_execution_prompts_and_original_keys(store):
    first=request_recommendations(store,scope='test',surface='native');i1=first['items'][0]
    exp1=store.expose(first['request_id'],i1['operation_id'],method='native_visible_500ms')
    values={'title':'课堂记录','body':'内容含 {{title}}，保持原文'}
    one=dispatch_command(store,prepare_execution(store,exp1,values))
    with store.db:save_display(store,i1['workflow_revision'],display(i1['workflow']),{})
    second=request_recommendations(store,scope='test',surface='native');i2=second['items'][0]
    exp2=store.expose(second['request_id'],i2['operation_id'],method='native_visible_500ms')
    with pytest.raises(ValueError):prepare_execution(store,exp2,{'笔记标题':'课堂记录','要记什么？':'正文'})
    with pytest.raises(ValueError):prepare_execution(store,exp2,{'title':'课堂记录','body':''})
    two=dispatch_command(store,prepare_execution(store,exp2,values))
    assert one['prompt']==two['prompt'] and one['goal']==two['goal']
    assert '内容含 {{title}}，保持原文' in two['prompt']
    assert first['items'][0]['display']['title']==flow()['name'] # frozen old snapshot
    assert i2['display']['title']=='新建备忘录'
    assert json.loads(store.rows('recommendation_executions')[1]['parameters'])==values


def test_new_group_copy_has_separate_background_call(store):
    store.ingest(sample('different',flow('另一条方法')))
    def admission(packet,*a,**kw):
        assert 'new_operation_display' not in kw['system_prompt']
        return decision(packet,'different'),{}
    assert review_one(store,None,model=admission)
    assert len(store.rows('operation_jobs'))==1 and not store.rows('operation_displays')
    assert process_display(store,None,model=lambda p,*a,**k:({'display':display(p['workflow'])},{}))
    assert len(store.rows('operation_jobs'))==2 and len(store.rows('operation_displays'))==1
    assert len(heads(store))==2


def test_synthesis_does_not_generate_display_or_change_active_copy(store):
    original=next(iter(heads(store).values()))
    with store.db:save_display(store,original['id'],display(original['flow']),{})
    f=flow();f['conditions']=['若输入区尚未聚焦，先聚焦正文编辑区'];store.ingest(sample('b',f))
    novelty=dict(kind='condition',reason='新增焦点条件',evidence=[dict(path='/conditions/0',quote=f['conditions'][0])])
    review_one(store,None,model=lambda p,*a,**k:(decision(p,novelty=novelty),{}))
    token=catalog_state(store)['token']
    def update(packet,*a,**kw):
        assert 'current_display' not in packet
        return update_model(packet)
    assert review_one(store,None,model=update)
    candidate=next(r for r in store.rows('operation_revisions') if r['status']=='candidate')
    assert read_display(store,candidate['id']) is None
    assert catalog_state(store)['token']==token
    assert recommend(store)['items'][0]['display']['title']=='新建备忘录'
    assert len(store.rows('operation_jobs'))==2 # admission + update, no extra display request


def test_separate_bad_copy_does_not_undo_admission_or_auto_retry(store):
    store.ingest(sample('different',flow('另一条方法')))
    review_one(store,None,model=lambda p,*a,**k:(decision(p,'different'),{}))
    process_display(store,None,model=lambda *a,**k:({'display':{'bad':1}},{}))
    assert len(heads(store))==2 and store.rows('operation_admissions')[0]['status']=='new_operation'
    bad=next(j for j in store.rows('operation_jobs') if j['kind']=='display')
    assert bad['status']=='failed'
    # Fill the unrelated first seed; only the failed revision remains.
    other=next(r for r in heads(store).values() if r['id']!=bad['subject_id'])
    with store.db:save_display(store,other['id'],display(other['flow']),{})
    assert not process_display(store,None,model=lambda *a,**k:pytest.fail('auto retry'))
    assert process_display(store,None,retry=True,model=lambda p,*a,**k:({'display':display(p['workflow'])},{}))


def test_busy_and_failed_jobs_do_not_start_repeated_calls(store,monkeypatch):
    from proximic_ring.agent_workflow.operations import operation_display_jobs as jobs
    monkeypatch.setattr(jobs,'agent_busy',lambda:True)
    assert not process_display(store,None,model=lambda *a,**k:pytest.fail('busy'))
    monkeypatch.setattr(jobs,'agent_busy',lambda:False)
    calls=[]
    def bad(*a,**k):calls.append(1);raise ValueError('模拟失败')
    assert process_display(store,None,model=bad)
    assert not process_display(store,None,model=bad) and len(calls)==1
    assert process_display(store,None,model=bad,retry=True) and len(calls)==2


def test_display_lease_recovery_and_stale_head_cannot_overwrite_new_version(store):
    old=next(iter(heads(store).values()))
    def model(packet,*a,**k):
        with store.db:
            rid=add_revision(store,old['operation_id'],dict(flow(),conditions=['新条件']),parent=old['id'])
            store.db.execute('UPDATE operation_heads SET active_revision=? WHERE operation_id=?',(rid,old['operation_id']))
        return {'display':display(packet['workflow'])},{}
    process_display(store,None,model=model)
    assert read_display(store,old['id'])
    assert recommend(store)['items'][0]['display_status']=='pending'
    from proximic_ring.agent_workflow.operations.operation_jobs import create_job
    rid=next(iter(heads(store).values()))['id']
    with store.db:
        jid=create_job(store,'display',rid,{})
        store.db.execute("UPDATE operation_jobs SET at='2020-01-01T00:00:00+00:00' WHERE id=?",(jid,))
    recover(store)
    assert not process_display(store,None,model=lambda *a,**k:pytest.fail('crash auto retry'))


def test_explicit_copy_refresh_retains_history_and_frozen_recommendations(store):
    rev=next(iter(heads(store).values()))
    with store.db:save_display(store,rev['id'],display(rev['flow']),{})
    frozen=request_recommendations(store,scope='test');before=store.rows('operation_revisions')
    newer=dict(display(rev['flow']),title='创建笔记')
    assert process_display(store,None,operation_id=rev['operation_id'],refresh=True,
        model=lambda p,*a,**k:({'display':newer},{'thinking_requested':'enabled'}))
    assert len(store.rows('operation_display_history'))==2
    assert read_display(store,rev['id'])['content']==newer
    assert store.rows('operation_revisions')==before
    assert frozen['items'][0]['display']['title']=='新建备忘录'
    assert recommend(store)['items'][0]['display']['title']=='创建笔记'
    assert not process_display(store,None,model=lambda *a,**k:pytest.fail('implicit rewrite'))
