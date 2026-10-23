"""Workflow 页面共用的历史前台应用展示；不采集新状态、不推断目标应用。

可用于推荐的应用沿用 context_features 的时间有效性判定。组汇总按来源任务
去重，只统计真实用户任务；测试来源单列。展示不写回流程、不进入 LLM 输入。
"""
import html
import json
from collections import Counter

from proximic_ring.agent_workflow.recommendation.recommendation_context import context_features, instant

NAMES = {'com.openai.codex': 'Codex', 'com.apple.Notes': '备忘录',
         'com.apple.Music': 'Apple Music', 'com.apple.Safari': 'Safari',
         'com.google.Chrome': 'Google Chrome', 'cn.shandianshuo.desktop': '闪电说'}


def foreground(context, occurred_at=None):
    if isinstance(context, str):
        try: context = json.loads(context)
        except ValueError: context = {}
    context = context if isinstance(context, dict) else {}
    focus = context.get('before_agent_focus', context)
    focus = focus if isinstance(focus, dict) else {}
    observed = next((focus[k] for k in ('bundle', 'bundle_id', 'app')
                     if isinstance(focus.get(k), str) and focus[k].strip()), None)
    anchor = instant(occurred_at) or instant(context.get('invoked_at'))
    # Never reinterpret an undated historic record as a freshly captured scene.
    effective = context_features(context, anchor, historical=True)['app'] if anchor else None
    name = NAMES.get(effective or observed) or focus.get('app') or observed or '未知'
    return dict(name=str(name), app=effective, observed=observed,
                captured_at=str(focus.get('captured_at') or '未记录'),
                status='可用于场景统计' if effective else
                       '采集时间缺失或失效，不用于场景统计' if observed else '未记录，不推断')


def label(value):
    name = value['name']
    if value['observed'] and value['observed'] != name:
        name += ' · ' + value['observed']
    return html.escape(name)


def render_foreground(context, occurred_at=None):
    value = foreground(context, occurred_at)
    return ("<p class='foreground-context'><strong>发起任务前台 App：</strong>" + label(value) +
            "<br><span class='reading-meta group-meta'>" + html.escape(value['status']) +
            ' · 采集时间：' + html.escape(value['captured_at']) + '</span></p>')


def group_foregrounds(samples):
    tasks = {}
    for sample in sorted(samples, key=lambda s: s['id']):
        source = sample.get('source') or {}
        key = ((source['session_id'], source['episode_id']) if source.get('session_id') and source.get('episode_id')
               else ('sample', sample['id']))
        tasks.setdefault((sample['kind'], key), sample)
    real = [s for s in tasks.values() if s['kind'] == 'user']
    counts = Counter(); names = {}; missing = 0
    for sample in real:
        value = foreground(sample.get('context'), sample.get('occurred_at'))
        if value['app']:
            counts[value['app']] += 1
            names[value['app']] = value['name']
        else: missing += 1
    return dict(total=len(real), missing=missing,
                excluded=sum(s['kind'] != 'user' for s in tasks.values()),
                apps=[dict(app=app, name=names[app], count=count)
                      for app, count in sorted(counts.items(), key=lambda row: (-row[1], row[0]))])


def render_group_foregrounds(samples, *, compact=False):
    data = group_foregrounds(samples)
    parts = [html.escape(row['name']) + f" {row['count']} 次" for row in data['apps']]
    if data['missing']: parts.append(f"未知或失效 {data['missing']} 次")
    summary = '；'.join(parts) or '暂无真实任务场景'
    if compact:
        return "<span class='group-meta' style='display:block'>历史前台 App：" + summary + '</span>'
    body = "<section class='group-foreground'><h3>发起场景：历史前台 App</h3><p>" + summary + '</p>'
    if data['apps']:
        body += '<table><thead><tr><th>应用</th><th>应用标识（如 Bundle ID）</th><th>真实任务次数</th></tr></thead><tbody>'
        for row in data['apps']:
            body += '<tr><td>' + html.escape(row['name']) + '</td><td><code>' + html.escape(row['app']) + f"</code></td><td>{row['count']}</td></tr>"
        body += '</tbody></table>'
    body += (f"<p class='group-meta'>共 {data['total']} 次真实任务；其中 {data['missing']} 次前台应用未知或失效。"
             f"另有 {data['excluded']} 次测试或未知来源任务，不计入真实场景统计。"
             '按原始任务去重；重新提取不增加次数。这里展示发起场景，流程中的目标应用另行标注。</p></section>')
    return body
