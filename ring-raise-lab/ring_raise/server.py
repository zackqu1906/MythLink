from __future__ import annotations

import asyncio
import errno
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlparse, parse_qs
import webbrowser
from urllib.request import urlopen

from .capture import Capture, scan
from .common import ROOT, read_json, session_path, write_json
from .data import audit, load_samples, validate_annotations
from .multimodal import audio_audit, validate_multimodal


def serve(root, port=8767, open_browser=True):
    root = Path(root).resolve()
    capture = Capture(root)
    token = secrets.token_urlsafe(24)
    annotation_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def respond(self, value, status=200, content_type='application/json'):
            data = value.encode('utf-8') if isinstance(value, str) else json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type+'; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def host_ok(self):
            return self.headers.get('Host') in (f'127.0.0.1:{port}', f'localhost:{port}')

        def send_audio(self, path):
            size = path.stat().st_size
            start, end, status = 0, size-1, 200
            requested = self.headers.get('Range')
            if requested:
                import re
                match = re.fullmatch(r'bytes=(\d+)-(\d*)', requested)
                if not match:
                    return self.respond({'error':'unsupported range'},416)
                start = int(match[1])
                end = min(int(match[2]), end) if match[2] else end
                if not 0 <= start <= end < size:
                    return self.respond({'error':'invalid range'},416)
                status = 206
            self.send_response(status)
            self.send_header('Content-Type','audio/wav')
            self.send_header('Content-Length',str(end-start+1))
            self.send_header('Accept-Ranges','bytes')
            self.send_header('Cache-Control','no-store')
            if status == 206:
                self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
            self.end_headers()
            with path.open('rb') as f:
                f.seek(start)
                remaining = end-start+1
                while remaining:
                    data = f.read(min(65536, remaining))
                    if not data:
                        break
                    self.wfile.write(data)
                    remaining -= len(data)

        def do_GET(self):
            if not self.host_ok():
                return self.respond({'error': 'invalid host'}, 403)
            url = urlparse(self.path)
            try:
                if url.path == '/':
                    page = (ROOT/'ring_raise/dashboard.html').read_text().replace('__API_TOKEN__', token)
                    return self.respond(page, content_type='text/html')
                if url.path == '/api/status':
                    return self.respond(capture.snapshot())
                if url.path == '/api/sessions':
                    sessions = []
                    for p in sorted(root.glob('*/metadata.json'), reverse=True):
                        meta = read_json(p)
                        ann = read_json(p.parent/'annotations.json') if (p.parent/'annotations.json').exists() else {}
                        sessions.append(dict(session=p.parent.name, config=meta['config'], complete=meta['complete'],
                                             reviewed=ann.get('reviewed', False), synthetic=meta.get('synthetic',False)))
                    return self.respond(sessions)
                if url.path == '/api/session':
                    path = session_path(root, parse_qs(url.query)['id'][0])
                    if path.name == capture.snapshot()['session'] and capture.is_recording():
                        raise ValueError('请停止保存后再复核')
                    meta = read_json(path/'metadata.json')
                    plot = []
                    try:
                        a, x, _ = load_samples(path)
                        stride = max(1, len(a)//5000)
                        plot = [[float(a['t'][i]), *[float(v) for v in x[i]]] for i in range(0,len(a),stride)]
                        quality = audit(path)
                    except (ValueError, IndexError) as exc:
                        quality = dict(usable=False, blockers=[str(exc)], samples=0, duration_s=meta.get('duration_s',0),
                                       endpoint_rate_hz=0, gaps=0, saturated_samples=0)
                    multi = read_json(path/'multimodal_annotations.json') if (path/'multimodal_annotations.json').exists() else None
                    return self.respond(dict(metadata=meta, audit=quality, audio_audit=audio_audit(path), multimodal=multi,
                                             annotations=read_json(path/'annotations.json'), plot=plot))
                if url.path == '/api/audio':
                    path = session_path(root, parse_qs(url.query)['id'][0])
                    if path.name == capture.snapshot()['session'] and capture.is_recording():
                        raise ValueError('请停止保存后再播放')
                    return self.send_audio(path/'audio.wav')
                return self.respond({'error':'not found'},404)
            except Exception as exc:
                return self.respond({'error': str(exc)},400)

        def do_POST(self):
            if not self.host_ok() or self.headers.get('X-Lab-Token') != token:
                return self.respond({'error':'invalid request token'},403)
            try:
                length = int(self.headers.get('Content-Length','0'))
                if not 0 <= length <= 1_000_000:
                    raise ValueError('请求过大')
                body = json.loads(self.rfile.read(length) or b'{}')
                if self.path == '/api/scan':
                    if capture.snapshot()['connection'] != 'disconnected':
                        raise ValueError('请先断开连接再扫描')
                    return self.respond(asyncio.run(scan()))
                if self.path == '/api/connect':
                    capture.connect(body.get('device',''))
                    return self.respond({'ok':True})
                if self.path == '/api/disconnect':
                    capture.disconnect()
                    return self.respond({'ok':True})
                if self.path == '/api/start':
                    return self.respond({'session':capture.start(body)})
                if self.path == '/api/stop':
                    capture.stop()
                    return self.respond({'ok':True})
                if self.path == '/api/annotations':
                    with annotation_lock:
                        path = session_path(root, body['session'])
                        if read_json(path/'metadata.json').get('synthetic'):
                            raise ValueError('合成自检标注只读，不能改成真实观察标签')
                        if path.name == capture.snapshot()['session'] and capture.is_recording():
                            raise ValueError('采集中不能修改标注')
                        ann = validate_annotations(path, body['annotations'])
                        history = path/'annotation_history'
                        history.mkdir(exist_ok=True)
                        write_json(history/f'{time.time_ns()}.json', read_json(path/'annotations.json'))
                        write_json(path/'annotations.json',ann)
                    return self.respond({'ok':True})
                if self.path == '/api/multimodal':
                    with annotation_lock:
                        path = session_path(root, body['session'])
                        if path.name == capture.snapshot()['session'] and capture.is_recording():
                            raise ValueError('采集中不能修改标注')
                        ann = validate_multimodal(path, body['annotations'])
                        history = path/'annotation_history'
                        history.mkdir(exist_ok=True)
                        write_json(history/f'multimodal-{time.time_ns()}.json',read_json(path/'multimodal_annotations.json'))
                        write_json(path/'multimodal_annotations.json',ann)
                    return self.respond({'ok':True})
                return self.respond({'error':'not found'},404)
            except Exception as exc:
                return self.respond({'error':str(exc)},400)

    try:
        server = ThreadingHTTPServer(('127.0.0.1',port),Handler)
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        with urlopen(f'http://127.0.0.1:{port}/api/status',timeout=2) as response:
            existing = json.load(response)
        if existing.get('application') != 'ring-raise-lab-v2' or existing.get('data_root') != str(root):
            raise ValueError('端口被其他程序或不同数据目录占用，请使用另一个 --port') from exc
        print(f'实验台已在运行：http://127.0.0.1:{port}',flush=True)
        if open_browser:
            webbrowser.open(f'http://127.0.0.1:{port}')
        return
    print(f'双路采集实验台：http://127.0.0.1:{port}\n数据目录：{root}\n点击连接建立 BLE；点击开始才启动 IMU / 麦克风。',flush=True)
    if open_browser:
        webbrowser.open(f'http://127.0.0.1:{port}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        capture.close()
        server.server_close()
