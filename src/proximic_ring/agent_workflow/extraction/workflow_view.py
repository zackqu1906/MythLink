"""Compact workflow library; audit details live on separate local pages."""
from __future__ import annotations
from proximic_ring.agent_workflow.task_metadata import COMPACT_FORMATS, profile_text
import html
import json

from proximic_ring.agent_workflow.recording.records import _page, _pretty, _write
from proximic_ring.agent_workflow.extraction.workflow_artifacts import save_flow, latest_successful_jobs
from proximic_ring.agent_workflow.extraction.workflow_store import atomic_json
from .workflow_version import PROMPT_VERSION
from proximic_ring.agent_workflow.extraction.workflow_retry import retry_message, MAX_RETRIES
from proximic_ring.agent_workflow.recording.record_controls import task_controls, workflow_toolbar

from proximic_ring.agent_workflow.reporting.page_view import stats_cards, brief
from proximic_ring.agent_workflow.reporting.foreground_view import render_foreground
from proximic_ring.agent_workflow.extraction.workflow_limits import MAX_INPUT_CHARS

esc = html.escape


def _list(values):
    return '<ul>'+''.join('<li>'+esc(v)+'</li>' for v in values)+'</ul>'


def _audit_page(store, job, result):
    raw=f"../sessions/{job['session_id']}/episode-{job['episode_id']}.html"
    refs=result['evidence_index']

    def links(ids):
        parts=[]
        for key in ids:
            ref=refs[key]
            target=raw+('#step-'+ref['step_id'] if ref.get('step_id') else '')
            parts.append(f"<a href='{esc(target,quote=True)}'>{esc(key)}</a>")
            for event in ref['event_refs']:
                parts.append(f"<a href='{esc(raw,quote=True)}#event-{int(event['seq'])}'>#{int(event['seq'])}</a>")
        return ' · '.join(dict.fromkeys(parts))

    body=("<p><a href='library.html'>← 简洁流程</a></p>"
          "<p>本页是提取依据和本次执行情况，不是复用时的流程主体。引用校验不证明模型概括正确。</p>"
          f"<p><a href='{esc(raw,quote=True)}'>原始任务步骤</a> · "
          f"<a href='{job['id']}.input.json'>原始证据快照与完整引用</a> · "
          f"<a href='{esc(job['result_file'],quote=True)}'>提取详情 JSON</a></p>")
    if (store.directory/(job['id']+'.model-input.json')).exists():
        body+=(f"<p><a href='{job['id']}.model-input.json'>实际模型输入</a> · "
               f"<a href='{job['id']}.compaction.json'>输入压缩统计</a></p>")
    if (store.directory/(job['id']+'.trajectory.json')).exists():
        body+=(f"<p><a href='{job['id']}.trajectory.json'>精简轨迹</a> · "
               f"<a href='{job['id']}.trajectory-selection.json'>精简规则与省略来源</a></p>")
    if (store.directory/(job['id']+'.model-output.json')).exists():
        body+=f"<p><a href='{job['id']}.model-output.json'>模型原始输出（整理前）</a></p>"
    if (store.directory/(job['id']+'.review.json')).exists():
        body+=f"<p><a href='{job['id']}.review.json'>本地整理依据与前后版本</a> · 未重放验证</p>"
    body += task_controls(job['session_id'],job['episode_id'])
    episode=result.get('source',{}).get('episode',{})
    if result.get('task_profile'):
        body += '<p>'+esc(profile_text(result['task_profile']))+'</p><p>分类来源：'+links(result['task_profile']['sources'])+'</p>'
    body += render_foreground(episode.get('context'),episode.get('created_at'))
    if result.get('source_changed_after_extraction'):
        body += "<p class='warning'>提取期间原始记录发生变化，本版仅代表保存的输入快照。</p>"
    for i,w in enumerate(result['workflows']):
        body += f"<h2 id='workflow-{i}'>{esc(w['name'])}</h2>"
        if w.get('task_profile'):
            body += '<p>'+esc(profile_text(w['task_profile']))+'</p><p>'+links(w['task_profile']['sources'])+'</p>'
        body += '<details><summary>参数实例及来源</summary><pre>'+_pretty(w['parameters'])+'</pre></details>'
        if result.get('format') == 'agent-workflow-v7':
            body += '<p>' + esc(w['goal']) + '</p><ol>' + ''.join('<li>' + esc(s) + '</li>' for s in w['steps']) + '</ol>'
            body += '<p>流程来源：' + links(w['sources']) + '</p>'
            continue
        if result.get('format') in COMPACT_FORMATS:
            for n,s in enumerate(w['steps'],1):
                body+=f"<h3>{n}. {esc(s['instruction'])}</h3><p>来源：{links(s['sources'])}</p>"
                if s.get('kind'):body+='<p>步骤类型：'+('工具方法' if s['kind']=='tool' else '回答方法（不推断内部思考）')+'</p>'
                if s.get('check'):body+='<p>关键判断（下次执行）：'+esc(s['check'])+'</p>'
            body+='<h3>最终完成判断（下次执行）</h3><p>'+esc(w['completion']['check'])+'</p><p>'+links(w['completion']['sources'])+'</p>'
            body+='<details><summary>必要前提和恢复来源</summary><pre>'+_pretty(dict(conditions=w['conditions'],fallbacks=w['fallbacks']))+'</pre></details>'
            continue
        for n,s in enumerate(w['steps'],1):
            body += f"<h3>{n}. {esc(s['title'])}</h3><p>{esc(s['action'])}</p><p>{esc(s.get('method', s.get('target','')))}</p><p>来源：{links(s['evidence_ids'])}</p>"
            for o in s['observations']:
                body += f"<p>同次调用返回的观察（模型概括）：{esc(o['summary'])} · {links(o['evidence_ids'])}</p>"
            if not s['observations']: body += '<p>未记录该调用的明确观察，不补齐前后状态。</p>'
            body += f"<p>预期（待验证）：{esc(s['expected_outcome'])}</p>"+_list(s['uncertainties'])
        body += '<h3>条件分支的来源</h3><pre>'+_pretty(w['recovery_notes'])+'</pre>'
        body += '<h3>本次实际结果（未独立验证）</h3><p>'+esc(w['outcome']['summary'])+'</p>'
        body += '<p>'+links(w['outcome']['evidence_ids'])+'</p>'+_list(w['limitations'])
    body += '<h2>提取说明</h2>'+_list(result.get('notes', result.get('extraction_notes', [])))
    body += '<details><summary>版本、来源、模型与校验状态</summary><pre>'+_pretty(
        {k:v for k,v in result.items() if k not in ('workflows','evidence_index','extraction_notes')})+'</pre></details>'
    filename=job['id']+'.audit.html'
    _write(store.directory/filename,_page('Workflow 提取详情',body))
    return filename


def export_workflows(store):
    """Statistics and task summaries first; full steps and provenance stay folded."""
    jobs=store.jobs()
    successful=list(latest_successful_jobs(jobs))
    current={j['id'] for j in successful}
    successful_tasks={(j['session_id'],j['episode_id']) for j in successful}
    latest={}
    for job in jobs:latest.setdefault((job['session_id'],job['episode_id']),job)
    library=[];exported={};warnings=[]
    for job in jobs:
        if not job.get('result_file'):continue
        try:
            result=json.loads((store.directory/job['result_file']).read_text(encoding='utf-8'))
            filename,flow=save_flow(store.directory,job['id'],result)
            audit=_audit_page(store,job,result)
            exported[job['id']]=(flow,result,audit)
        except (OSError,ValueError,KeyError,TypeError):
            warnings.append('有提取记录暂不可读取，已保留原始文件。');continue
        if job['id'] in current:
            library.append(dict(session_id=job['session_id'],episode_id=job['episode_id'],job_id=job['id'],
                workflow_file=filename,text_file=job['id']+'.flow.txt',audit_file=job['result_file'],status='draft',
                workflow_count=len(flow['workflows'])))
    counts={status:sum(j['status']==status for j in latest.values()) for status in ('queued','running','failed')}
    body='<p class="reading-summary">从用户任务记录中提取的流程草稿。先看目标和规模，再展开步骤；概括使用已有名称与目标，不额外调用模型。</p>'
    body+=stats_cards([(sum(e['workflow_count'] for e in library),'当前 Workflow'),(len(latest),'来源任务'),
                      (counts['queued']+counts['running'],'最新提取处理中'),(counts['failed'],'最新提取失败')])
    body+='<p class="reading-meta">每个任务展示最近一次成功提取；状态按各任务最新尝试统计。后续重试失败不会删除已有流程，重新提取不增加使用次数。</p>'
    body+=f'<p class="reading-meta">提取或校验失败默认自动重试最多 {MAX_RETRIES} 次（首次请求之外），间隔 10、30、60 秒；等待 Agent 空闲后开始。配置错误、来源缺失和进程中断需处理后手动重试。</p>'
    body+=f'<p class="reading-meta">后台输入本地上限：{MAX_INPUT_CHARS:,} 字符；提取按精简后输入计算。字符数不是 token 数，模型服务仍独立校验上下文容量。</p>'
    body+=workflow_toolbar('library.html')
    body+=''.join('<p class="warning">'+esc(w)+'</p>' for w in warnings)
    for job in latest.values():
        if job['status'] not in ('failed','queued','running'):continue
        message=retry_message(job)
        anchor=(f" id='task-{job['session_id']}-{job['episode_id']}'" if (job['session_id'],job['episode_id']) not in successful_tasks else '')
        body+=f"<section{anchor}><h3>{brief(job['goal'])}</h3><p>{esc(message)}</p>"
        body+=task_controls(job['session_id'],job['episode_id'],initial_message=message)+'</section>'
    for job in jobs:
        if job['id'] not in current or job['id'] not in exported:continue
        flow,result,audit=exported[job['id']]
        episode=result.get('source',{}).get('episode',{})
        body+=f"<section id='task-{job['session_id']}-{job['episode_id']}'>"
        for i,w in enumerate(flow['workflows']):
            body+=f"<article><h2>{esc(w['name'])}</h2><p class='reading-summary'>{brief(w['goal'])}</p>"
            if w.get('task_profile'):body+='<p class="reading-meta">'+esc(profile_text(w['task_profile']))+'</p>'
            body+=f"<p class='reading-meta'>{len(w['steps'])} 个步骤 · {len(w['parameters'])} 个参数<br>提取时间：{esc(job['created_at'])} · 草稿，尚未独立执行验证</p>"
            body+=render_foreground(episode.get('context'),episode.get('created_at'))
            body+='<details><summary>展开完整 Workflow</summary><p>'+esc(w['goal'])+'</p><h3>参数</h3>'
            body+=('<dl>'+''.join(f"<dt><code>{esc(p['name'])}</code></dt><dd>{esc(p['description'])}</dd>" for p in w['parameters'])+'</dl>' if w['parameters'] else '<p>无可变参数。</p>')
            body+='<h3>操作步骤</h3><ol>'
            for step in w['steps']:
                if isinstance(step, str):
                    body += '<li><p>' + esc(step) + '</p></li>'
                    continue
                body+=f"<li><p>{esc(step['action'])}</p>"
                if 'completion' not in w:body+=f"<p>执行方法：{esc(step.get('method',step.get('target','未记录')))}</p>"
                if step.get('check'):body+=f"<p class='muted'>{'关键判断' if 'completion' in w else '完成条件'}：{esc(step['check'])}</p>"
                body+='</li>'
            body+='</ol>'
            if w.get('completion'):body+='<p><strong>最终完成判断：</strong>'+esc(w['completion'])+'</p>'
            if w.get('conditions') or w.get('fallbacks'):
                body+='<h3>使用条件与异常处理</h3>'+_list(w.get('conditions', []))
                for fallback in w.get('fallbacks', []):body+=f"<p>当 {esc(fallback['when'])}：{esc(fallback['action'])}</p>"
            body+='</details><details><summary>来源、版本与文本导出</summary>'
            body+=f"<p>来源任务：{esc(job['goal'])}</p><p>提取版本：{esc(job['prompt_version'])}</p>"
            if job['prompt_version']!=PROMPT_VERSION:body+='<p>旧版草稿，可重新提取升级。</p>'
            body+='<p>来源审计保留完整引用，按需打开；不影响本页阅读。</p>'
            if result.get('generation',{}).get('local_review'):
                body+='<p>本版含提取后的本地来源核对与简化；审计页保留模型原文及修改依据。</p>'
            body+=f"<p><a href='{job['id']}.flow.txt'>自然语言文本</a> · <a href='{audit}#workflow-{i}'>来源审计</a></p>"
            body+=task_controls(job['session_id'],job['episode_id'])+'</details></article>'
        if not flow['workflows']:
            body+='<p>本任务未提炼出可复用流程。</p>'+_list(result.get('notes', result.get('extraction_notes', [])))
        body+='</section>'
    if not jobs:body+='<p>尚无提取任务。启用后自动提取新任务，已有任务需手动选择。</p>'
    body+="<details id='attempts'><summary>提取历史与旧版本</summary>"
    for job in jobs:
        body+=f"<details id='attempt-{job['id']}'><summary>{brief(job['goal'])} · {esc(retry_message(job))}</summary><p>{esc(job['created_at'])}</p>"
        if job.get('parent_job_id'):
            body+=f"<p><a href='#attempt-{job['parent_job_id']}'>查看触发本次重试的失败记录</a></p>"
        if job['id'] in exported:
            body+=f"<p><a href='{job['id']}.flow.txt'>该版自然语言文本</a> · <a href='{job['id']}.audit.html'>该版审计</a></p>"
        elif (store.directory/(job['id']+'.rejected.json')).exists():body+=f"<p><a href='{job['id']}.rejected.json'>未通过校验的响应</a></p>"
        body+=f"<p><a href='{job['id']}.input.json'>本版证据快照</a></p>"
        if (store.directory/(job['id']+'.model-input.json')).exists():
            body+=(f"<p><a href='{job['id']}.model-input.json'>实际模型输入</a> · "
                   f"<a href='{job['id']}.compaction.json'>输入压缩统计</a></p>")
        if (store.directory/(job['id']+'.trajectory.json')).exists():
            body+=(f"<p><a href='{job['id']}.trajectory.json'>精简轨迹</a> · "
                   f"<a href='{job['id']}.trajectory-selection.json'>精简规则与省略来源</a></p>")
        body+=task_controls(job['session_id'],job['episode_id'])+'</details>'
    body+='</details>'
    atomic_json(store.directory/'library.json',dict(schema_version=1,entries=library))
    page=_page('Workflow 库',body)
    _write(store.directory/'library.html',page);_write(store.directory/'index.html',page)
    from proximic_ring.agent_workflow.reporting.overview import export_overview
    export_overview(store.root)
    return store.directory/'library.html'
