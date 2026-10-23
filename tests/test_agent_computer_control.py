"""Computer Use must release the finished turn before handing control to the next."""
import queue

import pytest

from proximic_ring.agent_workflow.agent import flow as module
from proximic_ring.agent_workflow.agent.computer_control import release_turn
from proximic_ring.agent_workflow.agent.connection import ConnectionIssue, verify_computer_use


class Wire:
    def __init__(self):
        self.calls = []
        self.sent = []
        self.inbox = queue.Queue()
        self.results = []
        self.on_release = lambda: None
        self.closed = False

    def call(self, method, params, **options):
        self.calls.append((method, params, options))
        self.on_release()
        result = self.results.pop(0) if self.results else {'content': [], 'isError': False}
        if isinstance(result, Exception): raise result
        return result

    def send(self, method, params):
        self.sent.append((method, params))
        return len(self.sent)

    def close(self): self.closed = True


@pytest.fixture
def flow(tmp_path):
    app = module.Flow(with_ring=False, runtime_dir=tmp_path)
    app.rpc = Wire(); app.tid = 'this-thread'; app.turn = 'this-turn'
    yield app
    if not app.events.closed: app.events.close()


def ended(app, status='completed', turn='this-turn', thread='this-thread'):
    app.handle({'method': 'turn/completed', 'params': {
        'threadId': thread, 'turn': {'id': turn, 'status': status}}})


@pytest.mark.parametrize('status,cancelled,outcome', [
    ('completed', False, 'completed'), ('interrupted', False, 'interrupted'),
    ('failed', False, 'failed'), ('completed', True, 'interrupted'),
])
def test_companion_completion_follows_result_and_resets_for_next_task(flow, status, cancelled, outcome):
    flow.cancelled = cancelled
    ended(flow, status)
    assert flow.state['taskOutcome'] == outcome
    flow.start_task('下一条任务', 'test')
    assert flow.state['taskOutcome'] == ''


@pytest.mark.parametrize('status', ['completed', 'interrupted', 'failed'])
def test_terminal_turn_releases_exact_owner_before_queue_and_idle(flow, status):
    flow.text('接着处理下一件事', source='test')
    def while_releasing():
        assert flow.state['computerControlState'] == 'releasing'
        assert flow.state['status'] == '正在释放电脑控制'
        assert flow.rpc.sent == []
        assert len(flow.task_queue) == 1
    flow.rpc.on_release = while_releasing
    ended(flow, status)
    assert flow.rpc.calls == [('mcpServer/tool/call', {
        'threadId': 'this-thread', 'server': 'cua_repl', 'tool': 'turn_ended',
        'arguments': {'session_id': 'this-thread', 'turn_id': 'this-turn', 'hook_event_name': 'Stop'},
    }, {'timeout': 3}), ('mcpServer/tool/call', {
        'threadId': 'this-thread', 'server': 'cua_repl', 'tool': 'js_reset', 'arguments': {},
    }, {'timeout': 3})]
    if status == 'completed':
        assert flow.rpc.sent[0][0] == 'turn/start' and not flow.task_queue
    else:
        assert not flow.rpc.sent and not flow.task_queue
        assert flow.state['computerControlState'] == 'released'
        flow.text('重新提交新任务', source='test')
        assert flow.rpc.sent[-1][0] == 'turn/start'


def test_late_or_foreign_completion_cannot_release_or_clear_another_task(flow):
    ended(flow, thread='another-thread')
    ended(flow, turn='another-turn')
    assert flow.turn == 'this-turn' and not flow.rpc.calls
    ended(flow)
    flow.turn = 'next-turn'
    ended(flow)
    assert flow.turn == 'next-turn' and len(flow.rpc.calls) == 2
    flow.release_computer_control('this-turn')
    assert len(flow.rpc.calls) == 2


def test_timeout_retries_same_scope_without_advancing_queue(flow):
    flow.rpc.results = [TimeoutError('lost acknowledgement'), {'content': []}]
    ended(flow)
    assert len(flow.rpc.calls) == 3 and flow.rpc.calls[0] == flow.rpc.calls[1]
    assert flow.state['computerControlState'] == 'released'


@pytest.mark.parametrize('failure', [TimeoutError('offline'), {'content': [], 'isError': True}, {}])
def test_failed_cleanup_does_not_report_released_or_run_next_task(flow, failure):
    flow.rpc.results = [failure, failure]
    flow.text('留在队列中', source='test')
    with pytest.raises(ConnectionIssue, match='释放失败'): ended(flow)
    assert flow.state['computerControlState'] == 'error'
    assert not flow.rpc.sent and len(flow.task_queue) == 1
    assert 'this-turn' not in flow._released_turns
    assert flow.rpc.calls[-1][1]['tool'] == 'js_reset'


def test_native_reset_failure_keeps_task_unreleased(flow):
    flow.rpc.results = [{'content': []}, TimeoutError('native reset'), TimeoutError('native reset')]
    with pytest.raises(ConnectionIssue, match='释放失败'): ended(flow)
    assert flow.state['computerControlState'] == 'error'
    assert [p['tool'] for _, p, _ in flow.rpc.calls] == ['turn_ended', 'js_reset', 'js_reset']


def test_exit_interrupts_then_releases_only_its_turn_and_closes_transport(flow, monkeypatch):
    monkeypatch.setattr(flow, 'setup', lambda: None)
    flow.stop.set()
    flow.run()
    assert flow.rpc.sent == [('turn/interrupt', {'threadId': 'this-thread', 'turnId': 'this-turn'})]
    assert len(flow.rpc.calls) == 2 and flow.rpc.closed
    assert flow.state['computerControlState'] == 'released'


def test_missing_cleanup_capability_is_reported_before_any_task():
    rpc = Wire()
    rpc.results = [{'data': [{'name': 'cua_repl', 'tools': {'js': {}}}]}]
    with pytest.raises(ConnectionIssue, match='释放接口'): verify_computer_use(rpc, 'this-thread')
    assert not rpc.sent


def test_non_ui_turn_without_computer_use_does_not_block_next_task(flow):
    flow._computer_use_ready=False
    flow.text('接着回答另一个问题',source='test')
    ended(flow)
    assert not flow.rpc.calls
    assert flow.rpc.sent[0][0]=='turn/start' and not flow.task_queue


@pytest.mark.parametrize('method',['item/started','item/completed'])
def test_observed_computer_use_requires_cleanup_even_after_failed_capability_probe(flow,method):
    flow._computer_use_ready=False
    flow.handle({'method':method,'params':{'threadId':flow.tid,'turnId':flow.turn,
        'item':{'id':'call','type':'mcpToolCall','server':'cua_repl','tool':'js'}}})
    ended(flow)
    assert [params['tool'] for _,params,_ in flow.rpc.calls]==['turn_ended','js_reset']
    assert flow.state['computerControlState']=='released'
