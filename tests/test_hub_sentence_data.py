"""Sentence validation, real-history ranking and explicit audio ownership."""
import copy
import threading
import pytest

# Preserve legacy behavior tests behind an explicit test-only opt-in.
pytestmark = pytest.mark.usefixtures('legacy_workflow_pipeline')
from test_operation_display import store,display
from proximic_ring.agent_workflow.operations.operation_display import validate_display,save_display,read_display
from proximic_ring.agent_workflow.operations.operation_prototypes import heads
from proximic_ring.agent_workflow.operations.operation_display_jobs import process_display
from proximic_ring.agent_workflow.recommendation.recommendation_service import request_recommendations
from proximic_ring.agent_workflow.execution.recommendation_execution import prepare_execution,mark_execution
from proximic_ring.voice.utterance_commands import UtteranceCommands


def test_history_candidates_count_real_values_keep_scope_and_freeze_snapshot(store):
    def record(values,state='completed',scope='test'):
        r=request_recommendations(store,scope=scope,surface='cli');item=r['items'][0]
        exp=store.expose(r['request_id'],item['operation_id'],method='cli_confirmed')
        identifier=prepare_execution(store,exp,dict(title=values,body='真实正文'))
        mark_execution(store,identifier,state)
        return identifier
    a=record('最常用');b=record('最常用');record('后来一次');record('失败值','failed');record('尚未执行','prepared')
    frozen=request_recommendations(store,scope='test')
    candidates=frozen['items'][0]['parameter_candidates']['title']
    assert [c['value'] for c in candidates]==['最常用','后来一次']
    assert candidates[0]['count']==2 and set(candidates[0]['execution_ids'])=={a,b}
    # User/test operations share stable IDs but their actual use values must not mix.
    from proximic_ring.agent_workflow.recommendation.recommendation_candidates import parameter_candidates
    oid=frozen['items'][0]['operation_id']
    assert parameter_candidates(store,oid,['title'],scope='user')=={'title':[]}
    record('后来一次');record('后来一次')
    assert request_recommendations(store,scope='test')['items'][0]['parameter_candidates']['title'][0]['value']=='后来一次'
    assert candidates[0]['value']=='最常用' and candidates[0]['count']==2


@pytest.mark.parametrize('sentence',[[dict(parameter='invented')],[dict(parameter='title')],
    [dict(parameter='title'),dict(parameter='title'),dict(parameter='body')],
    [dict(text='<b>'),dict(parameter='title'),dict(parameter='body')]])
def test_sentence_cannot_invent_omit_duplicate_or_inject_parameter(store,sentence):
    flow=next(iter(heads(store).values()))['flow'];d=display(flow);d['sentence']=sentence
    with pytest.raises(ValueError):validate_display(d,flow)


def test_legacy_display_upgrade_retains_history_and_definition(store):
    revision=next(iter(heads(store).values()));d=display(revision['flow']);old=copy.deepcopy(d);old.pop('sentence')
    with store.db:save_display(store,revision['id'],old,{})
    before=store.rows('operation_revisions')
    assert process_display(store,None,model=lambda *a,**k:({'display':d},{}))
    assert read_display(store,revision['id'])['content']==d
    assert store.rows('operation_revisions')==before and len(store.rows('operation_display_history'))==2
    assert not process_display(store,None,model=lambda *a,**k:pytest.fail('duplicate paid request'))


def test_explicit_utterance_waits_for_ime_and_tap_even_in_automatic_mode():
    import numpy as np
    from test_asr_controller import make_gate,StreamingRecorder,activate_event,reject_event
    sink=StreamingRecorder();gate=make_gate(sink,start_on_gesture=False,end_on_tap=False)
    connection=threading.Event();commands=UtteranceCommands(connection);ack=[];result=[]
    commands.request('start',ack.append,result.append);commands.drain(gate,allowed=True)
    assert result==['start'] and not gate.active
    block=np.ones(320,dtype=np.float32)
    gate.process(block,[activate_event(320)]);assert not gate.active and not sink.started
    ack[0](True);gate.process(block,[]);assert gate.active and len(sink.started)==1
    for i in range(10):gate.process(block,[reject_event(960+i*320)])
    assert gate.active and not sink.ended
    commands.request('end',None,result.append);commands.drain(gate,allowed=True);gate.process(block,[])
    assert result[-1]=='end' and not gate.active and len(sink.ended)==1
    assert not gate.start_on_gesture and not gate._explicit_utterance
    gate.process(block,[activate_event(4480)]);assert gate.active # Original automatic behavior resumes.


def test_cancelled_connection_never_queues_new_audio_and_closed_gate_rejects():
    connection=threading.Event();commands=UtteranceCommands(connection);results=[]
    commands.request('start',None,results.append)
    commands.drain(None,allowed=False);assert results==['unavailable']
    connection.set();assert not commands.request('start',None,results.append)


def test_old_failure_does_not_block_upgrade_but_new_failure_requires_manual_retry(store):
    from proximic_ring.agent_workflow.operations.operation_jobs import create_job,finish_job
    revision=next(iter(heads(store).values()));legacy=display(revision['flow']);legacy.pop('sentence')
    with store.db:
        jid=create_job(store,'display',revision['id'],{})
        finish_job(store,jid,{}, {},status='failed',error='旧版失败')
        save_display(store,revision['id'],legacy,{})
    calls=[]
    def broken(*a,**k):calls.append(1);raise OSError('模拟网络失败')
    assert process_display(store,None,model=broken)
    assert len(calls)==1 and not process_display(store,None,model=broken)
    assert read_display(store,revision['id'])['content']==legacy
