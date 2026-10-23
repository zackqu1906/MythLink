"""Deterministic, read-only step projections over recorded Agent evidence.

Identity proves which result belongs to a call. Only explicit returned
observation evidence is annotated, within its own call; no adjacent-call
result is promoted to a before/after state.
"""
from __future__ import annotations

import hashlib
import re

VIEW_VERSION = 2
CALL_FIELDS = {
    'mcpToolCall': ('server', 'tool', 'arguments', 'readOnlyHint'),
    'dynamicToolCall': ('namespace', 'tool', 'arguments'),
    'commandExecution': ('command', 'cwd', 'commandActions', 'source'),
    'fileChange': ('changes',),
    'webSearch': ('query', 'action'),
    'imageView': ('path',),
    'imageGeneration': ('revisedPrompt', 'transparentBackground'),
    'collabAgentToolCall': ('tool', 'prompt', 'receiverThreadIds'),
    'sleep': ('durationMs',),
}
RESULT_FIELDS = {
    'mcpToolCall': ('result', 'error', 'durationMs'),
    'dynamicToolCall': ('contentItems', 'success', 'durationMs'),
    'commandExecution': ('aggregatedOutput', 'exitCode', 'durationMs'),
    'fileChange': ('changes',),
    'webSearch': ('results',),
    'imageView': (),
    'imageGeneration': ('result', 'failure', 'savedPath'),
    'collabAgentToolCall': ('agentsStates',),
    'sleep': ('durationMs',),
}
REASONS = {
    'no_explicit_observation': '未记录到可明确识别的观察内容；本次原始返回仍保留在执行结果中',
    'no_prior_explanation': '本次调用前的对应时间段内没有收到 Agent 文字或推理摘要',
    'start_event_missing': '缺少调用开始事件，无法确定动作前的时间边界',
    'completion_event_missing': '尚无调用结束事件，结果可能仍在生成或已经丢失',
    'scope_missing': '任务、线程或轮次标识不足，不做跨条目的消息时间关联',
    'intra_call_order_unknown': '观察属于本次调用；没有逐动作执行轨迹，不标为某个动作的前状态或后状态',
    'overlapping_calls': '有工具调用在时间上重叠，无法确认独立的状态变化',
    'intervening_user_control': '两条证据之间有用户输入、修改或控制事件',
    'partial_explanation': '只收到部分说明增量，未收到该说明的完成条目',
    'not_action_intent_verified': '按实际消息时间展示，不认定为本次动作的行动理由',
    'recording_incomplete': '会话存在写入错误、丢失事件或序号缺口，关联可能不完整',
    'source_quality_flags': '原始证据带有过滤、缺失或附件质量标记，请检查原始事件',
    'tool_failure_or_partial_result': '工具报告失败或错误，返回内容可能仅为部分结果',
    'indexed_item_without_events': '只有条目索引，缺少可核对的原始事件',
    'late_item_events': '条目结束后仍有事件；结束后的增量没有拼入结果',
    'request_lifecycle_only': '结果依据为客户端工具请求/回复，缺少完整条目结束事件',
    'client_reply_attempt': '记录的是客户端尝试回复，不证明服务端已收到或工具条目已完成',
    'unassigned_evidence': '部分条目缺少标识或类型，保留在原始时间线中',
    'call_arguments_missing': '没有记录到调用参数，不能从返回内容反推',
    'no_result_payload': '收到了结束事件，但未记录到单独的结果正文或错误内容',
}


def _params(event):
    payload = event.get('payload') or {}
    value = payload.get('params')
    return value if isinstance(value, dict) else {}


def _key(value):
    return tuple(value.get(k) for k in ('episode_id', 'thread_id', 'turn_id', 'item_id'))


def _ref(event):
    return {k: event.get(k) for k in ('event_id', 'seq', 'at', 'method', 'episode_id',
                                     'thread_id', 'turn_id', 'item_id', 'direction', 'request_id')}


def _refs(events):
    return [_ref(e) for e in events]


def _snapshot(events, cutoff=None):
    """Reconstruct only text known by cutoff; never borrow a later final message."""
    value, used, complete = {}, [], False
    for event in events:
        if cutoff is not None and event['seq'] >= cutoff:
            break
        if complete or event['direction'] != 'incoming':
            continue
        p, method = _params(event), event['method']
        item = p.get('item')
        if isinstance(item, dict):
            value.update(item)
            # Do not retain mutable references to the source snapshot.
            if isinstance(value.get('summary'), list):
                value['summary'] = list(value['summary'])
            complete = method == 'item/completed'
        elif method == 'item/reasoning/summaryTextDelta':
            index = p.get('summaryIndex', 0)
            if not isinstance(index, int) or not 0 <= index < 10000:
                continue
            value['type'] = 'reasoning'
            summary = value.setdefault('summary', [])
            while len(summary) <= index:
                summary.append('')
            summary[index] += p.get('delta', '')
        elif method == 'item/agentMessage/delta':
            value['type'] = 'agentMessage'
            value['text'] = value.get('text', '') + p.get('delta', '')
        elif method == 'item/commandExecution/outputDelta':
            value['aggregatedOutput'] = value.get('aggregatedOutput', '') + p.get('delta', '')
        else:
            continue
        used.append(event)
    return value, used, complete


def _groups(session):
    groups = {}
    for item in session['items']:
        groups[_key(item)] = {'index': item, 'events': []}
    server_requests = {}
    for event in sorted(session['events'], key=lambda e:e['seq']):
        key = _key(event) if event.get('item_id') else None
        if event['direction'] == 'incoming' and event['method'] == 'item/tool/call':
            call_id = _params(event).get('callId')
            if not key and call_id:
                key = (*_key(event)[:3], call_id)
            server_requests[event.get('request_id')] = key
        elif event['direction'] == 'incoming' and event.get('request_id') is not None and event['method'] != 'rpc/response':
            server_requests.pop(event['request_id'],None)
        elif not key and event['direction'] == 'outgoing' and event['method'] == 'rpc/response':
            origin = server_requests.get(event.get('request_id'))
            if origin and origin[:3] == _key(event)[:3]:
                key = origin
        if key:
            groups.setdefault(key, {'index': None, 'events': []})['events'].append(event)
    calls, explanations = [], []
    for key, group in groups.items():
        events = sorted(group['events'], key=lambda e:e['seq'])
        value, _, _ = _snapshot(events)
        value = value or (group['index'] or {}).get('payload', {})
        kind = value.get('type')
        if kind in ('reasoning', 'agentMessage'):
            explanations.append((key, events))
        # Some protocol versions expose dynamic calls only as client requests.
        requests = [e for e in events if e['direction'] == 'incoming' and e['method'] == 'item/tool/call']
        if kind not in CALL_FIELDS and not requests:
            continue
        starts = [e for e in events if e['direction'] == 'incoming' and e['method'] == 'item/started']
        ends = [e for e in events if e['direction'] == 'incoming' and e['method'] == 'item/completed']
        fallback = kind not in CALL_FIELDS or bool(kind == 'dynamicToolCall' and not ends and requests)
        if fallback:
            p = _params(requests[0])
            value = {'type':'dynamicToolCall', **{k:p[k] for k in ('tool','arguments','namespace') if k in p}}
            replies = [e for e in events if e['direction'] == 'outgoing' and e['method'] == 'rpc/response'
                       and e.get('request_id') == requests[0].get('request_id')]
            if replies:
                response = replies[0]['payload']
                result = response.get('result')
                if isinstance(result,dict):
                    value.update({k:result[k] for k in ('contentItems','success') if k in result})
                if response.get('error'):
                    value['status'] = 'failed'
                    value['error'] = response['error']
            starts, ends = requests[:1], replies[:1]
        if ends:
            starts = [e for e in starts if e['seq'] < ends[0]['seq']]
        first = events[0]['seq'] if events else group['index']['first_seq']
        calls.append({'key':key, 'events':events, 'value':value, 'fallback':fallback,
                      'identity_kind':'item_id' if group['index'] or any(e.get('item_id') for e in events) else 'call_id',
                      'start':starts[0] if starts else None, 'end':ends[0] if ends else None,
                      'first_seq':first})
    return sorted(calls, key=lambda c:c['first_seq']), explanations


def _scope(call):
    return call['key'][:3]


def _complete_scope(call):
    return all(_scope(call))


def _returned_observations(call):
    """Conservative adapter for the current CUA result format.

    Inspect returned blocks, not arbitrary code keywords or tool descriptions.
    Unknown formats stay in execution_result. A screenshot is identified only
    for a direct screenshot invocation with a returned saved image; arbitrary
    emitted/generated images are not evidence of a screen capture.
    """
    value, end = call['value'], call['end']
    if not end or value.get('type') != 'mcpToolCall' or value.get('server') != 'cua_repl' or value.get('tool') != 'js':
        return []
    result = value.get('result')
    if not isinstance(result, dict):
        return []
    metadata = result.get('_meta')
    surface = metadata.get('codex/toolSurface') if isinstance(metadata, dict) else None
    if not isinstance(surface, dict) or surface.get('kind') != 'computerUse':
        return []
    arguments = value.get('arguments')
    code = arguments.get('code') if isinstance(arguments, dict) else None
    direct_screenshot = isinstance(code, str) and re.fullmatch(
        r'\s*await\s+[A-Za-z_$][\w$]*\.(?:getScreenshot|getAXStateAndScreenshot)\(\s*\)\s*;?\s*', code)
    content = result.get('content')
    if not isinstance(content, list):
        return []
    entries = []
    for index, block in enumerate(content):
        if not isinstance(block, dict):
            continue
        kind, basis = None, None
        text = block.get('text')
        if block.get('type') == 'text' and isinstance(text, str):
            # These are actual CUA output envelopes. Documentation, arbitrary
            # plain text, and a matching string inside a larger block don't qualify.
            if re.match(r'^Window: "[^\n]*", App: [^\n]+\.\n\d+ ', text):
                kind = 'accessibility_tree'
            elif text.startswith('The following is a diff from the previous accessibility tree for Window: '):
                kind = 'accessibility_diff'
            elif text.startswith('There has been no change in the accessibility tree for Window: '):
                kind = 'accessibility_unchanged'
            if kind:
                basis = 'cua_computer_use_state_response'
        elif (block.get('type') == 'image' and direct_screenshot
              and block.get('availability') == 'saved' and block.get('attachment')):
            kind, basis = 'screenshot', 'direct_cua_screenshot_with_returned_image'
        if kind:
            reasons = ['intra_call_order_unknown']
            if _failed(call): reasons.append('tool_failure_or_partial_result')
            if end.get('quality'): reasons.append('source_quality_flags')
            entries.append({'source':'same_call_result', 'kind':kind, 'basis':basis,
                'source_item_id':call['key'][3] if call['identity_kind']=='item_id' else None,
                'source_call_id':call['key'][3], 'position':'within_call_unlocated',
                'result_path':['result','content',index], 'content':block,
                'event_refs':[_ref(end)], 'reasons':reasons})
    return entries


def _failed(call):
    value = call['value']
    result = value.get('result')
    return (value.get('status') in ('failed', 'declined', 'cancelled') or value.get('error')
            or value.get('success') is False or value.get('exitCode') not in (None, 0)
            or isinstance(result, dict) and result.get('isError'))


def _control(event):
    item = _params(event).get('item')
    return (event['method'] in ('turn/steer', 'turn/interrupt', 'control', 'answer_input', 'input')
            or isinstance(item,dict) and item.get('type') == 'userMessage')


def _slot(entries, reason, *, absent='missing'):
    return {'availability':'recorded' if entries else absent, 'entries':entries,
            'reasons':[] if entries else [reason]}


def build_step_view(session, episode_id):
    """Project existing records; no model, tools, file reads, or source mutations."""
    events = sorted(session['events'], key=lambda e:e['seq'])
    calls, explanations = _groups(session)
    selected = [c for c in calls if c['key'][0] == episode_id]
    meta = session['session']
    incomplete = bool(meta.get('recordingError') or meta.get('recordingDroppedEvents') or
                      events and events[0]['seq'] != 1 or
                      any(b['seq'] != a['seq'] + 1 for a,b in zip(events, events[1:])))

    steps = []
    for call in selected:
        value, start, end = call['value'], call['start'], call['end']
        anchor = start['seq'] if start else call['first_seq']
        same = [c for c in selected if _scope(c) == _scope(call)] if _complete_scope(call) else []
        overlapping = [c for c in calls if c is not call
                       and c['first_seq'] < (end['seq'] if end else float('inf'))
                       and (c['end']['seq'] if c['end'] else float('inf')) > anchor]
        warnings = []
        if incomplete: warnings.append('recording_incomplete')
        if not start: warnings.append('start_event_missing')
        if not end: warnings.append('completion_event_missing')
        if not _complete_scope(call): warnings.append('scope_missing')
        if overlapping: warnings.append('overlapping_calls')
        if not call['events']: warnings.append('indexed_item_without_events')
        if end and any(e['seq'] > end['seq'] for e in call['events']): warnings.append('late_item_events')
        if call['fallback']: warnings.append('request_lifecycle_only')
        if any(e.get('quality') for e in call['events']): warnings.append('source_quality_flags')
        if value['type'] in ('mcpToolCall','dynamicToolCall') and value.get('arguments') is None:
            warnings.append('call_arguments_missing')

        observations, reasoning = _returned_observations(call), []
        prior = [c for c in same if c is not call and c['end'] and c['end']['seq'] < anchor]
        if start and same:
            floor = max((c['end']['seq'] for c in prior), default=0)
            for key, explanation_events in explanations:
                if key[:3] != _scope(call):
                    continue
                text, used, complete = _snapshot(explanation_events, anchor)
                if not used or used[-1]['seq'] <= floor or text.get('phase') == 'final_answer':
                    continue
                content = (text.get('summary') if text.get('type') == 'reasoning' else text.get('text'))
                if not content:
                    continue
                reasons = ['not_action_intent_verified']
                if not complete: reasons.append('partial_explanation')
                if any(used[-1]['seq'] < e['seq'] < anchor and _control(e) for e in events):
                    reasons.append('intervening_user_control')
                reasoning.append({'source':text['type'], 'content':content,
                                  'event_refs':_refs(used), 'reasons':reasons})
        for entry in observations + reasoning:
            entry['reasons'] = list(dict.fromkeys(entry['reasons'] +
                [w for w in warnings if w in ('recording_incomplete','overlapping_calls','scope_missing')]))

        missing_reasoning = 'start_event_missing' if not start else 'scope_missing' if not same else 'no_prior_explanation'
        raw_id = '\0'.join(str(v) for v in (meta.get('session_id'), *call['key']))
        step_id = hashlib.sha256(raw_id.encode()).hexdigest()[:24]
        fields = RESULT_FIELDS[value['type']] + ('error',)
        result_data = {k:value[k] for k in fields if k in value and value[k] is not None}
        result_reasons = [] if end else ['completion_event_missing']
        if end and not any(k != 'durationMs' for k in result_data):
            result_reasons.append('no_result_payload')
        if end and call['fallback']:
            result_reasons.append('client_reply_attempt')
        if end and end.get('quality'):
            result_reasons.append('source_quality_flags')
        call_evidence = [e for e in call['events'] if e['direction']=='incoming'
                         and e['method'] in ('item/started','item/completed','item/tool/call')
                         and (not end or e['seq'] <= end['seq'])]
        steps.append({'step_id':step_id, 'ordinal':len(steps)+1, 'granularity':'tool_call',
            'episode_id':episode_id, 'thread_id':call['key'][1], 'turn_id':call['key'][2],
            'item_id':call['key'][3] if call['identity_kind']=='item_id' else None,
            'call_id':call['key'][3], 'identity_kind':call['identity_kind'], 'first_seq':call['first_seq'],
            'started_at':start['at'] if start else None, 'ended_at':end['at'] if end else None,
            'preceding_agent_messages':_slot(reasoning, missing_reasoning),
            'tool_call':{'availability':'recorded' if call['events'] else 'unverified',
                'type':value['type'], 'data':{k:value[k] for k in CALL_FIELDS[value['type']] if k in value},
                'event_refs':_refs(call_evidence),
                'reasons':['call_arguments_missing'] if 'call_arguments_missing' in warnings else []},
            'execution_result':{'availability':'recorded' if end else 'missing',
                'reported_status':value.get('status'), 'completion_received':bool(end and not call['fallback']),
                'basis':'client_reply_attempt' if end and call['fallback'] else 'item_completion' if end else 'not_observed',
                'data':result_data,
                'event_refs':_refs([end] if end else []),
                'reasons':result_reasons},
            'recorded_observations':_slot(observations,
                'completion_event_missing' if not end else 'no_explicit_observation', absent='not_identified'),
            'source_event_refs':_refs(call['events']), 'warnings':warnings,
            'goal_verification':'unknown'})
    recognized = {e['event_id'] for c in selected for e in c['events']}
    unassigned = [e for e in events if e.get('episode_id') == episode_id and e['direction']=='incoming'
                  and (e['method'] == 'item/tool/call' or isinstance(_params(e).get('item'),dict)
                       and _params(e)['item'].get('type') not in ('reasoning','agentMessage','plan','userMessage'))
                  and e['event_id'] not in recognized]
    return {'version':VIEW_VERSION, 'method':'deterministic_event_projection',
            'session_id':meta.get('session_id'), 'episode_id':episode_id,
            'source_last_seq':events[-1]['seq'] if events else None,
            'granularity':'tool_call', 'observation_policy':'explicit_returns_in_own_call_only',
            'model_called':False, 'screenshots_captured':False, 'steps':steps,
            'warnings':(['recording_incomplete'] if incomplete else []) + (['unassigned_evidence'] if unassigned else []),
            'unassigned_event_refs':_refs(unassigned)}
