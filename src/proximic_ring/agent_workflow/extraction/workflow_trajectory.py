"""Local, conservative selection of extraction evidence; raw snapshots stay intact.

v1 deliberately does NOT guess which AX nodes matter. All action arguments,
observations, result/error payloads, user messages and clarifications survive.
Only verified standalone tool documentation and unambiguous routine automatic
approval pairs are reduced. Unknown formats, mixed docs/results, unmatched
approvals, denials and manual decisions are kept verbatim.

Every removal has E IDs, original event refs, a path and/or a content hash in a
separate selection manifest. The model still sees each action/observation with
its original identity. Condensed permissions never prove an action succeeded.
"""
from __future__ import annotations

import copy
import hashlib
from collections import Counter, defaultdict

from proximic_ring.agent_workflow.extraction.workflow_compaction import encoded

TRAJECTORY_VERSION = 'local-trajectory-v1'

# Verified standalone documentation returned by cua_repl.js on 2026-10-18:
# Computer Use / API / Native apps / Workflow / Output / Notes / confirmation
# policy, with no task-specific state. Match the complete UTF-8 body, not merely
# a heading or keyword. Appending an error, observation, or new text invalidates
# this match and preserves the whole block. New documentation versions must be
# inspected before adding a digest; do not auto-learn hashes from user content.
KNOWN_CUA_DOCUMENTATION = {
    '6270dbaf2c13542aafde21449b529ddd8e7913e1f0b26c1f069bf1cc5fa50e98':
        'Computer Use API and confirmation documentation, verified 2026-10-18',
}
AUTOMATIC_ALLOW = '自动允许（开关已开启）'


def _method(entry):
    methods = {r.get('method') for r in entry.get('event_refs', [])}
    return next(iter(methods)) if len(methods)==1 else None


def _automatic_pairs(entries):
    """Pair explicit allow events by typed request ID, protocol and application.

Only exact known shapes are accepted. IDs reused across epochs produce more
than one candidate and are left untouched. Both events must have real ordered
sequence numbers: automatic_approval first, matching request_decision later.
An extra/manual/denied decision for the same key makes the match ambiguous.
This summarizes permissions only, never removes or merges tool calls.
"""
    candidates = defaultdict(lambda: defaultdict(list))
    for entry in entries:
        if entry.get('kind') != 'control_event' or entry.get('truncated'): continue
        method, content = _method(entry), entry.get('content')
        if method not in ('automatic_approval','request_decision') or not isinstance(content,dict):continue
        if not all(k in content for k in ('requestId','method','app')):continue
        key=encoded([content['requestId'],content['method'],content['app']])
        candidates[key][method].append(entry)
    pairs=[]
    for values in candidates.values():
        approvals,decisions=values['automatic_approval'],values['request_decision']
        if len(approvals)!=1 or len(decisions)!=1:continue
        approval,decision=approvals[0],decisions[0]
        a,d=approval['content'],decision['content']
        if set(a)!={'requestId','method','app','count'}:continue
        fields={'requestId','method','disposition','app','tool','epoch'}
        if set(d)!=fields and not (set(d)==fields|{'paused'} and d['paused'] is False):continue
        if a['method']!='mcpServer/elicitation/request' or d['disposition']!=AUTOMATIC_ALLOW:continue
        if type(a['requestId']) not in (int,str) or a['requestId']=='':continue
        if type(a['count']) is not int or a['count']<1 or type(d['epoch']) is not int:continue
        if not isinstance(a['app'],str) or not a['app'] or not isinstance(d['tool'],str) or not d['tool']:continue
        a_seq=[r.get('seq') for r in approval['event_refs']]
        d_seq=[r.get('seq') for r in decision['event_refs']]
        if not a_seq or not d_seq or any(type(s) is not int for s in a_seq+d_seq):continue
        if max(a_seq)>=min(d_seq):continue
        pairs.append((approval,decision))
    return pairs


def select_trajectory(packet):
    """Return a selected packet and an auditable manifest; no model or file I/O.

Do not overwrite .input.json with this projection. The manifest's hashes/paths
refer to that immutable evidence snapshot, which in turn refers to raw events.
Existing per-entry truncation is not repaired or hidden by selection.
"""
    selected=copy.deepcopy(packet)
    entries=selected.get('evidence',[])
    calls={e.get('step_id'):e for e in entries if e.get('kind')=='call' and e.get('step_id')}
    changes=[]
    for entry in entries:
        if entry.get('kind')!='result' or entry.get('truncated'):continue
        call=calls.get(entry.get('step_id'),{})
        if call.get('truncated'):continue
        call_content=call.get('content')
        if not isinstance(call_content,dict):continue
        data=call_content.get('data',{})
        if not isinstance(data,dict) or data.get('server')!='cua_repl' or data.get('tool')!='js':continue
        content=entry.get('content')
        if not isinstance(content,dict):continue
        result_data=content.get('data')
        if not isinstance(result_data,dict):continue
        result=result_data.get('result')
        if not isinstance(result,dict) or not isinstance(result.get('content'),list):continue
        for i,block in enumerate(result['content']):
            # No recursive text deletion: only standalone MCP text blocks with
            # this exact shape and a reviewed full-body documentation digest.
            if not isinstance(block,dict) or set(block)!={'type','text'} or block['type']!='text':continue
            text=block['text']
            if not isinstance(text,str):continue
            digest=hashlib.sha256(text.encode()).hexdigest()
            if digest not in KNOWN_CUA_DOCUMENTATION:continue
            path=['data','result','content',i,'text']
            changes.append(dict(rule='verified_tool_documentation',evidence_ids=[entry['id']],
                content_path=path,sha256=digest,original_chars=len(text),
                description=KNOWN_CUA_DOCUMENTATION[digest],event_refs=copy.deepcopy(entry['event_refs'])))
            block['text']='[提取时省略已核验版本的通用 Computer Use 工具文档；不是任务观察或结果。原文见本项证据的 content_path 与本地轨迹筛选清单。]'
    pairs=_automatic_pairs(entries)
    omitted=set()
    groups=defaultdict(list)
    for approval,decision in pairs:
        groups[(decision['content']['app'],decision['content']['method'])].append((approval,decision))
        omitted.update((approval['id'],decision['id']))
        changes.append(dict(rule='routine_automatic_approval',
            evidence_ids=[approval['id'],decision['id']],request_id=approval['content']['requestId'],
            event_refs=copy.deepcopy(approval['event_refs']+decision['event_refs'])))
    summaries=[]
    for (app,method),members in groups.items():
        ids=[e['id'] for pair in members for e in pair]
        seqs=[r['seq'] for pair in members for e in pair for r in e['event_refs']]
        summaries.append(dict(app=app,method=method,automatic_allowed_requests=len(members),
            tools=dict(Counter(d['content']['tool'] for _,d in members)),
            evidence_ids=ids,event_seq_range=[min(seqs),max(seqs)],
            interpretation='仅汇总自动许可；不证明调用已执行或目标成功，不能作为界面观察。'))
    selected['evidence']=[e for e in entries if e['id'] not in omitted]
    if changes:
        selected['trajectory_selection']=dict(version=TRAJECTORY_VERSION,
            omitted_documentation_blocks=sum(c['rule']=='verified_tool_documentation' for c in changes),
            routine_control_summaries=summaries,
            observation_policy='all_recorded_observations_preserved; no inferred state or cross-call rebinding')
    source_counts=dict(Counter(e['kind'] for e in packet.get('evidence',[])))
    retained_counts=dict(Counter(e['kind'] for e in selected.get('evidence',[])))
    manifest=dict(version=TRAJECTORY_VERSION,source_fingerprint=packet.get('fingerprint'),changes=changes,
        source_counts=source_counts,retained_counts=retained_counts,
        omitted_documentation_blocks=sum(c['rule']=='verified_tool_documentation' for c in changes),
        condensed_control_events=len(omitted),automatic_allowed_requests=len(pairs),
        additional_observation_truncations=0,extra_model_calls=0,
        source_location='<job>.input.json → original event_refs')
    return selected,manifest
