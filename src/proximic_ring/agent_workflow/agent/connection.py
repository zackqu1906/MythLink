"""Read-only readiness checks on the same connection used by Agent tasks."""


class ConnectionIssue(RuntimeError):
    def __init__(self, state, message):
        super().__init__(message)
        self.state = state


def verify_login(rpc):
    account = rpc.call('account/read', {'refreshToken': False})
    if not isinstance(account, dict) or 'requiresOpenaiAuth' not in account:
        raise ConnectionIssue('error', '无法检查登录状态，请更新本地 Codex 后重试。')
    if account['requiresOpenaiAuth'] and not account.get('account'):
        raise ConnectionIssue('login', '请先在本地 Codex 登录，再返回连接。')


def verify_computer_use(rpc, thread_id):
    cursor = None
    seen = set()
    while True:
        params = {'threadId': thread_id, 'serverName': 'cua_repl',
                  'detail': 'toolsAndAuthOnly', 'limit': 100}
        if cursor: params['cursor'] = cursor
        result = rpc.call('mcpServerStatus/list', params)
        for server in result.get('data', []):
            if server.get('name') != 'cua_repl': continue
            if (server.get('authStatus') != 'notLoggedIn'
                    and server.get('runtimeStatus') in (None, 'connected')
                    and not server.get('toolsError') and 'js' in server.get('tools', {})):
                if not {'turn_ended','js_reset'} <= server['tools'].keys():
                    raise ConnectionIssue('tools', '电脑操作工具缺少任务结束释放接口，请更新本地 Codex 后重新连接。')
                return
            raise ConnectionIssue('tools', '电脑操作工具尚未就绪，请在本地 Codex 启用 Computer Use 后重新连接。')
        cursor = result.get('nextCursor')
        if not cursor or cursor in seen: break
        seen.add(cursor)
    raise ConnectionIssue('tools', '未找到电脑操作工具，请在本地 Codex 安装并启用 Computer Use 后重新连接。')
