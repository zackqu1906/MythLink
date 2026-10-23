"""统一回查入口，只读已有数据，输出可再生成的 HTML/JSON。

这里不创建业务数据库，不调用推荐器、模型或桌面工具，也不记录曝光/反馈。
推荐页展示最近 50 次已保存请求的原始排序；查看历史不产生一次新推荐。
读取每个 SQLite 时使用独立只读事务。跨数据库的计数是导出时快照，
不假装是一笔全局事务；缺失/损坏的来源明确提示，其余入口仍可查看。
"""
from __future__ import annotations

import argparse
import html
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

from proximic_ring.agent_workflow.extraction.workflow_store import atomic_json, atomic_text, now
from proximic_ring.agent_workflow.recording.records import _page
from proximic_ring.agent_workflow.reporting.pipeline_view import render_pipeline
from proximic_ring.agent_workflow.reporting.page_view import stats_cards, brief

LIMIT = 50


def _read(path, queries, warnings):
    if not path.is_file():
        return {}
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
            db.row_factory = sqlite3.Row
            db.execute('BEGIN')
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            return {key: [dict(r) for r in db.execute(sql)] for key, (table, sql) in queries.items() if table in tables}
    except (sqlite3.Error, OSError):
        warnings.append(f'{path.name} 暂不可读取；未修改来源数据。')
        return {}


def _link(root, relative, label):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        return html.escape(label) + ' <small>（尚未生成）</small>'
    return f"<a href='{quote(relative, safe='/#')}'>{html.escape(label)}</a>"


def _count(data, name):
    return data.get(name, [{'n': 0}])[0]['n']


def _recommendations(root, data, warnings):
    directory = root / 'recommendations'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    requests = data.get('requests', [])
    body = "<p><a href='../overview.html'>← 全流程总览</a></p>"
    body += (f"<p>共 {_count(data, 'request_count')} 次已保存请求，展示最近 {LIMIT} 次。以下是当时的推荐列表，"
             "不是当前桌面的实时推荐。查看本页不产生新请求、曝光或反馈；未曝光不算拒绝。</p>")
    body += stats_cards([(_count(data, 'request_count'), '已保存推荐请求'), (_count(data, 'exposure_count'), '实际曝光'), (_count(data, 'feedback_count'), '反馈记录')])
    for row in requests:
        try:
            snapshot = json.loads(row['snapshot'])
            if not isinstance(snapshot, dict) or not isinstance(snapshot.get('items'), list):
                raise ValueError('invalid snapshot')
        except (ValueError, TypeError):
            warnings.append('有推荐快照格式异常，原始数据库仍保留。')
            continue
        # Database IDs are generated UUIDs. Reject malformed historical IDs
        # before using them as export filenames; never follow arbitrary paths.
        identifier = row['id']
        if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in '0123456789abcdef' for c in identifier):
            warnings.append('有推荐请求标识异常，未导出对应文件。')
            continue
        exposures = [e for e in data.get('exposures', []) if e['request_id'] == identifier]
        ids = {e['id'] for e in exposures}
        feedback = [f for f in data.get('feedback', []) if f['exposure_id'] in ids]
        executions = [e for e in data.get('executions', []) if e['exposure_id'] in ids]
        archive = dict(request={k: v for k, v in row.items() if k != 'snapshot'}, snapshot=snapshot,
                       exposures=exposures, feedback=feedback, executions=executions)
        atomic_json(directory / (identifier + '.json'), archive)
        esc = html.escape
        names='、'.join(str(item.get('name','未命名')) for item in snapshot['items'][:3]) or '本次没有推荐候选'
        scope={'user':'真实使用','test':'测试演示'}.get(row['scope'],row['scope'])
        surface={'native':'应用面板','web':'网页','cli':'命令行'}.get(row['surface'],row['surface'])
        body += (f"<details id='request-{identifier}'><summary><strong>{brief(names,100)}</strong>"
                 f"<span class='reading-meta' style='display:block'>{esc(row['at'])} · {esc(scope)} · {esc(surface)} · "
                 f"{len(snapshot['items'])} 项 · 已曝光 {len(exposures)} 项</span></summary><ol>")
        for item in snapshot['items']:
            body += f"<li>{esc(str(item.get('name', '未命名')))} · 分数 {esc(str(item.get('score', '未知')))}"
            body += '<p>' + esc('；'.join(str(r) for r in item.get('reasons', []))) + '</p></li>'
        body += '</ol><details><summary>本次请求场景</summary><pre>' + esc(json.dumps(snapshot.get('context', {}),ensure_ascii=False,indent=2)) + '</pre></details>'
        body += '<p>反馈：' + esc('、'.join(f['kind'] for f in feedback) or '尚无显式反馈') + '</p>'
        for execution in executions:
            body += f"<p>执行：{esc(execution['state'])} · {esc(execution['goal'])} · {esc(execution['error'])}</p>"
            body += '<details><summary>执行参数、状态和来源标识</summary><pre>' + esc(json.dumps(execution, ensure_ascii=False, indent=2)) + '</pre></details>'
        body += f"<details><summary>原始快照导出</summary><p><a href='{identifier}.json'>请求、曝光、反馈与执行记录 JSON</a></p></details></details>"
    if not requests:
        body += '<p>尚无已保存的推荐请求。请在应用中主动唤起推荐。</p>'
    atomic_text(directory / 'index.html', _page('历史推荐列表与反馈', body))


def export_overview(root):
    root = Path(root).resolve()
    warnings = []
    sessions = list((root / 'sessions').glob('*/session.json'))
    tasks = sum(_count(_read(p.parent / 'index.sqlite3',
        {'tasks': ('episodes', 'SELECT count(*) AS n FROM episodes')}, warnings), 'tasks') for p in sessions)
    queue = _read(root / 'workflows/queue.sqlite3', {
        'jobs': ('jobs', 'SELECT status,count(*) AS n FROM jobs GROUP BY status'),
    }, warnings)
    latest = f"SELECT id FROM recommendation_requests ORDER BY at DESC,id DESC LIMIT {LIMIT}"
    shown = f'SELECT id FROM recommendation_exposures WHERE request_id IN ({latest})'
    queries = {
        'groups': ('operation_heads', 'SELECT count(*) AS n FROM operation_heads'),
        'members': ('operation_memberships', 'SELECT count(*) AS n FROM operation_memberships m JOIN samples s ON s.id=m.sample_id AND s.version_id=m.version_id'),
        'samples': ('samples', 'SELECT kind,count(*) AS n FROM samples GROUP BY kind'),
        'request_count': ('recommendation_requests', 'SELECT count(*) AS n FROM recommendation_requests'),
        'exposure_count': ('recommendation_exposures', 'SELECT count(*) AS n FROM recommendation_exposures'),
        'feedback_count': ('recommendation_feedback', 'SELECT count(*) AS n FROM recommendation_feedback'),
        'requests': ('recommendation_requests', f'SELECT * FROM recommendation_requests ORDER BY at DESC,id DESC LIMIT {LIMIT}'),
        'exposures': ('recommendation_exposures', f'SELECT * FROM recommendation_exposures WHERE request_id IN ({latest})'),
        'feedback': ('recommendation_feedback', f'SELECT * FROM recommendation_feedback WHERE exposure_id IN ({shown})'),
        'executions': ('recommendation_executions', f'SELECT * FROM recommendation_executions WHERE exposure_id IN ({shown})'),
    }
    data = _read(root / 'operations/catalog.sqlite3', queries, warnings)
    _recommendations(root, data, warnings)
    stats = dict(generated_at=now(), root=str(root), sessions=len(sessions), tasks=tasks,
                 extraction_jobs=queue.get('jobs', []), groups=_count(data, 'groups'), memberships=_count(data, 'members'),
                 samples_by_scope=data.get('samples', []), requests=_count(data, 'request_count'),
                 exposures=_count(data, 'exposure_count'), feedback=_count(data, 'feedback_count'), warnings=warnings)
    atomic_json(root / 'overview.json', stats)
    from proximic_ring.agent_workflow.recording.workbench_view import export_workbench
    try:
        export_workbench(root)
    except (OSError, ValueError, sqlite3.Error):
        warnings.append('任务操作台暂不可刷新，其余历史入口仍可查看。')
    body = f"<p class='muted'>数据根目录：<code>{html.escape(str(root))}</code></p><p class='muted'>导出时间：{html.escape(stats['generated_at'])}。只读查看不会产生推荐曝光。</p>"
    body += ("<article><h2>任务操作台</h2><p>按时间查看全部任务，逐个重新提取 Workflow、"
             "合并或生成通用流程，并按通用组提取参数。</p><p>" +
             _link(root, 'workbench.html', '打开任务操作台 →') + '</p></article>')
    body += render_pipeline(root, _link, stats)
    body += '<details><summary>数据范围与诊断导出</summary>'
    body += '<p>样本范围：' + html.escape('；'.join(f"{r['kind']}：{r['n']}" for r in stats['samples_by_scope']) or '尚无样本') + '。测试与真实使用保持隔离。</p>'
    body += '<p>' + ' · '.join(_link(root, rel, label) for rel, label in [
        ('workflows/status.json','提取队列状态'),('operations/status.json','归并状态'),
        ('operations/catalog.json','操作库 JSON 导出'),('overview.json','总览计数 JSON')]) + '</p>'
    body += '</details><details><summary>独立测试与历史报告</summary><ul>'
    reports = set()
    for pattern in ('workflow-tests/*.html', 'workflow-dedup/report.html', 'workflow-trajectory/report.html', 'recommendation-tests/*/result.json',
                    'recommendation-tests/*/agent-experience/operations/index.html',
                    'operation-prototype-tests/*/index.html', 'operation-prototype-tests/*/operations/index.html',
                    'operation-prototype-tests/*/operations/*.html', 'operations/embedding-benchmark/index.html'):
        reports.update(p.relative_to(root).as_posix() for p in root.glob(pattern) if p.is_file())
    body += ''.join('<li>'+_link(root,rel,rel)+'</li>' for rel in sorted(reports)) or '<li>尚无测试报告</li>'
    body += '</ul></details>'
    body += ''.join('<p class="warning">'+html.escape(w)+'</p>' for w in warnings)
    atomic_text(root / 'overview.html', _page('Agent → Workflow → 推荐：全流程总览', body))
    return root / 'overview.html'


def main(argv=None):
    parser = argparse.ArgumentParser(description='导出全流程只读总览和历史推荐，不调用模型。')
    parser.add_argument('--root', type=Path, required=True)
    print(export_overview(parser.parse_args(argv).root))


if __name__ == '__main__':
    main()
