"""Opt-in application output for SDK events; no BLE/model/recognition changes.

Mapping follows TouchTabletDemo firmware_demo.session.NativeSink. No second C-D
curve. One bounded worker owns native calls; disabling invalidates queued work.
"""
import asyncio
from collections import deque
from dataclasses import asdict, dataclass
import math
import threading
import time

from .types import Motion, Click, ContactUpdate

PERMISSION_HELP = '系统设置 → 隐私与安全性 → 辅助功能：允许启动服务的终端/Python，随后重新检查权限；必要时重启服务。'


def accessibility_allowed():
    try:
        from ApplicationServices import AXIsProcessTrusted
    except ImportError as exc:
        raise RuntimeError('系统控制依赖未安装：.venv/bin/python -m pip install -e ".[web,cursor]"') from exc
    return bool(AXIsProcessTrusted())  # Read only: no prompt or permission grant.


@dataclass(frozen=True)
class CursorConfig:
    gain: float = 1.
    scroll_gain: float = 1.
    finger_mapping: bool = True
    clicks: bool = False

    def __post_init__(self):
        for v in (self.gain, self.scroll_gain):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not .25 <= v <= 8:
                raise ValueError('系统光标/滚动倍率必须为 0.25–8')
        if type(self.finger_mapping) is not bool or type(self.clicks) is not bool:
            raise ValueError('映射与点击开关必须为布尔值')


class NativeOutput:
    """Lazy native backend. Constructed only after explicit enable + permission."""
    def __init__(self, *, mouse=None, buttons=None, quartz=None):
        if mouse is None:
            try:
                from pynput import mouse as module
                import Quartz
            except ImportError as exc:
                raise RuntimeError('缺少系统控制依赖，请安装 .[web,cursor]') from exc
            mouse, buttons, quartz = module.Controller(), module.Button, Quartz
        self.mouse, self.buttons, self.quartz = mouse, buttons, quartz

    def move(self, dx, dy):
        self.mouse.move(dx, dy)

    def scroll(self, dy):
        q = self.quartz
        event = q.CGEventCreateScrollWheelEvent(None, q.kCGScrollEventUnitPixel, 1, dy)
        if event is None:
            raise RuntimeError('无法创建 Quartz 滚动事件')
        q.CGEventPost(q.kCGHIDEventTap, event)

    def click(self, button, count):
        self.mouse.click(getattr(self.buttons, button.lower()), count)


class ApplicationSink:
    """Pure mapping with injected output; used only by the single worker."""
    def __init__(self, output, config):
        self.output, self.config = output, config
        self.clicked = deque(maxlen=256)
        self.reset()

    def reset(self):
        self.x = self.y = self.wheel = 0.
        self.route = None

    def emit(self, event, valid=lambda: True):
        if not valid():
            return
        if isinstance(event, ContactUpdate) and event.phase in ('END', 'CANCEL'):
            self.wheel = 0.
        elif isinstance(event, Click):
            if not self.config.clicks or event.is_preview or event.click_id in self.clicked:
                return
            if event.button not in ('LEFT', 'RIGHT') or event.count not in (1, 2):
                raise ValueError('无效的正式点击')
            self.clicked.append(event.click_id)
            if valid():
                self.output.click(event.button, event.count)
        elif isinstance(event, Motion):
            if not math.isfinite(event.dx) or not math.isfinite(event.dy):
                raise ValueError('非有限位移')
            scroll = self.config.finger_mapping and event.mode == 'TWO_FINGER'
            route = (scroll, event.episode_id if scroll else None)
            if route != self.route:
                self.reset()
                self.route = route
            if scroll:
                self.wheel -= event.dy*self.config.scroll_gain  # Quartz positive is up.
                value = math.trunc(self.wheel)
                self.wheel -= value
                if value and valid():
                    self.output.scroll(value)
            else:
                self.x += event.dx*self.config.gain
                self.y += event.dy*self.config.gain
                dx, dy = math.trunc(self.x), math.trunc(self.y)
                self.x -= dx
                self.y -= dy
                if (dx or dy) and valid():
                    self.output.move(dx, dy)


class SystemCursor:
    def __init__(self, *, demo=False, output_factory=NativeOutput, permission_check=accessibility_allowed,
                 on_fault=None, capacity=128, clock=time.monotonic):
        self.demo, self.output_factory, self.permission_check = demo, output_factory, permission_check
        self.on_fault, self.clock = on_fault, clock
        self.queue = asyncio.Queue(maxsize=capacity)
        self.task = None
        self.preparation = None
        self.epoch = 0
        self.enabled = False
        self.permission = 'unchecked'
        self.message = '系统控制已关闭'
        self.config = CursorConfig()
        self.token = threading.Event()
        self.sink = self.key = None
        self.sequence = -1
        self.expired = 0

    async def background(self, function):
        if self.preparation is not None and not self.preparation.done():
            raise RuntimeError('原生准备尚未结束，请稍候')
        self.preparation = asyncio.create_task(asyncio.to_thread(function), name='ring-cursor-prepare')
        # Cancelling an HTTP command cannot cancel a running native import.
        # Retain the task until it ends, but never activate from its callback.
        self.preparation.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
        return await asyncio.shield(self.preparation)

    async def check_permission(self):
        if self.demo:
            self.permission = 'demo'
            self.message = '模拟模式禁止系统输出'
            return False
        epoch = self.epoch
        try:
            allowed = await self.background(self.permission_check)
            if self.epoch != epoch:
                return False
            self.permission = 'allowed' if allowed else 'denied'
            self.message = '辅助功能权限已允许' if allowed else PERMISSION_HELP
            if not allowed and self.enabled:
                self.disable(PERMISSION_HELP)
            return allowed
        except asyncio.CancelledError:
            self.disable('权限检查已取消')
            raise
        except Exception as exc:
            if self.epoch == epoch:
                self.permission = 'unavailable'
                self.disable(str(exc))
            raise

    async def enable(self, key, config, *, still_current=lambda: True):
        if self.demo:
            raise RuntimeError('模拟模式禁止开启系统控制')
        if (self.enabled or (self.task is not None and not self.task.done())
                or (self.preparation is not None and not self.preparation.done())):
            raise RuntimeError('请先停止系统控制，等待当前原生调用完成')
        epoch = self.epoch
        ticket = threading.Event()
        ticket.set()
        self.token = ticket
        self.message = '正在后台检查权限并准备原生输出，系统控制尚未开启'

        def prepare():
            allowed = self.permission_check()
            if not allowed or not ticket.is_set():
                return allowed, None
            return allowed, self.output_factory()

        try:
            allowed, output = await self.background(prepare)
        except asyncio.CancelledError:
            self.disable('系统控制准备已取消')
            raise
        except Exception as exc:
            if self.epoch == epoch:
                self.permission = 'unavailable'
                self.disable(str(exc))
            raise
        # Commit only on the event loop, after every potentially slow operation.
        if self.epoch != epoch or not ticket.is_set():
            return False
        if not still_current():
            self.disable('原会话已结束，系统控制准备结果已丢弃')
            return False
        self.permission = 'allowed' if allowed else 'denied'
        if not allowed:
            self.disable(PERMISSION_HELP)
            raise PermissionError(PERMISSION_HELP)
        self.sink = ApplicationSink(output, config)
        self.config, self.key, self.sequence = config, key, -1
        self.enabled = True
        self.message = '系统控制已开启'
        return True

    def disable(self, reason='系统控制已停止'):
        self.epoch += 1
        self.enabled = False
        self.token.clear()
        self.sink = self.key = None  # Old worker owns its sink; never reuse its remainder.
        while not self.queue.empty():
            self.queue.get_nowait()
        self.message = reason

    def fault(self, exc):
        self.disable(str(exc))
        if self.on_fault:
            self.on_fault(exc)

    def submit(self, event):
        if not self.enabled or (event.meta.session_id, event.meta.generation) != self.key:
            return
        if event.meta.sequence <= self.sequence:
            return
        self.sequence = event.meta.sequence
        if isinstance(event, Click) and event.is_preview:
            return
        try:
            self.queue.put_nowait((event, self.clock(), self.token, self.sink))
        except asyncio.QueueFull:
            self.fault(RuntimeError('系统输出队列已满，已停用；请手动重新开启'))
            return
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run(), name='ring-system-cursor')

    def dispatch(self, event, token, sink, arrived):
        expiry_reported = False
        def valid():
            nonlocal expiry_reported
            if not token.is_set():
                return False
            now = self.clock()
            expired = ((isinstance(event, Motion) and (now-event.meta.source_time_ns/1e9 > .2 or now-arrived > .2))
                       or (isinstance(event, Click) and now-arrived > .25))
            if expired:
                if not expiry_reported:
                    self.expired += 1
                    sink.reset()
                    expiry_reported = True
                return False
            if not self.permission_check():
                raise PermissionError(PERMISSION_HELP)
            return token.is_set()
        sink.emit(event, valid=valid)

    async def run(self):
        while not self.queue.empty():
            event, arrived, token, sink = self.queue.get_nowait()
            if not token.is_set():
                continue
            limit = .2 if isinstance(event, Motion) else .25 if isinstance(event, Click) else None
            source_old = isinstance(event, Motion) and self.clock()-event.meta.source_time_ns/1e9 > .2
            if source_old or (limit is not None and self.clock()-arrived > limit):
                self.expired += 1
                sink.reset()
                continue
            try:
                await asyncio.to_thread(self.dispatch, event, token, sink, arrived)
            except Exception as exc:
                if token.is_set():
                    if isinstance(exc, PermissionError):
                        self.permission = 'denied'
                    self.fault(exc)

    async def drain(self):
        if self.task:
            await asyncio.shield(self.task)

    async def close(self):
        self.disable('控制页面关闭或服务退出，系统控制已停止')
        await self.drain()
        if self.preparation:
            await asyncio.gather(asyncio.shield(self.preparation), return_exceptions=True)

    def snapshot(self):
        preparing = bool(self.preparation and not self.preparation.done())
        return dict(enabled=self.enabled, permission=self.permission, message=self.message, preparing=preparing,
                    pending=self.queue.qsize(), inflight=bool(self.task and not self.task.done()) or preparing,
                    expired=self.expired, config=asdict(self.config))
