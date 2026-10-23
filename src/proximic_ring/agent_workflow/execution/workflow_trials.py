"""Manual, paired Workflow trials. No desktop executor and no model calls here."""
from __future__ import annotations

import copy
import hashlib
import html
import json
import re
import sqlite3
import statistics
import time
import uuid
from pathlib import Path

from proximic_ring.agent_workflow.extraction.workflow_artifacts import compact_workflows, latest_successful_jobs, workflow_steps
from proximic_ring.agent_workflow.extraction.workflow_store import atomic_json, atomic_text, now
from proximic_ring.agent_workflow.parameters.parameter_contract import bind_parameters,render_parameters,omission_guidance

PARAMETER = re.compile(r'\{\{([a-z][a-z0-9_]*)\}\}')
MODES = {'baseline': '原任务', 'natural_language': '自然语言步骤'}
HISTORY_MODES = {**MODES, 'workflow': 'JSON Workflow（历史）'}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def available_workflows(root):
    """Read current successful drafts, including legacy results, without exporting."""
    directory = Path(root) / 'workflows'
    db_path = directory / 'queue.sqlite3'
    if not db_path.exists(): return []
    with sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1) as db:
        db.row_factory = sqlite3.Row
        jobs = [dict(row) for row in db.execute('SELECT * FROM jobs ORDER BY created_at DESC')]
    entries = []
    for job in latest_successful_jobs(jobs):
        try:
            path = (directory / job['result_file']).resolve()
            if path.parent != directory.resolve(): continue
            result = json.loads(path.read_text(encoding='utf-8'))
            for index, flow in enumerate(compact_workflows(result)['workflows']):
                entries.append(dict(key=f"{job['id']}:{index}", name=flow['name'], flow=flow,
                    source=dict(job_id=job['id'], workflow_index=index,
                        session_id=job['session_id'], episode_id=job['episode_id'],
                        prompt_version=job['prompt_version'], audit_file=job['result_file'])))
        except (OSError, ValueError, KeyError, TypeError): continue
    return entries


def render(value, parameters):
    if isinstance(value, str):
        # One substitution pass: parameter values are data, not another template.
        return render_parameters(value,parameters)
    if isinstance(value, list): return [render(item, parameters) for item in value]
    if isinstance(value, dict): return {key: render(item, parameters) for key, item in value.items()}
    return value


def make_case(entry, goal, parameters, initial_state):
    """Freeze the two alternative inputs. JSON stays inside the trial record."""
    flow = copy.deepcopy(entry['flow'])
    names = {p['name'] for p in flow['parameters']}
    parameters=bind_parameters(flow,parameters)
    if not goal.strip() or not initial_state.strip():
        raise ValueError('请填写本次任务和两组共用的起始状态。')
    text = entry.get('natural_language_workflow') or workflow_steps(flow)
    if not isinstance(text, str) or not text.strip():
        raise ValueError('自然语言步骤不能为空。')
    if set(PARAMETER.findall(text + goal)) - names:
        raise ValueError('自然语言步骤或任务包含未声明的参数。')
    value = dict(schema_version=5, goal=render(goal.strip(), parameters),
        parameters=dict(parameters), initial_state=initial_state.strip(), workflow=flow,
        source=copy.deepcopy(entry['source']), natural_language_workflow=text,
        renderer_version=entry.get('renderer_version', 'model-steps-1'),
        input_style='direct_goal_or_steps', parameter_binding='reference')
    value['id'] = digest(value)[:32]
    return value


def task_prompt(case, mode):
    if mode not in MODES:
        raise ValueError('仅比较原任务与自然语言步骤；JSON 测试已停用。')
    if case.get('schema_version') != 5:
        raise ValueError('旧测试快照仅供回查，请从当前任务目录或流程重新准备两组测试。')
    if mode == 'baseline': return case['goal']+omission_guidance(case['workflow'],case['parameters'])
    return render(case['natural_language_workflow'], case['parameters'])+omission_guidance(case['workflow'],case['parameters'])


def validate_case(value):
    """Validate a frozen case without re-rendering values or changing its guide."""
    if isinstance(value, dict) and value.get('schema_version') == 5:
        keys = {'schema_version','id','goal','parameters','initial_state','workflow','source',
                'natural_language_workflow','renderer_version','input_style','parameter_binding'}
        if set(value) != keys or value['input_style'] != 'direct_goal_or_steps' or value['parameter_binding'] != 'reference':
            raise ValueError('测试快照格式不正确，请重新准备。')
        if value['id'] != digest({k:v for k,v in value.items() if k!='id'})[:32]:
            raise ValueError('测试快照已改变，请重新准备。')
        if not isinstance(value['goal'],str) or not value['goal'].strip():
            raise ValueError('测试目标无效。')
        if not isinstance(value['natural_language_workflow'],str) or not value['natural_language_workflow'].strip():
            raise ValueError('自然语言步骤不能为空。')
        # The stored goal is already bound; literal braces in values are data.
        make_case(dict(flow=value['workflow'], source=value['source'],
                       natural_language_workflow=value['natural_language_workflow']),
                  '验证参数', value['parameters'], value['initial_state'])
        return copy.deepcopy(value)
    # Read-only compatibility: retain the exact schemas and hashes of old trials.
    keys = {'schema_version','id','goal','parameters','initial_state','workflow','source','execution_guidance'}
    if isinstance(value, dict) and value.get('schema_version') in (2,3,4):
        keys |= {'natural_language_workflow','renderer_version'}
        if value['schema_version'] in (3,4): keys.add('parameter_binding')
        if value['schema_version']==4: keys.add('input_style')
    if not isinstance(value, dict) or set(value) != keys or value['schema_version'] not in (1,2,3,4):
        raise ValueError('测试快照格式不正确，请重新准备。')
    if value['id'] != digest({k:v for k,v in value.items() if k!='id'})[:32]:
        raise ValueError('测试快照已改变，请重新准备。')
    if not isinstance(value['goal'],str) or not value['goal'].strip() or not isinstance(value['execution_guidance'],str):
        raise ValueError('测试目标或执行指南无效。')
    if value['schema_version'] in (2,3,4) and (not isinstance(value['natural_language_workflow'],str)
            or not value['natural_language_workflow'].strip() or not isinstance(value['renderer_version'],str)):
        raise ValueError('自然语言 Workflow 快照无效。')
    if value['schema_version'] in (3,4):
        if value['parameter_binding']!='reference' or set(PARAMETER.findall(value['natural_language_workflow']))-set(value['parameters']):
            raise ValueError('自然语言 Workflow 参数引用无效。')
    if value['schema_version']==4 and value['input_style']!='direct_goal_or_steps':
        raise ValueError('直接步骤实验格式无效。')
    # Goal is already resolved. Literal braces inside a parameter are data.
    make_case(dict(flow=value['workflow'],source=value['source']), '验证参数', value['parameters'], value['initial_state'])
    return copy.deepcopy(value)


class TrialStore:
    def __init__(self, root):
        self.root = Path(root)
        self.directory = self.root / 'workflow-tests'
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def case(self, value):
        path = self.directory / (value['id'] + '.case.json')
        if not path.exists(): atomic_json(path, value)
        if value.get('natural_language_workflow'):
            text_path = self.directory / (value['id'] + '.workflow.txt')
            if not text_path.exists():
                text = value['natural_language_workflow']
                atomic_text(text_path, task_prompt(value,'natural_language') if value['schema_version']==5
                            else text if value['schema_version']==3 else render(text,value['parameters']))

    def save(self, trial):
        atomic_json(self.directory / (trial['id'] + '.run.json'), trial)

    def cases(self):
        values = []
        for path in sorted(self.directory.glob('*.case.json'), key=lambda p:p.stat().st_mtime, reverse=True):
            try: values.append(json.loads(path.read_text(encoding='utf-8')))
            except (OSError, ValueError): continue
        return values

    def runs(self, case_id=None):
        rows = []
        for path in self.directory.glob('*.run.json'):
            try:
                row = json.loads(path.read_text(encoding='utf-8'))
                if case_id is None or row['case_id'] == case_id: rows.append(row)
            except (OSError, ValueError, KeyError): continue
        return sorted(rows, key=lambda row: row['started_at'])

    def verify(self, run_id, success, *, verified_by='user', note=''):
        if not re.fullmatch(r'[a-f0-9]{32}', run_id): raise ValueError('无效测试标识')
        path = self.directory / (run_id + '.run.json')
        row = json.loads(path.read_text(encoding='utf-8'))
        if row['status'] != 'completed': raise ValueError('仅正常结束的运行可标记目标完成情况。')
        row.update(goal_verification='success' if success else 'failure', verified_at=now(),
                   verified_by=verified_by, verification_note=note)
        self.save(row)


class Trial:
    """One manual run, measured with a monotonic clock in the Agent control loop."""
    def __init__(self, store, case, mode, *, clock=time.monotonic):
        prompt = task_prompt(case, mode)
        self.store, self.case, self.clock = store, case, clock
        self.tool_ids = set()
        self.started = clock()
        self.store.case(case)
        self.row = dict(schema_version=1, id=uuid.uuid4().hex, case_id=case['id'], mode=mode,
            started_at=now(), status='running', goal_verification='unknown', elapsed_seconds=None,
            tool_calls=0, human_requests=0, interventions=[], session_id=None, episode_id=None,
            thread_id=None, turn_id=None, environment={}, final_message='',
            timing='accepted_run_to_turn_completed_including_thread_setup_and_user_waits',
            initial_state_confirmed=True, parameters=case['parameters'],
            prompt_format=mode, prompt_characters=len(prompt), prompt_sha256=digest(prompt),
            renderer_version=case.get('renderer_version') if mode=='natural_language' else None)
        self.row['prompt_file'] = self.row['id'] + '.prompt.txt'
        atomic_text(self.store.directory / self.row['prompt_file'], prompt)
        self.store.save(self.row)

    def observe(self, event):
        method = event.get('method', '')
        params = event.get('params', {})
        item = params.get('item', {})
        if method == 'item/started' and item.get('type') == 'mcpToolCall':
            self.tool_ids.add(item.get('id'))
            self.row['tool_calls'] = len(self.tool_ids)
        if method == 'item/completed' and item.get('type') == 'agentMessage':
            self.row['final_message'] = item.get('text', '')
        if method == 'thread/tokenUsage/updated': self.row['token_usage'] = params.get('tokenUsage')

    def finish(self, status, *, error=''):
        if self.row['status'] != 'running': return self.row
        self.row.update(status=status, ended_at=now(), elapsed_seconds=round(self.clock()-self.started, 3), error=error)
        self.store.save(self.row)
        return self.row


def comparison(rows):
    """Descriptive, matched results only. Never claim statistical significance."""
    eligible = [r for r in rows if r['mode'] in MODES and r['status'] == 'completed' and r['goal_verification'] == 'success'
                and not r.get('interventions') and r.get('initial_state_confirmed')
                and r.get('environment', {}).get('model')
                and isinstance(r.get('elapsed_seconds'), (float, int))]
    # Match only the same immutable task+parameters+workflow snapshot and setup.
    groups = {}
    for row in eligible:
        key = (row['case_id'], digest(row.get('environment', {})))
        groups.setdefault(key, {mode: [] for mode in MODES})[row['mode']].append(row['elapsed_seconds'])
    summaries = []
    for left, right in (('baseline','natural_language'),):
        pairs = [(a,b) for group in groups.values() for a,b in zip(group[left],group[right])]
        if not pairs: continue
        base = statistics.median(a for a,_ in pairs)
        guided = statistics.median(b for _,b in pairs)
        delta = base-guided
        percent = abs(delta)/base*100 if base > 0 else 0
        summaries.append(f'{MODES[left]} → {MODES[right]}：可比较 {len(pairs)} 对，中位数 {base:.1f} → {guided:.1f} 秒；'
                         f'{MODES[right]} {"快" if delta >= 0 else "慢"} {abs(delta):.1f} 秒（{percent:.1f}%）。')
    if not summaries: return '尚无可比较的完整样本：各组需正常结束、确认目标完成，且模型已知、执行设置一致。'
    return '\n'.join(summaries) + '\n这是描述性结果，不代表统计显著；建议交替顺序、多次复测。'


def export_trials(root, *, case_ids=None, output_name=None):
    if output_name is not None and (Path(output_name).name != output_name or not output_name.endswith('.html')):
        raise ValueError('报告文件名必须是当前目录下的 HTML 文件。')
    from proximic_ring.agent_workflow.recording.records import _page, _write
    store = TrialStore(root)
    esc = html.escape
    body = ("<p><a href='../overview.html'>← 全流程总览</a></p>"
            '<p>从执行请求受理到 Agent 本轮结束，包含新会话准备和人工等待；不包含手动复位。'
            '各组使用新会话，固定同一任务、参数和流程版本。任务结束不等于目标完成。</p>'
            '<p>起始状态由操作者确认，未由程序独立验证；应用缓存、网络和模型波动仍可能影响结果。</p>')
    for case in store.cases():
        if case_ids is not None and case['id'] not in case_ids: continue
        rows = store.runs(case['id'])
        body += f"<h2>{esc(case['goal'])}</h2><p>共同起点：{esc(case['initial_state'])}</p>"
        body += f"<p>任务版本：{esc(case['id'])}</p>"
        body += f"<p style='white-space:pre-line'>{esc(comparison(rows) if case.get('schema_version') == 5 else '历史测试方案，仅供回查；不计入当前两组对比。')}</p><p><a href='{case['id']}.case.json'>内部任务快照（含来源与参数）</a>"
        if case.get('natural_language_workflow'):
            body += f" · <a href='{case['id']}.workflow.txt'>自然语言流程快照</a>"
        body += '</p>'
        body += '<table><tr><th>时间（UTC）</th><th>组别</th><th>状态</th><th>耗时</th><th>工具调用</th><th>目标验证</th></tr>'
        for row in rows:
            duration = f"{row['elapsed_seconds']:.1f} 秒" if row.get('elapsed_seconds') is not None else '未结束'
            body += '<tr>'+''.join('<td>'+esc(str(v))+'</td>' for v in (
                row['started_at'], HISTORY_MODES.get(row['mode'], row['mode']), row['status'], duration, row['tool_calls'], row['goal_verification']))+'</tr>'
        body += '</table>'
        for row in rows:
            body += f"<details><summary>{esc(HISTORY_MODES.get(row['mode'], row['mode']))} · {esc(row['started_at'])} · 详情</summary>"
            body += f"<p><a href='{row['id']}.run.json'>运行记录</a></p><pre>{esc(row.get('final_message',''))}</pre>"
            if row.get('prompt_file'):
                body += f"<p><a href='{row['id']}.prompt.txt'>实际发送给 Agent 的完整提示词</a> · {row.get('prompt_characters',0)} 字符</p>"
            body += f"<p>人工请求次数：{row['human_requests']}；干预：{esc(str(row.get('interventions',[])))}</p>"
            body += f"<p>验证者：{esc(row.get('verified_by','未验证'))}；{esc(row.get('verification_note',''))}</p></details>"
    path = store.directory / (output_name or ('report.html' if case_ids is None else 'report-selected.html'))
    _write(path, _page('Workflow 实测对比', body))
    return path
