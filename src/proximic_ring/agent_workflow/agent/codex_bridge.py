"""Single-owner, two-window task coordination. Called only on Flow's thread."""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import queue
import time

from .codex_ipc import DesktopIPC, VERSIONS
from .codex_state import DesktopState


class CodexBridge:
    def __init__(self, flow, result, transport=None):
        self.flow = flow
        self.model = DesktopState(result)
        self.ipc = transport or DesktopIPC()
        self.followers = set()
        self.pending = {}
        self.completed = OrderedDict()
        self.inflight = set()
        self.last_flush = 0
        self.failed = False
        self.titled = False

    def connect(self):
        self.ipc.connect()
        self.ipc.broadcast('thread-stream-following-status-requested', 1,
                           dict(hostId='local', conversationId=self.model.tid))
        self.flow.publish(codexSyncState='ready', codexSyncMessage='已连接 Codex，可在两边继续对话',
                          codexThreadId=self.model.tid)

    def accepts(self, request):
        p = request.get('params') or {}
        version = VERSIONS.get(request.get('method'))
        compatible = request.get('version') == version
        if request.get('method') == 'thread-follower-interrupt-turn':
            compatible |= request.get('version') == 3 and not p.get('expectedTurnId')
        return (self.flow.tid == self.model.tid and p.get('conversationId') == self.model.tid
                and p.get('hostId', 'local') == 'local' and request.get('hostId') is None
                and version is not None and compatible)

    def outgoing(self, event):
        p = event.get('params') or {}
        if event.get('method') == 'turn/start' and p.get('threadId') == self.model.tid:
            self.model.prepare_turn(p)
            if not self.titled:
                text = next((v.get('text','') for v in p.get('input',[]) if v.get('type')=='text'), '')
                self.model.state['title'] = 'Mythlink · '+(text.strip().split('\n')[0][:48] or 'Agent 任务')
                self.model.dirty = True
                self.titled = True
        if 'method' not in event and 'id' in event:
            self.model.remove_request(event['id'])

    def incoming(self, event):
        self.model.observe(event)
        if 'method' not in event and event.get('id') in self.pending:
            request, operation = self.pending.pop(event['id'])
            if event.get('error'):
                self._reply(request, error=event['error'].get('message','Codex 拒绝了请求'))
            else:
                result = event.get('result') or {}
                if operation == 'interrupt':
                    result = dict(ok=True, interruptedTurnId=request['params'].get('expectedTurnId'))
                else: result = {'result':result}
                self.flush(force=True)
                self._reply(request, result)

    def _reply(self, request, result=None, error=None):
        key = (request.get('sourceClientId'), request['requestId'])
        self.inflight.discard(key)
        self.completed[key] = (result, error)
        while len(self.completed) > 256: self.completed.popitem(last=False)
        self.ipc.respond(request, result, error)

    def poll(self):
        if self.failed: return
        try:
            if self.ipc.closed.is_set():
                raise ConnectionError('Codex 窗口同步已断开；当前 Agent 已停止，请重新连接')
            for _ in range(64):
                try: event = self.ipc.inbox.get_nowait()
                except queue.Empty: break
                kind = event.get('type')
                if kind == 'client-discovery-request':
                    self.ipc.send(dict(type='client-discovery-response', requestId=event['requestId'],
                        response={'canHandle':self.accepts(event.get('request') or {})}))
                elif kind == 'request':
                    self._request(event)
                elif kind == 'broadcast':
                    self._broadcast(event)
            self.flush()
        except (OSError, ValueError, ConnectionError) as exc:
            self.failed = True
            self.flow.publish(codexSyncState='error', codexSyncMessage=str(exc),
                              connectionMessage=str(exc))
            # The desktop can resume persisted history after owner loss. Retire
            # this executor first; never reconnect it over a new native owner.
            self.flow.stop.set()

    def _broadcast(self, event):
        p = event.get('params') or {}
        if event.get('method') == 'client-status-changed' and p.get('status') == 'disconnected':
            self.followers.discard(p.get('clientId'))
        if (event.get('method') == 'thread-stream-following-changed' and event.get('version') == 1
                and p.get('hostId') == 'local' and p.get('conversationId') == self.model.tid):
            client = event.get('sourceClientId')
            if p.get('following') and client:
                self.followers.add(client)
                self.flow.publish(codexSyncMessage='正在同步到 Codex，两个窗口均可继续对话',
                                  codexFollowerCount=len(self.followers))
                self.flush(force=True)
            else: self.followers.discard(client)

    def flush(self, force=False):
        now = time.monotonic()
        if self.failed or not self.followers or not (force or self.model.dirty and now-self.last_flush >= .1):
            return
        self.ipc.broadcast('thread-stream-state-changed', 11,
            dict(hostId='local', conversationId=self.model.tid, change=self.model.snapshot()),
            self.followers)
        self.last_flush = now

    def _request(self, event):
        if not self.accepts(event):
            self.ipc.respond(event, error='此请求不属于当前 Mythlink 会话或协议版本不兼容')
            return
        key = (event.get('sourceClientId'), event['requestId'])
        if key in self.completed:
            result, error = self.completed[key]
            self.ipc.respond(event, result, error)
            return
        if key in self.inflight: return
        self.inflight.add(key)
        self.flow.event('codex_desktop_request', method=event['method'])
        try:
            method, p = event['method'], event['params']
            if method == 'thread-owner-discovery':
                self._reply(event, {'supportsUntrustedAppInput':False})
            elif method == 'thread-follower-read-model-settings':
                state = self.model.state
                self._reply(event, {'settings':dict(model=state['latestModel'],
                    reasoningEffort=state['latestReasoningEffort'], daybreakEnabled=None,
                    resumeState='resumed', mode='default', threadSource=state['threadSource'],
                    modelProvider=state['modelProvider'])})
            elif method == 'thread-follower-load-complete-history':
                self.followers.add(event['sourceClientId'])
                self.flush(force=True)
                self._reply(event, {'revision':self.model.revision})
            elif method == 'thread-follower-start-turn':
                self._start(event)
            elif method == 'thread-follower-steer-turn':
                self._steer(event)
            elif method == 'thread-follower-interrupt-turn':
                self._interrupt(event)
            else:
                self._decision(event)
        except (ValueError, KeyError, TypeError) as exc:
            self._reply(event, error=str(exc))

    @staticmethod
    def _input(items):
        if not isinstance(items, list) or not items or len(items) > 200:
            raise ValueError('请输入任务内容')
        if any(not isinstance(i, dict) or i.get('type') not in
               ('text','image','localImage','skill','mention') for i in items):
            raise ValueError('此输入类型暂不支持，请在 Agent 窗口提交')
        return deepcopy(items)

    @staticmethod
    def _text(items):
        return '\n'.join(i.get('text','') for i in items if i.get('type') == 'text')

    def _start(self, event):
        flow = self.flow
        if flow.turn or flow.start_id or flow.cancelled or flow.pending or flow.task_queue:
            raise ValueError('任务仍在进行，请修改当前任务或等本轮完成')
        envelope = event['params'].get('turnStart') or {}
        raw, context = envelope.get('request') or {}, envelope.get('context') or {}
        if any(context.get(k) for k in ('responseItems','mcpAppModelContextAttachments',
                'passiveContext','environmentDraftReplacement','aeonInitialization','commentAttachments')):
            raise ValueError('此附加上下文暂不支持，请在 Agent 窗口提交图片或文件')
        inputs = self._input(raw.get('input'))
        if raw.get('threadId') != flow.tid:
            raise ValueError('任务已切换，请刷新对话')
        # Preserve server-owned permissions/environment. Reject mismatches rather
        # than silently granting a wider scope or lying about the selected policy.
        for key, expected in (('cwd',self.model.settings.get('cwd')),
                ('approvalPolicy',self.model.settings.get('approvalPolicy')),
                ('approvalsReviewer',self.model.settings.get('approvalsReviewer')),
                ('sandboxPolicy',self.model.settings.get('sandbox')),
                ('permissions',(self.model.settings.get('activePermissionProfile') or {}).get('id'))):
            if raw.get(key) is not None and raw[key] != expected:
                raise ValueError('请在 Agent 当前权限和工作目录下继续；暂不支持从 Codex 更换运行环境')
        params = {key:deepcopy(raw[key]) for key in ('model','effort','summary','serviceTier',
            'outputSchema','clientUserMessageId','collaborationMode') if key in raw}
        params.update(threadId=flow.tid, input=inputs)
        flow.cancelled = False
        flow.epoch += 1
        text = self._text(inputs)
        flow.episode_id = flow.recorder.new_episode(text, 'codex_desktop') if flow.recorder else None
        flow.event('input',text=text,source='codex_desktop',mode='normal')
        flow.event('episode/starting',episodeId=flow.episode_id,
                   executionContext={'availability':'not_captured','reason':'codex_desktop_submission'})
        flow.publish(transcript=text, agent='', status='启动任务', detail='来自 Codex 的任务',
                     lastSubmission='Codex 对话', computerControlState='active')
        flow.start_id = flow.rpc.send('turn/start', params)
        flow.event('start_sent',requestId=flow.start_id,text=text,source='codex_desktop')
        self.pending[flow.start_id] = (event, 'start')

    def _steer(self, event):
        flow, p = self.flow, event['params']
        if not flow.turn or flow.cancelled:
            raise ValueError('本轮已结束，请重新发送为下一条消息')
        if p.get('additionalContext') or p.get('toolOutput'):
            raise ValueError('此附加上下文暂不支持，请在 Agent 窗口提交')
        inputs = self._input(p.get('input'))
        flow.epoch += 1
        params = dict(threadId=flow.tid, expectedTurnId=flow.turn, input=inputs)
        if p.get('clientUserMessageId'): params['clientUserMessageId'] = p['clientUserMessageId']
        request_id = flow.rpc.send('turn/steer', params)
        flow.event('steer_sent',turnId=flow.turn,requestId=request_id,text=self._text(inputs),epoch=flow.epoch)
        self.pending[request_id] = (event, 'steer')
        flow.steer_requests.add(request_id)
        flow.publish(transcript=self._text(inputs), detail='Codex 已提交当前任务修改', lastSubmission='Codex 修改')
        flow.release()

    def _interrupt(self, event):
        flow, p = self.flow, event['params']
        if not flow.turn or p.get('expectedTurnId',flow.turn) != flow.turn:
            raise ValueError('要停止的任务已结束，请刷新对话')
        p['expectedTurnId'] = flow.turn
        flow.control('cancel','codex_desktop')
        # Cancellation has its own request ordering gate in Flow. Its completion
        # is acknowledged only after the App Server reports that turn ended.
        self.pending[('interrupt', flow.turn)] = (event, 'interrupt')

    def turn_completed(self, turn_id):
        pending = self.pending.pop(('interrupt', turn_id), None)
        if pending:
            self.flush(force=True)
            self._reply(pending[0], dict(ok=True, interruptedTurnId=turn_id))

    def _decision(self, event):
        flow, p, method = self.flow, event['params'], event['method']
        rid = p.get('requestId')
        # Desktop normalizes request IDs to strings.
        rid = next((key for key in flow.pending if str(key) == str(rid)), None)
        request = flow.pending.get(rid)
        if not request or flow.cancelled or request['epoch'] != flow.epoch:
            raise ValueError('该请求已结束或失效，请等待下一条提示')
        expected = {
            'thread-follower-command-approval-decision':'item/commandExecution/requestApproval',
            'thread-follower-file-approval-decision':'item/fileChange/requestApproval',
            'thread-follower-permissions-request-approval-response':'item/permissions/requestApproval',
            'thread-follower-submit-mcp-server-elicitation-response':'mcpServer/elicitation/request',
            'thread-follower-submit-user-input':'item/tool/requestUserInput'}
        actual = request['method']
        if method == 'thread-follower-submit-user-input' and actual == 'item/tool/call':
            answers = (p.get('response') or {}).get('answers', {})
            text = '\n'.join(str(a) for v in answers.values() for a in v.get('answers',[]))
            if not text.strip(): raise ValueError('回答不能为空')
            flow.answer(request, text)
        else:
            if expected.get(method) != actual:
                raise ValueError('请求类型不匹配')
            result = {'decision':p['decision']} if 'decision' in p else p.get('response')
            if not isinstance(result, dict): raise ValueError('回答格式无效')
            if request['kind'] == 'permission':
                index = next((i for i,o in enumerate(request['options'])
                              if o.get('enabled') and o.get('result') == result), None)
                if index is None:
                    raise ValueError('此授权选项暂不支持，请在 Agent 窗口确认')
                # Use the existing consent path, including local app grants and
                # the cancellation gate, rather than installing server rules.
                flow.choose(request['token'],index,'codex_desktop')
            else:
                if request.get('secret') or request.get('blocked'):
                    raise ValueError('请在 Agent 窗口处理此请求')
                flow.reply(rid, result, '在 Codex 中回答')
        self.flush(force=True)
        self._reply(event, {})

    def close(self):
        self.ipc.close()
