import json
import queue
import socket
import struct
import threading
from copy import deepcopy

import pytest

from proximic_ring.agent_workflow.agent.codex_ipc import encode, read_frame, DesktopIPC
from proximic_ring.agent_workflow.agent.codex_state import DesktopState
from proximic_ring.agent_workflow.agent.codex_bridge import CodexBridge
from proximic_ring.agent_workflow.agent.flow import Flow


def thread_result():
    return dict(thread=dict(id='owned', createdAt=1, updatedAt=1, cwd='/test',
                            source='appServer', originator='mythlink', turns=[]),
                model='test-model', reasoningEffort='medium', cwd='/test',
                approvalPolicy='on-request', approvalsReviewer='user',
                activePermissionProfile={'id':':workspace'}, sandbox={'type':'workspaceWrite'})


class Transport:
    def __init__(self):
        self.inbox=queue.Queue()
        self.closed=threading.Event()
        self.sent=[]
        self.replies=[]
        self.broadcasts=[]
    def connect(self): pass
    def close(self): self.closed.set()
    def send(self, message): self.sent.append(message)
    def respond(self, request, result=None, error=None):
        self.replies.append((request['requestId'],result,error))
    def broadcast(self, method, version, params, targets=None):
        self.broadcasts.append((method,version,deepcopy(params),targets))


@pytest.fixture
def bridge(tmp_path):
    f=Flow(with_ring=False,runtime_dir=tmp_path)
    f.tid='owned'
    transport=Transport()
    b=CodexBridge(f,thread_result(),transport)
    class RPC:
        def __init__(self): self.sent=[]
        def send(self,method,params):
            self.sent.append((method,params))
            b.outgoing(dict(method=method,params=params))
            return len(self.sent)
        def write(self,message): b.outgoing(message)
    f.rpc=RPC()
    f.codex_bridge=b
    b.connect()
    yield b,f,transport
    f.close_recording()
    f.events.close()


def request(method='start-turn', rid='r1', **params):
    from proximic_ring.agent_workflow.agent.codex_ipc import VERSIONS
    method='thread-follower-'+method
    return dict(type='request',method=method,version=VERSIONS[method],
                requestId=rid,sourceClientId='desktop',
                params=dict(conversationId='owned',**params))


def start_request(rid='r1', **params):
    return request(rid=rid,turnStart=dict(request=dict(threadId='owned',
                   input=[{'type':'text','text':'native task'}],**params),context={}))


def test_transport_fragmented_utf8_and_bounded_frames():
    a,b=socket.socketpair()
    payload=encode({'text':'同一会话'})
    def write():
        for byte in payload: a.sendall(bytes([byte]))
    worker=threading.Thread(target=write);worker.start()
    assert read_frame(b)=={'text':'同一会话'}
    worker.join()
    a.sendall(struct.pack('<I',2**30))
    with pytest.raises(ValueError):read_frame(b)
    a.close();b.close()


def test_transport_secure_socket_and_real_request_roundtrip(tmp_path):
    import tempfile
    from pathlib import Path
    directory=tempfile.TemporaryDirectory(prefix='ml-ipc-',dir='/tmp')
    path=Path(directory.name)/'ipc.sock'
    server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
    try: server.bind(str(path))
    except PermissionError:
        server.close();directory.cleanup()
        pytest.skip('当前测试沙箱不允许绑定本地 socket；在本机集成环境单独验证')
    path.chmod(0o600);server.listen()
    done=threading.Event()
    def serve():
        peer,_=server.accept()
        try:
            init=read_frame(peer)
            assert init['params']['clientType']=='mythlink'
            peer.sendall(encode(dict(type='response',requestId=init['requestId'],
                                    resultType='success',result={'clientId':'assigned'})))
            message=read_frame(peer)
            peer.sendall(encode(dict(type='broadcast',method='echo',params=message)))
            done.wait(2)
        finally:peer.close()
    worker=threading.Thread(target=serve);worker.start()
    client=DesktopIPC(path);client.connect()
    client.broadcast('test',1,{'hello':'世界'})
    result=client.inbox.get(timeout=2)
    assert result['params']['sourceClientId']=='assigned'
    assert result['params']['params']['hello']=='世界'
    done.set();client.close();worker.join();server.close();directory.cleanup()


def test_only_owned_compatible_requests_can_be_discovered(bridge):
    b,_,transport=bridge
    own=start_request()
    assert b.accepts(own)
    for change in ({'version':999},{'hostId':'remote'},
                   {'params':{'conversationId':'unrelated'}}):
        assert not b.accepts({**own,**change})
    b._request({**own,'params':{'conversationId':'unrelated'}})
    assert transport.replies[-1][2]


def test_native_submission_ack_waits_for_server_and_retries_do_not_execute_twice(bridge):
    b,f,t=bridge
    command=start_request()
    b._request(command)
    assert not t.replies and f.start_id==1
    b._request(command)
    assert len(f.rpc.sent)==1
    b.incoming({'id':1,'result':{'turn':{'id':'turn1'}}})
    assert t.replies[-1][1]=={'result':{'turn':{'id':'turn1'}}}
    b._request(command)
    assert len(f.rpc.sent)==1 and len(t.replies)==2


def test_native_rejection_and_stale_steer_are_visible_without_second_turn(bridge):
    b,f,t=bridge
    b._request(start_request())
    b.incoming({'id':1,'error':{'message':'server rejected'}})
    assert t.replies[-1][2]=='server rejected'
    b._request(request('steer-turn',rid='steer',input=[dict(type='text',text='update')]))
    assert t.replies[-1][2] and len(f.rpc.sent)==1


def test_native_cannot_expand_permissions_or_send_untrusted_response_items(bridge):
    b,f,t=bridge
    b._request(start_request(approvalPolicy='never'))
    assert t.replies[-1][2] and not f.rpc.sent
    r=start_request('context')
    r['params']['turnStart']['context']['responseItems']=[dict(type='message')]
    b._request(r)
    assert t.replies[-1][2] and not f.rpc.sent


def test_follower_only_gets_owned_state_and_lost_connection_retires_executor(bridge):
    b,f,t=bridge
    b._broadcast(dict(type='broadcast',method='thread-stream-following-changed',version=1,
        sourceClientId='desktop',params=dict(hostId='local',conversationId='owned',following=True)))
    method,version,params,targets=t.broadcasts[-1]
    assert method=='thread-stream-state-changed' and version==11 and targets=={'desktop'}
    assert params['change']['conversationState']['id']=='owned'
    t.closed.set();b.poll()
    assert f.stop.is_set() and f.state['codexSyncState']=='error'


def test_projection_streams_text_tools_questions_and_keeps_history_on_completion():
    model=DesktopState(thread_result())
    model.prepare_turn({'input':[dict(type='text',text='hello')]})
    def send(method,**params):
        model.observe(dict(method=method,params=dict(threadId='owned',turnId='t',**params)))
    send('turn/started',turn=dict(id='t',status='inProgress',items=[]))
    send('item/started',item=dict(id='a',type='agentMessage',text='',phase='commentary'))
    send('item/agentMessage/delta',itemId='a',delta='working')
    send('item/started',item=dict(id='c',type='commandExecution',aggregatedOutput=''))
    send('item/commandExecution/outputDelta',itemId='c',delta='result')
    send('item/started',item=dict(id='r',type='reasoning',summary=[]))
    send('item/reasoning/summaryTextDelta',itemId='r',summaryIndex=0,delta='summary')
    model.observe(dict(id=99,method='item/tool/call',params=dict(threadId='owned',turnId='t',
        callId='q',tool='ask_ring_user',arguments=dict(question='which?',options=['A','B']))))
    assert model.state['requests'][0]['method']=='item/tool/requestUserInput'
    assert model.state['requests'][0]['params']['questions'][0]['question']=='which?'
    send('turn/completed',turn=dict(id='t',status='completed',items=[]))
    state=model.snapshot()['conversationState']
    items=state['turnHistory']['history']['entitiesByKey']['turn:t']['items']
    assert items[0]['text']=='working' and items[1]['aggregatedOutput']=='result'
    assert items[2]['summary']==['summary']
    assert state['requests']==[] and state['threadRuntimeStatus']=={'type':'idle'}


def test_desktop_message_arrays_are_complete_before_first_streamed_item():
    # The desktop continuation detector reads params.input before App Server's
    # canonical userMessage arrives. Optional API fields must already exist.
    from proximic_ring.agent_workflow.agent.attachments import turn_input
    inputs = turn_input('这个文件', [
        dict(kind='image', path='/test/window.png', automatic=True, context={}),
        dict(kind='file', path='/test/report.pdf', name='report.pdf')])
    original = deepcopy(inputs)
    model = DesktopState(thread_result())
    model.prepare_turn({'input':inputs})
    model.observe(dict(method='turn/started', params=dict(threadId='owned',
        turn=dict(id='t', status='inProgress', items=[]))))
    turn = model.snapshot()['conversationState']['turnHistory']['history']['entitiesByKey']['turn:t']
    assert turn['items'] == []
    texts = [i for i in turn['params']['input'] if i['type'] == 'text']
    assert len(texts) == 3
    assert all(i['text_elements'] == [] for i in texts)
    assert turn['params']['input'][1] == dict(type='localImage', path='/test/window.png')
    assert inputs == original


@pytest.mark.parametrize('kind', ['userMessage', 'steeringUserMessage'])
@pytest.mark.parametrize('elements', [None, []])
def test_streamed_user_content_is_safe_for_desktop(kind, elements):
    model = DesktopState(thread_result())
    content = [dict(type='text', text='继续', text_elements=elements)]
    event = dict(method='item/started', params=dict(threadId='owned', turnId='t',
        item=dict(id='u', type=kind, content=content)))
    original = deepcopy(event)
    model.observe(event)
    assert model.turns['t']['items'][0]['content'][0]['text_elements'] == []
    assert event == original


def test_projection_preserves_native_text_placeholders_and_rejects_invalid_arrays():
    from proximic_ring.agent_workflow.agent.codex_state import desktop_input
    elements = [dict(byteRange=dict(start=0, end=6), placeholder='file-reference')]
    inputs = [dict(type='text', text='文件', text_elements=elements)]
    assert desktop_input(inputs) == inputs
    assert desktop_input(inputs)[0]['text_elements'] is not elements
    with pytest.raises(ValueError, match='text_elements'):
        desktop_input([dict(type='text', text='bad', text_elements={})])


@pytest.mark.parametrize('build, supported', [('20156', True), ('20770', True), ('99999', False)])
def test_desktop_build_gate_remains_explicit(tmp_path, build, supported):
    import plistlib
    from proximic_ring.agent_workflow.agent.codex_ipc import desktop_supported
    contents = tmp_path/'ChatGPT.app/Contents'
    contents.mkdir(parents=True)
    (contents/'Info.plist').write_bytes(plistlib.dumps({'CFBundleVersion':build}))
    assert desktop_supported(contents/'Resources/codex') is supported


def test_native_answer_resolves_original_ring_request_once(bridge):
    b,f,t=bridge
    event=dict(id=42,method='item/tool/call',params=dict(threadId='owned',turnId='turn',
         callId='q',tool='ask_ring_user',arguments=dict(question='选哪一个?',options=['A','B'])))
    f.handle(event)
    r=request('submit-user-input',requestId='42',
              response={'answers':{'ring_answer':{'answers':['B']}}})
    b._request(r)
    assert not f.pending and t.replies[-1][2] is None
    b._request(request('submit-user-input',rid='other',requestId=42,response={}))
    assert t.replies[-1][2]


def test_native_approval_uses_offered_choices_and_rejects_stale_grants(bridge):
    b,f,t=bridge
    f.handle(dict(id=43,method='item/commandExecution/requestApproval',params=dict(
        threadId='owned',turnId='turn',command='pwd',availableDecisions=['accept','decline'])))
    b._request(request('command-approval-decision',requestId=43,decision='acceptForSession'))
    assert 43 in f.pending and t.replies[-1][2]
    b._request(request('command-approval-decision',rid='allowed',requestId=43,decision='accept'))
    assert not f.pending and t.replies[-1][2] is None


def test_legacy_interrupt_binds_to_current_turn_and_rejects_other_turn(bridge):
    b,f,t=bridge
    f.turn='live'
    r=request('interrupt-turn',mode='user-stop');r['version']=3
    assert b.accepts(r)
    b._request(r)
    assert f.rpc.sent[-1]==('turn/interrupt',{'threadId':'owned','turnId':'live'})
    assert not t.replies
    b.turn_completed('live')
    assert t.replies[-1][1]=={'ok':True,'interruptedTurnId':'live'}


def test_server_settings_updates_projection_and_next_turn_defaults():
    model=DesktopState(thread_result())
    model.observe(dict(method='thread/settings/updated',params=dict(threadId='owned',
        threadSettings=dict(model='new-model',effort='high',approvalPolicy='on-request'))))
    assert model.state['latestModel']=='new-model'
    assert model.turn('next')['params']['model']=='new-model'
