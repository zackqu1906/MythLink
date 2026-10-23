"""Derive execution facts from an episode snapshot, without semantic inference."""
from collections import Counter

CAPABILITIES = {
    'commandExecution': 'command_execution', 'fileChange': 'file_changes',
    'webSearch': 'web_search', 'imageView': 'image_reading',
    'imageGeneration': 'image_generation', 'mcpToolCall': 'mcp_tool',
    'dynamicToolCall': 'client_tool', 'collabAgentToolCall': 'agent_delegation',
    'sleep': 'waiting',
}


def execution_profile(detail):
    calls = []
    for step in detail['step_view']['steps']:
        call, result = step['tool_call'], step['execution_result']
        data = call.get('data') or {}
        calls.append(dict(step_id=step['step_id'], call_id=step['call_id'],
            tool_type=call['type'], capability=CAPABILITIES.get(call['type'], 'other_tool'),
            tool={k: data[k] for k in ('server', 'namespace', 'tool') if k in data},
            reported_status=result.get('reported_status'),
            completion_received=result['completion_received'], event_refs=step['source_event_refs']))
    items = detail['items']
    responses = [i for i in items if i['type'] == 'agentMessage'
                 and i['payload'].get('phase') in (None, 'final_answer') and i['payload'].get('text')]
    unknown = sorted({i['type'] for i in items if i['type'] not in
                      set(CAPABILITIES) | {'agentMessage', 'reasoning', 'plan', 'userMessage'}})
    session = detail['session']
    warnings = list(detail['step_view']['warnings'])
    if session.get('recordingError') or session.get('recordingDroppedEvents'):
        warnings.append('recording_incomplete')
    if unknown:
        warnings.append('unclassified_item_types')
    return dict(version=1, basis='recorded_events_only',
        mode='tools' if calls else 'response_only' if responses and not warnings else 'unknown',
        tool_call_count=len(calls), tool_type_counts=dict(Counter(c['tool_type'] for c in calls)),
        capabilities=sorted({c['capability'] for c in calls} | ({'text_response'} if responses else set())),
        response_count=len(responses), item_type_counts=dict(Counter(i['type'] for i in items)),
        unknown_item_types=unknown, warnings=list(dict.fromkeys(warnings)), calls=calls,
        interpretation='Tool capabilities are observed facts, not task purposes or proof of success. No inferred reasoning or UI states.')


def model_profile(profile):
    """Small model input; exact event references remain in the local source."""
    return {k: v for k, v in profile.items() if k not in ('calls', 'interpretation')} | {
        'tools': [dict(tool_type=c['tool_type'], **c['tool']) for c in profile['calls']]}
