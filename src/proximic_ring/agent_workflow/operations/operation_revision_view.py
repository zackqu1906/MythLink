"""阶段 8：导出通用流程、候选及版本来源；原始会话不混入可执行步骤正文。"""
import html
import json
from proximic_ring.agent_workflow.extraction.workflow_artifacts import workflow_text
from proximic_ring.agent_workflow.extraction.workflow_store import atomic_json
from proximic_ring.agent_workflow.recording.records import _write
from proximic_ring.agent_workflow.operations.operation_display import read_display


def render_revisions(store,group,*,selection='all'):
    """Render active/other revisions separately without changing any version state."""
    esc=html.escape;active=group.get('active_revision')
    if not active:return '<p>历史操作，尚未建立独立通用流程。</p>'
    oid=group['operation_id'];body=''
    revisions=[r for r in group['revision_history'] if selection=='all'
               or (r['id']==active['id'])==(selection=='active')]
    if not revisions:return '<p class="group-meta">目前没有候选或历史版本。</p>'
    for rev in sorted(revisions,key=lambda r:(r['id']!=active['id'],r['created_at'])):
        relative=f"definitions/{rev['operation_id']}/{rev['id']}"
        (store.directory/relative).parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        display=read_display(store,rev['id'])
        atomic_json(store.directory/(relative+'.json'),dict(rev,display=display))
        _write(store.directory/(relative+'.txt'),workflow_text(rev['flow']))
        current=rev['id']==active['id']
        label='当前版本' if current else '候选：尚未启用' if rev['status']=='candidate' else '已被修订替代的候选' if rev['status']=='superseded' else '历史版本'
        body+=f"<details><summary>{label} · {rev['id'][:10]} · {esc(rev['change_kind'])}</summary>"
        body+=f"<p>{esc(rev['reason'])}</p><p>核验状态：{esc(rev['validation'].get('state','unknown'))}；来源支持不等于通用版本已经执行验证。</p>"
        if current and rev['validation'].get('state')=='reviewed_merge_not_replayed':
            body+='<p><strong>已审阅并实际合并。</strong>分支、参数迁移与成员归属已核对；尚未重放执行来源任务。原始定义与来源保留在下方组内 Workflow 和历史版本。</p>'
        elif rev['change_kind']=='generalization' and rev['status']=='candidate':
            body+='<p><strong>泛化候选，仅供审阅。</strong>当前分组、参数和可执行版本保持不变；来源流程与参数去向在下方生成依据中，尚未核验各分支或迁移成员。</p>'
        notes=rev['validation'].get('review_notes',[])
        if isinstance(notes,list) and notes:
            heading='历史候选的审阅意见（此版本已被替代）' if rev['status']=='superseded' else '审阅发现的待完善项'
            body+='<p><strong>'+heading+'：</strong></p><ul>'+''.join('<li>'+esc(n)+'</li>' for n in notes if isinstance(n,str))+'</ul>'
        if display:
            body+=f"<p>Hub 标题：<strong>{esc(display['content']['title'])}</strong> · {esc(display['content']['summary'])}</p>"
            body+='<table><thead><tr><th>原参数名</th><th>原定义</th><th>Hub 标签</th><th>输入提示</th></tr></thead><tbody>'
            for parameter in rev['flow']['parameters']:
                copy=display['content']['parameters'][parameter['name']]
                body+='<tr>'+''.join('<td>'+esc(t)+'</td>' for t in (parameter['name'],parameter['description'],copy['label'],copy['placeholder']))+'</tr>'
            body+='</tbody></table>'
        else:body+='<p>Hub 简洁文案尚未生成，暂使用原始名称与说明。</p>'
        body+=f"<p><a href='{relative}.txt'>自然语言通用步骤</a> · <a href='{relative}.json'>版本、来源与修改依据</a></p>"
        body+=f"<pre>{esc(workflow_text(rev['flow']))}</pre><p>完整版本 ID：<code>{rev['id']}</code></p>"
        body+=f"<details><summary>来源与生成依据</summary><pre>{esc(json.dumps(dict(sources=rev['sources'],generation=rev['generation']),ensure_ascii=False,indent=2))}</pre></details></details>"
    return body


def render_admissions(store):
    esc=html.escape;admissions=store.rows('operation_admissions');updates=store.rows('operation_updates')
    body='<h2>增量入组与修订记录</h2><p>新经验 → BGE-M3 召回最多三个组的通用流程 → LLM 复核。same 统一进入泛化；返回原流程时归组，有改进时保存候选，无法融合时保留冲突说明。候选需审阅来源分支和参数迁移后启用。历史普通增量候选保留原 Trials 入口。</p>'
    for merge in store.rows('operation_merges'):
        body+=f"<details><summary>已完成实际合并 · {esc(merge['id'][:10])}</summary><pre>{esc(json.dumps(json.loads(merge['detail']),ensure_ascii=False,indent=2))}</pre></details>"
    for row in admissions:
        detail=json.loads(row['detail'])
        body+=f"<details><summary>{esc(row['status'])} · 样本 {esc(row['sample_id'][:10])}</summary><pre>{esc(json.dumps(detail,ensure_ascii=False,indent=2))}</pre></details>"
    for row in updates:
        body+=f"<details><summary>修订 {esc(row['status'])} · {row['id'][:10]}</summary><pre>{esc(json.dumps(dict(row,novelty=json.loads(row['novelty'])),ensure_ascii=False,indent=2))}</pre></details>"
    for job in store.rows('operation_jobs'):
        body+=f"<p>{esc(job['kind'])} · {esc(job['status'])} · <a href='{job['id']}.operation-input.json'>模型输入</a>"
        if (store.directory/(job['id']+'.operation-result.json')).exists():body+=f" · <a href='{job['id']}.operation-result.json'>输出与耗时</a>"
        if job['error']:body+=' · '+esc(job['error'])
        body+='</p>'
    return body
