"""Read the local Codex catalog without loading or claiming any conversation."""
from ..recording.codex_link import codex_thread_url
from .rpc import RPC


def list_recent_threads(runtime_dir, codex_path, *, rpc_factory=RPC):
    runtime_dir.mkdir(parents=True, exist_ok=True)
    rpc = rpc_factory(runtime_dir=runtime_dir, codex_path=codex_path)
    try:
        rpc.initialize()
        result = rpc.call('thread/list', {
            'limit': 40, 'sortKey': 'updated_at', 'sortDirection': 'desc',
            'archived': False, 'sourceKinds': ['cli', 'vscode', 'appServer'],
            'useStateDbOnly': True,
        }, timeout=8)
    finally:
        rpc.close()
    rows, seen = [], set()
    for thread in result.get('data', []):
        tid = thread.get('id')
        if (not codex_thread_url(tid) or tid in seen or thread.get('archived')
                or thread.get('ephemeral') is not False):
            continue
        seen.add(tid)
        title = str(thread.get('name') or thread.get('preview') or '未命名对话')
        rows.append({'id': tid, 'title': title.splitlines()[0][:160],
                     'cwd': str(thread.get('cwd') or ''),
                     'updatedAt': thread.get('updatedAt', 0)})
    rows.sort(key=lambda row: row['updatedAt'], reverse=True)
    return rows[:5]
