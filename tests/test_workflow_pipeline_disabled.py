"""Production defaults must block work before queues, models or RPC side effects."""
import io
import threading
from types import SimpleNamespace

import pytest

from proximic_ring.agent_workflow import pipeline_policy
from proximic_ring.agent_workflow.extraction import workflows
from proximic_ring.agent_workflow.extraction.workflow_store import WorkflowStore
from proximic_ring.agent_workflow.operations import operation_cli, operation_service
from proximic_ring.agent_workflow.recording import workbench_actions
from proximic_ring.agent_workflow.recording.records_server import RecordsServer
from proximic_ring.agent_workflow.recommendation.recommendation_service import request_recommendations
from proximic_ring.agent_workflow.execution.recommendation_execution import prepare_execution, dispatch_command
from proximic_ring.agent_workflow.execution.recommendation_agent import start_recommendation
from proximic_ring.agent_workflow.execution import workflow_trial_cli
from proximic_ring.agent_workflow.ui.workflow_service import WorkflowService
from test_inline_ui import inline_ui
from test_recommendation_execution import catalog

BRIDGE_SEND = WorkflowService.send


def test_default_gate_leaves_queued_work_and_raw_evidence_untouched(tmp_path):
    from test_agent_workflows import finished
    from proximic_ring.agent_workflow.recording.recording import ExperienceRecorder
    recorder = ExperienceRecorder(tmp_path)
    store = WorkflowStore(tmp_path)
    try:
        _, packet = finished(recorder)
        job = store.enqueue(packet)
        before = (recorder.directory / 'events.jsonl').read_bytes()
        assert not pipeline_policy.legacy_workflow_enabled()
        assert workflows.process_one(store, None, model=lambda *a: pytest.fail('model called')) is False
        assert workbench_actions.process_one(store, None) is False
        workflows.scan(store, '', set())
        assert len(store.jobs()) == 1
        assert store.jobs()[0]['id'] == job and store.jobs()[0]['status'] == 'queued'
        assert (recorder.directory / 'events.jsonl').read_bytes() == before
        assert operation_service.update_catalog(tmp_path) is False
        assert operation_service.review_catalog(tmp_path, None) is False
        assert not (tmp_path / 'operations').exists()
    finally:
        store.close()
        recorder.close()


@pytest.mark.parametrize('action', ['extract', 'latest', 'retry-failed', 'automatic',
                                  'workbench/run', 'workbench/adopt',
                                  'operations/recommend', 'operations/exposure', 'operations/feedback'])
def test_web_actions_cannot_create_queues_or_reenable_pipeline(tmp_path, action):
    server = object.__new__(RecordsServer)
    server.root = tmp_path
    server._actions = threading.Lock()
    with pytest.raises(ValueError, match='已停用'):
        server.action('api/' + action, {'automatic': True})
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('invoke', [
    lambda root: workflows.main(['--root', str(root), '--run']),
    lambda root: workflows.main(['--root', str(root), '--latest']),
    lambda root: workflows.main(['--root', str(root), '--serve']),
    lambda root: operation_cli.main(['recommend', '--root', str(root)]),
    lambda root: operation_cli.main(['parameters', '--root', str(root)]),
    lambda root: operation_cli.main(['sync', '--root', str(root)]),
])
def test_cli_mutations_fail_before_creating_data(tmp_path, invoke):
    with pytest.raises(SystemExit) as exc:
        invoke(tmp_path)
    assert exc.value.code == 2
    assert not list(tmp_path.iterdir())


def test_direct_worker_recommendation_and_execution_entries_are_disabled(tmp_path):
    for invoke in (
        lambda: workflows.serve(tmp_path, '', input_stream=io.StringIO()),
        lambda: request_recommendations(None),
        lambda: prepare_execution(None, 'old-exposure', {}),
        lambda: dispatch_command(None, 'old-ticket'),
        lambda: workbench_actions.enqueue(tmp_path, {}),
        lambda: workbench_actions.adopt(tmp_path, {}),
    ):
        with pytest.raises(ValueError, match='已停用'):
            invoke()
    messages = []
    flow = SimpleNamespace(publish=lambda **kw: messages.append(kw))
    start_recommendation(flow, {'execution_id': 'old-ticket'})
    from proximic_ring.agent_workflow.agent.flow import Flow
    Flow.start_workflow_trial(flow, {})
    assert len(messages) == 2 and all('已停用' in str(message) for message in messages)
    assert workflow_trial_cli.main(['run', '--root', str(tmp_path)]) == 1
    assert not list(tmp_path.iterdir())


def test_ui_does_not_start_worker_or_restore_saved_auto_preference(inline_ui, monkeypatch):
    from proximic_ring.agent_workflow.ui.workflow_service import WorkflowService
    from PySide6.QtCore import QObject
    from test_agent_ui import finish_fixture_voice
    owner, _, _, root, _ = inline_ui
    finish_fixture_voice(owner)
    agent = owner.agent
    owner._settings.setValue('agent/workflowAuto', True)
    monkeypatch.setattr(WorkflowService, 'send', lambda *a: pytest.fail('worker sent'))
    monkeypatch.setattr(agent.recommendations, '_job', lambda *a, **kw: pytest.fail('recommendation started'))
    agent.workflowAuto = True
    agent._configure_workflows()
    agent.recommendations.open()
    agent.openWorkflowTrials()
    assert not agent.workflowAuto and not agent.workflowPipelineEnabled
    assert not agent._workflow_timer.isActive() and agent._workflow_service is None
    assert agent.recommendations.dialog is None and agent._workflow_trial_dialog is None
    switch = root.findChild(QObject, 'agentWorkflowAutoSwitch')
    assert switch is not None and not switch.property('enabled')
    assert root.findChild(QObject, 'openWorkflowRecommendations').property('enabled')


def test_bridge_send_is_inert_even_outside_controller(tmp_path, monkeypatch):
    # Bypass only the shared fixture's mocked send, without starting a process.
    service = WorkflowService(tmp_path, '', lambda value: None)
    BRIDGE_SEND(service, {'type': 'configure', 'automatic': True})
    assert service.thread is None and service.commands.empty()
    assert not list(tmp_path.iterdir())


def test_history_export_does_not_recompute_or_emit_recommendations(catalog, monkeypatch):
    from proximic_ring.agent_workflow.operations import operation_view
    assert not pipeline_policy.legacy_workflow_enabled()
    monkeypatch.setattr(operation_view, 'recommend', lambda *a, **kw: pytest.fail('ranking ran'))
    path = operation_view.export_operations(catalog)
    text = path.read_text()
    assert '创建笔记' in text and '已停用' in text
    assert "id='op-recommend'" not in text
    assert not catalog.rows('recommendation_requests')
