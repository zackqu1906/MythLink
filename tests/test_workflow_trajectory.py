"""Local selection is fail-closed and must preserve every operational fact."""
import copy
import hashlib
import io
import json

import pytest

# Preserve legacy behavior tests behind an explicit test-only opt-in.
pytestmark = pytest.mark.usefixtures('legacy_workflow_pipeline')

from test_agent_recording import recorder
from test_agent_workflows import finished, answer
from proximic_ring.agent_workflow.extraction import workflow_trajectory as trajectory
from proximic_ring.agent_workflow.extraction.workflow_input import prepare_model_input
from proximic_ring.agent_workflow.extraction.workflow_model import extract
from proximic_ring.agent_workflow.extraction.workflow_store import WorkflowStore
from proximic_ring.agent_workflow.extraction.workflows import process_one
from proximic_ring.agent_workflow.reporting.trajectory_benchmark import benchmark_packet, verify_selection, write_report
from proximic_ring.text_processing.model import LLMSettings


def control(identifier, method, content, seq):
    return dict(id=identifier,kind='control_event',content=content,truncated=False,original_chars=100,
        event_refs=[dict(seq=seq,event_id=f'event-{seq}',method=method)],step_id=None,call_id=None,result_path=None)


def approval_pair(request_id=1,seq=1000):
    common=dict(requestId=request_id,method='mcpServer/elicitation/request',app='com.example.App')
    return [control(f'E{seq}','automatic_approval',dict(common,count=1),seq),
            control(f'E{seq+1}','request_decision',dict(common,disposition=trajectory.AUTOMATIC_ALLOW,
                                                     tool='get_app_state',epoch=1),seq+1)]


@pytest.fixture
def sample(recorder,monkeypatch):
    _,packet=finished(recorder)
    docs='## Computer Use\n'+('Known API documentation.\n'*400)
    monkeypatch.setattr(trajectory,'KNOWN_CUA_DOCUMENTATION',{hashlib.sha256(docs.encode()).hexdigest():'fixture docs'})
    result=next(e for e in packet['evidence'] if e['kind']=='result')
    result['content']['reported_status']='failed'
    result['content']['data']['error']={'message':'App lookup failed'}
    result['content']['data']['result']['content'].append({'type':'text','text':docs})
    packet['evidence']+=approval_pair()
    packet['evidence'].append(control('E2000','answer_input',dict(text='用户更正：只看附近五公里内的结果'),2000))
    return packet,docs,result['id']


def test_verified_docs_and_routine_pairs_reduce_but_actions_observations_errors_survive(sample):
    packet,docs,result_id=sample
    original=copy.deepcopy(packet)
    prepared=prepare_model_input(packet)
    assert packet==original
    assert prepared.selection['omitted_documentation_blocks']==1
    assert prepared.selection['condensed_control_events']==2
    assert prepared.selection['automatic_allowed_requests']==1
    assert docs not in json.dumps(prepared.model_packet,ensure_ascii=False)
    assert 'App lookup failed' in json.dumps(prepared.model_packet,ensure_ascii=False)
    assert '用户更正：只看附近五公里内的结果' in json.dumps(prepared.model_packet,ensure_ascii=False)
    verify_selection(packet,prepared)
    kept=next(e for e in prepared.trajectory['evidence'] if e['id']==result_id)
    assert kept['content']['reported_status']=='failed'
    assert kept['event_refs']==next(e for e in packet['evidence'] if e['id']==result_id)['event_refs']
    assert set(prepared.trajectory['trajectory_selection']['routine_control_summaries'][0]['evidence_ids'])=={'E1000','E1001'}


@pytest.mark.parametrize('change',['unknown_hash','mixed_state','wrong_server','observation','truncated','extra_block_fields'])
def test_documentation_like_task_content_is_not_removed_without_exact_verified_match(sample,change):
    packet,docs,result_id=sample
    result=next(e for e in packet['evidence'] if e['id']==result_id)
    block=result['content']['data']['result']['content'][1]
    if change=='unknown_hash':block['text']=docs.replace('Known','Changed',1)
    elif change=='mixed_state':block['text']=docs+'\nWindow: "Notes", App: Notes.\n0 standard window Notes'
    elif change=='wrong_server':next(e for e in packet['evidence'] if e['kind']=='call')['content']['data']['server']='other'
    elif change=='observation':result['kind']='observation'
    elif change=='truncated':result['truncated']=True
    elif change=='extra_block_fields':block['task_result']='important'
    prepared=prepare_model_input(packet)
    assert prepared.selection['omitted_documentation_blocks']==0
    kept=next(e for e in prepared.trajectory['evidence'] if e['id']==result_id)
    assert kept==result


@pytest.mark.parametrize('change',['denied','manual','unpaired','reused_id','extra_fields','reverse_order','truncated','bad_count'])
def test_uncertain_or_nonautomatic_permission_events_stay_verbatim(sample,change):
    packet,_,_=sample
    a,d=packet['evidence'][-3:-1]
    assert a['id']=='E1000' and d['id']=='E1001'
    if change=='denied':d['content']['disposition']='拒绝'
    elif change=='manual':d['content']['disposition']='用户允许'
    elif change=='unpaired':packet['evidence'].remove(d)
    elif change=='reused_id':packet['evidence']+=approval_pair(seq=3000)
    elif change=='extra_fields':d['content']['error']='permission state unknown'
    elif change=='reverse_order':a['event_refs'][0]['seq']=1002
    elif change=='truncated':d['truncated']=True
    elif change=='bad_count':a['content']['count']='unknown'
    prepared=prepare_model_input(packet)
    assert prepared.selection['condensed_control_events']==0
    assert [e for e in prepared.trajectory['evidence'] if e['kind']=='control_event']==[
        e for e in packet['evidence'] if e['kind']=='control_event']


def test_selection_can_be_applied_without_changing_raw_fingerprint_or_observation_binding(sample):
    packet,_,_=sample
    before=packet['fingerprint']
    first=prepare_model_input(packet);second=prepare_model_input(packet)
    assert first==second
    assert packet['fingerprint']==first.trajectory['fingerprint']==before
    from proximic_ring.agent_workflow.extraction.workflow_evidence import validate_workflows
    validate_workflows(answer(packet),packet)
    bad=answer(packet)
    bad['workflows'][0]['steps'][1]['observations']=bad['workflows'][0]['steps'][0]['observations']
    with pytest.raises(ValueError,match='观察不能借用其他调用'):
        validate_workflows(bad,packet)


def test_queue_transport_and_audit_use_same_selected_input(sample,recorder):
    packet,_,_=sample
    settings=LLMSettings(enabled=True,provider='volcengine',model='fixture',api_key='fixture')
    sent=[]
    def fake(req,timeout):
        sent.append(json.loads(req.data))
        return io.BytesIO(json.dumps(dict(status='completed',output=[dict(type='message',role='assistant',
            content=[dict(type='output_text',text=json.dumps(answer(packet)))])])).encode())
    store=WorkflowStore(recorder.root)
    try:
        job=store.enqueue(packet)
        assert process_one(store,settings,model=lambda p,s:extract(p,s,urlopen=fake))
        assert store.jobs()[0]['status']=='completed'
        saved=json.loads((store.directory/(job+'.model-input.json')).read_text())
        assert saved==json.loads(sent[0]['input'][1]['content'][0]['text'])
        assert json.loads((store.directory/(job+'.input.json')).read_text())==packet
        selection=json.loads((store.directory/(job+'.trajectory-selection.json')).read_text())
        assert selection['condensed_control_events']==2
        assert json.loads((store.directory/(job+'.trajectory.json')).read_text())==prepare_model_input(packet).trajectory
        result=json.loads((store.directory/(job+'.audit.json')).read_text())
        assert 'E1000' in result['evidence_index'] and result['evidence_index']['E1000']['event_refs']
    finally:store.close()


def test_benchmark_checks_round_trip_and_preserves_recording(sample,recorder,tmp_path):
    packet,_,_=sample
    journal=(recorder.directory/'events.jsonl').read_bytes()
    report,prepared=benchmark_packet(packet,repeats=2)
    assert report['final_chars']<report['dedup_only_chars']
    assert all(report['checks'].values()) and not report['production_queue_modified']
    assert report['model_calls']==0
    report['entry_check']={'passed':True}
    report['goal']='<script>not executable</script>'
    page=write_report(tmp_path/'report',recorder.root,report,prepared)
    assert '&lt;script&gt;' in page.read_text() and '<script>' not in page.read_text()
    assert not (recorder.root/'workflows/queue.sqlite3').exists()
    assert (recorder.directory/'events.jsonl').read_bytes()==journal
