"""Release only this thread/turn's Computer Use resources, without another model turn."""
from proximic_ring.agent_workflow.agent.connection import ConnectionIssue


def release_turn(rpc, thread_id, turn_id):
    failure = None
    steps = [('turn_ended', {'session_id': thread_id, 'turn_id': turn_id, 'hook_event_name': 'Stop'}),
             ('js_reset', {})]
    # The browser hook alone leaves the native Sky connection alive. Reset the
    # thread's REPL as well: that closes its native pipes, not the user's apps.
    # This thread runs one turn at a time; a new turn cannot start until we return.
    for tool, arguments in steps:
        try:
            _cleanup_call(rpc, {'threadId': thread_id, 'server': 'cua_repl',
                               'tool': tool, 'arguments': arguments})
        except (RuntimeError, TimeoutError, OSError) as exc:
            failure = exc  # Still attempt native cleanup if the browser hook failed.
    if failure:
        raise ConnectionIssue('tools',
            '本轮电脑控制释放失败，已停止后续任务。请重新连接 Agent 后再试。') from failure


def _cleanup_call(rpc, params):
    # The turn hook deduplicates; reset is safe to repeat while the next turn is
    # gated. A lost response must not cause cleanup to touch another thread.
    for attempt in range(2):
        try:
            result = rpc.call('mcpServer/tool/call', params, timeout=3)
            if not isinstance(result, dict) or not isinstance(result.get('content'), list) or result.get('isError'):
                raise RuntimeError('Computer Use did not acknowledge turn cleanup')
            return
        except (RuntimeError, TimeoutError, OSError):
            if attempt:
                raise
