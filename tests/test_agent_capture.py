"""Capture is an attachment transaction; it must not become an Agent command."""
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_inline_ui import inline_ui
from test_agent_ui import finish_fixture_voice
from test_ui_shell import visual_child
from test_touchpad_click_routing import Mouse, until
from proximic_ring.agent_workflow.capture.capture_geometry import Selection


def test_rectangle_retina_negative_origin_and_clipped_extrema():
    selection = Selection((-600, -200, 600, 400))
    for point in [(-590.5, -189.5), (-550, -160), (-560, -130)]: selection.add(*point)
    assert selection.pixels(1200, 800) == (19, 21, 81, 119)
    selection.add(-900, -900); selection.add(100, 300)
    assert selection.pixels(1200, 800) == (0, 0, 1200, 800)
    selection.add(float('nan'), 0)
    assert selection.bounds == (-600, -200, 0, 200)


def test_decimation_keeps_extrema_and_tiny_selection_is_rejected():
    selection = Selection((0, 0, 1000, 1000))
    selection.add(1, 1)
    with pytest.raises(ValueError): selection.pixels(2000, 2000)
    selection.add(0, 0); selection.add(1000, 1000)
    for i in range(12000): selection.add(i % 100, (i * 3) % 100)
    assert len(selection.points) <= 4096
    assert selection.pixels(2000, 1500) == (0, 0, 2000, 1500)


@pytest.fixture
def capture(inline_ui, monkeypatch, tmp_path):
    from PySide6.QtGui import QImage, QColor, QCursor
    from proximic_ring.agent_workflow.capture import capture_controller as module
    from proximic_ring.agent_workflow.ui.agent_overlay import AgentOverlay
    owner = inline_ui[0]; agent = owner.agent
    owner._proximity.close()
    original_cursor = QCursor.pos()
    finish_fixture_voice(owner)
    agent._visible = agent._focused = True
    agent._workflow_timer.stop(); agent._memory_timer.stop()
    sent = []; monkeypatch.setattr(agent, '_send', sent.append)
    monkeypatch.setattr(module, 'cache_root', lambda: tmp_path)
    def backend(specs, excluded, cancelled):
        images = []
        for spec in specs:
            _, _, w, h = spec['geometry']
            image = QImage(w * 2, h * 2, QImage.Format_RGB32)
            image.fill(QColor('#3e85b7')); images.append(image)
        return images
    agent.capture.backend = backend
    overlay = AgentOverlay(agent, owner); overlay.reduce_motion = True
    overlay.focus('task')
    yield owner, agent.capture, overlay, sent, tmp_path
    agent.capture.cancel(); overlay.close(); agent.close()
    QCursor.setPos(original_cursor)


def ready(capture):
    from PySide6.QtTest import QTest
    deadline = time.monotonic() + 2
    while capture.phase == 'preparing' and time.monotonic() < deadline: QTest.qWait(10)
    assert capture.phase == 'locating', capture.agent.errorMessage


def draw(capture):
    x, y, _, _ = capture.windows[0].spec['geometry']
    capture.tap(0, x + 50, y + 40)
    capture.move(x + 150, y + 40); capture.move(x + 150, y + 140)
    capture.tap(0, x + 50, y + 100)
    assert capture.phase == 'preview'


def test_button_trace_preview_attach_and_explicit_send(capture):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QImage
    from PySide6.QtTest import QTest
    owner, cap, overlay, sent, root = capture; agent = owner.agent
    gesture = 'swipe-right'
    task = overlay.windows['task']
    button = visual_child(task.rootObject(), 'agentCaptureButton')
    assert button.isVisible() and button.isEnabled()
    point = button.mapToScene(QPointF(button.width()/2, button.height()/2)).toPoint()
    QTest.mouseClick(task, Qt.LeftButton, Qt.NoModifier, point); ready(cap)
    overlay.layout()
    assert not task.isVisible() and agent.pickingAttachments
    # Swipes cannot confirm before a complete selection exists.
    assert agent.filter_gesture(SimpleNamespace(name=gesture), owner._disconnect_event)
    QTest.qWait(10); assert cap.phase == 'locating'
    draw(cap)
    assert agent.filter_gesture(SimpleNamespace(name=gesture), owner._disconnect_event)
    QTest.qWait(30)
    assert not cap.active and not sent and not agent.pickingAttachments
    assert task.isVisible()
    item, = agent.inputAttachments
    image = QImage(item['path']); assert (image.width(), image.height()) == (200, 200)
    assert len(list(root.rglob('*.png'))) == 1
    assert visual_child(task.rootObject(), 'agentAttachmentPreview0').property('paintedWidth') > 0
    # Screenshot is only a draft attachment until the normal send path runs.
    agent.submit('task')
    assert len(sent) == 1 and sent[0]['attachments'][0]['path'] == item['path']


def test_cancel_redraw_stale_swipe_and_original_turn(capture):
    from PySide6.QtTest import QTest
    owner, cap, _, sent, root = capture; agent = owner.agent
    agent.workspace.update({'turnId': 'original'})
    cap.start('task'); ready(cap)
    cap.tap(0, 50, 40); cap.move(150, 140)
    old_context = cap.context
    cap.tap(0, 50, 140)
    cap._handle_input(old_context, 'swipe-right', None, time.monotonic())
    assert cap.active and cap.phase == 'preview'
    cap.reset(); assert cap.phase == 'locating' and cap.selection is None
    draw(cap)
    agent.workspace.update({'turnId': 'next'})
    cap.confirm(); QTest.qWait(10)
    with pytest.raises(ValueError, match='原任务已结束'): agent.workspace.submission('task')
    assert not sent
    agent.workspace.remove_attachment('task', agent.inputAttachments[0]['path'])
    existing = list(root.rglob('*.png'))
    cap.start('task'); ready(cap); draw(cap); cap.cancel()
    assert list(root.rglob('*.png')) == existing


@pytest.mark.parametrize('phase', ['drawing', 'preview'])
@pytest.mark.parametrize('input_kind', ['ring', 'keyboard'])
def test_left_clears_selection_then_exits_down_does_not_attach(capture, phase, input_kind):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    owner, cap, overlay, sent, root = capture
    owner.agent.edit('task', '保留草稿')
    cap.start('task'); ready(cap)
    frame = cap.windows[0].image
    if phase == 'preview': draw(cap)
    else: cap.tap(0, 50, 40); cap.move(150, 140)
    def send(direction):
        if input_kind == 'ring':
            assert owner.agent.filter_gesture(SimpleNamespace(name='swipe-' + direction), owner._disconnect_event)
        else: QTest.keyClick(cap.windows[0], {'left': Qt.Key_Left, 'down': Qt.Key_Down}[direction])
        QTest.qWait(20)
    send('down')
    assert cap.phase == phase and not owner.agent.inputAttachments
    send('left')
    assert cap.active and cap.phase == 'locating' and cap.selection is None
    assert cap.selected_screen == -1 and cap.windows[0].image is frame
    assert owner.agent.pickingAttachments and '左滑退出' in cap.notice
    send('left')
    assert not cap.active and not owner.agent.pickingAttachments
    assert overlay.windows['task'].isVisible() and owner.agent.draft('task') == '保留草稿'
    assert not sent and not owner.agent.inputAttachments and not list(root.rglob('*.png'))


def test_left_without_selection_exits_and_invalidates_pending_capture(capture):
    from PySide6.QtTest import QTest
    owner, cap, _, sent, root = capture
    cap.start('task'); ready(cap)
    cap.consume(SimpleNamespace(name='swipe-left'), owner._disconnect_event); QTest.qWait(20)
    assert not cap.active
    waiting = threading.Event(); started = threading.Event()
    def slow(*_): started.set(); waiting.wait(2); return []
    cap.backend = slow; cap.start('task'); assert started.wait(1)
    cap.consume(SimpleNamespace(name='swipe-left'), owner._disconnect_event); QTest.qWait(20)
    assert not cap.active and cap.cancelled.is_set()
    waiting.set(); QTest.qWait(30)
    assert not sent and not owner.agent.inputAttachments and not list(root.rglob('*.png'))


def test_toolbar_avoids_dock_and_work_area_changes_keep_selection(capture):
    _, cap, _, _, _ = capture
    cap.start('task'); ready(cap); draw(cap)
    window = cap.windows[0]; selection = cap.selection
    sx, sy, width, height = window.spec['geometry']
    for dock_height in (0, 80, 150):
        window.spec['available_geometry'] = (sx, sy + 24, width, height - 24 - dock_height)
        window.refresh()
        assert window.toolbar.geometry().bottom() + 1 <= min(height - dock_height - 32, height - 96)
        assert window.toolbar.y() >= 24
    cap._available_geometry_changed()
    assert cap.phase == 'preview' and cap.selection is selection


def test_late_worker_and_permission_failure_restore_task(capture, monkeypatch):
    from PySide6.QtTest import QTest
    from proximic_ring.agent_workflow.capture.capture_backend import CapturePermissionError
    owner, cap, overlay, sent, root = capture
    waiting = threading.Event(); started = threading.Event()
    def slow(*_): started.set(); waiting.wait(2); return []
    cap.backend = slow
    cap.start('task'); assert started.wait(1)
    cap.cancel(); waiting.set(); QTest.qWait(50)
    assert not cap.active and not owner.agent.inputAttachments and not list(root.rglob('*.png'))
    requested = []
    monkeypatch.setattr(owner._inline_input.permissions, 'openScreenRecordingSettings', lambda: requested.append(1))
    def denied(*_): raise CapturePermissionError('需要屏幕录制权限')
    cap.backend = denied; cap.start('task')
    deadline = time.monotonic() + 2
    while cap.active and time.monotonic() < deadline: QTest.qWait(10)
    assert not cap.active and requested == [1]
    assert '屏幕录制' in owner.agent.errorMessage and overlay.windows['task'].isVisible() and not sent


def test_existing_pointer_is_reused_tap_deduplicated_and_context_released(capture):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QCursor
    from PySide6.QtTest import QTest
    from test_ui_touchpad import prepare
    owner, cap, _, _, _ = capture
    pointer, source = prepare(owner); pointer.start(); QTest.qWait(20)
    source.future.set_result(None); QTest.qWait(20)
    output = pointer._run.output
    original_context = output.options['click_context']()
    cap.start('task'); ready(cap)
    assert len(source.starts) == 1 and pointer._run.output is output
    assert output.options['click_context']() == cap.context
    QCursor.setPos(QPoint(50, 40))
    owner.agent.filter_gesture(SimpleNamespace(name='tap'), owner._disconnect_event)
    QTest.qWait(10); assert cap.phase == 'locating'  # Duplicate firmware tap ignored.
    output.options['on_capture_click'](cap.context); QTest.qWait(10)
    assert cap.phase == 'drawing'
    cap.move(150, 140)
    output.options['on_capture_click'](cap.context); QTest.qWait(10)
    assert cap.phase == 'preview' and not pointer.strokeSession.engaged
    cap.cancel()
    assert pointer.active and source.stops == 0 and pointer._capture_target is None
    assert output.options['click_context']() != original_context
    pointer.stop(); QTest.qWait(20)


def test_capture_started_pointer_stops_on_disconnect_and_no_input_loss(capture):
    from PySide6.QtTest import QTest
    from test_ui_touchpad import prepare
    owner, cap, _, sent, _ = capture; agent = owner.agent
    agent.edit('task', '保留原草稿')
    pointer, source = prepare(owner)
    cap.start('task'); ready(cap); QTest.qWait(20)
    assert len(source.starts) == 1
    source.future.set_result(None); QTest.qWait(20)
    owner._connected = False; owner.connectedChanged.emit(); QTest.qWait(30)
    assert not cap.active and source.stops == 1 and agent.draft('task') == '保留原草稿' and not sent
    owner._inline_input._view['phase'] = 'listening'
    cap.start('task'); assert not cap.active


def test_cancel_button_escape_and_display_change_do_not_save(capture):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QPushButton
    owner, cap, _, sent, root = capture
    cap.start('task'); ready(cap)
    window = cap.windows[0]
    buttons = window.findChildren(QPushButton)
    assert len(buttons) == 2 and all('Esc' not in button.text() for button in buttons)
    assert window.back.text() == '← 退出截图' and window.confirm.text() == '发送 →'
    draw(cap)
    assert window.back.text() == '← 取消选区'
    QTest.mouseClick(window.back, Qt.LeftButton)
    assert cap.active and cap.selection is None and window.back.text() == '← 退出截图'
    QTest.mouseClick(window.back, Qt.LeftButton)
    assert not cap.active
    cap.start('task'); ready(cap); draw(cap)
    QTest.keyClick(cap.windows[0], Qt.Key_Escape)
    assert not cap.active
    cap.start('task'); ready(cap); cap._screen_changed()
    assert not cap.active and '显示器布局' in owner.agent.errorMessage
    assert not sent and not list(root.rglob('*.png'))


@pytest.mark.parametrize('clicks', [False, True])
def test_native_pointer_worker_routes_capture_clicks_without_mouse_or_stroke(clicks):
    from proximic_ring.touchpad.touchpad_mouse import TouchpadMouseOutput
    from ring_python_sdk.touchpad import TouchpadMove, TouchpadClick
    mouse = Mouse(); ready_event = threading.Event(); ended = threading.Event(); taps = []
    context = [((1, 1), 'capture')]
    output = TouchpadMouseOutput(connection=threading.Event(), gain=1, clicks=clicks, invert_y=False,
        on_ready=ready_event.set, on_end=lambda *_: ended.set(), mouse_factory=lambda: mouse,
        click_context=lambda: context[0], on_capture_click=lambda ctx: taps.append((ctx, mouse.point)),
        on_double_click=lambda _: pytest.fail('capture must never enter stroke mode'))
    output.start(); assert ready_event.wait(1); output.activate()
    try:
        output.submit(TouchpadMove(30, 40, 1, 1, time.monotonic()))
        click = TouchpadClick(2, time.monotonic())
        output.submit(click); until(lambda: len(taps) == 1)
        output.submit(click)  # Duplicate SDK callbacks must not end the trace.
        output.submit(TouchpadClick(3, time.monotonic())); until(lambda: len(taps) == 2)
        assert taps == [(context[0], (40, 60))] * 2
        assert not mouse.clicked and mouse.double_targets == 0
        context[0] = (0, 'closed')
        output.submit(TouchpadClick(4, time.monotonic()))
        if clicks: until(lambda: len(mouse.clicked) == 1)
    finally:
        output.stop(); assert ended.wait(1)
