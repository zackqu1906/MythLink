"""Dedup must save space without erasing actions, observations or provenance."""
import copy
import io
import json

import pytest

# Preserve legacy behavior tests behind an explicit test-only opt-in.
pytestmark = pytest.mark.usefixtures('legacy_workflow_pipeline')

from test_agent_recording import recorder, start, snapshot
from test_agent_step_view import call, state_result
from test_agent_workflows import finished, answer
from proximic_ring.agent_workflow.extraction import workflow_evidence as evidence
from proximic_ring.agent_workflow.extraction.workflow_compaction import compact_packet, encoded, expand_model_packet
from proximic_ring.agent_workflow.extraction.workflow_model import extract
from proximic_ring.agent_workflow.extraction.workflow_store import WorkflowStore
from proximic_ring.agent_workflow.extraction.workflows import process_one
from proximic_ring.agent_workflow.reporting.dedup_benchmark import benchmark_packet, write_report
from proximic_ring.text_processing.model import LLMSettings


def repeated_packet(recorder, *, count=12):
    episode=start(recorder)
    text='Window: "Notes", App: Notes.\n0 standard window Notes\n' + '目标笔记与正文\n'*2200
    for n in range(count):
        call(recorder,'read'+str(n),'started')
        call(recorder,'read'+str(n),result=state_result(text))
    return episode, evidence.build_evidence(snapshot(recorder),episode)


def test_large_repeated_results_fit_without_erasing_independent_observations(recorder):
    episode, packet=repeated_packet(recorder)
    original=copy.deepcopy(packet)
    journal=(recorder.directory/'events.jsonl').read_bytes()
    compact, stats=compact_packet(packet)
    assert stats['original_chars']>evidence.MAX_INPUT_CHARS>stats['model_input_chars']
    assert evidence.prepare_evidence(snapshot(recorder),episode)==packet
    restored=expand_model_packet(compact)
    assert len(restored['evidence'])==len(packet['evidence'])
    observations=[e for e in restored['evidence'] if e['kind']=='observation']
    assert len(observations)==12
    assert len({e['step_id'] for e in observations})==12
    for before,after in zip(packet['evidence'],restored['evidence']):
        assert (after['id'],after['kind'],after['step_id'],after['call_id']) == (
            before['id'],before['kind'],before['step_id'],before['call_id'])
        expected=copy.deepcopy(before['content'])
        if before['kind']=='call': expected.pop('event_refs')
        assert after['content']==expected
        assert before['event_refs']  # refs remain in local snapshot
    assert packet==original
    assert (recorder.directory/'events.jsonl').read_bytes()==journal


def test_only_exact_duplicates_are_shared_and_literal_markers_round_trip():
    text='same body '*100
    values=[{'text':text},{'text':text},{'text':text+'!'},
            {'$content_ref':'C1'}, {'nested':{'$literal_content':{'x':text}}}]
    packet={'evidence':[dict(id=f'E{n}',kind='result',step_id=f'S{n}',content=v,
                            event_refs=[{'seq':n*3+1},{'seq':n*3+3}]) for n,v in enumerate(values)]}
    compact,stats=compact_packet(packet)
    assert stats['saved_chars']>0
    restored=expand_model_packet(compact)
    assert [e['content'] for e in restored['evidence']]==values
    assert [e['id'] for e in restored['evidence']]==[e['id'] for e in packet['evidence']]
    assert restored['evidence'][0]['event_seq_range']==[1,3]
    # The table is plain original content, including any reserved-looking keys.
    collision={'$content_ref':'C1','large':text}
    packet['evidence'][0]['content']=collision
    packet['evidence'][1]['content']=copy.deepcopy(collision)
    compact,_=compact_packet(packet)
    assert expand_model_packet(compact)['evidence'][0]['content']==collision


def test_small_inputs_never_expand_and_compaction_is_deterministic():
    packet={'evidence':[{'id':'E1','kind':'message','content':'小文本'}]}
    compact,stats=compact_packet(packet)
    assert stats['mode']=='original' and compact==packet
    assert compact_packet(packet)==(compact,stats)


def test_nonmatching_nested_refs_are_not_silently_dropped():
    packet={'evidence':[dict(id='E1',kind='call',content={'event_refs':[{'seq':9}]},
        event_refs=[{'seq':1,'extra':'audit '*100}])]}
    compact,_=compact_packet(packet)
    assert expand_model_packet(compact)['evidence'][0]['content']['event_refs']==[{'seq':9}]


def test_ceiling_applies_after_dedup_and_still_rejects_unique_large_input(recorder,monkeypatch):
    _,packet=finished(recorder)
    for n,entry in enumerate(packet['evidence']):
        entry['content']={'unique':str(n)*9000}
    from proximic_ring.agent_workflow.extraction.workflow_input import prepare_model_input
    stats=prepare_model_input(packet).stats
    monkeypatch.setattr(evidence,'build_evidence',lambda *a:packet)
    monkeypatch.setattr(evidence,'MAX_INPUT_CHARS',stats['model_input_chars']-1)
    with pytest.raises(ValueError,match='去重后仍超过'):
        evidence.prepare_evidence(None,None)
    monkeypatch.setattr(evidence,'MAX_INPUT_CHARS',stats['model_input_chars'])
    assert evidence.prepare_evidence(None,None)==packet


def test_transport_sends_saved_compact_input_while_validation_uses_original_refs(recorder):
    _,packet=finished(recorder)
    # Add an identical long body without changing call/observation identities.
    for entry in packet['evidence']:
        if entry['kind'] in ('result','observation'): entry['content']['fixture_body']='界面正文'*400
    store=WorkflowStore(recorder.root)
    requests=[]
    settings=LLMSettings(enabled=True,provider='volcengine',model='test',api_key='fixture')
    def fake(req,timeout):
        requests.append(json.loads(req.data))
        return io.BytesIO(json.dumps(dict(status='completed',output=[dict(type='message',role='assistant',
            content=[dict(type='output_text',text=json.dumps(answer(packet)))])])).encode())
    try:
        job=store.enqueue(packet)
        # Pending jobs created by the previous version have only .input.json.
        (store.directory/(job+'.model-input.json')).unlink()
        (store.directory/(job+'.compaction.json')).unlink()
        assert process_one(store,settings,model=lambda p,s:extract(p,s,urlopen=fake))
        assert store.jobs()[0]['status']=='completed'
        sent=json.loads(requests[0]['input'][1]['content'][0]['text'])
        assert sent==json.loads((store.directory/(job+'.model-input.json')).read_text())
        assert json.loads((store.directory/(job+'.input.json')).read_text())==packet
        stats=json.loads((store.directory/(job+'.compaction.json')).read_text())
        assert stats['model_input_chars']==len(requests[0]['input'][1]['content'][0]['text'])
        result=json.loads((store.directory/(job+'.audit.json')).read_text())
        assert result['generation']['input_compaction']==stats
        assert result['evidence_index']['E1']['event_refs']==packet['evidence'][0]['event_refs']
        page=(store.directory/(job+'.audit.html')).read_text()
        assert job+'.model-input.json' in page and job+'.compaction.json' in page
    finally: store.close()


def test_oversized_unique_input_never_calls_provider(monkeypatch):
    import proximic_ring.agent_workflow.extraction.workflow_model as model
    monkeypatch.setattr(model,'MAX_INPUT_CHARS',10)
    packet={'evidence':[dict(id='E1',kind='message',content='unique'*100)]}
    with pytest.raises(ValueError,match='未发送模型'):
        extract(packet,None,urlopen=lambda *a,**k:pytest.fail('network requested'))


def test_benchmark_is_read_only_and_reports_preserved_content(recorder,tmp_path):
    _,packet=finished(recorder)
    before=(recorder.directory/'events.jsonl').read_bytes()
    stats=benchmark_packet(packet,repeats=2)
    assert stats['round_trip_verified'] and stats['source_snapshot_unchanged']
    assert stats['extra_model_calls']==0
    row=dict(stats,session_id=packet['source']['session_id'],episode_id=packet['source']['episode_id'],goal='<script>')
    path=write_report(tmp_path/'report',recorder.root,[row])
    assert '&lt;script&gt;' in path.read_text() and '<script>' not in path.read_text()
    assert not (recorder.root/'workflows/queue.sqlite3').exists()
    assert before==(recorder.directory/'events.jsonl').read_bytes()
