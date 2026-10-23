"""v7 structural/reference validation; no claim of semantic or execution proof."""
import re
from proximic_ring.agent_workflow.workflow_content import FORMAT, FIELDS, validate_plain


def validate(value, packet):
    from proximic_ring.agent_workflow.extraction.workflow_input import prepare_model_input
    allowed = {'workflows', 'notes'}
    if not isinstance(value, dict) or set(value) not in (allowed, allowed | {'format'}):
        raise ValueError('简洁提取需要 workflows 和 notes')
    if 'format' in value and value['format'] != FORMAT:
        raise ValueError('简洁提取版本错误')
    flows, notes = value['workflows'], value['notes']
    if not isinstance(flows, list) or len(flows) > 8:
        raise ValueError('Workflow 列表无效')
    if not isinstance(notes, list) or any(not isinstance(n, str) or not n.strip() for n in notes):
        raise ValueError('提取说明无效')
    visible = set(prepare_model_input(packet).selection['trace']['visible_evidence_ids'])
    registry = {e['id']: e for e in packet['evidence']}
    for flow in flows:
        if not isinstance(flow, dict) or set(flow) != FIELDS | {'sources'}:
            raise ValueError('Workflow 字段不符合简洁格式')
        validate_plain({k: flow[k] for k in FIELDS})
        ids = flow['sources']
        if (not isinstance(ids, list) or not ids or len(ids) > 100
                or any(not isinstance(i, str) or i not in visible or i not in registry for i in ids)):
            raise ValueError('Workflow 引用了未提供的证据')
        def observed(e):
            c = e.get('content')
            return e['kind'] == 'call' or (e['kind'] == 'message' and isinstance(c, dict)
                and c.get('type') == 'agentMessage' and c.get('phase') in (None, 'final_answer')
                and isinstance(c.get('text'), str) and c['text'].strip()
                and any(r.get('method') == 'item/completed' for r in e.get('event_refs', [])))
        if not any(observed(registry[i]) for i in ids):
            raise ValueError('Workflow 缺少实际操作或最终回答来源')
        if any(registry[i]['kind'] == 'call' and 'cua' in str(registry[i].get('content', {})).lower() for i in ids):
            if any(re.search(r'(?:索引|控件编号)\s*[:：=]?\s*\d+|\bclick\(\s*\d+', s) for s in flow['steps']):
                raise ValueError('执行步骤不能包含历史控件数字索引')
    if not flows and not notes:
        raise ValueError('空提取结果必须说明原因')
    return value
