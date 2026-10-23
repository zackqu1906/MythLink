"""Read/export local Agent evidence: python -m proximic_ring.agent_workflow.recording.records."""
from __future__ import annotations

import argparse
import html
import json
import sqlite3
from pathlib import Path

from proximic_ring.agent_workflow.recording.recording import records_root
from proximic_ring.agent_workflow.recording.step_view import build_step_view, REASONS
from proximic_ring.agent_workflow.recording.record_controls import CONTROLS_CSS, task_controls, workflow_toolbar
from proximic_ring.agent_workflow.recording.codex_link import task_links, link_html
from proximic_ring.agent_workflow.recording.task_profile import execution_profile
from proximic_ring.agent_workflow.task_metadata import profile_text, validate_profile

STATUS = {"queued":"排队中", "starting":"正在启动", "running":"执行中", "completed":"本轮结束",
          "interrupted":"已中断", "failed":"执行失败", "unfinished":"未正常结束",
          "start_failed":"启动失败", "cancelled_before_start":"执行前取消"}


def read_session(directory):
    """Read one consistent snapshot; never modify the live writer's database."""
    directory = Path(directory)
    meta = json.loads((directory / "session.json").read_text(encoding="utf-8"))
    db = sqlite3.connect((directory / "index.sqlite3").resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    try:
        db.execute("BEGIN")
        result = {"session":meta, "directory":str(directory)}
        for table in ("episodes", "items", "observations", "events"):
            order = "seq" if table == "events" else "created_at" if table == "episodes" else "rowid"
            rows = [dict(r) for r in db.execute(f"SELECT * FROM {table} ORDER BY {order}")]
            for row in rows:
                for key in ("payload", "quality", "context"):
                    if key in row:
                        row[key] = json.loads(row[key])
            result[table] = rows
        return result
    finally:
        db.close()


def sessions(root=None):
    root = Path(root) if root is not None else records_root()
    return sorted((root / "sessions").glob("*/session.json"), key=lambda p:p.stat().st_mtime, reverse=True)


def episode_detail(session, episode_id):
    episode = next(e for e in session["episodes"] if e["id"] == episode_id)
    detail = {"session":session["session"], "episode":episode,
            **{key:[r for r in session[key] if r.get("episode_id") == episode_id]
               for key in ("events", "items", "observations")},
            "step_view":build_step_view(session, episode_id),
            "interpretation":{"goal_verification":"unknown", "semantic_workflow":"not_generated",
                "observations":"The legacy observations index stores tool results. Step observations only annotate explicit returned evidence within its own call; no adjacent before/after states are inferred.",
                "reasoning":"Only returned summaries are retained; absence means not observed."}}
    detail['execution_profile'] = execution_profile(detail)
    return detail


def _classifications(root):
    """Read existing extraction results only; opening records never invokes a model."""
    latest = {}
    for path in (root/'workflows').glob('*.audit.json'):
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            if value.get('source_changed_after_extraction'):
                continue
            profile = value.get('task_profile')
            if profile is not None:
                validate_profile(profile, with_sources=True)
            source, generation = value['source'], value['generation']
            key = (source['session_id'], source['episode_id'])
            entry = dict(profile=profile, created_at=generation['created_at'],
                         prompt_version=generation['prompt_version'], audit_file=str(path.relative_to(root)))
            if key not in latest or entry['created_at'] > latest[key]['created_at']:
                latest[key] = entry
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return latest


def _pretty(value):
    return html.escape(json.dumps(value, ensure_ascii=False, indent=2))


def _attachments(value):
    if isinstance(value, dict):
        if value.get("attachment") and value.get("mime_type", "").startswith("image/"):
            yield value
        for child in value.values():
            yield from _attachments(child)
    elif isinstance(value, list):
        for child in value:
            yield from _attachments(child)


def _page(title, body):
    # All content is escaped. No remote resources or scripts run in exported evidence.
    return ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; img-src 'self' file:; style-src 'unsafe-inline'\">"
        f"<title>{html.escape(title)}</title><style>"
        "body{font:15px/1.65 system-ui,sans-serif;background:#f5f5f3;color:#222;max-width:1050px;margin:40px auto;padding:0 24px}"
        "a{color:#245eb5}h1{font-size:26px}h2{font-size:19px;margin-top:28px}"
        "article,details{background:white;border:1px solid #ddd;border-radius:10px;padding:12px 18px;margin:12px 0}"
        "summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.6 ui-monospace,monospace}"
        "small,.muted{color:#666}img{max-width:100%;height:auto;border:1px solid #ddd}"
        ".warning{background:#fff1ce;padding:12px;border-radius:8px}table{width:100%;border-collapse:collapse}"
        "td,th{text-align:left;vertical-align:top;padding:9px;border-bottom:1px solid #ddd}"
        f"{CONTROLS_CSS}</style><body><h1>{html.escape(title)}</h1>{body}</body></html>")


def _write(path, text):
    from uuid import uuid4
    temp = path.with_suffix(path.suffix + '.' + uuid4().hex + ".tmp")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def _event_links(refs):
    return ' · '.join(f"<a href='#event-{int(r['seq'])}' title='{html.escape(str(r.get('event_id','')),quote=True)}'>"
                      f"事件 #{int(r['seq'])}</a>" for r in refs)


def _reasons(codes):
    return '；'.join(html.escape(REASONS.get(code,code)) for code in dict.fromkeys(codes))


def _images(value, directory):
    body, seen = '', set()
    for attachment in _attachments(value):
        relative = attachment['attachment']
        target = (directory / relative).resolve()
        if target in seen or not target.is_relative_to((directory / 'attachments').resolve()) or not target.is_file():
            continue
        seen.add(target)
        body += (f"<details><summary>查看已记录的返回图片</summary><img loading='lazy' alt='工具返回的图片' "
                 f"src='{html.escape(relative,quote=True)}'></details>")
    return body


def _call_label(step):
    call = step['tool_call']
    return '.'.join(str(call['data'][k]) for k in ('server','namespace','tool') if call['data'].get(k)) or call['type']


def _evidence_source(entry, steps):
    """Resolve provenance by recorded identity/evidence, never by proximity."""
    call_id = entry.get('source_call_id')
    if call_id:
        event_ids = {r['event_id'] for r in entry['event_refs'] if r.get('event_id')}
        matches = [step for step in steps if step['call_id'] == call_id and
                   any(r.get('event_id') in event_ids for r in step['execution_result']['event_refs'])]
        detail = f"<p>来源调用标识：<code>{html.escape(str(call_id))}</code></p>"
        if len(matches) == 1:
            step = matches[0]
            link = (f"<a href='#step-{html.escape(step['step_id'],quote=True)}'>"
                    f"步骤 {step['ordinal']} · {html.escape(_call_label(step))}</a>")
            return '来源：' + link, detail
        return '来源：已记录的工具调用（来源步骤未唯一匹配）', detail
    item_ids = list(dict.fromkeys(r['item_id'] for r in entry['event_refs'] if r.get('item_id')))
    detail = '<p>来源消息标识：' + ' · '.join(html.escape(str(i)) for i in item_ids) + '</p>' if item_ids else ''
    return '来源：Agent 消息（见原始事件）', detail


def _step_slot(title, slot, directory, steps):
    body = f"<h3>{html.escape(title)}</h3>"
    if not slot['entries']:
        return body + f"<p class='muted'>{_reasons(slot['reasons'])}</p>"
    labels = {'accessibility_tree':'无障碍界面树', 'accessibility_diff':'无障碍界面树变化',
              'accessibility_unchanged':'无障碍界面树未变化', 'screenshot':'截图返回',
              'reasoning':'返回的推理摘要', 'agentMessage':'Agent 返回的文字'}
    for entry in slot['entries']:
        origin, detail = _evidence_source(entry, steps)
        label = labels.get(entry.get('kind',entry['source']),entry['source'])
        body += (f"<details><summary>已记录 · {html.escape(label)} · {origin}</summary>"
                 + detail +
                 f"<p class='muted'>{_reasons(entry['reasons'])}</p>"
                 f"<p>{_event_links(entry['event_refs'])}</p><pre>{_pretty(entry['content'])}</pre>"
                 + _images(entry['content'],directory) + '</details>')
    return body


def _step_html(view, directory, filename):
    body = ("<h2>步骤视图</h2><p>每次工具调用为一个步骤，按实际记录顺序排列，展示原始参数和本次执行结果。"
            "只有返回内容中可明确识别的观察才列出，且只属于产生它的调用；不从相邻调用补齐前后状态。</p>"
            "<p>一次调用可能包含操作和观察，保留完整代码，不推断内部动作全部执行成功。"
            "调用前的 Agent 文字按时间展示，不自动认定为行动理由。提交前应用上下文仅保留在任务上下文中。</p>"
            f"<p><a href='{html.escape(filename,quote=True)}'>下载步骤 JSON（含原始事件引用）</a></p>")
    if view['warnings']:
        body += f"<p class='warning'>{_reasons(view['warnings'])}</p>"
    if not view['steps']:
        body += "<p class='muted'>当前任务没有可整理的工具调用。原始事件仍保留在下方时间线。</p>"
    for step in view['steps']:
        call, result = step['tool_call'], step['execution_result']
        label = _call_label(step)
        body += (f"<article id='step-{step['step_id']}'><h3>步骤 {step['ordinal']} · {html.escape(label)}</h3>"
                 f"<p class='muted'>调用标识：{html.escape(str(step['call_id']))} · "
                 f"开始：{html.escape(step['started_at'] or '未记录')} · 结束：{html.escape(step['ended_at'] or '未记录')}</p>")
        if step['warnings']:
            body += f"<p class='warning'>{_reasons(step['warnings'])}</p>"
        body += _step_slot('调用前 Agent 文字',step['preceding_agent_messages'],directory,view['steps'])
        body += (f"<h3>工具调用</h3><p>{_event_links(call['event_refs'])}</p>"
                 f"<pre>{_pretty(call['data'])}</pre>")
        result_label = ('收到条目结束事件' if result['completion_received'] else
                        '记录到客户端回复尝试' if result['basis']=='client_reply_attempt' else '缺少结束事件')
        body += (f"<h3>执行结果</h3><p>工具报告状态：{html.escape(str(result['reported_status'] or '未提供'))}"
                 f" · {result_label}</p>"
                 f"<p>{_event_links(result['event_refs'])}</p>")
        if result['reasons']:
            body += f"<p class='muted'>{_reasons(result['reasons'])}</p>"
        if result['data']:
            body += f"<details><summary>返回结果与错误</summary><pre>{_pretty(result['data'])}</pre></details>"
            body += _images(result['data'],directory)
        body += _step_slot('本次调用中的观察',step['recorded_observations'],directory,view['steps'])
        body += (f"<details><summary>本步骤全部原始事件引用</summary><p>{_event_links(step['source_event_refs'])}"
                 "</p></details></article>")
    if view['unassigned_event_refs']:
        body += '<p>未整理成步骤的条目：' + _event_links(view['unassigned_event_refs']) + '</p>'
    return body


def export_html(root=None):
    """Create a local, script-free index and per-task evidence pages."""
    root = Path(root) if root is not None else records_root()
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    workflow_page = 'library.html' if (root/'workflows/library.html').exists() else 'index.html'
    from proximic_ring.agent_workflow.reporting.page_view import stats_cards
    rows, warnings = [], []
    classifications = _classifications(root)
    task_statuses=[]
    for manifest in sessions(root):
        try:
            data = read_session(manifest.parent)
        except (OSError, ValueError, sqlite3.Error) as exc:
            warnings.append(f"会话 {manifest.parent.name} 暂不可读取（{type(exc).__name__}）；原始日志仍可检查。")
            continue
        meta = data["session"]
        codex_links = task_links(data)
        if meta.get("recordingError") or meta.get("recordingDroppedEvents"):
            warnings.append(f"会话 {meta['session_id']} 记录不完整：{meta.get('recordingError','')}，丢失 {meta.get('recordingDroppedEvents',0)} 个事件。")
        for episode in reversed(data["episodes"]):
            task_statuses.append(episode["status"])
            detail = episode_detail(data, episode["id"])
            page = manifest.parent / ("episode-" + episode["id"] + ".html")
            steps_path = page.with_suffix('.steps.json')
            _write(steps_path,json.dumps(detail['step_view'],ensure_ascii=False,indent=2))
            classification = classifications.get((meta['session_id'], episode['id']))
            profile_path = page.with_suffix('.profile.json')
            _write(profile_path,json.dumps(dict(execution=detail['execution_profile'],
                classification=classification),ensure_ascii=False,indent=2))
            body = ("<p><a href='../../index.html'>← 全部工作记录</a></p>"
                f"<p><a href='../../workbench.html#{meta['session_id']}%2F{episode['id']}'>在操作台处理此任务 →</a></p>"
                f"<p>{html.escape(episode['created_at'])} · {html.escape(episode['source'])} · "
                f"{html.escape(STATUS.get(episode['status'],episode['status']))}</p>"
                f"<p>{link_html(codex_links[episode['id']])}</p>"
                "<p class='warning'>目标完成情况：未自动验证。工具完成、计划完成和 Agent 的最终陈述均不等于目标已验证成功。"
                "本页步骤由记录直接整理；界面状态仅以实际返回内容为依据。</p>"
                + workflow_toolbar(f'../../workflows/{workflow_page}',navigation=False)
                + task_controls(meta['session_id'], episode['id'], status=episode['status'])
                + f"<details><summary>任务上下文与标识</summary><pre>{_pretty(episode)}</pre></details>")
            profile = detail['execution_profile']
            body += (f"<h2>任务类型与执行方式</h2><p>{html.escape(profile_text(classification.get('profile') if classification else None))}</p>"
                     f"<p>实际记录：{profile['tool_call_count']} 次工具调用 · {profile['response_count']} 条回答；"
                     f"工具类型：{html.escape('、'.join(profile['tool_type_counts']) or '未记录工具调用')}。</p>"
                     "<p class='muted'>分类随后台提取生成；工具类型仅说明实现方式，不据此推断任务目的或执行成功。</p>"
                     f"<p><a href='{profile_path.name}'>执行信息与分类来源</a></p>")
            if classification:
                body += f"<p><a href='../../{html.escape(classification['audit_file'],quote=True)}'>分类证据与提取版本</a></p>"
            if not any(i["type"] == "reasoning" and i["payload"].get("summary") for i in detail["items"]):
                body += "<p class='muted'>未收到推理摘要；未补写或推测。</p>"
            body += _step_html(detail['step_view'],manifest.parent,steps_path.name)
            body += "<details><summary>原有消息与工具条目</summary>"
            for item in detail["items"]:
                p = item["payload"]
                label = {"agentMessage":"Agent 文字", "reasoning":"推理摘要", "plan":"计划",
                         "mcpToolCall":"工具调用", "dynamicToolCall":"客户端工具"}.get(item["type"],item["type"])
                body += (f"<article><strong>{html.escape(label)} {html.escape(str(p.get('tool','')))}</strong>"
                         f" <small>{html.escape(item['item_id'])} · {html.escape(item['status'])}</small>")
                if item["type"] in ("agentMessage", "plan"):
                    body += f"<pre>{html.escape(p.get('text',''))}</pre>"
                elif item["type"] == "reasoning":
                    body += "<pre>" + html.escape("\n\n".join(p.get("summary", []))) + "</pre>"
                body += f"<details><summary>完整条目 · 事件 {item['first_seq']}–{item['last_seq']}</summary><pre>{_pretty(p)}</pre></details>"
                body += _images(p,manifest.parent)
                body += "</article>"
            body += '</details>'
            body += ("<h2>完整事件时间线</h2><p class='muted'>一条事件是一次 RPC 消息或客户端本地记录，不等于一次任务或操作。"
                     "同一次调用会产生开始、返回、权限处理、状态变化等多条事件；文字增量也分别记录。"
                     "包含计划更新、追问、回答、用户干预、用量、错误和原始调用。图片数据已提取为附件。</p>")
            for event in detail["events"]:
                body += (f"<details id='event-{event['seq']}'><summary>#{event['seq']} "
                         f"{html.escape(event['at'])} · {html.escape(event['direction'])} · {html.escape(event['method'])}</summary>"
                         f"<pre>{_pretty(event)}</pre></details>")
            body += "<p><a href='events.jsonl'>原始事件日志</a> · <a href='session.json'>会话元数据</a></p>"
            _write(page, _page(episode["goal"], body))
            href = page.relative_to(root).as_posix()
            rows.append(f"<tr><td>{html.escape(episode['created_at'])}</td><td><a href='{href}'>"
                        f"{html.escape(episode['goal'])}</a><br><small>{html.escape(episode['source'])}</small></td>"
                        f"<td>{html.escape(STATUS.get(episode['status'],episode['status']))}</td><td>"
                        + link_html(codex_links[episode['id']])
                        + task_controls(meta['session_id'],episode['id'],status=episode['status'])
                        + '</td></tr>')
        # Keep setup and unresolved events accessible, even for a connection with no tasks.
        diagnostic = "<p><a href='../../index.html'>← 全部工作记录</a></p><pre>" + _pretty(meta) + "</pre>"
        for event in data["events"]:
            if not event.get("episode_id"):
                diagnostic += f"<details><summary>#{event['seq']} {html.escape(event['method'])}</summary><pre>{_pretty(event)}</pre></details>"
        _write(manifest.parent / "session.html", _page("会话与未关联事件", diagnostic))
    body = "<p>用户通过 Agent 完成的任务记录。点击任务查看本次调用、返回内容和事件；任务结束不自动等同于目标已验证成功。</p>"
    body += stats_cards([(len(task_statuses),'已记录任务'),(sum(s=='completed' for s in task_statuses),'本轮正常结束'),
                         (sum(s in ('queued','starting','running') for s in task_statuses),'进行中'),
                         (sum(s not in ('completed','queued','starting','running') for s in task_statuses),'失败或中断')])
    body += workflow_toolbar(f'workflows/{workflow_page}')
    body += "".join(f"<p class='warning'>{html.escape(w)}</p>" for w in warnings)
    body += ("<table><thead><tr><th>发起时间（UTC）</th><th>任务</th><th>执行状态</th><th>操作</th></tr></thead><tbody>" +
             "".join(rows) + "</tbody></table>" if rows else "<p>尚无任务记录。启用 Agent 并提交任务后会自动记录。</p>")
    body += "<details><summary>会话与原始日志</summary><ul>"
    for manifest in sessions(root):
        relative = manifest.parent.relative_to(root).as_posix()
        body += f"<li><a href='{relative}/session.html'>{html.escape(manifest.parent.name)}</a> · <a href='{relative}/events.jsonl'>事件日志</a></li>"
    _write(root / "index.html", _page("Agent 任务与记录", body + "</ul></details>"))
    from proximic_ring.agent_workflow.reporting.overview import export_overview
    export_overview(root)
    return root / "index.html"


def main(argv=None):
    parser = argparse.ArgumentParser(description="回查本地 Agent 记录；不调用模型、不操作桌面。")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--episode", help="导出指定 Episode 的完整 JSON")
    parser.add_argument("--html", action="store_true", help="生成本地 HTML 回查页面")
    args = parser.parse_args(argv)
    if args.html:
        print(export_html(args.root)); return
    for manifest in sessions(args.root):
        data = read_session(manifest.parent)
        if args.episode:
            if any(e["id"] == args.episode for e in data["episodes"]):
                print(json.dumps(episode_detail(data,args.episode),ensure_ascii=False,indent=2)); return
        else:
            for episode in data["episodes"]:
                print(json.dumps(episode,ensure_ascii=False))
    if args.episode:
        parser.error("未找到该 Episode")


if __name__ == "__main__":
    main()
