"""分组回查页：总览 → 通用 Workflow → 每条来源经验；只读导出，不做推断。

组数按 library 的稳定 operation_id 统计；成员按已准入 sample 统计。
两次任务即使提取出完全相同的版本，也分别展示，避免把版本去重误当成经验去重。
详情与总览放在同一目录，使静态文件和本地服务共用相同的来源链接。
"""
from __future__ import annotations

import html
import json
from urllib.parse import quote

from proximic_ring.agent_workflow.operations.operation_revision_view import render_revisions
from proximic_ring.agent_workflow.recording.records import _page, _write
from proximic_ring.agent_workflow.extraction.workflow_artifacts import workflow_text
from proximic_ring.agent_workflow.task_metadata import profile_text
from proximic_ring.agent_workflow.recommendation.recommendation_view import score_detail

from proximic_ring.agent_workflow.reporting.page_view import brief
from proximic_ring.agent_workflow.reporting.foreground_view import render_foreground, render_group_foregrounds

esc = html.escape

GROUP_STYLES = """<style>
.group-intro{color:#626b69;margin-bottom:24px}
.group-stats{display:flex;flex-wrap:wrap;gap:16px;margin:22px 0 28px}
.group-stat{padding:18px 24px;background:#fff;border:1px solid #dce4e0;border-radius:12px;min-width:135px}
.group-stat strong{display:block;font-size:34px;line-height:1.2;color:#186657}
.group-stat span{display:block;margin-top:8px;color:#5b6662}
.group-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:16px;margin-bottom:32px}
.group-card{padding:22px;background:#fff;border:1px solid #dce4e0;border-radius:12px;color:inherit;text-decoration:none}
.group-card:hover,.group-card:focus-within{border-color:#186657;box-shadow:0 2px 12px #18665715}
.group-card summary strong{display:block;margin:8px 0;font-size:19px}
.group-card p{margin:8px 0;overflow-wrap:anywhere}
.group-card .group-cta{margin-top:auto;padding-top:18px;color:#186657;font-weight:600}
.group-card[open]{grid-column:1/-1}
.group-tag{font-size:12px;color:#52665e}
.group-meta{color:#626b69;font-size:13px;overflow-wrap:anywhere}
.group-section{margin-top:30px}
.group-section>h2{font-size:22px}
.group-section pre{line-height:1.9}
.group-current>details{border-left:4px solid #186657}
.workflow-member summary{font-weight:600}
.workflow-member summary .group-meta{display:block;font-weight:400;margin:4px 0 0 18px}
.group-audit{margin-top:28px}
@media(max-width:600px){.group-stat{flex:1;min-width:100px;padding:14px}.group-stat strong{font-size:28px}}
</style>"""


def group_filename(operation_id):
    # Stable IDs, not display names, determine links across regenerated reports.
    return 'group-' + quote(operation_id, safe='') + '.html'


def render_group_overview(groups, ranking=None, contents=None):
    members = [s for group in groups for s in group['samples']]
    versions = {s['version_id'] for s in members}
    body = GROUP_STYLES + "<p class='group-intro'>每个组代表一种完整操作。先看操作概括、成员数量和分数；点击卡片在本页展开通用 Workflow、成员与版本。</p>"
    body += "<div class='group-stats' aria-label='Workflow 分组统计'>"
    for count, label in ((len(groups), '个 Workflow 组'), (len(members), '条组内 Workflow'),
                         (len(versions), '个不同来源版本')):
        body += f"<div class='group-stat'><strong>{count}</strong><span>{label}</span></div>"
    body += "</div><p class='group-meta'>仅统计已入组的记录；待复核记录不计入。相同版本来自多次任务时，每次任务仍单独列出。</p>"
    if not groups:
        return body + '<p>目前还没有 Workflow 组。已有提取结果可通过后台同步与入组处理后在这里查看。</p>'
    if ranking:
        body += "<p class='group-meta'>下列分数为导出时的真实使用统计快照；未提供唤起场景，按总体频率回退。不是已展示推荐，不计曝光。</p>"
    scores = {i['operation_id']:i for i in (ranking or {}).get('items',[])}
    body += "<div class='group-grid'>"
    for group in groups:
        oid = group['operation_id']
        candidate_count = sum(r['status'] == 'candidate' for r in group['revision_history'])
        apps = ' · '.join({'com.apple.Notes': '备忘录', 'com.apple.Music': '音乐',
                           'com.apple.Safari': 'Safari'}.get(a, a) for a in sorted(group['apps']))
        status = '当前通用 Workflow 可查看' if group['active_revision'] else '尚未建立通用 Workflow'
        if candidate_count:
            status += f' · {candidate_count} 个候选待验证'
        body += (f"<details class='group-card' id='op-{esc(oid)}'><summary>"
                 f"<span class='group-tag'>流程涉及应用（文本标签）：{esc(apps or '未识别')}</span><strong>{esc(group['name'])}</strong>"
                 f"<span class='group-meta'>{len(group['samples'])} 条成员 Workflow · {len(group['variants'])} 个版本 · {status}</span>"
                 f"<span class='reading-summary' style='display:block;margin:10px 0'>{brief(group['goal'])}</span>"
                 + render_group_foregrounds(group['samples'],compact=True)
                 + (f"<span class='group-meta'>推荐统计分数 {scores[oid]['score']:.4f}</span>" if oid in scores else '')
                 + "</summary>" + (contents or {}).get(oid, '<p>暂无详情。</p>') + '</details>')
    return body + '</div>'


def _source_links(store, sample):
    """Only link existing files inside this recording root, never inferred observations."""
    source = sample['source']
    sid, eid = source.get('session_id', ''), source.get('episode_id', '')
    candidates = []
    if sid and eid:
        candidates.append((store.root / 'sessions' / sid / ('episode-' + eid + '.html'),
                           f'../sessions/{quote(sid, safe="")}/episode-{quote(eid, safe="")}.html',
                           '原始任务步骤和事件'))
    filename = source.get('source_file', '')
    if filename.startswith(('../workflows/', 'dataset/')):
        candidates.append((store.directory / filename, quote(filename, safe='/'), '提取依据与来源事件引用'))
    links = [f"<a href='{esc(href)}'>{label}</a>" for path, href, label in candidates
             if path.resolve().is_relative_to(store.root.resolve()) and path.is_file()]
    return '<p>' + ' · '.join(links) + '</p>' if links else '<p class="group-meta">此报告目录中没有可打开的原始记录文件；已保存的来源引用见下方。</p>'


def render_group_details(store, group, ranking=None):
    oid = group['operation_id']
    body = ""
    # The index already shows the goal in its card summary. Keep shared details
    # free of that duplicate; standalone pages add their own introduction below.
    body += (f"<p class='group-meta'>稳定操作 ID：<code>{esc(oid)}</code>"
             f"<br>{len(group['samples'])} 条组内 Workflow · {len(group['variants'])} 个不同来源版本</p>"
             )
    if group.get('workflow',{}).get('task_profile'):
        body += '<p>'+esc(profile_text(group['workflow']['task_profile']))+'</p>'
    if group.get('merged_operation_ids'):
        body += '<p>已实际合并 '+str(len(group['merged_operation_ids'])+1)+' 个原组；旧组 ID 保留为别名，原始流程和来源可在下方逐条查看。</p>'
    body += render_group_foregrounds(group['samples'])
    if group.get('display_record'):
        body += (f"<p>Hub 简洁标题：<strong>{esc(group['display']['title'])}</strong>"
                 f" · {esc(group['display']['summary'])}</p>")
    if ranking:
        item = next(i for i in ranking['items'] if i['operation_id']==oid)
        body += score_detail(item, ranking)
    body += "<section id='general-workflow' class='group-section group-current'><h2>通用 Workflow</h2>"
    body += '<p>以下是这个组当前用于推荐的通用流程。</p>' if group['active_revision'] else ''
    body += render_revisions(store, group, selection='active') + '</section>'
    from proximic_ring.agent_workflow.parameters.parameter_view import render_parameters
    body += render_parameters(store, group)
    body += (f"<section id='members' class='group-section'><h2>组内 Workflow · {len(group['samples'])} 条</h2>"
             '<p>以下保留各次任务提取的完整流程，可展开对照通用流程。</p>')
    variants = {v['version_id']: v['flow'] for v in group['variants']}
    for vid, flow in variants.items():
        _write(store.directory / (vid + '.txt'), workflow_text(flow))
    # A task is a member, not a unique flow hash. Never drop repeated experience.
    for index, sample in enumerate(sorted(group['samples'], key=lambda s: (s['occurred_at'], s['id']), reverse=True), 1):
        flow = variants[sample['version_id']]
        kind = {'test': '测试任务', 'user': '真实用户任务'}.get(sample['kind'], '来源类型未知')
        body += (f"<details class='workflow-member' id='member-{index}'><summary>{index}. {esc(flow['name'])}"
                 f"<span class='group-meta'>{kind} · {esc(sample['occurred_at'] or '时间未记录')}</span></summary>"
                 + render_foreground(sample['context'],sample['occurred_at']) +
                 f"<p><a href='{esc(sample['version_id'])}.txt'>打开自然语言完整步骤</a></p>"
                 f"<pre>{esc(workflow_text(flow))}</pre>")
        body += '<details><summary>来源文件（按需回查）</summary>' + _source_links(store, sample) + '</details>'
        source=sample['source']
        if source.get('session_id') and source.get('episode_id'):
            target=quote(source['session_id']+'/'+source['episode_id'],safe='')
            body+=f"<p><a href='../workbench.html#{target}'>在操作台重新归组或提取此任务参数 →</a></p>"
        metadata = dict(sample_id=sample['id'], version_id=sample['version_id'], source=sample['source'])
        body += ("<details><summary>来源引用与版本 ID</summary>"
                 f"<pre>{esc(json.dumps(metadata, ensure_ascii=False, indent=2))}</pre></details>"
                 "<details><summary>原始场景（缺失项未推断）</summary>"
                 f"<pre>{esc(json.dumps(sample['context'], ensure_ascii=False, indent=2))}</pre></details></details>")
    if not group['samples']:
        body += '<p>当前没有已准入的成员记录。通用版本的历史来源仍保留在版本详情中。</p>'
    body += "</section><section id='revision-history' class='group-section'><h2>候选与历史版本</h2>"
    body += render_revisions(store, group, selection='other') + '</section>'
    # Embedded groups need unique anchors; legacy standalone URLs remain exported.
    for anchor in ('general-workflow','members','revision-history'):
        body=body.replace("id='"+anchor+"'", "id='"+oid+'-'+anchor+"'")
    body=body.replace("id='member-", "id='"+oid+"-member-")
    return body


def export_group(store, group, ranking=None, *, content=None):
    body=GROUP_STYLES+"<p><a href='index.html'>← 返回全部 Workflow 组</a></p>"
    body+=f"<p class='reading-summary'>{esc(group['goal'])}</p>"
    body+=render_group_details(store,group,ranking) if content is None else content
    # Preserve old bookmarked fragment identifiers on standalone group pages.
    body=body.replace("id='"+group['operation_id']+'-', "id='")
    path=store.directory/group_filename(group['operation_id'])
    _write(path,_page(group['name'],body))
    return path
