"""Opt-in installed Codex lifecycle probe. No inference turns or UI actions."""
import os
from pathlib import Path
import sys
import uuid

import pytest

from proximic_ring.agent_workflow.agent.computer_control import release_turn
from proximic_ring.agent_workflow.agent.connection import verify_computer_use
from proximic_ring.agent_workflow.agent.rpc import RPC


@pytest.mark.skipif(sys.platform != 'darwin' or os.environ.get('PROXIMIC_TEST_AGENT_CONTROL_NATIVE') != '1',
                   reason='opt-in installed Codex native connection lifecycle test')
def test_release_resets_only_owned_runtime_and_next_turn_can_initialize(tmp_path):
    binary = Path(os.environ.get('PROXIMIC_TEST_CODEX_PATH',
        '/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'))
    rpc = RPC(runtime_dir=tmp_path, codex_path=binary)
    threads = []
    def js(thread, code):
        tid, turn = thread
        result = rpc.call('mcpServer/tool/call', {
            'threadId': tid, 'server': 'cua_repl', 'tool': 'js',
            'arguments': {'code': code, 'title': '验证任务收尾的会话隔离'},
            '_meta': {'x-codex-turn-metadata': {'thread_id': tid, 'turn_id': turn}},
        }, timeout=20)
        assert not result.get('isError'), result
        return '\n'.join(c.get('text', '') for c in result['content'])
    try:
        rpc.initialize()
        for _ in range(2):
            tid = rpc.call('thread/start', {'cwd': str(tmp_path), 'ephemeral': True})['thread']['id']
            threads.append((tid, str(uuid.uuid4())))
            verify_computer_use(rpc, tid)
            # First call follows the documented discovery entry point. It
            # opens a native read-only connection, without capturing any app.
            js(threads[-1], 'await cua.listApps({emit: false});')
            js(threads[-1], 'var retainedAgentMarker = "present";')
        release_turn(rpc, *threads[0])
        assert 'present' in js(threads[1], 'nodeRepl.write(retainedAgentMarker);')
        # A fresh first call works after reset and old handles no longer exist.
        js(threads[0], 'await cua.listApps({emit: false});')
        assert 'undefined' in js(threads[0], 'nodeRepl.write(typeof retainedAgentMarker);')
    finally:
        for tid, turn in threads:
            try: release_turn(rpc, tid, turn)
            except Exception: pass
        rpc.close()
