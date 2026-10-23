"""Mythlink is a follower of a Codex-owned task; it never owns its execution.

Only metadata/queue operations use a short-lived App Server. Execution, steer,
interrupt, permissions and tool requests stay with the desktop's native owner.
The private follower protocol is build-gated and fails closed on owner loss.
"""
from __future__ import annotations

from copy import deepcopy
import queue
import subprocess
import time
import uuid

from .codex_ipc import DesktopIPC
from .connection import ConnectionIssue, verify_login
from .rpc import RPC
from .native_approvals import NativeApprovals

NATIVE_BUILDS = {'20770', '21161'}


def apply_change(state, revision, change):
    """Apply an atomic Immer patch batch. A gap requires a new snapshot."""
    if change.get('type') == 'snapshot':
        if revision is not None and change['revision'] < revision:
            return state, revision
        return deepcopy(change['conversationState']), change['revision']
    if change.get('type') != 'patches' or revision != change.get('baseRevision'):
        raise ValueError('Codex 状态版本不连续')
    result = deepcopy(state)
    for patch in change['patches']:
        path = patch['path']
        if not path:
            if patch['op'] not in ('add', 'replace'): raise ValueError('Invalid root patch')
            result = deepcopy(patch['value'])
            continue
        parent = result
        for key in path[:-1]: parent = parent[key]
        key, op = path[-1], patch['op']
        if op == 'remove':
            if isinstance(parent, list): parent.pop(key)
            else: del parent[key]
        elif op in ('add', 'replace'):
            value = deepcopy(patch['value'])
            if isinstance(parent, list) and op == 'add': parent.insert(key, value)
            else: parent[key] = value
        else: raise ValueError('Unsupported Codex patch')
    return result, change['revision']


def canonical_turns(state):
    history = state.get('turnHistory', {}).get('history', {})
    entities = history.get('entitiesByKey', {})
    keys = [entry['value'] for island in history.get('islands', [])
            for entry in island.get('entries', [])]
    return [entities[key] for key in dict.fromkeys(keys) if key in entities
            and not entities[key].get('remodexSyntheticTurnId')
            and not str(entities[key].get('turnId', '')).startswith('remodex-pending-turn:')]


class NativeCodex:
    def __init__(self, *, runtime_dir, codex_path, observer=None, on_state=None,
                 transport=None, rpc_factory=RPC, opener=None):
        self.root, self.codex_path = runtime_dir, codex_path
        self.observer, self.on_state = observer, on_state
        self.ipc = transport or DesktopIPC()
        self.rpc_factory = rpc_factory
        self.opener = opener or self._open
        self.inbox = queue.Queue()
        self.tid = self.owner = None
        self.state, self.revision = {}, None
        self.pending = {}
        self.next_id = 0
        self.first_start_id = None
        self.first_start_deadline = None
        self.started = set()
        self.completed = set()
        self.items = {}
        self.last_follow = 0
        self.closed = False
        self.attach_deadline = None
        self.attach_title = ''
        self.approvals = NativeApprovals(self)
        self._waiting_for_request = False

    @staticmethod
    def _open(tid, wake=False):
        subprocess.run(['open', 'codex://threads/' + ('wake/' if wake else '') + tid],
                       check=True, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    def metadata(self):
        rpc = self.rpc_factory(runtime_dir=self.root, codex_path=self.codex_path)
        try: rpc.initialize()
        except BaseException:
            rpc.close()
            raise
        return rpc

    def initialize(self):
        rpc = self.metadata()
        try: verify_login(rpc)
        finally: rpc.close()
        self.ipc.connect()

    def call(self, method, params=None, **kwargs):
        # Do not load/resume a task in this process, even for a status check.
        if method != 'account/read': raise ValueError('此操作由 Codex 原生负责')
        rpc = self.metadata()
        try: return rpc.call(method, params, **kwargs)
        finally: rpc.close()

    def _emit(self, event):
        if self.observer: self.observer('incoming', event)
        self.inbox.put(event)

    def _notify(self, **fields):
        if self.on_state: self.on_state(**fields)

    def _follow(self):
        if self.tid:
            self.ipc.broadcast('thread-stream-following-changed', 1,
                dict(hostId='local', conversationId=self.tid, following=True))
            self.last_follow = time.monotonic()

    def attach(self, thread_id):
        from ..recording.codex_link import codex_thread_url
        if self.tid or not codex_thread_url(thread_id):
            raise ValueError('无法连接此对话，请重新选择')
        rpc = self.metadata()
        try:
            thread = rpc.call('thread/read', {'threadId': thread_id, 'includeTurns': False}, timeout=8)['thread']
        finally:
            rpc.close()
        if thread.get('id') != thread_id or thread.get('ephemeral') is not False or thread.get('archived'):
            raise ValueError('此对话不可连接，请刷新列表')
        self.tid = thread_id
        self.attach_title = str(thread.get('name') or thread.get('preview') or '未命名对话').splitlines()[0][:160]
        self.attach_deadline = time.monotonic() + 30
        self._notify(codexThreadId=self.tid, codexThreadTitle=self.attach_title,
                     threadAttachState='connecting', codexSyncState='connecting',
                     status='正在连接已有对话…')
        self._follow()
        # Opening lets the desktop load its own conversation. Never resume it
        # in our metadata process, enqueue a prompt, or rename existing history.
        self.opener(self.tid)

    def _adopt_history(self):
        """Seed historical events so connecting does not replay old completions."""
        turns = canonical_turns(self.state)
        for turn in turns:
            tid = turn.get('turnId')
            if not tid or turn.get('status') == 'inProgress': continue
            self.started.add(tid)
            self.completed.add(tid)
            for item in turn.get('items', []):
                self.items[(tid, item.get('id'))] = (deepcopy(item),
                    bool(turn.get('completedItemIds', {}).get(item.get('id'))))
        last = turns[-1] if turns else {}
        active = self.active_turn()
        message = next((item.get('text', '') for turn in reversed(turns)
                        for item in reversed(turn.get('items', []))
                        if item.get('type') == 'agentMessage'), '')
        outcome = last.get('status', '') if not active else ''
        self.attach_deadline = None
        self._notify(threadAttachState='ready', codexSyncState='ready',
                     codexThreadId=self.tid, codexThreadTitle=self.attach_title,
                     turnId=active or '', taskOutcome=outcome, agent=message, detail='',
                     status='执行中' if active else '任务已完成' if outcome == 'completed' else '已连接对话')

    def _create_and_queue(self, params):
        raw = deepcopy(params['input'])
        for item in raw:
            if item.get('type') == 'text': item.setdefault('text_elements', [])
        text = next((v.get('text', '') for v in raw if v.get('type') == 'text'), '')
        title = 'Mythlink · ' + (text.strip().split('\n')[0][:48] or '附件任务')
        rpc = self.metadata()
        try:
            result = rpc.call('thread/start', {'cwd':str(self.root), 'ephemeral':False})
            self.tid = result['thread']['id']
            # Naming also persists an otherwise empty thread before owner handoff.
            rpc.call('thread/name/set', {'threadId':self.tid, 'name':title})
        finally: rpc.close()
        self._notify(codexThreadId=self.tid, codexThreadTitle=title)
        # Registration runs inside Codex, through its own normal tool. An external
        # name/set event cannot notify the native sidebar catalog. Keep the user's
        # input/attachments intact and make the startup request visible in history.
        raw.insert(0, {'type':'text', 'text':
            '此任务由 Mythlink 提交。开始执行前，请先调用 Codex 自带的 set_thread_title 工具，'
            f'将当前对话标题设置为“{title}”；工具成功后再处理下面的用户任务。'
            '授权和追问使用 Codex 原生交互。\n\n用户任务：', 'text_elements':[]})
        rpc = self.metadata()
        try:
            rpc.call('thread/queue/add', {'threadId':self.tid,
                'clientUserMessageId':str(uuid.uuid4()), 'input':raw})
        finally: rpc.close()
        # The creator has exited. Only the native owner may consume this queue.
        self._follow()
        self.opener(self.tid, wake=True)
        self.opener(self.tid)
        self._notify(codexSyncState='connecting', codexSyncMessage='已提交给 Codex，正在接收执行状态')

    def send(self, method, params):
        # The server accepts omitted text_elements, but the native renderer uses
        # the submitted request itself for optimistic history and requires it.
        params = deepcopy(params)
        for item in params.get('input', []):
            if item.get('type') == 'text': item.setdefault('text_elements', [])
        self.next_id += 1
        rid = self.next_id
        if self.observer: self.observer('outgoing', {'id':rid, 'method':method, 'params':params})
        if method == 'turn/start' and not self.tid:
            self.first_start_id = rid
            self.first_start_deadline = time.monotonic()+60
            try: self._create_and_queue(params)
            except Exception as exc:
                # Delivery may already be durable. Never retry automatically.
                self._emit({'id':rid, 'error':{'message':'提交状态未确认，请在 Codex 查看后再决定是否重发：'+str(exc)}})
                self._notify(codexSyncState='error', codexSyncMessage='提交状态未确认，请在 Codex 中查看')
            return rid
        if params.get('threadId') != self.tid or not self.owner:
            self._emit({'id':rid, 'error':{'message':'Codex 任务尚未连接，请在 Codex 中查看'}})
            return rid
        active = self.active_turn()
        if method == 'turn/start':
            if active:
                self._emit({'id':rid, 'error':{'message':'Codex 中仍有任务在运行'}})
                return rid
            name, version = 'thread-follower-start-turn', 2
            request = deepcopy(params)
            request['clientUserMessageId'] = str(uuid.uuid4())
            payload = {'conversationId':self.tid, 'turnStart':{'request':request, 'context':{}}}
        elif method in ('turn/steer', 'turn/interrupt'):
            expected = params.get('expectedTurnId', params.get('turnId'))
            if not active or expected != active:
                self._emit({'id':rid, 'error':{'message':'原任务已结束，操作未发送'}})
                return rid
            self.approvals.retire_current()
            if method == 'turn/steer':
                name, version = 'thread-follower-steer-turn', 1
                payload = {'conversationId':self.tid, 'expectedTurnId':expected, 'input':deepcopy(params['input']),
                           'clientUserMessageId':str(uuid.uuid4())}
                for item in payload['input']:
                    if item.get('type') == 'text': item.setdefault('text_elements', [])
                text = '\n'.join(i.get('text','') for i in params['input'] if i.get('type') == 'text')
                payload['restoreMessage'] = {
                    'id':payload['clientUserMessageId'], 'cwd':self.state.get('cwd'),
                    'context':{'prompt':text, 'addedFiles':[], 'fileAttachments':[],
                               'ideContext':None, 'imageAttachments':[], 'commentAttachments':[]},
                    'createdAt':int(time.time()*1000), 'text':text}
            else:
                name, version = 'thread-follower-interrupt-turn', 4
                payload = {'conversationId':self.tid, 'mode':'user-stop', 'expectedTurnId':expected}
        else: raise ValueError('不支持的原生任务操作：'+method)
        request_id = str(uuid.uuid4())
        self.pending[request_id] = (rid, name, self.owner, time.monotonic()+30)
        self.ipc.send(dict(type='request', requestId=request_id, targetClientId=self.owner,
                           method=name, version=version, params=payload, timeoutMs=30000))
        return rid

    def active_turn(self):
        return next((t.get('turnId') for t in reversed(canonical_turns(self.state))
                     if t.get('status') == 'inProgress' and t.get('turnId')), None)

    def _project(self):
        for turn in canonical_turns(self.state):
            tid, status = turn.get('turnId'), turn.get('status')
            if not tid: continue  # Native optimistic turns have no server ID yet.
            if tid not in self.started:
                self.started.add(tid)
                if self.first_start_id is not None:
                    self._emit({'id':self.first_start_id, 'result':{'turn':{'id':tid}}})
                    self.first_start_id = None
                    self.first_start_deadline = None
                self._emit({'method':'turn/started', 'params':{'threadId':self.tid, 'turn':{'id':tid}}})
            for item in turn.get('items', []):
                key = (tid, item.get('id'))
                done = bool(turn.get('completedItemIds', {}).get(item.get('id')))
                old = self.items.get(key)
                if old == (item, done): continue
                if old is None:
                    self._emit({'method':'item/started', 'params':{'threadId':self.tid, 'turnId':tid, 'item':item}})
                if item.get('type') == 'agentMessage' and (old is None or old[0].get('text') != item.get('text')):
                    # Complete text also handles native replacements, not just appends.
                    self._emit({'method':'item/completed', 'params':{'threadId':self.tid, 'turnId':tid, 'item':item}})
                elif done and (old is None or not old[1]):
                    self._emit({'method':'item/completed', 'params':{'threadId':self.tid, 'turnId':tid, 'item':item}})
                self.items[key] = (deepcopy(item), done)
            if status in ('completed', 'interrupted', 'failed') and tid not in self.completed:
                self.completed.add(tid)
                self._emit({'method':'turn/completed', 'params':{'threadId':self.tid,
                    'turn':{'id':tid, 'status':status, 'error':turn.get('error')}}})
        waiting = any(not request.get('completed') for request in self.state.get('requests', []))
        self._notify(nativeNeedsAttention=waiting,
            codexSyncState='ready', codexSyncMessage='Codex 原生执行；授权结果与 Codex 同步')
        if waiting: self._notify(status='等待 Codex 中的操作', detail='请在 Codex 中处理授权或追问')
        elif self._waiting_for_request and self.active_turn():
            self._notify(status='执行中', detail='')
        self._waiting_for_request = waiting

    def poll(self):
        if self.closed: return
        if self.ipc.closed.is_set():
            raise ConnectionIssue('error', '与 Codex 的状态连接已断开；任务可能仍在 Codex 运行，请在那里继续。')
        for _ in range(128):
            try: event = self.ipc.inbox.get_nowait()
            except queue.Empty: break
            kind = event.get('type')
            if kind == 'client-discovery-request':
                self.ipc.send(dict(type='client-discovery-response', requestId=event['requestId'],
                                   response={'canHandle':False}))
            elif kind == 'response' and self.approvals.response(event):
                pass
            elif kind == 'response' and event.get('requestId') in self.pending:
                rid, method, owner, _ = self.pending[event['requestId']]
                if event.get('handledByClientId') != owner or event.get('method') != method:
                    continue
                del self.pending[event['requestId']]
                if event.get('resultType') == 'success':
                    result = event.get('result') or {}
                    self._emit({'id':rid, 'result':result.get('result', result)})
                else: self._emit({'id':rid, 'error':{'message':event.get('error', 'Codex 拒绝了操作')}})
            elif kind == 'broadcast':
                p = event.get('params') or {}
                if (event.get('method') == 'client-status-changed' and
                        p.get('clientId') == self.owner and p.get('status') == 'disconnected'):
                    raise ConnectionIssue('error', 'Codex 执行窗口已断开，请在 Codex 中继续任务。')
                if (event.get('method') != 'thread-stream-state-changed' or p.get('conversationId') != self.tid
                        or p.get('hostId') != 'local' or event.get('version') != 11): continue
                source = event.get('sourceClientId')
                if self.owner and self.owner != source:
                    raise ConnectionIssue('error', 'Codex 任务所有者已变化；已停止发送，请在 Codex 继续。')
                try: state, revision = apply_change(self.state, self.revision, p['change'])
                except (ValueError, KeyError, IndexError, TypeError):
                    self._follow()
                    continue
                self.owner = source
                self.state, self.revision = state, revision
                if self.attach_deadline is not None: self._adopt_history()
                self._project()
        now = time.monotonic()
        self.approvals.expire(now)
        if self.first_start_deadline is not None and now >= self.first_start_deadline:
            raise ConnectionIssue('error', '任务已提交，但未收到 Codex 执行状态；请在 Codex 查看，避免重复提交。')
        if self.attach_deadline is not None and now >= self.attach_deadline:
            self._notify(threadAttachState='error', threadAttachError='连接超时，当前对话已保留；请打开目标对话后重试。')
            raise ConnectionIssue('error', '未收到已有对话的执行状态')
        if self.tid and self.revision is None and now-self.last_follow > 1: self._follow()
        for request_id, (rid, _, _, deadline) in list(self.pending.items()):
            if now >= deadline:
                del self.pending[request_id]
                self._emit({'id':rid, 'error':{'message':'Codex 操作回执超时，请在 Codex 查看；不会自动重发'}})

    def close(self):
        self.closed = True
        if self.tid and not self.ipc.closed.is_set():
            try: self.ipc.broadcast('thread-stream-following-changed', 1,
                dict(hostId='local', conversationId=self.tid, following=False))
            except OSError: pass
        self.ipc.close()
