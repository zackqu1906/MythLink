"""Read-only Codex links derived from native thread and turn evidence."""
from __future__ import annotations

import html
import re


def _thread_id(value):
    # Never accept routing keywords (e.g. "new"), URL queries or arbitrary schemes.
    return (value.lower() if isinstance(value, str) and re.fullmatch(
        r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', value) else None)


def codex_thread_url(value):
    tid = _thread_id(value)
    return f'codex://threads/{tid}' if tid else None


def task_links(session):
    """Require an observed turn and explicit persistence evidence for the same ID.

    Session-level thread/start responses can belong to formal or trial threads.
    Never infer the target from whichever thread was most recently created.
    """
    requests, persistence, targets = {}, {}, {}
    for event in session['events']:
        message = event['payload']
        method = message.get('method', '')
        request_id = message.get('id')
        key = (type(request_id), request_id) if isinstance(request_id, (str, int)) else None
        if event['direction'] == 'outgoing' and method and key is not None:
            requests[key] = method
        if event['direction'] != 'incoming':
            continue
        thread = None
        if method == 'thread/started':
            thread = (message.get('params') or {}).get('thread')
        elif not method and key is not None:
            origin = requests.pop(key, None)
            if origin in ('thread/start', 'thread/resume', 'thread/read'):
                thread = (message.get('result') or {}).get('thread')
        if isinstance(thread, dict):
            tid = _thread_id(thread.get('id'))
            ephemeral = thread.get('ephemeral')
            if tid and type(ephemeral) is bool:
                persistence.setdefault(tid, set()).add(ephemeral)
        # A queued/cancelled input alone does not prove it reached native history.
        if event.get('episode_id') and event.get('turn_id'):
            targets.setdefault(event['episode_id'], set()).add(event.get('thread_id'))

    links = {}
    for episode in session['episodes']:
        ids = targets.get(episode['id'], set())
        url, tid = None, None
        if not ids:
            reason = '没有已关联的 Codex 执行会话'
        elif len(ids) != 1:
            reason = '会话关联不唯一，无法确定打开目标'
        elif not (tid := _thread_id(next(iter(ids)))):
            reason = '会话 ID 缺失或格式不正确'
        elif persistence.get(tid) == {True}:
            reason = '临时会话未保存到 Codex'
        elif persistence.get(tid) != {False}:
            reason = '记录不足或冲突，无法确认会话已持久化'
        else:
            url = f'codex://threads/{tid}'
            reason = '打开本次任务所属的会话'
        links[episode['id']] = dict(url=url, thread_id=tid, reason=reason)
    return links


def link_html(link):
    if link['url']:
        return (f"<a class='codex-open' href='{html.escape(link['url'], quote=True)}' "
                f"title='{html.escape(link['reason'], quote=True)}' rel='noreferrer'>在 Codex 中打开</a>")
    return ("<span class='codex-unavailable muted'>"
            f"在 Codex 中打开：{html.escape(link['reason'])}</span>")
