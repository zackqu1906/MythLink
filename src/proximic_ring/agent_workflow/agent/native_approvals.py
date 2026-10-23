"""Request-bound decisions sent through the Codex owner's normal approval API."""
from copy import deepcopy
import time
import uuid

from .requests_ui import is_cua_app_consent

MODES = ('codex', 'auto', 'all')
METHODS = {
    'mcpServer/elicitation/request': 'thread-follower-submit-mcp-server-elicitation-response',
    'item/commandExecution/requestApproval': 'thread-follower-command-approval-decision',
    'item/fileChange/requestApproval': 'thread-follower-file-approval-decision',
    'item/permissions/requestApproval': 'thread-follower-permissions-request-approval-response',
}


def approval_response(request, mode):
    """Choose only an offered permission; questions/authentication are not grants."""
    if mode not in ('auto', 'all'):
        return None
    method, params = request.get('method'), request.get('params')
    if not isinstance(params, dict):
        return None
    if method == 'mcpServer/elicitation/request':
        meta, schema = params.get('_meta') or {}, params.get('requestedSchema')
        if not isinstance(meta, dict) or not isinstance(schema, dict):
            return None
        # Never invent form answers, OTPs, OAuth or user-verification responses.
        if (params.get('mode') != 'form' or schema.get('type') != 'object'
                or schema.get('properties') or schema.get('required')
                or meta.get('codex_approval_kind') != 'mcp_tool_call'):
            return None
        tool_params = meta.get('tool_params') or {}
        if not isinstance(tool_params, dict):
            return None
        app_consent = is_cua_app_consent(params)
        if mode == 'auto' and not app_consent:
            return None
        result = {'action': 'accept', 'content': {}}
        # Persist only an application grant that actually offers this choice.
        # Other tool/command approvals remain request-bound when the mode changes.
        # Codex still validates execution-bound connector confirmations itself.
        if app_consent:
            persist = meta.get('persist') or []
            if isinstance(persist, str): persist = [persist]
            if 'always' in persist: result['_meta'] = {'persist': 'always'}
            elif 'session' in persist: result['_meta'] = {'persist': 'session'}
        return result
    if mode != 'all':
        return None
    if method in ('item/commandExecution/requestApproval', 'item/fileChange/requestApproval'):
        decisions = params.get('availableDecisions')
        if decisions is None: decisions = ['accept', 'acceptForSession']
        if not isinstance(decisions, list): return None
        for decision in ('accept', 'acceptForSession'):
            if decision in decisions: return {'decision': decision}
    elif method == 'item/permissions/requestApproval':
        permissions = params.get('permissions')
        if (isinstance(permissions, dict) and permissions
                and set(permissions) <= {'network', 'fileSystem'}):
            return {'permissions': deepcopy(permissions), 'scope': 'turn'}
    return None


class NativeApprovals:
    def __init__(self, rpc):
        self.rpc = rpc
        self.mode = 'codex'
        self.attempted = set()
        self.pending = {}
        self.count = 0

    def requests(self):
        active = self.rpc.active_turn()
        if not active: return []
        return [request for request in self.rpc.state.get('requests', [])
                if isinstance(request, dict) and not request.get('completed')
                and isinstance(request.get('params'), dict)
                and request['params'].get('threadId') == self.rpc.tid
                and request['params'].get('turnId') in (None, '', active)
                and type(request.get('id')) in (str, int)]

    def key(self, request):
        return (self.rpc.active_turn(), request.get('method'), request['id'])

    def retire_current(self):
        # A steer must not automatically approve the old requested operation.
        self.attempted.update(self.key(request) for request in self.requests())

    def process(self):
        rpc = self.rpc
        if (self.mode == 'codex' or rpc.closed or rpc.ipc.closed.is_set()
                or not rpc.owner or not rpc.ipc.inbox.empty()):
            return
        for request in self.requests():
            key = self.key(request)
            if key in self.attempted: continue
            result = approval_response(request, self.mode)
            if result is None: continue
            params = {'conversationId': rpc.tid, 'requestId': request['id']}
            params.update(result if 'decision' in result else {'response': result})
            message = dict(type='request', requestId=str(uuid.uuid4()),
                targetClientId=rpc.owner, method=METHODS[request['method']], version=1,
                params=params, timeoutMs=30000)
            # Mark before writing. A timeout/disconnect must never replay a grant.
            self.attempted.add(key)
            self.pending[message['requestId']] = (message, time.monotonic() + 30)
            if rpc.observer: rpc.observer('outgoing', deepcopy(message))
            rpc.ipc.send(message)
            rpc._notify(status='正在自动授权', detail='正在向 Codex 提交授权结果')

    def response(self, event):
        pending = self.pending.get(event.get('requestId'))
        if pending is None: return False
        message, _ = pending
        if (event.get('handledByClientId') != message['targetClientId']
                or event.get('method') != message['method']):
            return True
        del self.pending[event['requestId']]
        if self.rpc.observer: self.rpc.observer('incoming', deepcopy(event))
        if event.get('resultType') == 'success':
            self.count += 1
            self.rpc._notify(autoApprovedCount=self.count,
                detail='自动授权回复已交给 Codex')
        else:
            self.rpc._notify(nativeNeedsAttention=True, status='需要在 Codex 处理',
                detail='Codex 未接受自动授权，请在 Codex 查看；不会自动重试')
        return True

    def expire(self, now):
        for rid, (_, deadline) in list(self.pending.items()):
            if now >= deadline:
                del self.pending[rid]
                self.rpc._notify(nativeNeedsAttention=True, status='授权回执未确认',
                    detail='自动授权回执超时，请在 Codex 查看；不会自动重发')
