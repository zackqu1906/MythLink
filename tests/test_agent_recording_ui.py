import queue
import json
import time
from urllib.request import Request, urlopen

import pytest

# Preserve legacy behavior tests behind an explicit test-only opt-in.
pytestmark = pytest.mark.usefixtures('legacy_workflow_pipeline')

from test_inline_ui import inline_ui
from test_agent_ui import finish_fixture_voice
from test_ui_shell import visual_child


def test_records_export_remains_available_without_card_button(inline_ui, monkeypatch, tmp_path):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtTest import QTest
    from proximic_ring.ui.agent import agent_controller as module
    from proximic_ring.ui.agent.agent_overlay import AgentOverlay
    from proximic_ring.agent_workflow.recording.recording import ExperienceRecorder
    from proximic_ring.agent_workflow.recording import records
    owner=inline_ui[0];agent=owner.agent
    finish_fixture_voice(owner)
    recorder=ExperienceRecorder(tmp_path/'records')
    recorder.new_episode('检查本地记录','test')
    assert recorder.flush()
    monkeypatch.setattr(records,'records_root',lambda:tmp_path/'records')
    agent.workspace.state['recordsRoot']=str(recorder.root)
    opened=[]
    monkeypatch.setattr(module.QDesktopServices,'openUrl',lambda url:opened.append(url.toString()) or True)
    agent._visible=agent._focused=True
    overlay=AgentOverlay(agent,owner);overlay.reduce_motion=True
    try:
        overlay.focus('task');QTest.qWait(60)
        window=overlay.windows['task']
        assert visual_child(window.rootObject(),'agentRecordsButton') is None
        assert visual_child(window.rootObject(),'agentWorkflowsButton') is None
        agent.openRecords()
        for _ in range(240):
            if opened:break
            QTest.qWait(25)
            time.sleep(.01)  # Let the Python export worker run, not only Qt events.
        assert agent._records_server is not None, (agent.errorMessage, agent._exporting_records)
        assert opened==[agent._records_server.overview_url]
        assert opened[0].startswith('http://127.0.0.1:')
        with urlopen(opened[0],timeout=3) as response:
            page=response.read().decode()
        assert '全流程总览' in page and 'Workflow' in page and '推荐' in page
        with urlopen(agent._records_server.url,timeout=3) as response:
            records_page=response.read().decode()
        assert '检查本地记录' in records_page and "data-action='extract'" in records_page
        assert agent._flow is None and not agent._commands
        agent._apply_state({'recordingError':'模拟写入失败'})
        assert '模拟写入失败' in agent.recordingWarning
    finally:
        recorder.close();overlay.close();agent.close()


def test_submission_preserves_editor_source_trigger_and_pre_focus_context(inline_ui, monkeypatch):
    from types import SimpleNamespace
    owner=inline_ui[0];agent=owner.agent
    fake=SimpleNamespace(inputs=queue.Queue(),stop=SimpleNamespace(set=lambda:None))
    agent._flow=fake
    agent._record_context={'app':'微信','bundle':'example.chat','captured_at':'earlier'}
    agent._submission_gesture={'gesture':'swipe-up'}
    original={'type':'text','text':'打开项目 A'}
    assert agent._send(original)
    _,command=fake.inputs.get_nowait()
    assert command['source']=='agent_editor'
    assert command['context']['before_agent_focus']['app']=='微信'
    assert command['context']['trigger']['gesture']=='swipe-up'
    assert original=={'type':'text','text':'打开项目 A'}
    agent._flow=None;agent.close()


def test_html_workflow_controls_use_background_queue_without_sending_agent_commands(inline_ui, monkeypatch, tmp_path):
    from PySide6.QtTest import QTest
    from proximic_ring.ui.agent import agent_controller as module
    from proximic_ring.agent_workflow.recording.recording import ExperienceRecorder
    from test_agent_workflows import finished
    from proximic_ring.agent_workflow.extraction.workflow_store import WorkflowStore
    owner=inline_ui[0]; agent=owner.agent
    # This test drives configure explicitly. The periodic heartbeat may otherwise
    # enqueue an extra idle config while the asynchronous HTML export completes.
    agent._workflow_timer.stop()
    finish_fixture_voice(owner)
    from proximic_ring.text_processing.model import LLMSettings
    monkeypatch.setattr(owner,'_llm_settings',lambda:LLMSettings(model='test',api_key='test',provider='volcengine'))
    agent.workspace.state['recordsRoot']=str(tmp_path/'records')
    recorder=ExperienceRecorder(tmp_path/'records')
    episode,_=finished(recorder)
    opened=[]
    monkeypatch.setattr(module.QDesktopServices,'openUrl',lambda url:opened.append(url.toString()) or True)
    try:
        agent.openRecords()
        for _ in range(240):
            if opened: break
            QTest.qWait(20)
            time.sleep(.01)
        assert agent._records_server is not None, (agent.errorMessage, agent._exporting_records)
        assert opened==[agent._records_server.overview_url]
        first=agent._workflow_service.commands.get_nowait()
        assert first['type']=='configure'
        assert first['settings']['model']=='test'
        agent.workspace.state['turnId']='busy'
        agent._configure_workflows()
        assert agent._workflow_service.commands.get_nowait()['busy'] is True
        server=agent._records_server
        def post(action,body):
            request=Request(server.origin+server.prefix+'api/'+action,data=json.dumps(body).encode(),
                headers={'Content-Type':'application/json','Origin':server.origin})
            with urlopen(request,timeout=3) as response: return json.load(response)
        result=post('extract',dict(session_id=recorder.session_id,episode_id=episode))
        assert len(result['jobs'])==1
        assert server.status()['busy'] is True
        store=WorkflowStore(recorder.root)
        try: assert store.jobs()[0]['status']=='queued'
        finally: store.close()
        post('automatic',{'automatic':False})
        QTest.qWait(30)
        assert agent.workflowAuto is False
        assert owner._settings.value('agent/workflowAuto',type=bool) is False
        assert agent._workflow_service.commands.get_nowait()['automatic'] is False
        assert agent._flow is None and agent._commands==[]
    finally: recorder.close();agent.close()


def test_invocation_is_frozen_before_dispatch_and_remember_does_not_replace_it(inline_ui, tmp_path):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from proximic_ring.agent_workflow.operations.operation_store import OperationStore
    from proximic_ring.agent_workflow.recommendation.recommendation_context import context_features
    owner=inline_ui[0];agent=owner.agent
    agent.workspace.state['recordsRoot']=str(tmp_path/'records')
    agent._capture_invocation(trigger='agent_show',pending=True)
    focus=dict(bundle='com.apple.Safari',captured_at=datetime.now(timezone.utc).isoformat())
    agent._focus_result(agent._focus_epoch,'capture',dict(status='remembered',context=focus))
    frozen=agent._recommendation_context()
    agent._focus_result(-1,'remember',dict(status='remembered',context=dict(bundle='com.apple.Music')))
    assert agent._recommendation_context()==frozen
    assert context_features(frozen)['app']=='com.apple.Safari'
    fake=SimpleNamespace(inputs=queue.Queue(),stop=SimpleNamespace(set=lambda:None));agent._flow=fake
    agent._send({'type':'text','text':'写笔记'})
    _,command=fake.inputs.get_nowait()
    assert command['context']['invocation_id']==frozen['invocation_id']
    assert command['context']['time_context']==frozen['time_context']
    agent._records_worker.submit(lambda:None).result(timeout=5)
    store=OperationStore(tmp_path/'records')
    try:
        rows=store.rows('recommendation_invocations');assert len(rows)==1
        assert json.loads(rows[0]['context'])['before_agent_focus']==focus
    finally:store.close();agent._flow=None;agent.close()


def test_failed_capture_does_not_reuse_old_application(inline_ui):
    from datetime import datetime, timezone
    from proximic_ring.agent_workflow.recommendation.recommendation_context import context_features
    agent=inline_ui[0].agent
    agent._record_context=dict(bundle='com.apple.Safari',captured_at=datetime.now(timezone.utc).isoformat())
    agent._capture_invocation(trigger='focus_toggle',pending=True)
    agent._focus_result(agent._focus_epoch,'capture',{'status':'unavailable'})
    assert context_features(agent._recommendation_context())['app'] is None
    assert agent._recommendation_context()['capture_status']=='unavailable'
    agent.close()
