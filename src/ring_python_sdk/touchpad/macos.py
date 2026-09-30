"""macOS system pointer adapter; importing this module never posts events."""
import math
import time


class MacSystemMouse:
    def __init__(self, quartz=None, accessibility=None):
        if quartz is None:
            import Quartz as quartz
        if accessibility is None:
            import ApplicationServices as accessibility
        self.q = quartz
        self.ax = accessibility
        self.enabled = False
        self.deadline = None
        self.gain = 1.
        self.invert_y = False
        self.clicks_enabled = True
        self.moves = self.clicks = 0

    def permitted(self):
        return bool(self.ax.AXIsProcessTrusted() and self.q.CGPreflightPostEventAccess())

    def request_permission(self):
        self.ax.AXIsProcessTrustedWithOptions({self.ax.kAXTrustedCheckOptionPrompt: True})
        if not self.q.CGPreflightPostEventAccess():
            self.q.CGRequestPostEventAccess()
        return self.permitted()

    def escape_pressed(self):
        return bool(self.q.CGEventSourceKeyState(self.q.kCGEventSourceStateCombinedSessionState, 53))

    def enable(self):
        if self.deadline is not None and time.monotonic()>=self.deadline:
            raise TimeoutError('倒计时结束，鼠标控制已停止')
        if not self.permitted():
            raise PermissionError('请先在系统设置 → 隐私与安全性 → 辅助功能中授权当前终端 / Python 程序。')
        self.enabled = True
        self.moves = self.clicks = 0

    def disable(self):
        self.enabled = False

    def _position(self):
        event = self.q.CGEventCreate(None)
        if event is None:
            raise RuntimeError('无法读取系统鼠标位置')
        p = self.q.CGEventGetLocation(event)
        return p.x, p.y

    def _clamp(self, x, y):
        error, displays, _ = self.q.CGGetActiveDisplayList(32, None, None)
        if error or not displays:
            raise RuntimeError('无法读取显示器，请在已登录的 Mac 桌面运行测试程序。')
        choices = []
        for display in displays:
            r = self.q.CGDisplayBounds(display)
            left, top = r.origin.x, r.origin.y
            right, bottom = left + r.size.width - 1, top + r.size.height - 1
            px, py = max(left, min(right, x)), max(top, min(bottom, y))
            choices.append(((px-x)**2 + (py-y)**2, px, py))
        _, x, y = min(choices)
        return x, y

    def _event(self, kind, point):
        event = self.q.CGEventCreateMouseEvent(None, kind, point, self.q.kCGMouseButtonLeft)
        if event is None:
            raise RuntimeError('无法创建系统鼠标事件')
        return event

    def apply(self, events):
        if not self.enabled:
            return
        try:
            if self.deadline is not None and time.monotonic()>=self.deadline:
                raise TimeoutError('倒计时结束，鼠标控制已停止')
            if not self.permitted():
                raise PermissionError('鼠标控制权限已失效，请重新授权。')
            if self.escape_pressed():
                raise InterruptedError('Esc 已停止鼠标控制')
            point = self._position()
            for event in events:
                if not self.enabled:
                    break
                if event['kind'] == 'move':
                    dx, dy = float(event['dx']), float(event['dy'])
                    if not all(math.isfinite(v) for v in (dx, dy, self.gain)):
                        raise ValueError('鼠标位移不是有限数值')
                    if dx == 0 and dy == 0:continue
                    x, y = point
                    point = self._clamp(x + dx*self.gain, y + dy*self.gain*(-1 if self.invert_y else 1))
                    self.q.CGEventPost(self.q.kCGHIDEventTap, self._event(self.q.kCGEventMouseMoved, point))
                    self.moves += 1
                elif event['kind'] == 'click' and self.clicks_enabled:
                    down = self._event(self.q.kCGEventLeftMouseDown, point)
                    up = self._event(self.q.kCGEventLeftMouseUp, point)
                    for e in (down, up):
                        self.q.CGEventSetIntegerValueField(e, self.q.kCGMouseEventClickState, 1)
                    try:
                        self.q.CGEventPost(self.q.kCGHIDEventTap, down)
                    finally:
                        self.q.CGEventPost(self.q.kCGHIDEventTap, up)
                    self.clicks += 1
        except Exception:
            self.disable()
            raise

    def handle(self, event):
        """Use directly as on_event. Ignore events older than 100 ms."""
        if time.monotonic()-event.timestamp > .1:
            return
        if event.kind == 'move':
            self.apply([{'kind': 'move', 'dx': event.dx, 'dy': event.dy}])
        elif event.kind == 'click':
            self.apply([{'kind': 'click'}])
