"""Background creation/resume plus safe following of an existing native owner."""
from copy import deepcopy
import queue
import time
import uuid

from .background_service import service_for
from .codex_ipc import DesktopIPC
from .native_codex import NativeCodex, canonical_turns
from .native_approvals import METHODS
from .connection import verify_login

_REPLIES = {**METHODS, 'item/tool/requestUserInput':'thread-follower-submit-user-input'}
_NATIVE_TURNS = {}
_EXITING = False


class BackgroundCodex(NativeCodex):
    def __init__(self, *, service_factory=service_for, **kwargs):
        super().__init__(**kwargs)
        self.service_factory=service_factory
        self.service=None
        self.emitted_requests={}
        self.decision_pending={}
        self.my_start_id=None
        self.owned=False

    def initialize(self):
        rpc=self.metadata()
        try:verify_login(rpc)
        finally:rpc.close()

    def _connect_ipc(self):
        if self.ipc.sock is not None and not self.ipc.closed.is_set():return
        deadline=time.monotonic()+5
        while True:
            try:self.ipc.connect();return
            except (FileNotFoundError,ConnectionRefusedError):
                if time.monotonic()>=deadline:raise
                time.sleep(.05)

    def discover(self, thread_id):
        # Discovery never loads a thread. Only an explicit no-client-found permits
        # a resume attempt, whose cross-process writer lock remains authoritative.
        ipc=DesktopIPC()
        try:
            try:ipc.connect()
            except FileNotFoundError:return None
            rid=str(uuid.uuid4())
            ipc.send({'type':'request','requestId':rid,'method':'thread-owner-discovery','version':1,
                'params':{'hostId':'local','conversationId':thread_id},'timeoutMs':2500})
            deadline=time.monotonic()+18
            while time.monotonic()<deadline:
                try:event=ipc.inbox.get(timeout=.1)
                except queue.Empty:continue
                if event.get('type')=='client-discovery-request':
                    ipc.send({'type':'client-discovery-response','requestId':event['requestId'],'response':{'canHandle':False}})
                if event.get('requestId')!=rid:continue
                if event.get('resultType')=='success':
                    if not event.get('handledByClientId'):raise RuntimeError('执行方回执缺少身份，未接管对话')
                    return event['handledByClientId']
                if event.get('error')=='no-client-found':return None
                raise RuntimeError('无法确认现有任务执行方；未接管对话')
            raise TimeoutError('任务执行方发现超时；未接管对话')
        finally:ipc.close()

    def attach(self, thread_id):
        from ..recording.codex_link import codex_thread_url
        if self.tid or not codex_thread_url(thread_id):raise ValueError('无法连接此对话，请重新选择')
        rpc=self.metadata()
        try:thread=rpc.call('thread/read',{'threadId':thread_id,'includeTurns':False},timeout=8)['thread']
        finally:rpc.close()
        if thread.get('id')!=thread_id or thread.get('ephemeral') is not False or thread.get('archived'):
            raise ValueError('此对话不可连接，请刷新列表')
        self._notify(threadAttachState='connecting',status='正在后台连接对话…')
        owner=self.discover(thread_id)
        if not owner:
            self.service=self.service_factory(self.codex_path)
            try:self.service.call('resume',{'threadId':thread_id})
            except RuntimeError as exc:
                if 'active writer' in str(exc):
                    raise RuntimeError('此对话仍由 Codex 执行，但尚未提供实时连接。请手动打开该对话一次，再重新连接；当前对话已保留。') from exc
                raise
            self.owned=True
        else:self.owner=owner
        self._connect_ipc()
        self.tid=thread_id
        self.attach_title=str(thread.get('name') or thread.get('preview') or '未命名对话').splitlines()[0][:160]
        self.attach_deadline=time.monotonic()+30
        self._notify(codexThreadId=thread_id,codexThreadTitle=self.attach_title,codexSyncState='connecting')
        self._follow()

    def _create_and_queue(self, params):
        raw=deepcopy(params['input'])
        for item in raw:
            if item.get('type')=='text':item.setdefault('text_elements',[])
        text=next((i.get('text','') for i in raw if i.get('type')=='text'),'')
        title='Mythlink · '+(text.strip().split('\n')[0][:48] or '附件任务')
        self.service=self.service_factory(self.codex_path)
        result=self.service.call('create',{'cwd':str(self.root),'title':title})
        self.tid=result['thread']['id'];self.owned=True
        self._notify(codexThreadId=self.tid,codexThreadTitle=title)
        self._connect_ipc();self._follow()
        self.service.call('turn/start',{'threadId':self.tid,'input':raw,'clientUserMessageId':str(uuid.uuid4())})
        self._notify(codexSyncState='connecting',codexSyncMessage='后台任务已提交，正在接收进度')

    def send(self, method, params):
        if _EXITING:raise RuntimeError('Mythlink 正在退出，未发送新任务')
        rid=super().send(method,params)
        if method=='turn/start' and not self.owned:self.my_start_id=rid
        return rid

    def _project(self):
        active=self.active_turn()
        current={}
        for request in self.state.get('requests',[]):
            p=request.get('params') or {}
            if request.get('method') not in _REPLIES or request.get('completed') or p.get('threadId')!=self.tid:continue
            if p.get('turnId') not in (None,'',active) and p.get('isBlocking') is not False:continue
            current[request['id']]=request
        # Project only actionable requests so a completed tool cannot leave the
        # card in a permanent waiting state.
        saved=self.state.get('requests',[])
        self.state['requests']=list(current.values())
        try:super()._project()
        finally:self.state['requests']=saved
        for rid in self.emitted_requests.keys()-current.keys():
            self._emit({'method':'serverRequest/resolved','params':{'threadId':self.tid,'requestId':rid}})
        for rid,request in current.items():
            if rid not in self.emitted_requests:self._emit(deepcopy(request))
        self.emitted_requests=deepcopy(current)
        self._notify(codexSyncMessage='后台实时同步；Codex 左侧列表可能稍后刷新')

    def write(self, message):
        rid=message.get('id');request=self.emitted_requests.get(rid)
        if not request or not self.owner or self.closed:raise ValueError('此请求已结束或执行方已断开，未发送回答')
        if 'error' in message:raise ValueError('此请求暂不支持在浮窗处理')
        result=deepcopy(message.get('result'))
        if not isinstance(result,dict):raise ValueError('回答格式不正确')
        key=self.approvals.key(request)
        if key in self.approvals.attempted:raise ValueError('此请求已有决定正在提交，未重复发送')
        self.approvals.attempted.add(key)
        remote_id=str(uuid.uuid4());params={'conversationId':self.tid,'requestId':rid}
        params.update(result if 'decision' in result else {'response':result})
        self.decision_pending[remote_id]=(rid,self.owner,_REPLIES[request['method']],time.monotonic()+30)
        self.ipc.send({'type':'request','requestId':remote_id,'targetClientId':self.owner,'version':1,
            'method':_REPLIES[request['method']],'params':params,'timeoutMs':30000})

    def poll(self):
        if _EXITING:return
        # Consume request-bound reply receipts before NativeCodex's normal stream
        # loop. Never retry an uncertain decision or grant after an owner change.
        events=[]
        for _ in range(128):
            try:event=self.ipc.inbox.get_nowait()
            except queue.Empty:break
            pending=self.decision_pending.get(event.get('requestId'))
            if event.get('type')=='response' and pending:
                rid,owner,method,_=pending
                if event.get('handledByClientId')!=owner or event.get('method')!=method:continue
                del self.decision_pending[event['requestId']]
                if event.get('resultType')!='success':
                    self.emitted_requests.pop(rid,None)
                    self._notify(detail='回答未被接受：'+str(event.get('error','未知错误'))+'；未自动重试')
            else:events.append(event)
        with self.ipc.inbox.mutex:
            for event in reversed(events):self.ipc.inbox.queue.appendleft(event)
        super().poll()
        if self.my_start_id is not None:
            with self.inbox.mutex:responses=list(self.inbox.queue)
            for event in responses:
                if event.get('id')==self.my_start_id and 'method' not in event:
                    turn=(event.get('result') or {}).get('turn') or {}
                    if turn.get('id'):_NATIVE_TURNS[(self.owner,self.tid)]=turn['id']
                    self.my_start_id=None
                    break
        for key,(rid,_,_,deadline) in list(self.decision_pending.items()):
            if time.monotonic()>=deadline:
                del self.decision_pending[key]
                self._notify(detail='回答回执超时，请核对任务状态；未自动重发')


def cancel_native_submissions():
    """Cancel only the exact native turns started through this Mythlink process."""
    global _EXITING
    _EXITING=True
    if not _NATIVE_TURNS:return
    ipc=DesktopIPC()
    try:
        ipc.connect()
        expected={}
        for (owner,tid),turn in list(_NATIVE_TURNS.items()):
            rid=str(uuid.uuid4());expected[rid]=owner
            ipc.send({'type':'request','requestId':rid,'targetClientId':owner,
                'method':'thread-follower-interrupt-turn','version':4,'timeoutMs':3000,
                'params':{'conversationId':tid,'expectedTurnId':turn,'mode':'user-stop'}})
        deadline=time.monotonic()+3
        while expected and time.monotonic()<deadline:
            try:event=ipc.inbox.get(timeout=.1)
            except queue.Empty:continue
            rid=event.get('requestId')
            if event.get('type')=='response' and event.get('handledByClientId')==expected.get(rid):
                expected.pop(rid,None)
    except (OSError,RuntimeError):pass
    finally:ipc.close();_NATIVE_TURNS.clear()
