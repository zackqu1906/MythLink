"""Minimal extraction must retain executable facts, not fabricate observations."""
import copy
import json
import pytest

# Preserve legacy behavior tests behind an explicit test-only opt-in.
pytestmark = pytest.mark.usefixtures('legacy_workflow_pipeline')
from test_agent_recording import recorder
from test_agent_workflows import finished
from proximic_ring.agent_workflow.extraction.workflow_input import prepare_model_input
from proximic_ring.agent_workflow.extraction.workflow_trace import project_trace,trim_ax
from proximic_ring.agent_workflow.extraction.workflow_compaction import expand_model_packet
from proximic_ring.agent_workflow.extraction.workflow_evidence import validate_workflows
from proximic_ring.agent_workflow.extraction.workflow_artifacts import compact_workflows,workflow_steps
from proximic_ring.agent_workflow.extraction.workflow_store import WorkflowStore
from proximic_ring.agent_workflow.extraction.workflows import process_one


def lean_answer(packet):
    calls=[e['id'] for e in packet['evidence'] if e['kind']=='call']
    result=next(e['id'] for e in packet['evidence'] if e['kind']=='result')
    return dict(format='desktop-workflow-v5',workflows=[dict(name='打开目标',goal='打开指定应用目标',parameters=[],
        steps=[dict(instruction='绑定目标应用并读取当前窗口。',sources=[calls[0]]),
               dict(instruction='根据当前界面的名称定位目标并打开。',sources=[calls[1]])],
        completion=dict(check='目标页面可见；未记录的状态不声称已验证。',sources=[result]),conditions=[],fallbacks=[])],extraction_notes=[])


def test_trace_groups_own_observations_and_retains_unknown_content(recorder):
    _,packet=finished(recorder);before=copy.deepcopy(packet)
    prepared=prepare_model_input(packet);trace=expand_model_packet(prepared.model_packet)
    calls=[r for r in trace['records'] if r['kind']=='call']
    assert len(calls)==2 and packet==before
    observation=next(e for e in packet['evidence'] if e['kind']=='observation')
    owners=[r for r in trace['records'] if observation['id'] in r['sources']]
    assert len(owners)==1 and owners[0]['call']==observation['step_id']
    assert not any(r['kind']=='observation' for r in trace['records'])
    packet['evidence'].append(dict(id='E999',kind='result',content={'unknown_format':'must survive'},event_refs=[],step_id=None))
    result,_=project_trace(packet)
    assert any(r['content']=={'unknown_format':'must survive'} for r in result['records'])


def test_ax_pruning_keeps_names_values_errors_targets_and_unknown_lines():
    text='Window: "Task", App: App.\n0 standard window Task, Secondary Actions: Raise\n\t1 container\n\t2 group\n\t3 text field (settable) Value: query\n\t4 text 评分 4.8\nError: permission denied\n'
    new,changes=trim_ax(text,{2})
    assert '1 container' not in new and '2 group' in new and '评分 4.8' in new
    assert 'Value: query' in new and 'Error: permission denied' in new
    assert {c['line'] for c in changes}=={2,3}
    assert trim_ax('Unknown result\n1 container\n',set())==('Unknown result\n1 container\n',[])


def test_sources_stay_local_and_checks_are_optional_in_execution_and_library(recorder):
    _,packet=finished(recorder);value=lean_answer(packet);validate_workflows(value,packet)
    store=WorkflowStore(recorder.root)
    try:
        job=store.enqueue(packet);assert process_one(store,None,model=lambda *a:(value,{}))
        assert store.jobs()[0]['status']=='completed'
        flow=compact_workflows(value)['workflows'][0]
        text=workflow_steps(flow)
        assert text.count('最终完成判断：')==1 and '完成条件：' not in text and '执行方法：' not in text
        assert 'sources' not in json.dumps(flow) and 'E1' not in text
        page=(store.directory/'library.html').read_text()
        assert '最终完成判断' in page and '执行方法：未记录' not in page
        audit=json.loads((store.directory/(job+'.audit.json')).read_text())
        assert audit['evidence_index']['E1']['event_refs']
        assert json.loads((store.directory/(job+'.input.json')).read_text())==packet
        from proximic_ring.agent_workflow.operations.operation_ingest import sample_from_audit
        from proximic_ring.agent_workflow.operations.operation_embeddings import workflow_texts
        sample=sample_from_audit(audit)
        assert flow['completion'] in workflow_texts(sample['flow'])['steps']
        from proximic_ring.agent_workflow.execution.workflow_trials import make_case,task_prompt
        case=make_case(dict(flow=flow,source={}),flow['goal'],{},'应用已打开')
        assert flow['completion'] in task_prompt(case,'natural_language')
    finally:store.close()


@pytest.mark.parametrize('fault',['foreign_source','noncall_step','empty_completion','undefined_parameter','historical_index'])
def test_lean_schema_rejects_unusable_or_untraceable_output(recorder,fault):
    _,packet=finished(recorder);value=lean_answer(packet);w=value['workflows'][0]
    if fault=='foreign_source':w['steps'][0]['sources']=['E999']
    elif fault=='noncall_step':w['steps'][0]['sources']=w['completion']['sources']
    elif fault=='empty_completion':w['completion']['check']=''
    elif fault=='undefined_parameter':w['goal']='打开 {{title}}'
    else:w['steps'][0]['instruction']='click(15)'
    with pytest.raises(ValueError):validate_workflows(value,packet)


def test_truncated_svg_bytes_can_be_omitted_but_error_envelope_must_survive():
    from proximic_ring.agent_workflow.extraction.workflow_payloads import svg_result
    call={'content':{'data':{'commandActions':[{'type':'read','path':'/tmp/image.svg'}]}}}
    result={'reported_status':'completed','data':{'aggregatedOutput':'<svg width="1">LONG</svg>\n','exitCode':0,'durationMs':1},'reasons':[]}
    entry={'content':json.dumps(result,ensure_ascii=False,separators=(',',':')),'truncated':True}
    assert svg_result(entry,call)['data']['exitCode']==0
    assert '<svg' not in svg_result(entry,call)['data']['aggregatedOutput']
    entry['content']=entry['content'].replace('"exitCode":0','"exitCode":1')
    assert svg_result(entry,call) is None


def test_misplaced_notes_are_repaired_without_changing_steps_or_losing_raw_output(recorder):
    _,packet=finished(recorder);value=lean_answer(packet)
    del value['extraction_notes'];value['workflows'][0]['extraction_notes']=['模型的原说明']
    original=copy.deepcopy(value)
    store=WorkflowStore(recorder.root)
    try:
        job=store.enqueue(packet);process_one(store,None,model=lambda *a:(value,{}))
        assert store.jobs()[0]['status']=='completed'
        raw=json.loads((store.directory/(job+'.model-output.json')).read_text())
        audit=json.loads((store.directory/(job+'.audit.json')).read_text())
        assert raw==original and value==original
        assert audit['extraction_notes']==['模型的原说明']
        assert audit['workflows'][0]['steps']==original['workflows'][0]['steps']
        assert audit['generation']['local_normalization']
    finally:store.close()
