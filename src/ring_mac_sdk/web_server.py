"""Local UI: one asyncio loop, one owning browser, serialized SDK commands."""
import asyncio
from collections import deque
from dataclasses import asdict, is_dataclass
import json
from pathlib import Path
import time
import webbrowser

from . import Ring, RingError, scan
from .system_cursor import SystemCursor, CursorConfig

ASSETS = Path(__file__).with_name('web')


class Controller:
    def __init__(self, *, demo=False, ring_factory=Ring, scanner=scan, cursor=None):
        if demo:
            from .web_demo import DemoRing
            ring_factory = DemoRing
        self.demo, self.factory, self.scanner = demo, ring_factory, scanner
        self.ring = None
        self.devices = {}
        self.lock = asyncio.Lock()
        self.logs = deque(maxlen=100)
        self.points = deque(maxlen=800)
        self.key = None
        self.revision = self.log_id = 0
        self.state = dict(demo=demo, busy=False, link='DISCONNECTED', stage='STOPPED',
                          name=None, firmware=None, battery=None, devices=[], error=None,
                          session=None, generation=0, has_ring=False, status_message='', diagnostics=None)
        self.cursor = cursor or SystemCursor(demo=demo)
        self.cursor.on_fault = self.error
        self.clear()

    def clear(self):
        self.revision += 1
        self.points.clear()
        self.state.update(position=[0., 0.], contact=None, preview=None, click=None)

    def log(self, kind, message, detail=None):
        self.log_id += 1
        self.logs.append(dict(id=self.log_id, time=time.strftime('%H:%M:%S'),
                              kind=kind, message=message, detail=detail))

    def connection(self, value):
        self.state['link'] = value
        if value != 'CONNECTED':
            self.cursor.disable('设备连接结束，系统控制已停止')
            self.clear()
        if value == 'DISCONNECTED':
            self.state.update(stage='STOPPED', battery=None)
        self.log('connection', value)

    def status(self, value):
        key = (value.session_id, value.generation)
        if key != self.key or value.stage != 'RUNNING':
            self.cursor.disable('触摸板停止、故障或代次变化；系统控制需手动重新开启')
            self.clear()
        if self.key and self.key[0] != key[0]:
            self.state['diagnostics'] = None
        self.key = key
        self.state.update(stage=value.stage, session=value.session_id, generation=value.generation,
                          status_message=value.message)
        self.log('status', value.stage, asdict(value))
        if value.stage == 'FAILED':
            error = getattr(getattr(self.ring, 'touchpad', None), 'error', None)
            self.error(error or RingError('stream_failed', value.message))

    def event(self, value):
        if self.state['stage'] != 'RUNNING' or self.state['link'] != 'CONNECTED':
            return
        key = (value.meta.session_id, value.meta.generation)
        if key != self.key:
            # Only RUNNING STATUS may authorize a new stream generation.
            return
        self.cursor.submit(value)  # Every SDK event, before the 10 Hz browser snapshot.
        kind = type(value).__name__
        if kind == 'Motion':
            # Raw SDK deltas for independent page-only visualization.
            x, y = self.state['position']
            self.state['position'] = [x+value.dx, y+value.dy]
            self.points.append([x+value.dx, y+value.dy, value.mode])
        elif kind == 'ContactUpdate':
            self.state['contact'] = None if value.phase in ('END', 'CANCEL') else dict(mode=value.mode, phase=value.phase)
            self.log('contact', value.phase+' / '+value.mode)
        elif kind == 'Click':
            if value.is_preview:
                self.state['preview'] = dict(id=value.click_id, expires=time.monotonic()+1)
                self.log('preview', '点击预告（尚未确认）')
            else:
                self.state['preview'] = None
                self.state['click'] = dict(button=value.button, count=value.count, id=value.click_id)
                self.log('click', f'正式点击 · {value.button} × {value.count}')

    def error(self, exc):
        response = getattr(exc, 'response', None)
        data = dict(reason=getattr(exc, 'reason', 'error'), message=str(exc),
                    device_code=getattr(exc, 'device_code', None), phase=getattr(exc, 'phase', None),
                    response=asdict(response) if is_dataclass(response) else response)
        hints = {'busy': '已有操作进行中，请等待完成。',
                 'not_ready': '请先连接设备，再开始触摸板。',
                 'timeout': '等待设备响应超时，请检查设备与蓝牙状态。',
                 'unsupported': '此设备或固件不支持当前操作。'}
        data['hint'] = hints.get(data['reason'], '请查看详细错误，确认设备状态后再手动重试。')
        if data['device_code'] == -16:
            data['hint'] = '设备拒绝启动：可能处于忙碌、充电、低电压或模式冲突，不能只凭 -16 确定原因。'
            battery = self.state['battery']
            if battery is not None and battery <= 5:
                data['hint'] += f' 本次连接最近读取的电量为 {battery}%，低电量可能相关；可先充电，再取下重试。'
        self.state['error'] = data
        self.log('error', data['hint'], data)

    async def execute(self, command):
        if command.get('action') == 'cursor_stop':
            self.cursor.disable('用户紧急停止系统控制')
            self.log('cursor', '系统控制已停止；队列与余量已丢弃')
            return
        if self.lock.locked():
            self.error(RingError('busy'))
            return
        async with self.lock:
            self.state.update(busy=True, error=None)
            try:
                action = command.get('action')
                if action == 'cursor_permission':
                    await self.cursor.check_permission()
                elif action == 'cursor_enable':
                    if self.state['link'] != 'CONNECTED' or self.state['stage'] != 'RUNNING':
                        raise RingError('not_ready', '请先连接设备并开始触摸板，再明确开启系统控制')
                    key, ring = self.key, self.ring
                    activated = await self.cursor.enable(key, CursorConfig(**command.get('config', {})),
                        still_current=lambda: (self.key == key and self.ring is ring
                                               and self.state['link'] == 'CONNECTED'
                                               and self.state['stage'] == 'RUNNING'))
                    if activated:
                        self.log('cursor', '用户开启系统控制', self.cursor.snapshot()['config'])
                elif action == 'scan':
                    if self.ring:
                        raise RingError('busy', '请先断开当前设备再扫描')
                    if self.demo:
                        self.devices = {'demo-ring': None}
                        self.state['devices'] = [dict(identifier='demo-ring', name='演示戒指 · 模拟数据', rssi=-42)]
                    else:
                        found = await self.scanner(5)
                        self.devices = {d.identifier: d for d in found}
                        self.state['devices'] = [dict(identifier=d.identifier, name=d.name or '未命名戒指', rssi=d.rssi) for d in found]
                    self.log('scan', f"发现 {len(self.devices)} 个设备")
                elif action == 'connect':
                    if self.ring:
                        raise RingError('busy', '请先断开当前设备')
                    identifier = command.get('identifier')
                    if identifier not in self.devices:
                        raise RingError('not_ready', '请先扫描并选择设备')
                    self.clear()
                    device = next(d for d in self.state['devices'] if d['identifier'] == identifier)
                    self.state.update(name=device['name'], firmware=None, battery=None)
                    callbacks = dict(on_connection=self.connection, on_touchpad=self.event, on_touchpad_status=self.status)
                    if not self.demo:
                        async def event(value):
                            self.event(value)
                        async def status(value):
                            self.status(value)
                        async def diagnostic(value):
                            if value.session_id == self.state['session']:
                                self.state['diagnostics'] = asdict(value)
                        callbacks.update(on_touchpad=event, on_touchpad_status=status, on_touchpad_diagnostics=diagnostic)
                    self.ring = self.factory(self.devices[identifier], **callbacks)
                    self.state['has_ring'] = True
                    info = await self.ring.connect()
                    self.state['firmware'] = info.firmware_version
                    self.state['battery'] = await self.ring.battery_level()
                elif action == 'disconnect':
                    await self.release()
                elif action == 'clear':
                    self.revision += 1
                    self.points.clear()
                    self.state['position'] = [0., 0.]
                    self.log('clear', '已清空轨迹，当前位置归零')
                elif action in ('start', 'stop', 'battery'):
                    if not self.ring or self.state['link'] != 'CONNECTED':
                        raise RingError('not_ready')
                    if action == 'start':
                        if self.state['stage'] not in ('STOPPED', 'FAILED'):
                            raise RingError('busy')
                        await self.ring.touchpad.start()
                    elif action == 'stop':
                        self.cursor.disable('触摸板停止，系统控制已停止')
                        await self.ring.touchpad.stop()
                        self.clear()
                        if self.ring.touchpad.status.stage == 'FAILED':
                            raise self.ring.touchpad.error or RingError('stop_unconfirmed')
                    else:
                        self.state['battery'] = await self.ring.battery_level()
                else:
                    raise ValueError('未知页面操作')
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.error(exc)
            finally:
                self.state['busy'] = False

    async def release(self):
        self.cursor.disable('页面或设备断开，系统控制已停止')
        ring, self.ring = self.ring, None
        try:
            if ring:
                await ring.disconnect()
        finally:
            await self.cursor.close()
            self.state.update(has_ring=False, firmware=None, name=None, session=None, generation=0)
            self.key = None
            self.connection('DISCONNECTED')

    def snapshot(self):
        if self.state['preview'] and self.state['preview']['expires'] < time.monotonic():
            self.state['preview'] = None
        return dict(**self.state, revision=self.revision, points=list(self.points), logs=list(self.logs),
                    cursor=self.cursor.snapshot())


def create_app(*, demo=False, controller=None):
    from aiohttp import web, WSMsgType
    controller = controller or Controller(demo=demo)
    owner = None
    handlers = set()
    cleanups = set()

    @web.middleware
    async def local_only(request, handler):
        # Origin + Host guard prevents unrelated web pages controlling local BLE.
        if request.host not in (f'127.0.0.1:{request.url.port}', f'localhost:{request.url.port}'):
            raise web.HTTPForbidden(text='仅允许 localhost')
        if request.path == '/ws' and request.headers.get('Origin') != f'http://{request.host}':
            raise web.HTTPForbidden(text='页面来源不匹配')
        response = await handler(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; connect-src 'self'; style-src 'self'; script-src 'self'; frame-ancestors 'none'"
        return response

    async def asset(request):
        name = request.match_info.get('name', 'index.html')
        if name not in ('index.html', 'app.js', 'style.css'):
            raise web.HTTPNotFound()
        return web.FileResponse(ASSETS/name)

    async def websocket(request):
        nonlocal owner
        if owner is not None:
            raise web.HTTPConflict(text='已有控制页面，请先关闭另一标签页')
        ws = web.WebSocketResponse(heartbeat=5, max_msg_size=4096)
        owner = ws  # reserve before yielding during handshake
        handler = asyncio.current_task()
        handlers.add(handler)
        commands = set()
        ticker = None

        async def publish():
            try:
                while not ws.closed:
                    await asyncio.wait_for(ws.send_json(controller.snapshot()), 2)
                    await asyncio.sleep(.1)
            except (ConnectionError, TimeoutError):
                await ws.close()

        async def cleanup():
            nonlocal owner
            for task in commands:
                task.cancel()
            await asyncio.gather(*commands, return_exceptions=True)
            if ticker:
                ticker.cancel()
                await asyncio.gather(ticker, return_exceptions=True)
            try:
                await controller.release()
            finally:
                await ws.close()
                owner = None
                handlers.discard(handler)

        try:
            await ws.prepare(request)
            ticker = asyncio.create_task(publish())
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                        if not isinstance(data, dict):
                            raise ValueError('操作格式错误')
                        if data.get('action') == 'cursor_stop':
                            await controller.execute(data)
                            continue
                        # Bound control backlog; one command may be in flight.
                        if commands:
                            controller.error(RingError('busy'))
                            continue
                        task = asyncio.create_task(controller.execute(data))
                        commands.add(task)
                        task.add_done_callback(commands.discard)
                    except (ValueError, TypeError) as exc:
                        controller.error(exc)
        finally:
            controller.cursor.disable('控制页面关闭，系统控制已停止')
            # Peer disconnect can cancel the HTTP handler during an await.
            # Cleanup must outlive that handler and retain ownership until done.
            task = asyncio.create_task(cleanup())
            cleanups.add(task)
            task.add_done_callback(cleanups.discard)
            await asyncio.shield(task)
        return ws

    async def shutdown(app):
        if owner:
            await owner.close(code=1001, message=b'Service shutting down')
        if handlers:
            await asyncio.gather(*list(handlers), return_exceptions=True)
        if cleanups:
            await asyncio.gather(*list(cleanups), return_exceptions=True)
        await controller.release()

    app = web.Application(middlewares=[local_only])
    app.router.add_get('/', asset)
    app.router.add_get('/ws', websocket)
    app.router.add_get('/{name}', asset)
    app.on_shutdown.append(shutdown)
    return app


def serve(*, port=8765, demo=False, open_browser=False):
    try:
        from aiohttp import web
    except ImportError as exc:
        raise RuntimeError('页面依赖未安装：python -m pip install ".[web]"') from exc
    url = f'http://127.0.0.1:{port}'
    app = create_app(demo=demo)
    if open_browser:
        async def open_page(app):
            asyncio.get_running_loop().call_later(.5, webbrowser.open, url)
        app.on_startup.append(open_page)
    print(f'Ring Studio · {"模拟模式（不访问蓝牙）" if demo else "真机模式（等待用户选择连接）"} · {url}', flush=True)
    web.run_app(app, host='127.0.0.1', port=port, shutdown_timeout=15)
