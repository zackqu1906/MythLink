"""Touchpad QML interaction and lifecycle without posting real system events."""
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
import os
import sys
import threading

import pytest
from test_inline_ui import inline_ui


class Output:
    def __init__(self, **options): self.options = options; self.stopped = False; self.seconds = None
    def start(self): self.options['on_ready']()
    def activate(self, seconds): self.seconds = seconds
    def stop(self): self.stopped = True
    def submit(self, event): pass


class Source:
    def __init__(self): self.starts = []; self.stops = 0; self.future = Future()
    def start_touchpad(self, **options): self.starts.append(options); return self.future
    def stop_touchpad(self):
        self.stops += 1
        f = Future(); f.set_result(None)
        return f


def prepare(controller):
    controller._connected = True
    controller._busy = False
    controller._disconnect_event = threading.Event()
    touchpad = controller.touchpad
    touchpad.output_factory = Output
    source = Source()
    touchpad.attach(source, controller._disconnect_event)
    controller.connectedChanged.emit()
    return touchpad, source


@pytest.mark.parametrize('size', [(1440, 940), (940, 700)])
def test_page_toggle_and_settings_share_existing_source(inline_ui, tmp_path, size):
    from PySide6.QtCore import QObject, QMetaObject, QPointF
    from PySide6.QtTest import QTest
    c, _, _, root, _ = inline_ui
    tp, source = prepare(c)
    root.resize(*size); root.setProperty('currentPage', 3); root.show(); QTest.qWait(80)
    page = root.findChild(QObject, 'touchpadPage')
    assert page.property('visible')
    before = c.recognitionEnabled
    toggle = root.findChild(QObject, 'touchpadToggleButton')
    pos = toggle.mapToScene(QPointF())
    assert pos.x() >= root.property('sidebarWidth') and pos.x()+toggle.width() <= root.width()
    assert toggle.property('enabled') and tp.seconds == 90
    shots = Path(os.environ.get('MYTHLINK_SCREENSHOT_DIR', str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(shots/f'touchpad-idle-{size[0]}.png'))
    QMetaObject.invokeMethod(toggle, 'click'); QTest.qWait(25)
    assert len(source.starts) == 1 and source.starts[0]['duration_s'] == 90
    output = tp._run.output
    source.future.set_result(None); QTest.qWait(25)
    assert tp.active and output.seconds == 90
    assert not root.findChild(QObject, 'touchpadGainSlider').property('enabled')
    source.starts[0]['on_stats'](SimpleNamespace(warmup_frames=200)); QTest.qWait(25)
    assert root.grabWindow().save(str(shots/f'touchpad-active-{size[0]}.png'))
    QMetaObject.invokeMethod(toggle, 'click'); QTest.qWait(25)
    assert output.stopped and source.stops == 1 and not tp.active
    assert c.recognitionEnabled == before
    tp.gain = 1.5; tp.clicks = False; tp.invertY = True; tp.seconds = 180
    assert c._settings.value('touchpad/gain') == 1.5
    assert tp.seconds == 180


def test_cancel_start_and_ignore_callbacks_from_old_run(inline_ui):
    from PySide6.QtTest import QTest
    c, _, _, _, _ = inline_ui
    tp, source = prepare(c)
    tp.start(); QTest.qWait(20)
    old = tp._run
    assert tp.busy
    tp.stop(); QTest.qWait(20)
    assert old.output.stopped and not tp.active and source.stops == 1
    source.future.set_result(None)
    source.starts[0]['on_stats'](SimpleNamespace(warmup_frames=200))
    QTest.qWait(20)
    assert tp.state == 'idle' and old.output.seconds is None


def test_stream_end_gates_pointer_before_queued_ui_update(inline_ui):
    from PySide6.QtTest import QTest
    c, _, _, _, _ = inline_ui
    tp, source = prepare(c)
    tp.start(); QTest.qWait(20)
    source.future.set_result(None); QTest.qWait(20)
    output = tp._run.output
    source.starts[0]['on_stopped'](TimeoutError('数据中断'))
    assert output.stopped
    QTest.qWait(20)
    assert tp.state == 'error' and '数据中断' in tp.message


def test_permission_failure_and_connection_boundary(inline_ui):
    from PySide6.QtTest import QTest
    c, _, _, _, _ = inline_ui
    tp, source = prepare(c)
    class NoPermission(Output):
        def start(self): self.options['on_end']('', PermissionError('permission'))
    tp.output_factory = NoPermission
    tp.start(); QTest.qWait(20)
    assert not source.starts and tp.state == 'error'
    assert ('辅助功能' if sys.platform == 'darwin' else 'Windows 输入权限') in tp.message
    tp.output_factory = Output
    tp.start(); QTest.qWait(20)
    old_output = tp._run.output
    c._disconnect_event.set(); c._connected = False; c.connectedChanged.emit()
    assert old_output.stopped
    QTest.qWait(20)
    old_connection = c._disconnect_event
    c._disconnect_event = threading.Event()
    tp.attach(source, old_connection)
    assert tp._source is None


def test_lock_and_exit_stop_pointer_without_restarting_recognition(inline_ui):
    from PySide6.QtTest import QTest
    c, _, _, _, _ = inline_ui
    tp, source = prepare(c)
    tp.start(); QTest.qWait(20)
    source.future.set_result(None); QTest.qWait(20)
    output = tp._run.output
    c.proximity.lockPreparing.emit()
    assert output.stopped
    QTest.qWait(20)
    assert source.stops == 1 and not tp.active
    source.future = Future()
    tp.start(); QTest.qWait(20)
    source.future.set_result(None); QTest.qWait(20)
    output = tp._run.output
    tp.close()
    assert output.stopped
    QTest.qWait(20)
    assert not tp.available and not tp.active
