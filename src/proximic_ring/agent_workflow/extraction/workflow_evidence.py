"""Bounded evidence packets and structural/provenance validation (no inference)."""
from __future__ import annotations

import hashlib
import json
import re

from proximic_ring.agent_workflow.recording.records import episode_detail
from proximic_ring.agent_workflow.extraction.workflow_prompt import PROMPT_VERSION
from proximic_ring.agent_workflow.extraction.workflow_compaction import ensure_input_budget
from proximic_ring.agent_workflow.extraction.workflow_input import prepare_model_input
from proximic_ring.agent_workflow.extraction.workflow_limits import MAX_INPUT_CHARS

MAX_ENTRY_CHARS = 40_000


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def prepare_evidence(session, episode_id):
    packet = build_evidence(session, episode_id)
    # Apply the existing ceiling to what the model actually receives, not to
    # the larger local audit snapshot. Raw records and refs remain unchanged.
    prepared = prepare_model_input(packet)
    ensure_input_budget(prepared.stats, MAX_INPUT_CHARS)
    return packet


def build_evidence(session, episode_id):
    """Build the audit snapshot; no budget check or remote call (also for dry runs)."""
    detail = episode_detail(session, episode_id)
    evidence = []

    def add(kind, content, refs, *, step=None, path=None):
        raw = encoded(content)
        truncated = len(raw) > MAX_ENTRY_CHARS
        evidence.append(dict(id=f'E{len(evidence)+1}', kind=kind,
            content=(raw[:MAX_ENTRY_CHARS//2] + '\n[中间省略]\n' + raw[-MAX_ENTRY_CHARS//2:]) if truncated else content,
            truncated=truncated, original_chars=len(raw), event_refs=refs,
            step_id=step['step_id'] if step else None,
            call_id=step['call_id'] if step else None, result_path=path))

    for step in detail['step_view']['steps']:
        add('call', step['tool_call'], step['tool_call']['event_refs'], step=step)
        # Keep results even when they are not recognizable observations.
        result = {k:v for k,v in step['execution_result'].items() if k != 'event_refs'}
        add('result', result, step['execution_result']['event_refs'], step=step)
        for entry in step['recorded_observations']['entries']:
            add('observation', {k:v for k,v in entry.items() if k not in ('event_refs','source_call_id')},
                entry['event_refs'], step=step, path=entry.get('result_path'))
    # Include returned summaries, final messages, plans, and explicit user clarifications.
    for item in detail['items']:
        if item['type'] in ('agentMessage', 'reasoning', 'plan', 'userMessage'):
            refs = [{k:e[k] for k in ('seq','event_id','item_id','method')} for e in detail['events']
                    if e.get('item_id') == item['item_id'] and e['method'] == 'item/completed']
            if not refs:
                refs = [{k:e[k] for k in ('seq','event_id','item_id','method')} for e in detail['events']
                        if e.get('item_id') == item['item_id']]
            add('message', item['payload'], refs)
    for event in detail['events']:
        local_control = event['direction']=='local' and event['method'] in (
            'input','answer_input','option_selected','control','request_decision','app_granted',
            'automatic_approval','stale_answer_ignored','stale_choice_ignored','steer_not_sent')
        rpc_control = event['direction']=='outgoing' and event['method'] in ('turn/steer','turn/interrupt')
        terminal = event['direction']=='incoming' and event['method'] in ('turn/completed','error')
        if local_control or rpc_control or terminal:
            add('control_event', event['payload'], [{k:event[k] for k in ('seq','event_id','method')}])
    packet = dict(schema_version=1, prompt_version=PROMPT_VERSION,
        source=dict(session_id=detail['session']['session_id'], episode_id=episode_id,
                    episode=detail['episode'], step_view_version=detail['step_view']['version'],
                    execution_profile=detail['execution_profile']),
        policy=dict(observations='own_call_only; no inferred before/after', images='metadata_only',
                    goal_verification='unknown', support_episode_count=1),
        recording_quality={k:detail['session'].get(k) for k in ('recordingError','recordingDroppedEvents')},
        warnings=detail['step_view']['warnings'], evidence=evidence)
    # Token-usage/diagnostic events may update the episode timestamp after it
    # ends without changing any supplied evidence. Do not pay to extract again.
    fingerprint_source = {**packet['source'], 'episode':{
        k:v for k,v in packet['source']['episode'].items() if k!='updated_at'}}
    packet['fingerprint'] = hashlib.sha256(encoded({**packet,'source':fingerprint_source}).encode()).hexdigest()
    packet['fingerprint_version'] = 2
    return packet


def validate_workflows(value, packet):
    """Validate shape and reference membership, not the truth of model interpretations."""
    if isinstance(value, dict) and ('notes' in value or value.get('format') == 'agent-workflow-v7'):
        from proximic_ring.agent_workflow.extraction.workflow_simple_contract import validate
        return validate(value, packet)
    if isinstance(value,dict) and 'format' in value:
        from proximic_ring.agent_workflow.extraction.workflow_contract import validate
        return validate(value,packet)
    registry = {e['id']:e for e in packet['evidence']}

    def obj(v, keys):
        if not isinstance(v, dict) or set(v) != set(keys.split()):
            raise ValueError('Workflow JSON 字段不符合约定；此对象需包含：'+keys)

    def string(v, allow_empty=False):
        if not isinstance(v, str) or len(v) > 6000 or (not allow_empty and not v.strip()):
            raise ValueError('Workflow 文本字段不合法')

    def array(v, limit=100):
        if not isinstance(v, list) or len(v) > limit:
            raise ValueError('Workflow 列表不合法')

    def strings(v):
        array(v)
        for s in v: string(s)

    def refs(v, kind=None):
        array(v)
        if not v or any(not isinstance(r,str) or r not in registry for r in v):
            raise ValueError('Workflow 引用了不存在或为空的证据')
        if kind == 'call' and not any(registry[r]['kind']=='call' for r in v):
            raise ValueError('行动步骤缺少真实工具调用引用')
        if kind == 'observation' and any(registry[r]['kind']!='observation' for r in v):
            raise ValueError('观察引用必须来自本次调用的明确观察')

    obj(value, 'workflows extraction_notes'); strings(value['extraction_notes']); array(value['workflows'], 8)
    for workflow in value['workflows']:
        obj(workflow, 'name intent parameters preconditions steps recovery_notes outcome limitations')
        string(workflow['name']); string(workflow['intent'])
        strings(workflow['preconditions']); strings(workflow['limitations'])
        array(workflow['parameters'], 30); names = set()
        for p in workflow['parameters']:
            obj(p, 'name description example evidence_ids')
            string(p['name']); string(p['description']); string(p['example'], True); refs(p['evidence_ids'])
            if not re.fullmatch(r'[a-z][a-z0-9_]*', p['name']) or p['name'] in names:
                raise ValueError('Workflow 参数名称非法或重复')
            names.add(p['name'])
        array(workflow['steps'], 60)
        if len(workflow['steps']) < 2: raise ValueError('Workflow 至少需要两个步骤')
        for step in workflow['steps']:
            obj(step, 'title action method target evidence_ids observations expected_outcome uncertainties')
            for k in ('title','action','method','target','expected_outcome'): string(step[k])
            if len(step['action']) > 240:
                raise ValueError('自然语言动作过长；具体工具和操作细节应放入 method')
            if re.search(r'(?:索引|控件编号)\s*[:：=]?\s*\d+|\bclick\(\s*\d+',step['action']+'\n'+step['method']):
                raise ValueError('执行步骤不能包含历史控件数字索引；请使用当前界面的语义定位')
            refs(step['evidence_ids'], 'call'); strings(step['uncertainties']); array(step['observations'])
            call_steps = {registry[r]['step_id'] for r in step['evidence_ids'] if registry[r]['kind']=='call'}
            for observation in step['observations']:
                obj(observation, 'summary evidence_ids'); string(observation['summary'])
                refs(observation['evidence_ids'], 'observation')
                if any(registry[r]['step_id'] not in call_steps for r in observation['evidence_ids']):
                    raise ValueError('观察不能借用其他调用的结果')
        array(workflow['recovery_notes'])
        for recovery in workflow['recovery_notes']:
            obj(recovery, 'condition action evidence_ids')
            string(recovery['condition']); string(recovery['action']); refs(recovery['evidence_ids'])
        obj(workflow['outcome'], 'summary evidence_ids')
        string(workflow['outcome']['summary']); refs(workflow['outcome']['evidence_ids'])
        used = set(re.findall(r'\{\{([a-z][a-z0-9_]*)\}\}', encoded({k:v for k,v in workflow.items() if k!='parameters'})))
        if used - names: raise ValueError('Workflow 使用了未声明的参数')
        if names - used: raise ValueError('Workflow 声明了没有使用的参数')
    if not value['workflows'] and not value['extraction_notes']:
        raise ValueError('空提取结果必须说明原因')
    return value
