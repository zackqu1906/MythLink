"""Native permission levels, request lifetime and parallel desktop decisions."""
from copy import deepcopy
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import time

import pytest

from proximic_ring.agent_workflow.agent.native_approvals import approval_response
from proximic_ring.agent_workflow.agent.native_codex import NativeCodex
from proximic_ring.agent_workflow.agent.native_flow import NativeFlow
from test_agent_native_codex import Transport, snapshot, state


def request(method='mcpServer/elicitation/request', **params):
    return {'id': 'approval-1', 'method': method, 'params': {
        'threadId': 'thread-1', 'turnId': 'turn-1', **params}}


def app_request():
    return request(mode='form', serverName='cua_repl',
        requestedSchema={'type': 'object', 'properties': {}},
        _meta={'codex_approval_kind': 'mcp_tool_call', 'connector_id': 'computer-use',
               'tool_name': 'get_app_state', 'tool_params': {'app': 'com.apple.Safari'},
               'persist': ['session', 'always'], 'riskLevel': 'high'})


@pytest.mark.parametrize('mode', ['auto', 'all'])
def test_application_grant_uses_exact_offered_always_scope(mode):
    req = app_request()
    original = deepcopy(req)
    assert approval_response(req, mode) == {
        'action': 'accept', 'content': {}, '_meta': {'persist': 'always'}}
    assert req == original
    req['params']['_meta']['persist'] = ['session']
    assert approval_response(req, mode)['_meta'] == {'persist': 'session'}
    assert approval_response(req, 'codex') is None


@pytest.mark.parametrize('method', ['item/commandExecution/requestApproval',
                                  'item/fileChange/requestApproval'])
def test_all_mode_honors_available_decisions_without_installing_rules(method):
    req = request(method, availableDecisions=['accept', 'acceptForSession', 'decline'])
    assert approval_response(req, 'auto') is None
    assert approval_response(req, 'all') == {'decision': 'accept'}
    req['params']['availableDecisions'] = ['acceptForSession', 'decline']
    assert approval_response(req, 'all') == {'decision': 'acceptForSession'}
    req['params']['availableDecisions'] = ['decline', {'acceptWithExecpolicyAmendment': {}}]
    assert approval_response(req, 'all') is None


def test_all_mode_grants_only_requested_permissions_for_current_turn():
    permissions = {'network': {'enabled': True}, 'fileSystem': {'write': ['/tmp/report']}}
    req = request('item/permissions/requestApproval', permissions=permissions)
    assert approval_response(req, 'auto') is None
    response = approval_response(req, 'all')
    assert response == {'permissions': permissions, 'scope': 'turn'}
    response['permissions']['fileSystem']['write'].append('/different')
    assert permissions['fileSystem']['write'] == ['/tmp/report']
    req['params']['permissions'] = {'unknown': True}
    assert approval_response(req, 'all') is None


def test_all_mode_allows_tool_confirmation_but_does_not_persist_it():
    req = app_request()
    req['params']['serverName'] = 'codex_apps'
    req['params']['_meta']['connector_id'] = 'another-connector'
    req['params']['_meta']['tool_params'] = {'plan_token': 'bound-plan', 'confirmation_summary': '{}'}
    assert approval_response(req, 'auto') is None
    assert approval_response(req, 'all') == {'action': 'accept', 'content': {}}


@pytest.mark.parametrize('change', [
    {'mode': 'url'}, {'mode': 'openai/userVerification'},
    {'requestedSchema': {'type': 'object', 'properties': {'approved': {'type': 'boolean'}}}},
    {'requestedSchema': {'type': 'object', 'required': ['password']}},
    {'_meta': {'codex_approval_kind': 'browser_auth'}},
    {'_meta': {'codex_approval_kind': 'browser_email_otp'}},
    {'_meta': []}, {'requestedSchema': None},
])
def test_authentication_and_form_answers_are_never_fabricated(change):
    req = app_request()
    req['params'].update(change)
    assert approval_response(req, 'all') is None


@pytest.mark.parametrize('method', ['item/tool/requestUserInput', 'item/tool/requestOptionPicker',
                                  'item/tool/call', 'unknown/requestApproval'])
def test_questions_and_unknown_requests_stay_in_codex(method):
    assert approval_response(request(method), 'all') is None


@pytest.fixture
def rpc(tmp_path):
    rpc = NativeCodex(runtime_dir=tmp_path, codex_path='/test/codex', transport=Transport())
    rpc.tid = 'thread-1'
    rpc.approvals.mode = 'auto'
    return rpc


def load_request(rpc, req=None, revision=1, **kwargs):
    value = state(**kwargs)
    value['requests'] = [req or app_request()]
    snapshot(rpc, value, revision)
    return value


def response(rpc, message, success=True, owner='native-owner'):
    rpc.ipc.inbox.put({'type': 'response', 'requestId': message['requestId'],
        'method': message['method'], 'handledByClientId': owner,
        'resultType': 'success' if success else 'error',
        'result': {'ok': True}, 'error': 'rejected'})
    rpc.poll()


def test_native_owner_receives_response_once_and_desktop_removes_prompt(rpc):
    updates = []
    rpc.on_state = lambda **fields: updates.append(fields)
    value = load_request(rpc)
    rpc.approvals.process()
    message = rpc.ipc.sent[-1]
    assert message['targetClientId'] == 'native-owner'
    assert message['method'] == 'thread-follower-submit-mcp-server-elicitation-response'
    assert message['version'] == 1
    assert message['params'] == {'conversationId': 'thread-1', 'requestId': 'approval-1',
        'response': {'action': 'accept', 'content': {}, '_meta': {'persist': 'always'}}}
    snapshot(rpc, value, 2)
    rpc.approvals.process()
    assert len(rpc.ipc.sent) == 1
    response(rpc, message)
    assert rpc.approvals.count == 1
    value['requests'] = []
    snapshot(rpc, value, 3)
    assert updates[-1]['status'] == '执行中'
    assert any(update.get('nativeNeedsAttention') is False for update in updates)


@pytest.mark.parametrize('mutate', [
    lambda req: req.update(completed=True),
    lambda req: req['params'].update(threadId='another-thread'),
    lambda req: req['params'].update(turnId='old-turn'),
])
def test_stale_or_other_task_requests_are_not_approved(rpc, mutate):
    req = app_request()
    mutate(req)
    load_request(rpc, req)
    rpc.approvals.process()
    assert rpc.ipc.sent == []


def test_user_decision_and_turn_completion_win_before_automatic_reply(rpc):
    load_request(rpc)
    snapshot(rpc, state(), 2)  # Codex already handled the request.
    rpc.approvals.process()
    assert rpc.ipc.sent == []
    load_request(rpc, revision=3, status='completed')
    rpc.approvals.process()
    assert rpc.ipc.sent == []


@pytest.mark.parametrize('outcome', ['error', 'timeout', 'wrong-owner'])
def test_failed_unknown_or_wrong_owner_reply_is_not_counted_or_retried(rpc, outcome):
    load_request(rpc)
    rpc.approvals.process()
    message = rpc.ipc.sent[-1]
    if outcome == 'timeout': rpc.approvals.expire(time.monotonic() + 31)
    else: response(rpc, message, success=outcome != 'error',
                   owner='other-owner' if outcome == 'wrong-owner' else 'native-owner')
    rpc.approvals.process()
    assert rpc.approvals.count == 0
    assert len(rpc.ipc.sent) == 1


def test_steer_retires_old_permission_and_mode_changes_do_not_replay(rpc):
    value = load_request(rpc)
    rpc.send('turn/steer', {'threadId': 'thread-1', 'expectedTurnId': 'turn-1',
                          'input': [{'type': 'text', 'text': '改用另一应用'}]})
    snapshot(rpc, value, 2)
    rpc.approvals.mode = 'all'
    rpc.approvals.process()
    assert [message['method'] for message in rpc.ipc.sent] == ['thread-follower-steer-turn']
    value['requests'][0]['id'] = 'new-approval'
    snapshot(rpc, value, 3)
    rpc.approvals.process()
    assert rpc.ipc.sent[-1]['params']['requestId'] == 'new-approval'


def test_flow_gates_approval_after_stop_disable_and_queued_input(rpc, tmp_path):
    flow = NativeFlow(with_ring=False, runtime_dir=tmp_path)
    flow.rpc = rpc
    try:
        load_request(rpc)
        flow.set_approval_mode('all')
        assert flow.state['approvalMode'] == 'all'
        flow.inputs.put(('command', {'type': 'cancel'}))
        flow.poll_pending_actions()
        assert not rpc.ipc.sent
        flow.inputs.get_nowait()
        flow.cancelled = True
        flow.poll_pending_actions()
        assert not rpc.ipc.sent
        flow.cancelled = False
        flow.command({'type': 'set_approval_mode', 'mode': 'codex'})
        flow.poll_pending_actions()
        assert not rpc.ipc.sent
        flow.set_auto_approve(True)
        assert flow.state['approvalMode'] == 'auto'
        flow.stop.set()
        flow.poll_pending_actions()
        assert not rpc.ipc.sent
        flow.stop.clear()
        flow.poll_pending_actions()
        assert len(rpc.ipc.sent) == 1
    finally:
        flow.events.close()


@pytest.mark.skipif(os.environ.get('PROXIMIC_TEST_CODEX_CONTRACT') != '1',
                    reason='opt in to read installed native approval forwarders')
def test_installed_codex_uses_the_same_approval_payloads():
    """Run only pure forwarding closures in an isolated VM with a fake IPC sink."""
    from test_agent_codex_sync_native import bundles
    from proximic_ring.agent_workflow.agent.native_codex import NATIVE_BUILDS
    app = Path('/Applications/ChatGPT.app/Contents')
    assert str(plistlib.loads((app / 'Info.plist').read_bytes())['CFBundleVersion']) in NATIVE_BUILDS
    names = {
        'forwardFollowerMcpElicitation': ('thread-follower-submit-mcp-server-elicitation-response',
            'response', {'action': 'accept', 'content': {}, '_meta': {'persist': 'always'}}),
        'forwardFollowerCommandApproval': ('thread-follower-command-approval-decision', 'decision', 'accept'),
        'forwardFollowerFileApproval': ('thread-follower-file-approval-decision', 'decision', 'accept'),
        'forwardFollowerPermissionsApproval': ('thread-follower-permissions-request-approval-response',
            'response', {'permissions': {'network': {'enabled': True}}, 'scope': 'turn'}),
    }
    found = {}
    for source in bundles(app / 'Resources/app.asar'):
        for name, (method, field, value) in names.items():
            match = re.search(name + r':(async\([^)]*\)=>\{.*?throw Error\([^)]*\)\})', source)
            if match:
                owner = re.search(r'(\w+)\.ipcBridge', match[1])[1]
                found[name] = {'code': match[1], 'owner': owner, 'value': value,
                    'expected': [method, {'conversationId': 'thread-1', 'requestId': 'approval-1', field: value},
                                 {'targetClientId': 'native-owner'}]}
    assert set(found) == set(names)
    script = """
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const fixtures = JSON.parse(fs.readFileSync(0, 'utf8'));
(async () => {
  for (const f of Object.values(fixtures)) {
    const calls = [];
    const owner = {ipcBridge: {request: async (...args) => {
      calls.push(JSON.parse(JSON.stringify(args))); return {resultType:'success'};
    }}};
    const forward = vm.runInNewContext('(' + f.code + ')', {[f.owner]: owner}, {timeout:1000});
    await forward('thread-1', 'approval-1', f.value, 'native-owner');
    assert.deepEqual(calls, [f.expected]);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run([str(app / 'Resources/cua_node/bin/node'), '-e', script],
        input=json.dumps(found), text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
