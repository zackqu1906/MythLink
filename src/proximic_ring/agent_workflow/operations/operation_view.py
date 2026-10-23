"""Inspectable whole-operation catalog and local recommendation feedback UI."""
from __future__ import annotations
import html
import json
from proximic_ring.agent_workflow.recommendation.operation_recommendations import library, recommend
from proximic_ring.agent_workflow.recording.records import _page, _write
from proximic_ring.agent_workflow.extraction.workflow_store import atomic_json, now
from proximic_ring.agent_workflow.operations.operation_revision_view import render_admissions
from proximic_ring.agent_workflow.operations.operation_group_view import export_group, render_group_overview, render_group_details
from proximic_ring.agent_workflow.recommendation.recommendation_view import controls, OPERATIONS_JS
from proximic_ring.agent_workflow import pipeline_policy

esc=html.escape


def export_operations(store):
    if not (store.directory/'status.json').exists():
        atomic_json(store.directory/'status.json',dict(updated_at=now(),warnings=[],note='请运行 sync 获取本地提取同步状态'))
    groups=library(store);pairs=store.rows('pairs')
    history_pairs=pairs
    pairs=[]
    manifest_path=store.directory/'dataset/manifest.json'
    manifest=json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    ranking=recommend(store,scope='user') if pipeline_policy.legacy_workflow_enabled() else None
    contents={g['operation_id']:render_group_details(store,g,ranking) for g in groups}
    body="<p><a href='../overview.html'>← 返回全流程总览</a> · <a href='../workbench.html'>任务操作台</a></p>"+render_group_overview(groups,ranking,contents)
    for group in groups:
        export_group(store,group,ranking,content=contents[group['operation_id']])
        from proximic_ring.agent_workflow.operations.operation_group_view import group_filename
        for old_id in group.get('merged_operation_ids',[]):
            _write(store.directory/group_filename(old_id),_page('此组已合并',
                '<p>原组已合并至 <a href="'+group_filename(group['operation_id'])+'">'+esc(group['name'])+
                '</a>。原始 Workflow、来源和历史版本均保留在合并后的组内。</p>'))
    if pipeline_policy.legacy_workflow_enabled():
        body+="<details><summary>手动推荐测试工具</summary>"+controls()+"</details>"
    else:
        body+="<p role='status'>"+esc(pipeline_policy.DISABLED_MESSAGE)+"</p>"
    body+="<details class='group-audit'><summary>归并依据与后台记录</summary>"
    body+='<h2>归并依据</h2><p>本地 BGE-M3：新流程与组的通用 Workflow 比较，0.75 × 目标余弦 + 0.25 × 步骤余弦，Top-3 后由模型判断同类。相似度不是同类概率；不再逐对补查或全局重分组。</p>'
    body+=render_admissions(store)
    embedding_path=store.directory/'embedding-status.json'
    if embedding_path.exists():
        embedding=json.loads(embedding_path.read_text())
        body+=f"<p>向量状态：{esc(embedding.get('status','unknown'))} · 缓存命中 {embedding.get('cache_hits',0)} / {embedding.get('requested',0)}"
        body+=f" · {esc(embedding.get('error',''))}</p><p><a href='embedding-status.json'>当前向量准备状态</a>"
        if (store.directory/'embedding-last-compute.json').exists():
            body+=" · <a href='embedding-last-compute.json'>最近一次模型计算的耗时和内存</a>"
        body+='</p>'
    for pair in pairs:
        detail=json.loads(pair['detail'])
        body+=f"<details><summary>{esc(pair['status'])} · {pair['left_id'][:8]} ↔ {pair['right_id'][:8]} · 相似度 {detail['score']:.3f}</summary>"
        body+=f"<p>{esc(detail.get('reason',''))}</p><pre>{esc(json.dumps(detail,ensure_ascii=False,indent=2))}</pre></details>"
    body+="<p><a href='status.json'>后台同步状态与未导入原因</a></p>"
    if history_pairs:
        body+=f"<details><summary>保留的历史 TF-IDF / BGE-M3 逐对候选与复核：{len(history_pairs)} 对</summary><pre>{esc(json.dumps(history_pairs,ensure_ascii=False,indent=2))}</pre></details>"
    if (store.directory/'embedding-benchmark/index.html').exists():
        body+="<p><a href='embedding-benchmark/index.html'>Embedding 召回效果、耗时与内存实测</a></p>"
    failures=[j for j in store.rows('review_jobs') if j['status']!='completed']
    if failures:body+='<h3>复核未完成</h3>'+''.join(f"<p>{esc(j['id'])}：{esc(j['status'])} {esc(j['error'])}</p>" for j in failures)
    demos={}
    if pipeline_policy.legacy_workflow_enabled() and any(s['kind']=='test' for s in store.rows('samples')):
        body+='<h2>固定测试场景演示</h2><p>以下应用场景是手动模拟，来源标记为 test；不证明真实推荐准确率，不计入真实习惯或偏好。</p>'
        for app in ('com.apple.Notes','com.apple.Music','com.apple.Safari'):
            result=recommend(store,dict(source='manual_demo',app=app),scope='test')
            demos[app]=result
            body+=f"<h3>{esc(app)}</h3><ol>"+''.join(
                f"<li>{esc(i['name'])} — {i['score']:.3f}；{esc('；'.join(i['reasons']))}</li>" for i in result['items'])+'</ol>'
    trials=manifest.get('trials',[])
    if trials:
        body+=f'<h2>历史测试数据集：{len(trials)} 条</h2><p>历史 JSON 测试只保留原始记录；新归并输入是最新版自然语言流程。未重新提取的记录不充当额外流程样本。</p>'
    for trial in trials:
        body+=f"<details><summary>{esc(trial.get('mode',''))} · {esc(trial['run_id'][:10])} · {esc(trial.get('goal',''))}</summary><pre>{esc(json.dumps(trial,ensure_ascii=False,indent=2))}</pre>"
        for label,key in [('原始执行结果','original'),('独立重新提取','extraction')]:
            path=trial.get(key)
            if path:body+=f"<p><a href='dataset/{esc(path,quote=True)}'>{label}</a></p>"
        body+='</details>'
    body+="<p>算法参考：<a href='https://sbert.net/examples/sentence_transformer/applications/semantic-search/README.html'>Sentence Transformers</a> · <a href='https://huggingface.co/BAAI/bge-m3'>BGE-M3</a> · <a href='https://arxiv.org/abs/2502.09667'>Summaries as Centroids（架构参考，本实现采用来源支持的可执行流程）</a></p>"
    body+='</details>'
    _write(store.directory/'index.html',_page('Workflow 分组',body))
    atomic_json(store.directory/'catalog.json',dict(operations=groups,pairs=pairs,historical_pairs=history_pairs,demos=demos,recommendation=ranking,
        admissions=store.rows('operation_admissions'),updates=store.rows('operation_updates'),jobs=store.rows('operation_jobs'),
        merges=[dict(r,detail=json.loads(r['detail'])) for r in store.rows('operation_merges')]))
    from proximic_ring.agent_workflow.parameters.parameter_history import current_records
    directory=store.directory/'parameter-history';directory.mkdir(exist_ok=True,mode=0o700)
    records=list(current_records(store))
    for record in records:
        if not (directory/(record['id']+'.json')).exists():atomic_json(directory/(record['id']+'.json'),record)
    atomic_json(directory/'index.json',dict(records=records))
    (store.directory/'merges').mkdir(exist_ok=True,mode=0o700)
    for row in store.rows('operation_merges'):
        atomic_json(store.directory/'merges'/(row['id']+'.json'),dict(row,detail=json.loads(row['detail'])))
    from proximic_ring.agent_workflow.reporting.overview import export_overview
    export_overview(store.root)
    return store.directory/'index.html'

