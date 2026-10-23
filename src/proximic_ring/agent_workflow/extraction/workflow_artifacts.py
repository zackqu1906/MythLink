"""Small reusable workflows, separate from extraction and provenance records."""
from __future__ import annotations
from proximic_ring.agent_workflow.task_metadata import COMPACT_FORMATS, compact_profile, profile_text

import json
import re

from proximic_ring.agent_workflow.extraction.workflow_store import atomic_json


def compact_workflows(result):
    """Project validated model output without new inference or model calls."""
    if result.get('format') == 'agent-workflow-v7' or ('notes' in result and 'format' not in result):
        from proximic_ring.agent_workflow.workflow_content import plain_flow
        return dict(schema_version=6, workflows=[plain_flow(w) for w in result['workflows']])
    if result.get('format') in COMPACT_FORMATS:
        return dict(schema_version=5 if result.get('format')=='agent-workflow-v6' else 4,workflows=[dict(name=w['name'],goal=w['goal'],
            **({'task_profile':compact_profile(w['task_profile'])} if 'task_profile' in w else {}),
            parameters=[dict(name=p['name'],description=p['description']) for p in w['parameters']],
            steps=[dict(action=s['instruction'],**({'check':s['check']} if 'check' in s else {})) for s in w['steps']],
            completion=w['completion']['check'],conditions=list(w['conditions']),
            fallbacks=[dict(when=f['when'],action=f['instruction']) for f in w['fallbacks']]) for w in result['workflows']])
    modern = result.get('schema_version', 1) >= 2 or any(
        'method' in s for w in result['workflows'] for s in w['steps'])
    workflows=[]
    for w in result['workflows']:
        flow=dict(name=w['name'], goal=w['intent'],
            steps=[(dict(action=s['action'], method=s['method'], check=s['expected_outcome']) if modern else
                    dict(action=s['action'], target=s['target'], check=s['expected_outcome'])) for s in w['steps']],
            conditions=list(w['preconditions']),
            fallbacks=[dict(when=r['condition'],action=r['action']) for r in w['recovery_notes']])
        used=set(re.findall(r'\{\{([a-z][a-z0-9_]*)\}\}',json.dumps(flow,ensure_ascii=False)))
        # An instance mentioned only in audit/outcome text is not a flow input.
        parameters=[{k:p[k] for k in ('name','description')} for p in w['parameters'] if p['name'] in used]
        workflows.append(dict(name=flow.pop('name'),goal=flow.pop('goal'),parameters=parameters,**flow))
    return {'schema_version':3 if modern else 2,'workflows':workflows}


def workflow_text(flow):
    """Render readable steps without inference, parameter rewriting or tool use."""
    lines = [flow['name'], '目标：' + flow['goal']]
    if flow.get('task_profile'):lines.append(profile_text(flow['task_profile']))
    if flow['parameters']:
        lines += ['参数：'] + [f"- {p['name']}：{p['description']}" for p in flow['parameters']]
    lines.append(workflow_steps(flow))
    return '\n'.join(lines)


def workflow_steps(flow):
    """The model-authored steps, without a second goal or generic guidance."""
    lines = ['操作步骤：']
    for n, step in enumerate(flow['steps'], 1):
        if isinstance(step, str):
            lines.append(f'{n}. {step}')
            continue
        if 'completion' in flow:
            lines.append(f"{n}. {step['action']}")
            if step.get('check'):lines.append('   关键判断：'+step['check'])
            continue
        lines.append(f"{n}. 动作：{step['action']}")
        if 'method' in step: lines.append('   执行方法：' + step['method'])
        else: lines.append('   旧版步骤，尚未拆分执行方法；目标：' + step.get('target', '未记录'))
        lines.append('   完成条件：' + step.get('check', '未提供'))
    if flow.get('completion'):lines.append('最终完成判断：'+flow['completion'])
    if flow.get('conditions'): lines += ['使用条件：'] + ['- ' + c for c in flow['conditions']]
    if flow.get('fallbacks'):
        lines += ['异常处理：'] + [f"- 当 {f['when']}：{f['action']}" for f in flow['fallbacks']]
    return '\n'.join(lines)


def save_flow(directory, job_id, result):
    """Idempotently project legacy/new audit records; leave originals intact."""
    filename=job_id+'.flow.json'
    path=directory/filename
    value=compact_workflows(result)
    try: unchanged=json.loads(path.read_text(encoding='utf-8'))==value
    except (OSError,ValueError): unchanged=False
    if not unchanged: atomic_json(path,value)
    from proximic_ring.agent_workflow.recording.records import _write
    _write(directory/(job_id+'.flow.txt'), '\n\n'.join(workflow_text(w) for w in value['workflows']))
    return filename,value


def latest_successful_jobs(jobs):
    """One extraction revision per real task, not one 'use' per model retry.

    No success/usage score is inferred. Completed extractions are still drafts.
    Different workflows within an artifact are identified by list position.
    """
    seen=set()
    for job in sorted(jobs,key=lambda j:j['created_at'],reverse=True):
        if job['status']!='completed' or not job.get('result_file'): continue
        source=(job['session_id'],job['episode_id'])
        if source in seen: continue
        seen.add(source)
        yield job
