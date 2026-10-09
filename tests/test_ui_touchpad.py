"""Touchpad QML interaction and lifecycle without posting real system events."""
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
import os
import threading

import pytest
from test_inline_ui import inline_ui


class Output:
    def __init__(self, **options): self.options = options; self.stopped = False; self.active = False
    def start(self): self.options['on_ready']()
    def activate(self): self.active = True
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


def test_pointer_and_stroke_use_separate_movement_without_restarting_stream(inline_ui):
    import time
    from PySide6.QtTest import QTest
    from ring_python_sdk.touchpad import TouchpadMove, TouchpadClick, TouchpadContact
    c, _, _, _, event = inline_ui
    c._utterance_active = False
    c._pending_inline_audio_start = None
    c._interaction_state = 'idle'
    event(phase='interrupted', ready=True, composing=False, writing=False,
          edit_requested=False, awaiting_readback=False)
    tp, source = prepare(c)
    tp.stroke_output_factory = Output
    tp.start(); QTest.qWait(20)
    source.future.set_result(None); QTest.qWait(20)
    callbacks = source.starts[0]
    pointer = []
    tp._run.output.submit = pointer.append
    now = time.monotonic()
    current_move = TouchpadMove(10, 20, 1, 310, now)
    old_move = TouchpadMove(1, 2, 1, 301, now)
    click = TouchpadClick(311, now)
    callbacks['on_event'](current_move)
    callbacks['on_pointer_move'](old_move)
    callbacks['on_event'](click)
    assert pointer == [old_move, click]
    tp.setInputMode('stroke'); QTest.qWait(20)
    assert tp.inputMode == 'stroke'
    strokes = []
    tp._run.output.submit = strokes.append
    callbacks['on_pointer_move'](old_move)
    callbacks['on_event'](current_move)
    callbacks['on_event'](click)
    assert strokes == [current_move, click]
    tp.setInputMode('pointer'); QTest.qWait(20)
    restored = []
    tp._run.output.submit = restored.append
    callbacks['on_pointer_move'](old_move)
    assert not restored  # No residual stroke motion after returning to mouse.
    next_move = TouchpadMove(1, 1, 1, 320, now)
    callbacks['on_pointer_move'](next_move)
    assert restored == [next_move]
    reset = TouchpadContact('reset', 0, now)
    callbacks['on_event'](reset)
    callbacks['on_pointer_move'](TouchpadMove(1, 1, 1, 201, now))
    assert restored[-1].step == 201
    assert len(source.starts) == 1 and source.stops == 0
    tp.stop(); QTest.qWait(20)


def test_frame_diagnostics_keep_drop_reasons_as_numeric_fields(inline_ui, monkeypatch):
    from dataclasses import replace
    from PySide6.QtTest import QTest
    from ring_python_sdk.touchpad import TouchpadStats
    c, *_ = inline_ui
    tp, source = prepare(c)
    logged = []
    monkeypatch.setattr(c, '_event_log', lambda event, **fields: logged.append((event, fields)))
    tp.start(); QTest.qWait(20)
    source.future.set_result(None); QTest.qWait(20)
    stats = TouchpadStats(tokens=100, warmup_frames=200)
    tp._receive(tp._run, 'stats', (10., stats, {}))
    tp._receive(tp._run, 'stats', (12., replace(stats, tokens=500),
                                 {'sdk_move': 350, 'sdk_contact_reset_expired_motion': 50,
                                  'stroke_verdict_timeout': 1, 'stroke_delivered': 4}))
    _, report = next(item for item in logged if item[0] == 'TOUCHPAD_STATS')
    assert report['token_hz'] == 200 and report['move_hz'] == 175
    assert report['count_sdk_contact_reset_expired_motion'] == 50
    assert report['count_stroke_verdict_timeout'] == 1 and report['count_stroke_delivered'] == 4
    assert 'events' not in report
    tp.stop(); QTest.qWait(20)


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
    assert toggle.property('enabled')
    assert root.findChild(QObject, 'touchpadDurationSelect') is None
    assert root.findChild(QObject, 'touchpadCountdown') is None
    shots = Path(os.environ.get('MYTHLINK_SCREENSHOT_DIR', str(tmp_path)))
    shots.mkdir(parents=True, exist_ok=True)
    assert root.grabWindow().save(str(shots/f'touchpad-idle-{size[0]}.png'))
    QMetaObject.invokeMethod(toggle, 'click'); QTest.qWait(25)
    assert len(source.starts) == 1 and source.starts[0]['duration_s'] is None
    output = tp._run.output
    source.future.set_result(None); QTest.qWait(25)
    assert tp.active and output.active
    assert toggle.property('text') == '停止触摸板'
    assert '直到手动停止' in root.findChild(QObject, 'touchpadRunStatus').property('text')
    assert not root.findChild(QObject, 'touchpadGainSlider').property('enabled')
    source.starts[0]['on_stats'](SimpleNamespace(warmup_frames=200)); QTest.qWait(25)
    assert root.grabWindow().save(str(shots/f'touchpad-active-{size[0]}.png'))
    root.setProperty('currentPage', 0); QTest.qWait(25)
    assert tp.active and not output.stopped and source.stops == 0
    root.setProperty('currentPage', 3); QTest.qWait(25)
    QMetaObject.invokeMethod(toggle, 'click'); QTest.qWait(25)
    assert output.stopped and source.stops == 1 and not tp.active
    assert c.recognitionEnabled == before
    tp.gain = 1.5; tp.clicks = False; tp.invertY = True
    assert c._settings.value('touchpad/gain') == 1.5


def test_legacy_stop_duration_is_retired(inline_ui):
    from proximic_ring.ui.touchpad_controller import TouchpadController
    c, *_ = inline_ui
    c._settings.setValue('touchpad/seconds', 30)
    reloaded = TouchpadController(c, output_factory=Output)
    assert not c._settings.contains('touchpad/seconds')
    reloaded.close()


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
    assert tp.state == 'idle' and not old.output.active


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
    assert not source.starts and tp.state == 'error' and '辅助功能' in tp.message
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


def test_pointer_reports_event_posting_denial_separately_from_accessibility(inline_ui):
    from PySide6.QtTest import QTest
    from proximic_ring.mac_permissions import MacPermissionError, PermissionState
    c, *_ = inline_ui
    tp, source = prepare(c)
    class PendingPostAccess(Output):
        def start(self):
            self.options['on_end']('', MacPermissionError(PermissionState(True, False)))
    tp.output_factory = PendingPostAccess
    tp.start(); QTest.qWait(20)
    assert tp.state == 'error' and not source.starts
    assert '辅助功能已开启' in tp.message and '鼠标事件发送权限尚未生效' in tp.message
