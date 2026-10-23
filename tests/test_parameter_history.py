"""Ordinary-task backfill uses only recorded values, separately from live dialogue."""
import copy
import json
import pytest

# Preserve legacy behavior tests behind an explicit test-only opt-in.
pytestmark = pytest.mark.usefixtures('legacy_workflow_pipeline')
from proximic_ring.agent_workflow.operations.operation_store import OperationStore
from proximic_ring.agent_workflow.operations.operation_ingest import sample_from_audit
from proximic_ring.agent_workflow.operations.operation_prototypes import prepare,heads,attach,add_revision
from proximic_ring.agent_workflow.operations.operation_display import save_display
from proximic_ring.agent_workflow.parameters.parameter_evidence import prepare_evidence,validate
from proximic_ring.agent_workflow.parameters.parameter_jobs import process_parameters
from proximic_ring.agent_workflow.parameters.parameter_history import current_records
from proximic_ring.agent_workflow.recommendation.recommendation_candidates import parameter_candidates
from proximic_ring.agent_workflow.recommendation.recommendation_service import request_recommendations
from proximic_ring.agent_workflow.recommendation.recommendation_catalog import catalog_state
from proximic_ring.agent_workflow.recommendation.operation_recommendations import recommend
from proximic_ring.agent_workflow.execution.recommendation_execution import prepare_execution,mark_execution
from test_operation_display import display


def task(store,identifier='one',title='数学课',*,scope='user',goal=None):
    directory=store.root/'workflows';directory.mkdir(exist_ok=True)
    ep=dict(id=identifier,status='completed',goal=goal or f'创建标题“{title}”、正文“第一行\n第二行”的笔记。',
        source='agent_editor' if scope=='user' else 'workflow_test',created_at='2026-10-19T01:00:00+00:00')
    src=dict(session_id='session',episode_id=identifier,episode=ep)
    audit=dict(format='desktop-workflow-v5',generation=dict(prompt_version='desktop-awm-5'),source=src,
        workflows=[dict(name='创建笔记',goal='创建 {{title}} 并记录 {{body}}',
            parameters=[dict(name='title',description='笔记标题',sources=['E1']),dict(name='body',description='正文',sources=['E1'])],
            steps=[dict(instruction='新建笔记并输入 {{title}} 和 {{body}}')],completion=dict(check='标题和内容可见'),conditions=[],fallbacks=[])])
    evidence=[dict(id='E1',kind='call',content=dict(data=dict(arguments=dict(code='await notes.paste('+json.dumps(title+'\n第一行\n第二行',ensure_ascii=False)+');'))),
        event_refs=[dict(event_id='event-'+identifier,seq=1,method='item/completed')],call_id='call-'+identifier)]
    (directory/(identifier+'.audit.json')).write_text(json.dumps(audit,ensure_ascii=False))
    (directory/(identifier+'.input.json')).write_text(json.dumps(dict(source=src,evidence=evidence),ensure_ascii=False))
    sample=sample_from_audit(audit,source_file='../workflows/'+identifier+'.audit.json')
    store.ingest(sample)
    if not heads(store):prepare(store)
    else:
        rev=next(iter(heads(store).values()));row=next(s for s in store.rows('samples') if s['id']==sample['id'])
        with store.db:attach(store,row,rev['operation_id'],rev['id'],'test-admitted')
    return sample['id']


@pytest.fixture
def history(tmp_path,monkeypatch):
    from proximic_ring.agent_workflow.parameters import parameter_jobs
    from proximic_ring.agent_workflow.operations import operation_model
    monkeypatch.setattr(parameter_jobs,'agent_busy',lambda:False)
    monkeypatch.setattr(operation_model,'agent_busy',lambda:False)
    store=OperationStore(tmp_path/'history');task(store)
    rev=next(iter(heads(store).values()))
    with store.db:save_display(store,rev['id'],display(rev['flow']),{})
    yield store
    store.close()


def model(packet,*args,**kwargs):
    assert '不得操作电脑' in kwargs['system_prompt']
    title='英语课' if any('英语课' in b['text'] for b in packet['evidence']) else '数学课'
    parameters=[]
    for name,value in [('title',title),('body','第一行\n第二行')]:
        b=next(b for b in packet['evidence'] if value in b['text'])
        parameters.append(dict(name=name,status='observed',value=value,evidence=[dict(block_id=b['id'],quote=b['text'])],reason='实际任务文本/输入'))
    return dict(parameters=parameters),dict(thinking_requested='enabled')


def candidates(store,scope='user'):
    return parameter_candidates(store,next(iter(heads(store))),['title','body'],scope=scope)


def test_backfill_grounded_provenance_and_no_lock_or_repeat(history):
    original={p:p.read_bytes() for p in (history.root/'workflows').iterdir()}
    before=history.rows('operation_revisions');calls=[]
    def network(packet,*a,**kw):
        other=OperationStore(history.root)
        try:
            with other.db:other.db.execute('BEGIN IMMEDIATE')
            assert not process_parameters(other,None,model=lambda *a,**k:pytest.fail('duplicate worker'))
        finally:other.close()
        calls.append(1);return model(packet,*a,**kw)
    assert process_parameters(history,None,model=network)
    assert not process_parameters(history,None,model=network) and len(calls)==1
    value=candidates(history)['title'][0]
    assert value['value']=='数学课' and value['count']==1
    src=value['sources'][0];assert src['kind']=='agent_task' and src['episode_id']=='one'
    assert src['evidence'][0]['source']['sha256']
    assert (history.directory/'parameter-history'/(src['record_id']+'.json')).exists()
    assert original=={p:p.read_bytes() for p in original} and history.rows('operation_revisions')==before


def test_code_literal_newlines_are_decoded_without_running_code(history):
    sid=task(history,'escaped',goal='记下内容')
    assert process_parameters(history,None,model=model,sample_id=sid)
    record=next(current_records(history));body=next(p for p in record['parameters'] if p['name']=='body')
    assert body['value']=='第一行\n第二行'
    source=body['evidence'][0]['source']
    assert source['transform']['kind']=='quoted_literal' and source['event_refs'][0]['event_id']=='event-escaped'
    assert source['json_pointer'].endswith('/arguments/code')


@pytest.mark.parametrize('bad',['value','quote','name','missing','uncertain_value'])
def test_untraceable_or_invalid_output_never_becomes_candidate(history,bad):
    def invalid(packet,*a,**kw):
        value,meta=model(packet,*a,**kw);p=value['parameters'][0]
        if bad=='value':p['value']='凭空编造'
        elif bad=='quote':p['evidence'][0]['quote']='来源没有的文字'
        elif bad=='name':p['name']='unknown'
        elif bad=='missing':value['parameters'].pop()
        else:p['status']='uncertain'
        return value,meta
    assert process_parameters(history,None,model=invalid)
    assert candidates(history)==dict(title=[],body=[])
    assert next(current_records(history))['status']=='failed'
    assert not process_parameters(history,None,model=lambda *a,**k:pytest.fail('automatic paid retry'))
    assert process_parameters(history,None,model=model,retry=True)
    assert len(history.rows('operation_parameter_history'))==2
    assert candidates(history)['title'][0]['count']==1


def test_missing_values_not_guessed_and_missing_file_recovers(history):
    path=history.root/'workflows/one.audit.json';data=path.read_bytes();path.unlink()
    assert process_parameters(history,None,model=lambda *a,**k:pytest.fail('missing audit'))
    assert next(current_records(history))['status']=='missing_source'
    assert not process_parameters(history,None,model=model)
    path.write_bytes(data)
    def unknown(packet,*a,**kw):
        return dict(parameters=[dict(name=p['name'],status='uncertain',value='',evidence=[],reason='无法确定实际值')
            for p in packet['general_workflow']['parameters']]),{}
    assert process_parameters(history,None,model=unknown)
    assert candidates(history)==dict(title=[],body=[])


def test_scope_duplicates_new_tasks_and_frozen_hub_snapshot(history):
    process_parameters(history,None,model=model)
    frozen=request_recommendations(history,scope='user');token=frozen['catalog']['token']
    score=recommend(history,scope='user')['items'][0]['score']
    task(history,'test-only',title='英语课',scope='test')
    assert not process_parameters(history,None,model=lambda *a,**k:pytest.fail('test data must not be sent automatically'))
    assert process_parameters(history,None,model=model,scope='test')
    assert candidates(history)['title'][0]['value']=='数学课'
    assert candidates(history,'test')['title'][0]['value']=='英语课'
    # A real new task contributes a distinct usage; two regenerated artifacts do not.
    task(history,'two',title='英语课');assert process_parameters(history,None,model=model)
    assert {p['value'] for p in candidates(history)['title']}=={'数学课','英语课'}
    assert catalog_state(history)['token']!=token
    assert len(frozen['items'][0]['parameter_candidates']['title'])==1
    assert frozen['items'][0]['parameter_candidates']['title'][0]['value']=='数学课'
    current=request_recommendations(history,scope='user')
    assert len(current['items'][0]['parameter_candidates']['title'])==2
    # Candidate extraction itself never changes recommendation usage frequency.
    score2=recommend(history,scope='user')['items'][0]['score']
    (history.root/'workflows/two.input.json').touch()
    assert process_parameters(history,None,model=model)
    assert all(p['count']==1 for p in candidates(history)['title'])
    assert recommend(history,scope='user')['items'][0]['score']==score2


def test_direct_hub_ticket_wins_and_same_episode_is_not_double_counted(history):
    process_parameters(history,None,model=model)
    request=request_recommendations(history,scope='user',surface='cli');item=request['items'][0]
    exposure=history.expose(request['request_id'],item['operation_id'],method='cli_confirmed')
    identifier=prepare_execution(history,exposure,dict(title='数学课',body='第一行\n第二行'))
    mark_execution(history,identifier,'completed',session_id='session',episode_id='one')
    assert candidates(history)['title'][0]['count']==1
    assert candidates(history)['title'][0]['execution_ids']==[identifier]
    assert not process_parameters(history,None,model=lambda *a,**k:pytest.fail('structured ticket'))


def test_revision_and_source_changes_invalidate_mapping_and_busy_defers(history,monkeypatch):
    from proximic_ring.agent_workflow.parameters import parameter_jobs
    monkeypatch.setattr(parameter_jobs,'agent_busy',lambda:True)
    assert not process_parameters(history,None,model=lambda *a,**k:pytest.fail('busy'))
    assert not history.rows('operation_jobs')
    monkeypatch.setattr(parameter_jobs,'agent_busy',lambda:False)
    process_parameters(history,None,model=model)
    rev=next(iter(heads(history).values()));flow=copy.deepcopy(rev['flow']);flow['conditions']=['备忘录可用']
    with history.db:
        rid=add_revision(history,rev['operation_id'],flow,parent=rev['id'])
        history.db.execute('UPDATE operation_heads SET active_revision=?',(rid,))
    assert candidates(history)==dict(title=[],body=[])
    assert process_parameters(history,None,model=model)
    assert candidates(history)['title'][0]['count']==1
    assert len(history.rows('operation_parameter_history'))==2


def test_background_catalog_chain_and_report(history):
    from proximic_ring.agent_workflow.operations.operation_catalog import review_one
    from proximic_ring.agent_workflow.operations.operation_view import export_operations
    assert review_one(history,None,model=model)
    assert not review_one(history,None,model=lambda *a,**k:pytest.fail('duplicate'))
    page=export_operations(history).read_text()
    assert 'Hub 历史参数候选与来源' in page and '数学课' in page
    assert (history.directory/'parameter-history/index.json').exists()
