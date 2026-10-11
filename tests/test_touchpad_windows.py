"""Windows pointer delivery and local sessions, without posting system input."""
import ctypes
from types import SimpleNamespace
import threading
import time

import pytest
from PySide6.QtCore import QObject
from PySide6.QtTest import QTest

from ring_python_sdk.touchpad.windows import WindowsSystemMouse, _Point, _Input
from ring_python_sdk.touchpad import TouchpadClick
from proximic_ring.touchpad_mouse import TouchpadMouseOutput
from test_inline_ui import inline_ui
from test_stroke_integration import idle
from test_stroke_session import until


class User32:
    def __init__(self):
        self.position = [0, 0]
        self.calls = []
        self.escape = False
        self.metrics = {76: -100, 77: -50, 78: 300, 79: 200}
        self.on_move = lambda: None
        self.sent = 2
    def GetCursorPos(self, value):
        point = ctypes.cast(value, ctypes.POINTER(_Point)).contents
        point.x, point.y = self.position
        return 1
    def SetCursorPos(self, x, y):
        self.position[:] = [x, y]
        self.calls.append(('move', x, y))
        self.on_move()
        return 1
    def GetSystemMetrics(self, code): return self.metrics[code]
    def GetAsyncKeyState(self, key): return 0x8000 if self.escape else 0
    def SendInput(self, count, inputs, size):
        self.calls.append(('input', count, size, [inputs[i].mi.dwFlags for i in range(count)]))
        return self.sent


def test_fractional_motion_gain_inversion_and_negative_monitor_origin():
    api = User32()
    mouse = WindowsSystemMouse(api)
    mouse.enable()
    assert not api.calls
    for _ in range(4): mouse.apply([dict(kind='move', dx=.25, dy=0)])
    assert api.position == [1, 0]
    mouse.gain, mouse.invert_y = 2, True
    mouse.apply([dict(kind='move', dx=-2, dy=3)])
    assert api.position == [-3, -6]
    assert mouse.apply([dict(kind='move', dx=-10000, dy=-10000)]) == dict(moves=1)
    assert api.position == [-100, 149]


def test_input_layout_click_order_toggle_and_native_double_click():
    api = User32()
    mouse = WindowsSystemMouse(api)
    mouse.enable()
    mouse.apply([dict(kind='move', dx=2, dy=0), dict(kind='click'), dict(kind='move', dx=3, dy=0)])
    assert ctypes.sizeof(_Input) == (40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28)
    assert api.calls == [('move', 2, 0), ('input', 2, ctypes.sizeof(_Input), [2, 4]), ('move', 5, 0)]
    assert mouse.double_click_target() is None
    mouse.complete_double_click()
    assert len(api.calls) == 4
    mouse.clicks_enabled = False
    mouse.complete_double_click()
    assert len(api.calls) == 4


def test_stop_between_events_prevents_queued_click_and_movement():
    api = User32()
    mouse = WindowsSystemMouse(api)
    mouse.enable()
    api.on_move = mouse.disable
    mouse.apply([dict(kind='move', dx=1, dy=1), dict(kind='click'), dict(kind='move', dx=2, dy=2)])
    assert api.calls == [('move', 1, 1)]
    mouse.apply([dict(kind='click')])
    assert len(api.calls) == 1


@pytest.mark.parametrize('failure', ['escape', 'input', 'invalid_motion'])
def test_failure_disables_pointer(failure):
    api = User32()
    mouse = WindowsSystemMouse(api)
    mouse.enable()
    if failure == 'escape':
        api.escape = True
        with pytest.raises(InterruptedError): mouse.check_health()
    elif failure == 'input':
        api.sent = 0
        with pytest.raises(OSError, match='SendInput'): mouse.apply([dict(kind='click')])
    else:
        with pytest.raises(ValueError): mouse.apply([dict(kind='move', dx=float('nan'), dy=0)])
    assert not mouse.enabled


def test_windows_worker_uses_user32_and_esc_stops_while_idle(monkeypatch):
    import proximic_ring.touchpad_mouse as module
    import ring_python_sdk.touchpad.windows as windows
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform='win32'))
    api = User32()
    monkeypatch.setattr(windows, 'WindowsSystemMouse', lambda: WindowsSystemMouse(api))
    ready, ended = threading.Event(), threading.Event()
    errors = []
    output = TouchpadMouseOutput(connection=threading.Event(), gain=1, clicks=True, invert_y=False,
        on_ready=ready.set, on_end=lambda reason, error: (errors.append(error), ended.set()))
    output.start()
    try:
        assert ready.wait(1) and not api.calls
        output.activate()
        api.escape = True
        assert ended.wait(1) and isinstance(errors[0], InterruptedError)
        assert not api.calls
    finally:
        output.stop()
        output.thread.join(1)


def test_windows_worker_preserves_ordinary_double_click():
    api = User32()
    ready, ended = threading.Event(), threading.Event()
    sessions = []
    output = TouchpadMouseOutput(connection=threading.Event(), gain=1, clicks=True, invert_y=False,
        mouse_factory=lambda: WindowsSystemMouse(api), on_ready=ready.set,
        on_end=lambda *_: ended.set(), on_double_click=sessions.append)
    output.start()
    try:
        assert ready.wait(1)
        output.activate()
        output.submit(TouchpadClick(1, time.monotonic()))
        deadline = time.monotonic()+1
        while not api.calls and time.monotonic() < deadline: time.sleep(.005)
        output.submit(TouchpadClick(2, time.monotonic()))
        deadline = time.monotonic()+1
        while len(api.calls) < 2 and time.monotonic() < deadline: time.sleep(.005)
        assert len(api.calls) == 2 and not sessions and not output.paused
    finally:
        output.stop()
        assert ended.wait(1)


def test_windows_controller_runs_local_stroke_without_mac_input_method(inline_ui, monkeypatch):
    import proximic_ring.ui.touchpad_controller as module
    monkeypatch.setattr(module, 'sys', SimpleNamespace(platform='win32'))
    c, bridge, root, tp, source = idle(inline_ui)
    assert tp.supported and tp.available
    assert not tp.macPermissionsRequired and not tp.externalStrokeSupported
    root.setProperty('currentPage', 3)
    root.findChild(QObject, 'touchpadPage').setProperty('section', 'stroke')
    root.show()
    root.requestActivate()
    QTest.qWait(40)
    editor = root.findChild(QObject, 'strokeTestEditor')
    editor.forceActiveFocus()
    until(lambda: tp._local_stroke_focus)
    # No macOS focus capture/source selection can be used on Windows.
    monkeypatch.setattr(c._inline_input, '_foreground_identity', lambda: pytest.fail('Mac focus query'))
    monkeypatch.setattr(c._inline_input, 'prepare_input_method', lambda *_: pytest.fail('Mac input method'))
    tp.start()
    until(lambda: bool(source.starts))
    source.future.set_result(None)
    until(lambda: tp.state == 'running')
    try:
        options = tp._run.output.options
        # This factory is the real Windows adapter, but enabling it posts nothing.
        mouse = options['mouse_factory']()
        mouse.enable()
        assert mouse.double_click_target() == dict(local=True)
        mouse.close()
        options['on_double_click'](dict(local=True))
        until(lambda: tp.strokeSession.phase == 'running')
        assert tp.inputMode == 'stroke' and tp._local_stroke_input
        tp.addStroke('h')
        char = tp.strokeCandidates[0]
        tp.selectCandidate(0)
        assert editor.property('text') == char and not tp.strokeCode
        assert not any(m['type'] == 'stroke_begin' for m in bridge.messages)
        tp._run.output.options['on_double_click']()
        until(lambda: tp.inputMode == 'pointer' and not tp.strokeSession.engaged)
        # The existing stream is reused across both mode switches.
        assert len(source.starts) == 1 and source.stops == 0
        run = tp._run
        tp._receive(run, 'ended', InterruptedError('Esc 已停止触摸板'))
        until(lambda: not tp.active and not tp.busy)
        assert tp.state == 'idle' and 'Esc' in tp.message
    finally:
        tp.close()
        QTest.qWait(30)
