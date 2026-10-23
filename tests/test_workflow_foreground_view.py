"""Display recorded invocation apps without confusing them with workflow targets."""
import copy
import io
import json
from datetime import datetime, timedelta, timezone

import pytest

# Preserve legacy behavior tests behind an explicit test-only opt-in.
pytestmark = pytest.mark.usefixtures('legacy_workflow_pipeline')
from test_agent_recording import recorder
from test_agent_workflows import finished
from test_workflow_trace import lean_answer
from proximic_ring.agent_workflow.reporting.foreground_view import foreground,render_foreground,group_foregrounds,render_group_foregrounds
from proximic_ring.agent_workflow.recommendation.recommendation_context import invocation_context
from proximic_ring.agent_workflow.operations.operation_store import OperationStore
from proximic_ring.agent_workflow.operations.operation_prototypes import prepare
from proximic_ring.agent_workflow.operations.operation_ingest import ingest_audits
from proximic_ring.agent_workflow.operations.operation_view import export_operations
from proximic_ring.agent_workflow.extraction.workflow_store import WorkflowStore
from proximic_ring.agent_workflow.extraction.workflows import process_one
from proximic_ring.agent_workflow.extraction.workflow_view import export_workflows

AT=datetime.now(timezone.utc)


def context(app='com.openai.codex',age=0):
    return invocation_context(dict(bundle=app,app='Captured name',window_title='private window title',
        captured_at=(AT-timedelta(seconds=age)).isoformat()),trigger='test',at=AT)


def test_captured_app_and_stale_or_missing_status_are_explicit_and_escaped():
    good=render_foreground(json.dumps(context()),AT.isoformat())
    assert 'Codex' in good and 'com.openai.codex' in good and '可用于场景统计' in good
    assert 'private window title' not in good
    stale=render_foreground(context(age=121),AT.isoformat())
    assert 'Codex' in stale and '失效' in stale and '不用于场景统计' in stale
    assert foreground(context(age=121),AT.isoformat())['app'] is None
    assert foreground(context())['app']=='com.openai.codex'  # dated invocation is a valid anchor
    for raw in [None,'invalid JSON',{'before_agent_focus':None},{'app':'Notes'}]:
        value=foreground(raw)
        assert value['app'] is None
    bad=context('<script>bad</script>');bad['before_agent_focus']['app']='<script>bad</script>'
    assert '<script>' not in render_foreground(bad,AT.isoformat())


def test_group_context_counts_real_episodes_once_and_excludes_test_sources():
    def sample(sid,episode,ctx,kind='user'):
        return dict(id=sid,source=dict(session_id='session',episode_id=episode),kind=kind,
                    occurred_at=AT.isoformat(),context=ctx)
    samples=[sample('a','one',context()),sample('b','one',context()),
             sample('c','two',context(age=500)),sample('d','three',context('com.apple.Music'),'test')]
    before=copy.deepcopy(samples);counts=group_foregrounds(samples)
    assert counts==dict(total=2,missing=1,excluded=1,apps=[dict(app='com.openai.codex',name='Codex',count=1)])
    page=render_group_foregrounds(samples)
    assert 'Codex 1 次' in page and '未知或失效 1 次' in page
    assert 'com.apple.Music' not in page and samples==before


def test_workflow_library_audit_group_and_members_show_source_app_without_changing_flows(recorder,monkeypatch):
    old=recorder.new_episode
    monkeypatch.setattr(recorder,'new_episode',lambda goal,source,ctx:old(goal,source,context()))
    _,packet=finished(recorder)
    ws=WorkflowStore(recorder.root);operations=OperationStore(recorder.root)
    try:
        job=ws.enqueue(packet)
        assert process_one(ws,None,model=lambda p,s:(lean_answer(p),{}))
        preserved={p:p.read_bytes() for suffix in ['.audit.json','.input.json','.flow.json','.flow.txt']
                   for p in [ws.directory/(job+suffix)]}
        export_workflows(ws)
        for name in ['library.html',job+'.audit.html']:
            page=(ws.directory/name).read_text()
            assert '发起任务前台 App' in page and 'Codex' in page and 'com.openai.codex' in page
        assert '480,000' in (ws.directory/'library.html').read_text()
        assert ingest_audits(operations)==(1,[]);prepare(operations);export_operations(operations)
        page=(operations.directory/'index.html').read_text()
        assert '历史前台 App：Codex 1 次' in page
        assert '发起任务前台 App' in page and '流程涉及应用（文本标签）' in page
        assert all(path.read_bytes()==data for path,data in preserved.items())
    finally:ws.close();operations.close()


def test_increased_input_budget_is_shared_by_queue_and_transport(tmp_path):
    from proximic_ring.agent_workflow.extraction.workflow_limits import MAX_INPUT_CHARS
    from proximic_ring.agent_workflow.extraction import workflow_evidence,workflow_model
    from proximic_ring.agent_workflow.operations.operation_jobs import create_job
    from proximic_ring.text_processing.model import LLMSettings
    assert MAX_INPUT_CHARS==workflow_evidence.MAX_INPUT_CHARS==workflow_model.MAX_INPUT_CHARS==480_000
    store=OperationStore(tmp_path);requests=[]
    packet=dict(new_workflow=dict(goal='步骤'*150_000))  # exceeds both previous local limits
    def http(request,timeout):
        requests.append(json.loads(request.data))
        return io.BytesIO(json.dumps(dict(status='completed',output=[dict(type='message',role='assistant',
            content=[dict(type='output_text',text='{}')])])).encode())
    settings=LLMSettings(provider='volcengine',model='fixture',api_key='fixture')
    try:
        with store.db:create_job(store,'admission','fixture',packet)
        workflow_model.request_json(packet,settings,system_prompt='fixture',urlopen=http)
        assert json.loads(requests[0]['input'][1]['content'][0]['text'])==packet
        assert requests[0]['max_output_tokens']==16000
        too_large=dict(new_workflow=dict(goal='x'*(MAX_INPUT_CHARS+1)))
        with pytest.raises(ValueError,match='480,000'):create_job(store,'admission','too-long',too_large)
        with pytest.raises(ValueError,match='未发送模型'):
            workflow_model.request_json(too_large,settings,system_prompt='fixture',urlopen=http)
        assert len(requests)==1 and len(store.rows('operation_jobs'))==1
    finally:store.close()
