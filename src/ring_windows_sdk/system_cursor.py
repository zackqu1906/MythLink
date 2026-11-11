"""Opt-in application output for SDK events; no BLE/model/recognition changes.

Mapping follows TouchTabletDemo firmware_demo.session.NativeSink. No second C-D
curve. One bounded worker owns native calls; disabling invalidates queued work.
"""
import asyncio
from collections import deque
import ctypes as C
from dataclasses import asdict, dataclass
from functools import cache
import math
import sys
import threading
import time

from .types import Motion, Click, ContactUpdate

PERMISSION_HELP = '当前桌面未接收输入。请解锁 Windows，返回启动服务的桌面。'
WHEEL_DELTA = 120
PIXELS_PER_NOTCH = 48


class _Point(C.Structure):
    _fields_ = [('x', C.c_int32), ('y', C.c_int32)]


class _MouseInput(C.Structure):
    _fields_ = [('dx', C.c_int32), ('dy', C.c_int32), ('mouseData', C.c_uint32),
                ('dwFlags', C.c_uint32), ('time', C.c_uint32), ('dwExtraInfo', C.c_size_t)]


class _Input(C.Structure):
    # MOUSEINPUT is the largest member of Win32 INPUT's union.
    _fields_ = [('type', C.c_uint32), ('mi', _MouseInput)]


@cache
def _user32():
    if sys.platform != 'win32':
        raise RuntimeError('系统光标需要 Windows 桌面。')
    dll = C.WinDLL('user32', use_last_error=True)
    dll.GetThreadDesktop.argtypes = [C.c_uint32]
    dll.GetThreadDesktop.restype = C.c_void_p
    dll.GetUserObjectInformationW.argtypes = [C.c_void_p, C.c_int, C.c_void_p, C.c_uint32, C.POINTER(C.c_uint32)]
    dll.GetUserObjectInformationW.restype = C.c_int
    dll.GetCursorPos.argtypes = [C.POINTER(_Point)]
    dll.GetCursorPos.restype = C.c_int
    dll.SetCursorPos.argtypes = [C.c_int, C.c_int]
    dll.SetCursorPos.restype = C.c_int
    dll.SendInput.argtypes = [C.c_uint, C.POINTER(_Input), C.c_int]
    dll.SendInput.restype = C.c_uint
    return dll


def accessibility_allowed():
    dll = _user32()
    desktop = dll.GetThreadDesktop(threading.get_native_id())
    active = C.c_int()
    # UOI_IO reports whether this exact desktop is currently receiving input.
    return bool(desktop and dll.GetUserObjectInformationW(desktop, 6, C.byref(active), C.sizeof(active), None)
                and active.value)


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
    """Win32 mouse output. Constructing the adapter never injects input."""
    def __init__(self, user32=None):
        self.dll = _user32() if user32 is None else user32

    def move(self, dx, dy):
        point = _Point()
        if not self.dll.GetCursorPos(C.byref(point)) or not self.dll.SetCursorPos(point.x+dx, point.y+dy):
            raise OSError('Windows 未能移动光标，请检查当前桌面。')

    def scroll(self, dy):
        event = _Input(mi=_MouseInput(mouseData=dy, dwFlags=0x0800))
        if self.dll.SendInput(1, C.byref(event), C.sizeof(_Input)) != 1:
            raise OSError('Windows 未接收滚动输入。请检查当前桌面及目标应用的权限。')

    def click(self, button, count):
        down, up = {'LEFT': (0x0002, 0x0004), 'RIGHT': (0x0008, 0x0010)}[button]
        flags = (down, up)*count
        events = (_Input * len(flags))(*(_Input(mi=_MouseInput(dwFlags=f)) for f in flags))
        sent = self.dll.SendInput(len(events), events, C.sizeof(_Input))
        if sent != len(events):
            # A partial batch can leave the button down. Only try its matching release.
            if sent % 2:
                release = _Input(mi=_MouseInput(dwFlags=up))
                self.dll.SendInput(1, C.byref(release), C.sizeof(_Input))
            raise OSError(f'Windows 仅接收 {sent}/{len(events)} 个点击事件。请检查目标应用的权限。')


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
                # Win32 accepts signed sub-notch wheel units; positive is up.
                self.wheel -= event.dy*self.config.scroll_gain*WHEEL_DELTA/PIXELS_PER_NOTCH
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
                 on_fault=None, capacity=512, clock=time.perf_counter):
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
        self.expired = 0  # Compatibility field: age no longer discards events.
        self.late = 0
        self.last_motion = None
        self.playback_interval = .004

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
            self.message = '当前桌面可接收输入' if allowed else PERMISSION_HELP
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
        self.last_motion = None
        self.playback_interval = .004
        self.enabled = True
        self.message = '系统控制已开启'
        return True

    def disable(self, reason='系统控制已停止'):
        self.epoch += 1
        self.enabled = False
        self.token.clear()
        self.last_motion = None
        self.playback_interval = .004
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
            if self.queue.qsize()+int(self.task is not None and not self.task.done()) >= self.queue.maxsize:
                raise asyncio.QueueFull
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
            if expired and not expiry_reported:
                self.late += 1
                expiry_reported = True
            if not self.permission_check():
                raise PermissionError(PERMISSION_HELP)
            return token.is_set()
        sink.emit(event, valid=valid)

    async def run(self):
        while not self.queue.empty():
            event, arrived, token, sink = self.queue.get_nowait()
            if not token.is_set():
                continue
            try:
                if isinstance(event, Motion):
                    age = self.clock()-arrived
                    if age > .05:
                        self.playback_interval = .002
                    elif age > .025:
                        self.playback_interval = min(self.playback_interval, .003)
                    elif age < .015:
                        self.playback_interval = .004
                previous, interval = self.last_motion, self.playback_interval
                def dispatch():
                    if not token.is_set():
                        return
                    started = None
                    if isinstance(event, Motion):
                        if previous is not None:
                            delay = previous + interval - self.clock()
                            if delay > 0:
                                time.sleep(delay)
                        if not token.is_set():
                            return
                        started = self.clock()
                    self.dispatch(event, token, sink, arrived)
                    return started
                started = await asyncio.to_thread(dispatch)
                if started is not None and token.is_set():
                    self.last_motion = started
                if self.queue.empty():
                    self.playback_interval = .004
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
                    expired=self.expired, late=self.late, playback_interval_ms=self.playback_interval*1000, config=asdict(self.config))
