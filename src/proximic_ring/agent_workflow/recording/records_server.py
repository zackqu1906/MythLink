"""App-owned loopback viewer. HTTP requests enqueue work, never invoke a model."""
from __future__ import annotations

import json
import html
import mimetypes
import secrets
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from proximic_ring.agent_workflow.recording.record_controls import CONTROLS_JS
from proximic_ring.agent_workflow.recording.records import export_html
from proximic_ring.agent_workflow.extraction.workflow_store import WorkflowStore, TERMINAL
from proximic_ring.agent_workflow.extraction.workflow_view import export_workflows
from .task_list import task_list
from proximic_ring.agent_workflow import pipeline_policy


class RecordsServer:
    def __init__(self, root, *, on_automatic, automatic=True, busy=False, on_context=None):
        self.root = Path(root).resolve()
        self.on_context = on_context or (lambda: {})
        self.on_automatic = on_automatic
        self._state_lock = threading.Lock()
        self._actions = threading.Lock()
        self._state = dict(automatic=automatic, busy=busy, error='')
        self.prefix = '/' + secrets.token_urlsafe(32) + '/'
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass  # URLs contain a per-process capability; do not log them.

            def reply(self, code, body, content_type='application/json; charset=utf-8'):
                if not isinstance(body, bytes):
                    body = json.dumps(body, ensure_ascii=False).encode('utf-8')
                self.send_response(code)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('X-Frame-Options', 'DENY')
                self.end_headers()
                self.wfile.write(body)

            def route(self):
                path = urlsplit(self.path).path
                if self.headers.get('Host') != owner.host or not path.startswith(owner.prefix):
                    self.reply(403, {'error': '记录页面连接已失效，请从应用重新打开'})
                    return None
                return unquote(path[len(owner.prefix):])

            def do_GET(self):
                route = self.route()
                if route is None:
                    return
                try:
                    if route == 'api/status':
                        self.reply(200, owner.status())
                    elif route == 'api/workbench':
                        from .workbench_data import snapshot
                        with owner._state_lock:
                            state = dict(owner._state)
                        if not pipeline_policy.legacy_workflow_enabled():
                            state.update(automatic=False, pipeline_enabled=False, error=pipeline_policy.DISABLED_MESSAGE)
                        self.reply(200, dict(snapshot(owner.root), worker=state))
                    elif route == 'workbench.js':
                        from .workbench_view import SCRIPT
                        self.reply(200, SCRIPT.encode(), 'text/javascript; charset=utf-8')
                    elif route == 'operations.js':
                        from proximic_ring.agent_workflow.recommendation.recommendation_view import OPERATIONS_JS
                        self.reply(200, OPERATIONS_JS.encode(), 'text/javascript; charset=utf-8')
                    elif route == 'controls.js':
                        self.reply(200, CONTROLS_JS.encode(), 'text/javascript; charset=utf-8')
                    elif not route.startswith('api/'):
                        path = (owner.root / (route or 'index.html')).resolve()
                        workbench = path == owner.root/'workbench.html'
                        if workbench:
                            from .workbench_view import export_workbench
                            export_workbench(owner.root)
                        read_only_report = path in {owner.root/'overview.html', owner.root/'recommendations/index.html'}
                        if read_only_report:
                            # Refresh saved-data views only. No ranking, exposure,
                            # queue changes or browser polling scripts on these pages.
                            from proximic_ring.agent_workflow.reporting.overview import export_overview
                            export_overview(owner.root)
                        allowed = {'.html', '.txt', '.json', '.jsonl', '.png', '.jpg', '.jpeg', '.gif', '.webp'}
                        if not path.is_relative_to(owner.root) or path.suffix not in allowed or not path.is_file():
                            self.reply(404, {'error': '未找到记录文件'})
                            return
                        content = path.read_bytes()
                        if path.suffix == '.html' and not read_only_report:
                            text = content.decode('utf-8').replace(
                                "default-src 'none'; img-src 'self' file:; style-src 'unsafe-inline'",
                                "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
                                "script-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'")
                            scripts = ('workbench.js',) if workbench else ('controls.js', 'operations.js')
                            if not pipeline_policy.legacy_workflow_enabled():
                                scripts = ('workbench.js',) if workbench else ('controls.js',)
                                text = text.replace('</body>', '<p role="status">' +
                                    html.escape(pipeline_policy.DISABLED_MESSAGE) + '</p></body>')
                            text = text.replace('</body>', ''.join(f"<script src='{owner.prefix}{s}'></script>" for s in scripts)+'</body>')
                            content = text.encode('utf-8')
                        mime = mimetypes.guess_type(path.name)[0] or 'text/plain'
                        if path.suffix == '.jsonl':
                            mime = 'text/plain'
                        self.reply(200, content, mime + ('; charset=utf-8' if mime.startswith('text/') else ''))
                    else:
                        self.reply(404, {'error': '未知操作'})
                except (OSError, ValueError, sqlite3.Error):
                    self.reply(503, {'error': '记录暂不可读取，请稍后刷新'})

            def do_POST(self):
                route = self.route()
                if route is None:
                    return
                # Same-origin JSON only. No CORS, URL-triggered writes or form submits.
                if (self.headers.get('Origin') != owner.origin or
                        self.headers.get('Content-Type', '').split(';')[0] != 'application/json'):
                    self.reply(403, {'error': '请从本机记录页面提交操作'})
                    return
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if not 0 < size <= (32768 if route == 'api/workbench/adopt' else 4096):
                        raise ValueError('请求大小不正确')
                    payload = json.loads(self.rfile.read(size))
                    if not isinstance(payload, dict):
                        raise ValueError('请求格式不正确')
                    result = owner.action(route, payload)
                    self.reply(200, result)
                except ValueError as exc:
                    self.reply(400, {'error': str(exc)})
                except (OSError, sqlite3.Error):
                    self.reply(503, {'error': '记录暂不可读取或写入，请稍后重试'})

        self.http = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.http.daemon_threads = True
        self.host = f'127.0.0.1:{self.http.server_port}'
        self.origin = 'http://' + self.host
        self.url = self.origin + self.prefix + 'index.html'
        self.overview_url = self.origin + self.prefix + 'overview.html'
        self.thread = threading.Thread(target=self.http.serve_forever, kwargs={'poll_interval': .1},
                                       daemon=True, name='AgentRecordsHTTP')
        self.thread.start()

    def update(self, **state):
        with self._state_lock:
            self._state.update(state)

    def close(self):
        def shutdown():
            self.http.shutdown()
            self.http.server_close()
        threading.Thread(target=shutdown, daemon=True, name='AgentRecordsClose').start()

    def status(self):
        store = WorkflowStore(self.root)
        try:
            with self._state_lock:
                state = dict(self._state)
            state['pipeline_enabled'] = pipeline_policy.legacy_workflow_enabled()
            if not state['pipeline_enabled']:
                state.update(automatic=False, error=pipeline_policy.DISABLED_MESSAGE)
            # Only public job metadata. Credentials stay in the existing stdin bridge.
            return dict(state, jobs=store.jobs(), tasks=task_list(self.root), terminal=sorted(TERMINAL))
        finally:
            store.close()

    def operation_action(self, route, payload):
        pipeline_policy.require_legacy_workflow()
        from proximic_ring.agent_workflow.operations.operation_store import OperationStore
        from proximic_ring.agent_workflow.recommendation.recommendation_service import request_recommendations, selection
        store = OperationStore(self.root)
        try:
            if route == 'api/operations/recommend':
                scope = payload.get('scope', 'user')
                if 'context' in payload:
                    if scope != 'test' or not isinstance(payload['context'], dict):
                        raise ValueError('手动模拟场景只能用于测试范围')
                    context = dict(payload['context'], source='manual_demo')
                else:
                    context = self.on_context() if scope == 'user' else {}
                return request_recommendations(store, context, scope=scope,
                    limit=payload.get('limit', 4), trigger=payload.get('trigger', 'manual'))
            if route == 'api/operations/exposure':
                identifier = store.expose(payload.get('request_id'), payload.get('operation_id'))
                return {'exposure_id': identifier}
            if route == 'api/operations/feedback':
                store.feedback(payload.get('exposure_id'), payload.get('operation_id'), payload.get('kind'))
                result = {'message': '反馈已记录；不改变本版统计分数，未执行电脑操作'}
                if payload.get('kind') == 'accepted':
                    result['selection'] = selection(store, payload.get('exposure_id'))
                return result
            raise ValueError('未知推荐操作')
        finally:
            store.close()

    def action(self, route, payload):
        with self._actions:
            if route != 'api/refresh':
                pipeline_policy.require_legacy_workflow()
            if route in ('api/workbench/run', 'api/workbench/adopt'):
                from .workbench_actions import enqueue, adopt
                return (adopt if route.endswith('/adopt') else enqueue)(self.root, payload)
            if route.startswith('api/operations/'):
                return self.operation_action(route, payload)
            if route == 'api/automatic':
                enabled = payload.get('automatic')
                if type(enabled) is not bool:
                    raise ValueError('自动提取设置不正确')
                self.on_automatic(enabled)
                self.update(automatic=enabled)
                return {'message': '已开启任务结束后自动提取' if enabled else '已关闭自动提取；已排队任务继续处理'}
            if route not in ('api/extract', 'api/latest', 'api/retry-failed', 'api/refresh'):
                raise ValueError('未知操作')
            store = WorkflowStore(self.root)
            try:
                if route == 'api/refresh':
                    export_workflows(store)
                    export_html(self.root)
                    return {'message': '记录已刷新'}
                if route == 'api/extract':
                    selected = [(payload.get('session_id'), payload.get('episode_id'))]
                elif route == 'api/latest':
                    task = next((t for t in task_list(self.root) if t['status'] in TERMINAL), None)
                    if task is None:
                        raise ValueError('还没有已结束的任务')
                    selected = [(task['session_id'], task['id'])]
                else:
                    latest = {}
                    for job in store.jobs():
                        latest.setdefault((job['session_id'], job['episode_id']), job)
                    selected = [key for key, job in latest.items() if job['status'] == 'failed']
                ids, errors = [], []
                from proximic_ring.agent_workflow.extraction.workflows import source_packet
                for sid, eid in selected:
                    if not isinstance(sid, str) or not isinstance(eid, str):
                        raise ValueError('任务标识格式不正确')
                    try:
                        packet = source_packet(self.root, sid, eid)
                        ids.append(store.enqueue(packet, manual=True))
                    except (ValueError, OSError, sqlite3.Error) as exc:
                        if route != 'api/retry-failed':
                            raise
                        errors.append(eid + '：' + (str(exc) if isinstance(exc, ValueError) else '原始记录暂不可读取'))
                message = f'已提交 {len(ids)} 个任务，等待后台提取；正在排队或提取的同一任务不会重复提交。'
                if not selected:
                    message = '没有需要重试的失败提取。'
                if errors:
                    message += ' 部分任务未提交：' + '；'.join(errors)
                return {'message': message, 'jobs': ids, 'errors': errors}
            finally:
                store.close()
