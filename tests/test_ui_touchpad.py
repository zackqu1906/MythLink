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
    def __init__(self): self.starts = []; self.stops = 0; self.future = Future(); self.gesture_changes = []
    def start_touchpad(self, **options): self.starts.append(options); return self.future
    def set_touchpad_gestures(self, callback=None):
        self.gesture_changes.append(callback)
        f = Future(); f.set_result(None)
        return f
    def stop_touchpad(self):
        self.stops += 1
        f = Future(); f.set_result(None)
        return f


def prepare(controller):
    class NoExternalTarget:
        def capture_reference(self):
            raise RuntimeError('No external text field in offscreen UI test')
    controller._desktop_target_adapter = lambda: NoExternalTarget()
    controller._connected = True
    controller._busy = False
    controller._recognition_enabled = False
    controller.recognitionEnabledChanged.emit()
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


def test_stroke_mode_switch_reuses_stream_and_shows_candidates(inline_ui, tmp_path):
    from PySide6.QtCore import QObject
    from PySide6.QtTest import QTest
    c, _, _, root, _ = inline_ui
    tp, source = prepare(c)
    root.setProperty('currentPage', 3)
    tp.start(); QTest.qWait(20)
    source.future.set_result(None); QTest.qWait(20)
    assert tp.active and len(source.starts) == 1
    tp.setInputMode('stroke'); QTest.qWait(40)
    assert tp.inputMode == 'stroke' and tp.active
    assert len(source.starts) == 1 and source.stops == 0
    assert callable(source.gesture_changes[-1])
    tp.addStroke('h'); QTest.qWait(30)
    assert tp.strokeCode == '一' and '一' in tp.strokeCandidates
    assert root.findChild(QObject, 'strokeCompositionPanel').property('visible')
    assert root.grabWindow().save(str(tmp_path / 'stroke-mode.png'))
    tp.undoStroke()
    assert tp.strokeCode == ''
    tp.setInputMode('pointer'); QTest.qWait(30)
    assert tp.inputMode == 'pointer' and len(source.starts) == 1
    assert source.gesture_changes[-1] is None
    tp.stop(); QTest.qWait(30)
    assert source.stops == 1


def test_mac_stroke_commit_uses_existing_input_method(inline_ui, monkeypatch):
    import proximic_ring.ui.touchpad_controller as touchpad_module
    c, bridge, _, _, _ = inline_ui
    tp, _ = prepare(c)
    monkeypatch.setattr(touchpad_module, 'sys', SimpleNamespace(platform='darwin'))
    c._inline_input._view = {**c._inline_input._view, 'phase': 'idle', 'ready': True}
    tp.setInputMode('stroke')
    tp.addStroke('h')
    assert bridge.messages[-1]['type'] == 'stroke_update'
    tp.selectCandidate(0)
    assert tp.strokeCode == '一'
    assert bridge.messages[-1]['type'] == 'stroke_commit'
    request = bridge.messages[-1]['request_id']
    c._inline_input._accept({'type': 'stroke_result', 'epoch': c._inline_input._epoch,
                             'client_id': c._inline_input._client_id, 'request_id': request,
                             'success': True})
    assert tp.strokeCode == ''
    tp.addStroke('h')
    tp.selectCandidate(0)
    request = bridge.messages[-1]['request_id']
    c._inline_input._accept({'type': 'stroke_result', 'epoch': c._inline_input._epoch,
                             'client_id': c._inline_input._client_id, 'request_id': request,
                             'success': False, 'error': 'target rejected'})
    assert tp.strokeCode == '一'
    assert 'target rejected' in tp.commitMessage


def test_windows_stroke_commit_targets_original_field(inline_ui, monkeypatch):
    import proximic_ring.ui.touchpad_controller as touchpad_module
    c, _, _, _, _ = inline_ui
    tp, _ = prepare(c)
    target = SimpleNamespace(window_handle=101, control_handle=202)
    inserted = []
    adapter = SimpleNamespace(is_foreground=lambda value: True,
                              inject=lambda value, text: inserted.append((value, text)))
    c._desktop_target_adapter = lambda: adapter
    monkeypatch.setattr(touchpad_module, 'sys', SimpleNamespace(platform='win32'))
    tp._stroke_target = target
    assert tp._commit_character('中') is True
    assert inserted == [(target, '中')]


def test_stroke_firmware_swipes_move_selection_and_edit_code(inline_ui):
    from PySide6.QtCore import QObject
    from PySide6.QtTest import QTest
    c, _, _, root, _ = inline_ui
    tp, source = prepare(c)
    root.setProperty('currentPage', 3); root.show(); QTest.qWait(40)
    tp.setInputMode('stroke')
    tp.start(); QTest.qWait(20)
    source.future.set_result(None); QTest.qWait(20)
    assert callable(source.starts[0]['on_gesture'])
    tp.addStroke('h')
    assert len(tp.strokeCandidates) == 5
    def swipe(class_id, kind='trigger', version=2):
        source.starts[0]['on_gesture'](SimpleNamespace(
            class_id=class_id, kind=kind, protocol_version=version))
        QTest.qWait(20)
    swipe(4)
    assert tp.selectedCandidate == 1
    swipe(3)
    assert tp.selectedCandidate == 0
    swipe(4, kind='event')
    assert tp.selectedCandidate == 0
    swipe(1)
    assert tp.strokeCode == ''
    tp.addStroke('s')
    swipe(2)
    assert tp.strokeCode == ''
    editor = root.findChild(QObject, 'strokeTestEditor')
    editor.setProperty('text', '你好'); editor.setProperty('cursorPosition', 2)
    swipe(1)
    assert editor.property('text') == '你'
    tp.addStroke('h'); swipe(1)
    assert tp.strokeCode == '' and editor.property('text') == '你'
    editor.setProperty('cursorPosition', 0); swipe(1)
    assert editor.property('text') == '你'
    editor.setProperty('text', '你😀好'); editor.setProperty('cursorPosition', 3)
    swipe(1)
    assert editor.property('text') == '你好' and editor.property('cursorPosition') == 1
    tp.stop(); QTest.qWait(20)


@pytest.mark.parametrize('size', [(1440, 940), (940, 700)])
def test_compact_stroke_page_keyboard_layout_and_local_target(inline_ui, tmp_path, size):
    from PySide6.QtCore import QObject, QPointF, Qt, QMetaObject, Q_ARG
    from PySide6.QtTest import QTest
    c, _, _, root, _ = inline_ui
    tp, _ = prepare(c)
    c._desktop_target_adapter = lambda: pytest.fail('local strokes must not query external focus or caret')
    root.resize(*size)
    root.setProperty('currentPage', 3)
    root.show()
    tp.setInputMode('stroke')
    QTest.qWait(60)
    assert tp._local_stroke_input and tp._stroke_target is None
    editor = root.findChild(QObject, 'strokeTestEditor')
    assert editor.isVisible()
    ring_toggle = root.findChild(QObject, 'strokeRingToggle')
    label = ring_toggle.findChild(QObject, 'actionText')
    assert ring_toggle.property('topPadding') == ring_toggle.property('bottomPadding') == 0
    assert abs(label.mapToScene(QPointF(0, label.height()/2)).y()
               - ring_toggle.mapToScene(QPointF(0, ring_toggle.height()/2)).y()) < 1
    editor.forceActiveFocus()
    QTest.keyClick(root, Qt.Key_H)
    assert tp.strokeCode == '一'
    assert root.findChild(QObject, 'strokeCodeLabel').property('text') == '一'
    assert root.findChild(QObject, 'strokeCodeLabel').isVisible()
    candidates = root.findChild(QObject, 'strokeCandidateRow')
    assert candidates.mapToScene(QPointF()).y() >= editor.mapToScene(QPointF()).y() + editor.height()
    for name in ('strokeSearchShell', 'strokeCandidateRow', 'strokeTools'):
        item = root.findChild(QObject, name)
        pos = item.mapToScene(QPointF())
        assert 0 <= pos.y() and pos.y() + item.height() <= root.height()
        assert pos.x() + item.width() <= root.width()
    selected = tp.strokeCandidates[0]
    QTest.keyClick(root, Qt.Key_1)
    assert editor.property('text') == selected and tp.strokeCode == ''
    assert editor.property('placeholderText') == ''
    editor.setProperty('text', '你好')
    assert tp.predictionMode and tp.strokeCandidates
    editor.setProperty('cursorPosition', 1)
    tp.addStroke('h')
    tp.selectCandidate(tp.strokeCandidates.index('一'))
    assert editor.property('text') == '你一好'
    QMetaObject.invokeMethod(editor, 'select', Q_ARG(int, 1), Q_ARG(int, 3))
    tp.addStroke('h')
    tp.selectCandidate(tp.strokeCandidates.index('一'))
    assert editor.property('text') == '你一'
    editor.forceActiveFocus()
    QTest.keyClick(root, Qt.Key_H)
    QTest.keyClick(root, Qt.Key_Backspace)
    assert tp.strokeCode == '' and editor.property('text') == '你一'
    tp.addStroke('h')
    QTest.qWait(30)
    assert root.grabWindow().save(str(tmp_path / f'stroke-search-{size[0]}.png'))
    root.setProperty('currentPage', 0)
    QTest.qWait(30)
    assert not tp._local_stroke_input


def test_local_followup_commit_uses_current_caret_and_new_stroke_exits_prediction(inline_ui, tmp_path):
    from PySide6.QtCore import QObject
    from PySide6.QtTest import QTest
    c, _, _, root, _ = inline_ui
    tp, _ = prepare(c)
    root.setProperty('currentPage', 3); root.show(); tp.setInputMode('stroke'); QTest.qWait(40)
    editor = root.findChild(QObject, 'strokeTestEditor')
    editor.setProperty('text', '你好'); editor.setProperty('cursorPosition', 2)
    assert tp.predictionMode
    QTest.qWait(30)
    assert root.grabWindow().save(str(tmp_path / 'stroke-prediction.png'))
    predicted = tp.strokeCandidates[0]
    tp.selectCandidate(0)
    assert editor.property('text') == '你好'+predicted
    tp.addStroke('h')
    assert not tp.predictionMode and tp.strokeCode == '一'
    tp.clearStrokes()
    editor.setProperty('cursorPosition', 0)
    assert not tp.predictionMode and not tp.strokeCandidates


def test_compact_stroke_page_ring_gestures_and_tap_commit(inline_ui, tmp_path):
    from PySide6.QtCore import QObject, QMetaObject
    from PySide6.QtTest import QTest
    c, _, _, root, _ = inline_ui
    tp, source = prepare(c)
    root.setProperty('currentPage', 3)
    root.show()
    tp.setInputMode('stroke')
    QTest.qWait(40)
    for _ in range(100):
        if tp._local_stroke_input: break
        QTest.qWait(10)
    assert tp._local_stroke_input
    c._desktop_target_adapter = lambda: pytest.fail('Ring local input must not query external caret')
    root.findChild(QObject, 'strokeModeButton').forceActiveFocus()
    QMetaObject.invokeMethod(root.findChild(QObject, 'strokeRingToggle'), 'click')
    QTest.qWait(20)
    assert root.findChild(QObject, 'strokeTestEditor').property('activeFocus')
    source.future.set_result(None); QTest.qWait(20)
    for _ in range(100):
        if tp.state == 'running': break
        QTest.qWait(10)
    assert tp.state == 'running'
    run = tp._run
    trace = [[0., 0.], [5., 6.], [10., 9.]]
    tp._post(run, 'trace', (run.output, trace, True)); QTest.qWait(20)
    canvas = root.findChild(QObject, 'strokeTrailCanvas')
    assert abs(canvas.width() - canvas.height()) < 1
    assert canvas.property('opacity') > 0
    assert root.grabWindow().save(str(tmp_path / 'stroke-trace.png'))
    QTest.qWait(330)
    assert canvas.property('opacity') == 0
    template = next(item for item in tp._stroke_dictionary().templates if item['category'] == 'h')
    recognition = tp._stroke_dictionary().recognize(template['trace'])
    assert recognition['recognizer'] == 'dtw_v1'
    assert recognition['sample_count'] == 32
    assert recognition['template_profile'] != 'none'
    tp._post(run, 'stroke', template['trace']); QTest.qWait(20)
    assert tp.strokeCode == '一'
    import struct
    from ring_python_sdk.swipe.processor import SwipeProcessor
    from ring_python_sdk.core.constants import SWIPE_GESTURE_IDS_V2
    firmware = SwipeProcessor(tmp_path / 'firmware.csv', print_events=False,
        print_triggers=False, print_profile=False, on_trigger=source.starts[0]['on_gesture'])
    seq = 0
    def swipe(class_id):
        nonlocal seq
        seq += 1
        probabilities = [float(value == class_id) for value in SWIPE_GESTURE_IDS_V2]
        packet = (b'\x26\x07' + struct.pack('<HBIf', seq, class_id, seq*1000, .99)
                  if class_id == 5 else
                  b'\x26\x06' + struct.pack('<HB12fIIf', seq, class_id, *probabilities, seq*1000, seq*1000, 1.))
        firmware.handle_notification(None, bytearray(packet))
        QTest.qWait(20)
    for _ in range(5): swipe(4)
    assert tp.candidatePage == 1 and tp.selectedCandidate == 0
    swipe(3)
    assert tp.candidatePage == 0 and tp.selectedCandidate == 4
    selected = tp.strokeCandidates[4]
    tp._post(run, 'tap', None); QTest.qWait(20)
    assert tp.strokeCode == '一'  # Mouse-click path cannot duplicate firmware tap.
    swipe(5)
    assert root.findChild(QObject, 'strokeTestEditor').property('text') == selected
    assert tp.strokeCode == ''
    tp.addStroke('h'); tp.addStroke('s')
    tp._post(run, 'tap', None); QTest.qWait(20)
    assert tp.strokeCode == '一 丨'
    swipe(1)
    assert tp.strokeCode == '一'
    swipe(2)
    assert tp.strokeCode == ''
    tp.stop(); QTest.qWait(20)
    assert source.stops == 1
    firmware.close()


def test_system_ime_preedit_hides_hint_without_moving_it(inline_ui):
    from PySide6.QtCore import QObject, QCoreApplication, QPointF
    from PySide6.QtGui import QInputMethodEvent
    from PySide6.QtTest import QTest
    c, _, _, root, _ = inline_ui
    tp, _ = prepare(c)
    root.setProperty('currentPage', 3); root.show(); tp.setInputMode('stroke')
    QTest.qWait(40)
    editor = root.findChild(QObject, 'strokeTestEditor')
    hint = root.findChild(QObject, 'strokeInputHint')
    editor.forceActiveFocus()
    position = hint.mapToScene(QPointF())
    assert hint.isVisible()
    QCoreApplication.sendEvent(editor, QInputMethodEvent('a', []))
    assert editor.property('text') == '' and editor.property('preeditText') == 'a'
    assert not hint.isVisible()
    QCoreApplication.sendEvent(editor, QInputMethodEvent('', []))
    assert hint.isVisible() and hint.mapToScene(QPointF()) == position
    commit = QInputMethodEvent('', [])
    commit.setCommitString('啊')
    QCoreApplication.sendEvent(editor, commit)
    assert editor.property('text') == '啊' and not hint.isVisible()


def test_real_stroke_worker_delivers_numeric_trace_to_qml(inline_ui, tmp_path):
    import time
    from PySide6.QtCore import QObject
    from PySide6.QtTest import QTest
    from ring_python_sdk.touchpad import TouchpadContact, TouchpadMove, TouchpadClickVerdict
    c, _, _, root, _ = inline_ui
    tp, source = prepare(c)
    # Feed production stroke worker through the SDK event callback.
    root.setProperty('currentPage', 3); root.show(); tp.setInputMode('stroke')
    QTest.qWait(40); tp.start(); QTest.qWait(40)
    source.future.set_result(None); QTest.qWait(40)
    for _ in range(100):
        if tp.state == 'running': break
        time.sleep(.005); QTest.qWait(10)
    assert tp.state == 'running'
    now = time.monotonic()
    deliver = source.starts[0]['on_event']
    deliver(TouchpadContact('down', 0, now))
    for step in range(25):
        deliver(TouchpadMove(2., 1., .99, step, now))
    QTest.qWait(60)
    trail = root.findChild(QObject, 'strokeTrailDisplay')
    canvas = root.findChild(QObject, 'strokeTrailCanvas')
    # Evaluate in QML to check nested numeric conversion, not just Python data.
    from PySide6.QtQml import QQmlExpression, QQmlEngine
    expr = QQmlExpression(QQmlEngine.contextForObject(trail), trail,
                         'points.length > 2 && typeof points[1][0] === "number"')
    for _ in range(50):
        result = expr.evaluate()
        if result[0] is True: break
        time.sleep(.005)  # Allow the Python worker to run between Qt polls.
        QTest.qWait(10)
    debug = QQmlExpression(QQmlEngine.contextForObject(trail), trail, 'JSON.stringify(points)').evaluate()
    assert result[0] is True, (debug, tp._run.output.collector.points, tp._run.output.collector.touching)
    assert canvas.property('opacity') == 1
    assert root.grabWindow().save(str(tmp_path / 'live-ring-trace.png'))
    deliver(TouchpadContact('up', 25, time.monotonic()))
    deliver(TouchpadClickVerdict(0, 25, False, time.monotonic()))
    for _ in range(100):
        if tp.strokeCode: break
        time.sleep(.005); QTest.qWait(10)
    assert tp.strokeCode
    tp.stop(); QTest.qWait(30)
