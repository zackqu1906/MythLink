"""Extraction-only projection, with local provenance separate from model text.

This is deliberately syntax-based, not a relevance classifier. It never guesses
which search result matters. Calls, errors, meaningful AX labels/values, unknown
payloads and user corrections survive. Only empty layout nodes and known AX
capability decorations are omitted. Numeric targets are kept even when empty.
Observations stay inside their actual result; references do not invent a state
before/after another call. Raw evidence and v1 selection remain unchanged.
"""
from __future__ import annotations
import copy
import re
from collections import Counter
from proximic_ring.agent_workflow.extraction.workflow_payloads import svg_result
from proximic_ring.agent_workflow.recording.task_profile import model_profile

VERSION = 'task-trace-v2'
NODE = re.compile(r'^([+~]?\s*)(\d+) (.*)$')
EMPTY_LAYOUT = re.compile(r'^(?:container|group|split group|scroll area|unknown|image)$')
CAPABILITY = re.compile(r', Secondary Actions: (?:Raise|Cancel|Make checklist item)(?=, (?:Value|ID|URL|Description):|$)')


def trim_ax(text, targets):
    """Keep exact line numbers for every omission; no substring budgets.

Only recognized CUA AX envelopes are processed. Diff deletion ranges, focused
elements, all nonempty labels/values and unknown lines are preserved. Removing
an empty layout-only line does not reparent or renumber surviving AX nodes.
"""
    if not isinstance(text,str) or not (text.startswith('Window: "') or
            text.startswith('The following is a diff from the previous accessibility tree for Window: ')):
        return text,[]
    output=[];changes=[]
    for n,line in enumerate(text.splitlines(keepends=True),1):
        end='\n' if line.endswith('\n') else ''
        body=line[:-1] if end else line
        m=NODE.match(body)
        if m and int(m[2]) not in targets and EMPTY_LAYOUT.fullmatch(m[3]):
            changes.append(dict(line=n,rule='empty_ax_layout'));continue
        new=CAPABILITY.sub('',body)
        if new!=body:changes.append(dict(line=n,rule='unused_ax_capability_listing'))
        output.append(new+end)
    return ''.join(output),changes


def project_trace(packet):
    """Return small chronological records and an exact local source map.

Evidence IDs remain aliases into the untouched input snapshot. A result block
and its observation share a single body ONLY when their content is exactly
equal and the observation belongs to the same call. Unknown result layouts are
kept whole. Returned reasoning and plan messages are not operational evidence;
the model gets actual calls, user messages and the final response instead.
"""
    entries=packet.get('evidence',[])
    records=[];sources={};changes=[];visible=set()
    targets=set()
    for e in entries:
        if e['kind']=='call':
            # Conservative union across calls: extra retained nodes are harmless.
            # This is only used to prevent omission, never to infer execution.
            content=str(e.get('content',''))
            targets.update(int(n) for n in re.findall(r'\.(?:click|setValue|select|performAction)\(\s*(\d+)',content))
    observations={}
    calls={e.get('step_id'):e for e in entries if e['kind']=='call'}
    for e in entries:
        sources[e['id']]={k:copy.deepcopy(v) for k,v in e.items() if k!='content'}
        if e['kind']=='observation':observations.setdefault(e.get('step_id'),[]).append(e)

    def seq(e):
        return min((r['seq'] for r in e.get('event_refs',[]) if type(r.get('seq')) is int),default=0)

    def append(e,kind,value,extra=()):
        ids=[e['id'],*extra];visible.update(ids)
        record=dict(sources=ids,kind=kind,seq=seq(e),content=value)
        if e.get('step_id'):record['call']=e['step_id']
        if e.get('truncated'):record['truncated_in_source']=True
        records.append(record)

    for e in entries:
        kind=e['kind'];c=e.get('content')
        if kind=='call':
            append(e,kind,copy.deepcopy(c.get('data',c)) if isinstance(c,dict) else c)
            if isinstance(c,dict) and c.get('type'):records[-1]['tool_type']=c['type']
        elif kind=='result':
            asset=svg_result(e,calls.get(e.get('step_id'),{}))
            if asset is not None:
                c=asset
                changes.append(dict(rule='opaque_svg_source',evidence_ids=[e['id']],content_path=['data','aggregatedOutput']))
            if not isinstance(c,dict) or 'data' not in c:append(e,kind,copy.deepcopy(c));continue
            data=copy.deepcopy(c.get('data',{}))
            # duration is diagnostics; error/status/result/unknown keys survive.
            if isinstance(data,dict):data.pop('durationMs',None)
            merged=[]
            result=data.get('result') if isinstance(data,dict) else None
            if isinstance(result,dict) and isinstance(result.get('content'),list):
                for i,block in enumerate(result['content']):
                    matches=[o for o in observations.get(e.get('step_id'),[])
                             if isinstance(o.get('content'),dict) and
                             o['content'].get('content')==block and
                             o.get('result_path')==['result','content',i] and not o.get('truncated')]
                    for o in matches:
                        merged.append(o['id'])
                    if matches:
                        if block.get('type')=='text':
                            body,edits=trim_ax(block.get('text'),targets)
                            if edits:
                                block['text']=body
                                changes.append(dict(rule='ax_projection',evidence_ids=[e['id']]+[o['id'] for o in matches],
                                                    content_path=['data','result','content',i,'text'],edits=edits))
                        # One body, but the model can still cite its actual observation.
                        result['content'][i]={'observation_sources':[o['id'] for o in matches],
                                             'observation_kind':matches[0]['content'].get('kind'),
                                             'content':block}
                        changes.append(dict(rule='own_observation_in_result',evidence_ids=[e['id']]+[o['id'] for o in matches]))
            append(e,kind,dict(status=c.get('reported_status'),availability=c.get('availability'),
                reasons=c.get('reasons',[]),data=data,
                **({'unknown_fields':{k:v for k,v in c.items() if k not in ('data','availability','reported_status','completion_received','basis','reasons')}}
                   if set(c)-{'data','availability','reported_status','completion_received','basis','reasons'} else {})),merged)
        elif kind=='observation':
            # Process after results below, to avoid any dependency on E ordering.
            continue
        elif kind=='message':
            if not isinstance(c,dict):append(e,kind,c);continue
            role=c.get('type')
            if role=='userMessage':append(e,'user',copy.deepcopy(c.get('content',c)))
            elif role=='agentMessage' and c.get('phase')=='final_answer':
                append(e,'reported_final',dict(text=c.get('text',''),unverified=True))
            elif role in ('reasoning','plan') or role=='agentMessage' and c.get('phase')=='commentary':
                changes.append(dict(rule='non_operational_agent_message',evidence_ids=[e['id']],message_type=role))
            else:append(e,kind,copy.deepcopy(c))
        elif kind=='control_event':
            methods={r.get('method') for r in e.get('event_refs',[])}
            method=next(iter(methods)) if len(methods)==1 else None
            if method in ('input','answer_input') and isinstance(c,dict):
                # Keep all answer payloads except known invocation bookkeeping.
                drop={'context','episodeId','mode','targetTurn','source','requestId'}
                append(e,'user', {k:copy.deepcopy(v) for k,v in c.items() if k not in drop})
            else:append(e,method or kind,copy.deepcopy(c))
        else:append(e,kind,copy.deepcopy(c))
    for e in entries:
        if e['kind']=='observation' and e['id'] not in visible:append(e,'observation',copy.deepcopy(e['content']))
    records.sort(key=lambda r:r['seq'])
    # Routine allows have no operational effect; v1 already audited every pair.
    episode=packet.get('source',{}).get('episode',{})
    model=dict(trace_format=VERSION,task=episode.get('goal',''),task_status=episode.get('status'),
        recording_quality=copy.deepcopy(packet.get('recording_quality',{})),
        warnings=copy.deepcopy(packet.get('warnings',[])),records=records)
    if packet.get('source',{}).get('execution_profile'):
        model['execution_profile']=model_profile(packet['source']['execution_profile'])
    manifest=dict(version=VERSION,sources=sources,changes=changes,visible_evidence_ids=sorted(visible),
        record_counts=dict(Counter(r['kind'] for r in records)),
        policy='No inferred state; no image pixels; unknown payloads retained; original snapshot unchanged')
    return model,manifest
