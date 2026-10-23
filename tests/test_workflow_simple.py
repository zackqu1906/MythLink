"""v7 extraction through storage, retrieval text and execution; no paid calls."""
import copy
import json
import pytest

# Preserve legacy behavior tests behind an explicit test-only opt-in.
pytestmark = pytest.mark.usefixtures('legacy_workflow_pipeline')

from test_agent_recording import recorder
from test_agent_workflows import finished
from test_operation_prototypes import store, sample, same, synthesized
from proximic_ring.agent_workflow.extraction.workflow_evidence import validate_workflows
from proximic_ring.agent_workflow.extraction.workflow_store import WorkflowStore
from proximic_ring.agent_workflow.extraction.workflows import process_one
from proximic_ring.agent_workflow.extraction.workflow_artifacts import compact_workflows, workflow_steps
from proximic_ring.agent_workflow.extraction.workflow_text import natural_workflow
from proximic_ring.agent_workflow.operations.operation_catalog import regroup, review_one
from proximic_ring.agent_workflow.operations.operation_ingest import sample_from_audit
from proximic_ring.agent_workflow.operations.operation_embeddings import workflow_texts
from proximic_ring.agent_workflow.operations.operation_prototypes import heads, revision
from proximic_ring.agent_workflow.operations.operation_merge import merge_reviewed, membership_token
from proximic_ring.agent_workflow.recommendation.recommendation_service import request_recommendations
from proximic_ring.agent_workflow.execution.recommendation_execution import prepare_execution, dispatch_command
from proximic_ring.agent_workflow.workflow_content import plain_flow


def answer(packet):
    return dict(workflows=[dict(name='打开指定目标',goal='打开 {{target}}',
        parameters=[dict(name='target',description='目标名称')],
        steps=['读取当前界面，按名称定位并打开 {{target}}。'],
        sources=[e['id'] for e in packet['evidence'] if e['kind']=='call'])],notes=[])


def test_v7_extraction_audit_import_embeddings_and_execution(recorder,store):
    _,packet=finished(recorder);raw=(recorder.directory/'events.jsonl').read_bytes()
    jobs=WorkflowStore(recorder.root)
    try:
        jid=jobs.enqueue(packet)
        assert process_one(jobs,None,model=lambda p,s:(answer(p),{}))
        assert jobs.jobs()[0]['status']=='completed'
        audit=json.loads((jobs.directory/(jid+'.audit.json')).read_text())
        assert audit['format']=='agent-workflow-v7' and audit['schema_version']==4
        assert audit['workflows'][0]['sources'] and audit['evidence_index']
        value=json.loads((jobs.directory/audit['workflow_file']).read_text())
        flow=value['workflows'][0]
        assert value['schema_version']==6 and set(flow)=={'name','goal','parameters','steps'}
        text=(jobs.directory/(jid+'.flow.txt')).read_text()
        assert flow['steps'][0] in text and '完成条件' not in text
        assert '流程来源' in (jobs.directory/(jid+'.audit.html')).read_text()
        assert '当前 Workflow' in (jobs.directory/'library.html').read_text()
        assert workflow_texts(flow)['steps']=='1. '+flow['steps'][0]
        assert flow['steps'][0] in natural_workflow(flow)
        assert store.ingest(sample_from_audit(audit,force_kind='test'));regroup(store)
        result=request_recommendations(store,scope='test',surface='cli');item=result['items'][0]
        exp=store.expose(result['request_id'],item['operation_id'],method='cli_confirmed')
        command=dispatch_command(store,prepare_execution(store,exp,{'target':'设置'}))
        assert '设置' in command['prompt'] and '{{target}}' not in command['prompt']
        assert '最终完成判断' not in command['prompt']
        assert raw==(recorder.directory/'events.jsonl').read_bytes()
    finally:jobs.close()


@pytest.mark.parametrize('fault',['foreign','message_only','unknown_parameter','unused_parameter','empty_steps','extra_completion'])
def test_v7_rejects_invalid_structure_and_unavailable_evidence(recorder,fault):
    _,packet=finished(recorder);value=answer(packet);flow=value['workflows'][0]
    if fault=='foreign':flow['sources']=['E999999']
    elif fault=='message_only':flow['sources']=[e['id'] for e in packet['evidence'] if e['kind']=='result']
    elif fault=='unknown_parameter':flow['steps']=['打开 {{unknown}}']
    elif fault=='unused_parameter':flow['parameters'].append(dict(name='unused',description='无用途'))
    elif fault=='empty_steps':flow['steps']=[]
    else:flow['completion']='多余字段'
    with pytest.raises(ValueError):validate_workflows(value,packet)


def test_legacy_projection_preserves_requirements_without_rewriting_original():
    from test_operation_prototypes import flow
    old=flow();old.update(completion='确认已保存',conditions=['已登录'],fallbacks=[dict(when='按钮不可用',action='使用菜单')])
    before=copy.deepcopy(old);projected=plain_flow(old);text='\n'.join(projected['steps'])
    assert old==before and set(projected)=={'name','goal','parameters','steps'}
    assert all(t in text for t in ('已登录','标题与正文已显示','确认已保存','按钮不可用','使用菜单'))


def test_single_group_candidate_can_admit_pending_source_after_explicit_review(store):
    store.ingest(sample());regroup(store)
    new=plain_flow(store.versions()[store.rows('samples')[0]['version_id']]);new['steps'].append('若按钮不可用，使用菜单创建。')
    store.ingest(sample('new',new));review_one(store,None,model=same)
    def model(packet,*a,**k):
        changed=copy.deepcopy(packet['current_workflow']);changed['steps']=new['steps']
        return synthesized(packet,changed),{}
    review_one(store,None,model=model)
    update=store.rows('operation_updates')[0];rev=revision(store,update['revision_id']);expected=rev['generation']['expected_heads']
    plan=dict(revision_id=rev['id'],expected_heads=expected,membership_token=membership_token(store,expected),
        review=dict(authorization='fixture reviewed candidate and source branches',execution_verified=False,
            branches={v:dict(condition='同目标',steps=[1,2],verification='确认已创建',parameter_notes='输入角色一致') for v in rev['generation']['parameter_bindings']}),
        parameter_moves={v:{target:dict(source=name) for name,targets in mapping.items() for target in targets}
                         for v,mapping in rev['generation']['parameter_bindings'].items()})
    assert len(expected)==1 and next(s for s in store.rows('samples') if s['id']=='new')['operation_id'] is None
    merge_reviewed(store,plan)
    assert next(iter(heads(store).values()))['id']==rev['id']
    assert len(store.rows('operation_memberships'))==2
    assert not any(a['status']=='generalization_pending' for a in store.rows('operation_admissions'))


def test_answer_only_task_can_produce_reusable_v7_workflow(recorder):
    from test_general_workflows import finish
    _,packet=finish(recorder)
    messages=[e['id'] for e in packet['evidence'] if e['kind']=='message' and
              isinstance(e['content'],dict) and e['content'].get('type')=='agentMessage'
              and e['content'].get('phase')=='final_answer']
    value=dict(workflows=[dict(name='解释方法',goal='解释 {{method}} 的原理与局限',
        parameters=[dict(name='method',description='待解释的方法')],
        steps=['针对 {{method}} 说明原理、适用场景和局限。'],sources=messages)],notes=[])
    validate_workflows(value,packet)
    assert compact_workflows(value)['workflows'][0]['steps']==value['workflows'][0]['steps']
