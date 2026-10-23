"""Frozen selection -> existing Flow -> actual recording -> background ingestion."""
import json
from datetime import datetime,timezone
from types import SimpleNamespace
import pytest

# Preserve legacy behavior tests behind an explicit test-only opt-in.
pytestmark = pytest.mark.usefixtures('legacy_workflow_pipeline')
from proximic_ring.agent_workflow.operations.operation_store import OperationStore
from proximic_ring.agent_workflow.operations.operation_prototypes import prepare,add_revision
from proximic_ring.agent_workflow.recommendation.recommendation_service import request_recommendations
from proximic_ring.agent_workflow.recommendation.recommendation_context import invocation_context
from proximic_ring.agent_workflow.recommendation.recommendation_catalog import catalog_state
from proximic_ring.agent_workflow.execution.recommendation_execution import prepare_execution,dispatch_command
from proximic_ring.agent_workflow.recording.recording import ExperienceRecorder
from proximic_ring.agent_workflow.agent.flow import Flow


@pytest.fixture
def catalog(tmp_path):
    store=OperationStore(tmp_path)
    flow=dict(name='创建笔记',goal='新建 {{title}}',parameters=[dict(name='title',description='标题')],
        steps=[dict(action='创建笔记',method='打开备忘录，新建并填写 {{title}}',check='标题一致')],conditions=[],fallbacks=[])
    store.ingest(dict(id='seed',kind='user',flow=flow,source=dict(session_id='s',episode_id='e'),context={},occurred_at=datetime.now(timezone.utc).isoformat()))
    prepare(store)
    yield store
    store.close()


def displayed(store):
    context=invocation_context(dict(bundle='com.apple.Safari',captured_at=datetime.now(timezone.utc).isoformat()),trigger='recommendation_gesture')
    result=request_recommendations(store,context,surface='native')
    item=result['items'][0];exposure=store.expose(result['request_id'],item['operation_id'],method='native_visible_500ms')
    return result,item,exposure


def test_parameters_are_required_single_pass_and_ticket_dispatch_is_idempotent(catalog):
    result,item,exposure=displayed(catalog)
    with pytest.raises(ValueError):prepare_execution(catalog,exposure,{})
    assert not catalog.rows('recommendation_feedback')
    ticket=prepare_execution(catalog,exposure,{'title':'literal {{title}}'})
    command=dispatch_command(catalog,ticket)
    assert 'literal {{title}}' in command['prompt']
    assert command['context']['before_agent_focus']['bundle']=='com.apple.Safari'
    assert command['context']['recommendation']['revision_id']==item['workflow_revision']
    with pytest.raises(ValueError):dispatch_command(catalog,ticket)
    with pytest.raises(ValueError):prepare_execution(catalog,exposure,{'title':'again'})


def test_catalog_changes_only_after_admission_or_activation(catalog):
    before=catalog_state(catalog);result,item,exposure=displayed(catalog)
    with catalog.db:
        add_revision(catalog,item['operation_id'],dict(item['workflow'],conditions=['new condition']),
                     parent=item['workflow_revision'],kind='condition')
    assert catalog_state(catalog)==before
    catalog.feedback(exposure,item['operation_id'],'rejected')
    assert catalog_state(catalog)==before
    sample=catalog.rows('samples')[0]
    catalog.ingest(dict(id='second',kind='user',flow=item['workflow'],source=dict(session_id='s',episode_id='e2'),context={},occurred_at=sample['occurred_at']))
    assert catalog_state(catalog)==before
    from proximic_ring.agent_workflow.operations.operation_catalog import review_one
    prepare(catalog);review_one(catalog,None) # exact source version uses its existing admission, no LLM
    assert catalog_state(catalog)['token']!=before['token']
    assert result['catalog']==before


def test_recommendation_dispatch_uses_existing_flow_and_records_origin(catalog,tmp_path):
    from proximic_ring.agent_workflow.recording.records import read_session
    result,item,exposure=displayed(catalog)
    command=dispatch_command(catalog,prepare_execution(catalog,exposure,{'title':'课堂记录'}))
    flow=Flow(with_ring=False,runtime_dir=tmp_path/'runtime',recording_dir=tmp_path)
    flow.recorder=ExperienceRecorder(tmp_path);flow.tid='thread'
    sent=[]
    flow.rpc=SimpleNamespace(send=lambda method,params:sent.append((method,params)) or 42)
    try:
        flow.command(command)
        assert sent[0][0]=='turn/start' and '课堂记录' in sent[0][1]['input'][0]['text']
        episode=flow.episode_id
        row=catalog.rows('recommendation_executions')[0]
        assert row['state']=='running' and row['episode_id']==episode
        flow.command(command)
        assert len(sent)==1
        assert flow.recorder.flush()
        recorded=read_session(flow.recorder.directory)['episodes'][0]
        assert recorded['source']=='workflow_recommendation'
        assert recorded['goal']=='新建 课堂记录'
        from proximic_ring.agent_workflow.execution.recommendation_agent import finish_recommendation
        finish_recommendation(flow,'completed')
        assert catalog.rows('recommendation_executions')[0]['state']=='completed'
        assert not [f for f in catalog.rows('recommendation_feedback') if f['category']=='outcome']
    finally:flow.recorder.close();flow.events.close()


def test_full_completed_record_extraction_ingestion_becomes_recommendable(tmp_path):
    from test_agent_workflows import finished,answer
    from proximic_ring.agent_workflow.extraction.workflow_store import WorkflowStore
    from proximic_ring.agent_workflow.extraction.workflows import process_one
    from proximic_ring.agent_workflow.operations.operation_service import update_catalog
    recorder=ExperienceRecorder(tmp_path);queue=WorkflowStore(tmp_path)
    try:
        _,packet=finished(recorder)
        queue.enqueue(packet)
        assert process_one(queue,None,model=lambda p,s:(answer(p),{'thinking_requested':'enabled'}))
        update_catalog(tmp_path)
        store=OperationStore(tmp_path)
        try:
            result=request_recommendations(store,surface='native')
            assert result['candidate_count']==1 and result['evidence']['tasks']==1
            assert result['items'][0]['workflow_revision']
            assert store.rows('samples')[0]['kind']=='user'
        finally:store.close()
    finally:queue.close();recorder.close()


def test_busy_flow_rejects_recommendation_without_queuing_or_steering(catalog,tmp_path):
    _,_,exposure=displayed(catalog)
    command=dispatch_command(catalog,prepare_execution(catalog,exposure,{'title':'课堂记录'}))
    flow=Flow(with_ring=False,runtime_dir=tmp_path/'runtime',recording_dir=tmp_path)
    flow.recorder=ExperienceRecorder(tmp_path);flow.tid='thread';flow.turn='already-active'
    try:
        flow.command(command)
        assert not flow.task_queue
        assert catalog.rows('recommendation_executions')[0]['state']=='not_started'
    finally:flow.recorder.close();flow.events.close()
