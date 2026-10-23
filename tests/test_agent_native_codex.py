"""Native ownership, real stream shape and safe control delivery contracts."""
from copy import deepcopy
import queue
import threading

import pytest

from proximic_ring.agent_workflow.agent.native_codex import NativeCodex, apply_change
from proximic_ring.agent_workflow.agent.native_flow import NativeFlow
from proximic_ring.agent_workflow.agent.connection import ConnectionIssue


class Transport:
    def __init__(self):
        self.inbox = queue.Queue(); self.closed = threading.Event(); self.sent = []
    def connect(self): pass
    def close(self): self.closed.set()
    def send(self, message): self.sent.append(message)
    def broadcast(self, method, version, params):
        self.send(dict(type='broadcast', method=method, version=version, params=params))


def state(turn='turn-1', status='inProgress', text='', done=False):
    item = {'id':'message-1', 'type':'agentMessage', 'text':text}
    entity = dict(turnId=turn, status=status, items=[item], completedItemIds={'message-1':done})
    return {'id':'thread-1', 'cwd':'/tmp/test', 'requests':[],
            'threadRuntimeStatus':{'type':'active' if status=='inProgress' else 'idle'},
            'turnHistory':{'kind':'canonical', 'history':{'entitiesByKey':{'turn:'+turn:entity},
                'islands':[{'entries':[{'key':'turn:'+turn, 'value':'turn:'+turn}]}]}}}


def snapshot(rpc, value, revision=1, owner='native-owner'):
    rpc.ipc.inbox.put(dict(type='broadcast', method='thread-stream-state-changed', version=11,
        sourceClientId=owner, params={'hostId':'local', 'conversationId':'thread-1',
        'change':{'type':'snapshot', 'revision':revision, 'conversationState':value}}))
    rpc.poll()


def drain(rpc):
    events=[]
    while not rpc.inbox.empty(): events.append(rpc.inbox.get_nowait())
    return events


@pytest.fixture
def rpc(tmp_path):
    value=NativeCodex(runtime_dir=tmp_path, codex_path='/test/codex', transport=Transport())
    value.tid='thread-1'
    return value


def test_creator_exits_before_queue_and_never_executes_or_claims_ownership(tmp_path):
    calls=[]; instances=[]; opened=[]
    class Metadata:
        def __init__(self, **_): self.closed=False; instances.append(self)
        def initialize(self): pass
        def close(self): self.closed=True
        def call(self, method, params):
            calls.append((method, deepcopy(params)))
            if method=='thread/start': return {'thread':{'id':'thread-1'}}
            if method=='thread/queue/add': assert instances[-2].closed
            return {}
    rpc=NativeCodex(runtime_dir=tmp_path, codex_path='/test/codex', transport=Transport(),
                    rpc_factory=Metadata, opener=lambda *args,**kwargs:opened.append((args,kwargs)))
    inputs=[{'type':'text','text':'解释图片'}, {'type':'localImage','path':'/tmp/test.png'}]
    rpc.send('turn/start', {'threadId':None, 'input':inputs})
    assert [m for m,_ in calls]==['thread/start','thread/name/set','thread/queue/add']
    assert all(instance.closed for instance in instances)
    assert calls[-1][1]['input'][-1]==inputs[-1]
    assert calls[-1][1]['input'][-2]['text']=='解释图片'
    assert inputs[0]=={'type':'text','text':'解释图片'}
    assert opened==[(('thread-1',),{'wake':True}), (('thread-1',),{})]
    assert not any(m.get('method')=='thread-owner-discovery' for m in rpc.ipc.sent)


def test_snapshot_and_patches_report_live_turn_once_and_ignore_persisted_status(rpc):
    rpc.first_start_id=1
    snapshot(rpc,state(text='处理中'))
    events=drain(rpc)
    assert events[0]=={'id':1,'result':{'turn':{'id':'turn-1'}}}
    assert [e.get('method') for e in events].count('turn/started')==1
    snapshot(rpc,state(text='处理中'), revision=2)
    assert drain(rpc)==[]
    snapshot(rpc,state(status='completed',text='完成',done=True),revision=3)
    events=drain(rpc)
    assert [e.get('method') for e in events].count('turn/completed')==1
    assert rpc.active_turn() is None
    snapshot(rpc,state(status='completed',text='完成',done=True),revision=4)
    assert drain(rpc)==[]


def test_gap_requests_snapshot_without_mutating_or_claiming_owner(rpc):
    snapshot(rpc,state()); drain(rpc)
    original=deepcopy(rpc.state)
    rpc.ipc.inbox.put(dict(type='broadcast',method='thread-stream-state-changed',version=11,
        sourceClientId='native-owner',params={'hostId':'local','conversationId':'thread-1',
        'change':{'type':'patches','baseRevision':5,'revision':6,'patches':[]}}))
    rpc.poll()
    assert rpc.state==original and rpc.revision==1
    assert rpc.ipc.sent[-1]['method']=='thread-stream-following-changed'
    with pytest.raises(KeyError):
        apply_change(original,1,{'type':'patches','baseRevision':1,'revision':2,
            'patches':[{'op':'remove','path':['missing']}]})


def test_stale_controls_are_rejected_and_live_controls_target_native_owner(rpc):
    snapshot(rpc,state()); drain(rpc)
    for method,extra in [('turn/steer',{'expectedTurnId':'old','input':[]}),
                         ('turn/interrupt',{'turnId':'old'})]:
        rpc.send(method,{'threadId':'thread-1',**extra})
        assert '原任务已结束' in drain(rpc)[0]['error']['message']
    rpc.send('turn/steer', {'threadId':'thread-1','expectedTurnId':'turn-1',
                           'input':[{'type':'text','text':'改为中文'}]})
    msg=rpc.ipc.sent[-1]
    assert msg['targetClientId']=='native-owner'
    assert msg['method']=='thread-follower-steer-turn'
    assert msg['params']['restoreMessage']['context']['commentAttachments']==[]
    assert msg['params']['input'][0]['text_elements']==[]
    assert msg['params']['restoreMessage']['cwd']=='/tmp/test'
    rpc.send('turn/interrupt',{'threadId':'thread-1','turnId':'turn-1'})
    assert rpc.ipc.sent[-1]['params']['expectedTurnId']=='turn-1'
    assert rpc.ipc.sent[-1]['version']==4


def test_changed_owner_fails_closed_without_replaying_controls(rpc):
    snapshot(rpc,state()); drain(rpc)
    with pytest.raises(ConnectionIssue,match='所有者已变化'):
        snapshot(rpc,state(),2,owner='other-owner')
    assert not rpc.pending


def test_followup_start_has_renderer_required_text_elements(rpc):
    snapshot(rpc,state(status='completed',done=True)); drain(rpc)
    inputs=[{'type':'text','text':'后续任务'}, {'type':'text','text':'附加上下文'}]
    rpc.send('turn/start',{'threadId':'thread-1','input':inputs})
    submitted=rpc.ipc.sent[-1]['params']['turnStart']['request']['input']
    assert all(item['text_elements']==[] for item in submitted)
    # Reproduce the exact native renderer predicate that previously crashed.
    assert not any(any(e.get('placeholder')=='aeon-continuation' for e in i['text_elements'])
                   for i in submitted if i['type']=='text')
    assert all('text_elements' not in item for item in inputs)


def test_close_unsubscribes_without_stopping_native_task(rpc):
    snapshot(rpc,state()); drain(rpc)
    rpc.close()
    assert rpc.ipc.sent[-1]['params']['following'] is False
    assert not any(m.get('method')=='thread-follower-interrupt-turn' for m in rpc.ipc.sent)


def test_native_flow_disconnect_keeps_task_running(tmp_path):
    class Adapter:
        inbox=queue.Queue()
        def close(self): self.closed=True
        def send(self,*_): pytest.fail('Disconnect must not interrupt the native task')
    flow=NativeFlow(with_ring=False,runtime_dir=tmp_path)
    adapter=Adapter()
    def setup(): flow.rpc=adapter; flow.turn='running'; flow.stop.set()
    flow.setup=setup
    flow.run()
    assert adapter.closed
    assert 'Codex 中继续' in flow.state['detail']


def test_native_completion_beating_cancel_is_not_reported_cancelled(tmp_path):
    flow=NativeFlow(with_ring=False,runtime_dir=tmp_path)
    flow.tid='thread-1';flow.turn='turn-1';flow.cancelled=True
    try:
        flow.handle({'method':'turn/completed','params':{'threadId':'thread-1',
                     'turn':{'id':'turn-1','status':'completed'}}})
        assert flow.state['taskOutcome']=='completed'
    finally:flow.events.close()


def test_attach_existing_thread_reads_metadata_and_adopts_history_without_replay(tmp_path):
    tid = '00000000-0000-4000-8000-000000000001'
    calls, opened, updates = [], [], []
    class Metadata:
        def __init__(self, **_): pass
        def initialize(self): pass
        def close(self): calls.append(('closed', {}))
        def call(self, method, params, **_):
            calls.append((method, params))
            return {'thread': {'id': tid, 'name': '以前的任务', 'ephemeral': False}}
    rpc = NativeCodex(runtime_dir=tmp_path, codex_path='/test/codex', transport=Transport(),
        rpc_factory=Metadata, opener=lambda *args, **kw: opened.append((args, kw)),
        on_state=lambda **fields: updates.append(fields))
    rpc.attach(tid)
    assert [method for method, _ in calls] == ['thread/read', 'closed']
    assert calls[0][1] == {'threadId': tid, 'includeTurns': False}
    assert opened == [((tid,), {})]
    saved = state(status='completed', text='上次完成的结果', done=True)
    saved['id'] = tid
    rpc.ipc.inbox.put(dict(type='broadcast', method='thread-stream-state-changed', version=11,
        sourceClientId='native-owner', params={'hostId': 'local', 'conversationId': tid,
        'change': {'type': 'snapshot', 'revision': 1, 'conversationState': saved}}))
    rpc.poll()
    assert drain(rpc) == []  # Historical completions cannot run finish hooks again.
    adopted = next(update for update in updates if update.get('threadAttachState') == 'ready')
    assert adopted['agent'] == '上次完成的结果' and adopted['turnId'] == ''
    assert adopted['codexThreadTitle'] == '以前的任务'
    rpc.send('turn/start', {'threadId': tid, 'input': [{'type': 'text', 'text': '继续处理'}]})
    sent = rpc.ipc.sent[-1]
    assert sent['method'] == 'thread-follower-start-turn'
    assert sent['params']['conversationId'] == sent['params']['turnStart']['request']['threadId'] == tid
    assert sent['params']['turnStart']['request']['input'][0]['text_elements'] == []
    rpc.close()


def test_attach_running_thread_keeps_active_turn_for_steer_and_cancel(rpc):
    rpc.attach_deadline = 1e20
    rpc.attach_title = '运行中的任务'
    snapshot(rpc, state(text='正在执行'))
    events = drain(rpc)
    assert any(e.get('method') == 'turn/started' for e in events)
    assert rpc.active_turn() == 'turn-1'
    rpc.send('turn/steer', {'threadId': 'thread-1', 'expectedTurnId': 'turn-1',
                          'input': [{'type': 'text', 'text': '改为中文'}]})
    assert rpc.ipc.sent[-1]['params']['conversationId'] == 'thread-1'
    rpc.send('turn/interrupt', {'threadId': 'thread-1', 'turnId': 'turn-1'})
    assert rpc.ipc.sent[-1]['params']['expectedTurnId'] == 'turn-1'

