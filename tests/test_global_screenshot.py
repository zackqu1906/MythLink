"""Global screenshot saves survive redraw/cancel and do not enter Agent drafts."""
from pathlib import Path
from types import SimpleNamespace
import os
import time

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage, QGuiApplication
from PySide6.QtTest import QTest

from test_agent_capture import capture
from test_inline_ui import inline_ui
from test_app_gestures import route
from test_gesture_ownership import outside
from test_ring_gestures import request
from proximic_ring.gestures.gesture_settings import GESTURE_LABELS, GlobalGestureBindings
from proximic_ring.screenshots.saved_capture import DIRECTORY_KEY, save_png


def start(cap):
    cap.start()
    deadline = time.monotonic() + 2
    while cap.phase == 'preparing' and time.monotonic() < deadline: QTest.qWait(10)
    assert cap.phase == 'locating', cap.status


def select_region(cap, origin=(40, 50), end=(200, 160)):
    window = cap.windows[0]
    QTest.mouseClick(window, Qt.LeftButton, pos=QPoint(*origin))
    QTest.mouseMove(window, QPoint(*end))
    QTest.mouseClick(window, Qt.LeftButton, pos=QPoint(*end))
    assert cap.phase == 'preview'


@pytest.fixture
def screenshot(capture, tmp_path, monkeypatch):
    owner, agent_capture, overlay, sent, _ = capture
    owner.agent._visible = owner.agent._focused = False
    cap = owner.ringGestures.screenshot
    # OCR/context ingestion is tested independently; no model work in UI tests.
    monkeypatch.setattr(cap, 'process_saved_capture', lambda path: None)
    cap.backend = agent_capture.backend
    cap.setDirectory(str(tmp_path / 'saved-images'))
    yield owner, cap, sent, tmp_path / 'saved-images'
    cap.cancel()


def test_two_clicks_reselect_save_and_copy_only_after_confirmation(screenshot):
    owner, cap, sent, root = screenshot
    start(cap)
    select_region(cap)
    assert not root.exists() and not owner.agent.inputAttachments
    # A third click replaces the old selection; the fourth fixes its corner.
    window = cap.windows[0]
    QTest.mouseClick(window, Qt.LeftButton, pos=QPoint(80, 90))
    stale = cap.context
    pointer = cap.pointer_context
    QTest.mouseMove(window, QPoint(230, 220))
    QTest.mouseMove(window, QPoint(180, 170))
    QTest.mouseClick(window, Qt.LeftButton, pos=QPoint(180, 170))
    assert cap.pointer_context == pointer
    cap._handle_input(stale, 'swipe-right', None, time.monotonic())
    assert cap.active and not root.exists()
    assert cap.consume(SimpleNamespace(name='swipe-right'), owner._disconnect_event)
    QTest.qWait(30)
    assert not cap.active
    paths = list(root.glob('*.png'))
    assert len(paths) == 1 and '截图-' in paths[0].name
    saved = QImage(str(paths[0]))
    assert (saved.width(), saved.height()) == (200, 160)
    assert QGuiApplication.clipboard().image() == saved
    assert cap.lastSavedPath == str(paths[0]) and '已保存并复制' in cap.status
    assert not sent and not owner.agent.inputAttachments
    start(cap); select_region(cap); cap.cancel()
    assert list(root.glob('*.png')) == paths


@pytest.mark.parametrize('gesture', ['tap', 'swipe-right'])
@pytest.mark.parametrize('pointer_active', [False, True])
def test_confirmation_gestures_are_independent_of_pointer_clicks(screenshot, gesture, pointer_active):
    from PySide6.QtGui import QCursor
    from test_ui_touchpad import prepare
    owner, cap, _, root = screenshot
    if pointer_active:
        pointer, source = prepare(owner)
        pointer.start(); QTest.qWait(20)
        source.future.set_result(None); QTest.qWait(20)
    start(cap)
    window = cap.windows[0]
    QCursor.setPos(40, 50)
    cap.consume(SimpleNamespace(name=gesture), owner._disconnect_event)
    QTest.qWait(10)
    assert cap.phase == 'locating' and cap.selection is None
    QTest.mouseClick(window, Qt.LeftButton, pos=QPoint(40, 50))
    QTest.mouseMove(window, QPoint(200, 160))
    cap.consume(SimpleNamespace(name=gesture), owner._disconnect_event)
    QTest.qWait(10)
    assert cap.phase == 'selecting' and not root.exists()
    stale = cap.context
    QTest.mouseClick(window, Qt.LeftButton, pos=QPoint(200, 160))
    cap._handle_input(stale, gesture, owner._disconnect_event, time.monotonic())
    assert cap.phase == 'preview' and not root.exists()
    # Gesture confirmation must save even with the cursor over the exit button.
    QCursor.setPos(window.back.mapToGlobal(window.back.rect().center()))
    cap.consume(SimpleNamespace(name=gesture), owner._disconnect_event)
    QTest.qWait(20)
    assert not cap.active
    saved, = root.glob('*.png')
    assert QGuiApplication.clipboard().image() == QImage(str(saved))
    if pointer_active:
        assert pointer.active
        pointer.stop(); QTest.qWait(20)


def test_failed_save_keeps_preview_and_existing_clipboard(screenshot, monkeypatch):
    from proximic_ring.screenshots import saved_capture
    _, cap, _, root = screenshot
    before = QImage(2, 2, QImage.Format_RGB32); before.fill(Qt.red)
    QGuiApplication.clipboard().setImage(before)
    start(cap); select_region(cap)
    def fail(*_): raise OSError('保存位置不可写')
    monkeypatch.setattr(saved_capture, 'save_png', fail)
    cap.confirm()
    assert cap.active and cap.phase == 'preview' and '不可写' in cap.notice
    assert not root.exists() and QGuiApplication.clipboard().image() == before


@pytest.mark.parametrize('origin,end', [
    ((40, 50), (200, 160)), ((200, 50), (40, 160)),
    ((40, 160), (200, 50)), ((200, 160), (40, 50)),
])
def test_clicks_fix_corners_hover_only_previews_and_third_click_restarts(screenshot, origin, end):
    _, cap, _, root = screenshot
    start(cap)
    window = cap.windows[0]
    QTest.mouseMove(window, QPoint(*origin))
    assert cap.selection is None and cap.phase == 'locating'
    QTest.mouseClick(window, Qt.LeftButton, pos=QPoint(*origin))
    QTest.mouseMove(window, QPoint(280, 240))
    QTest.mouseMove(window, QPoint(*end))
    assert cap.phase == 'selecting' and cap.selection.bounds == (40, 50, 200, 160)
    assert not window.confirm.isEnabled()
    cap.confirm()
    assert cap.active and not root.exists()
    QTest.mouseClick(window, Qt.LeftButton, pos=QPoint(*end))
    assert cap.phase == 'preview' and window.confirm.isEnabled()
    QTest.mouseMove(window, QPoint(300, 280))
    assert cap.selection.bounds == (40, 50, 200, 160)
    QTest.mouseClick(window, Qt.LeftButton, pos=QPoint(300, 280))
    assert cap.phase == 'selecting' and cap.selection.bounds == (300, 280, 300, 280)
    assert not window.confirm.isEnabled()
    QTest.mouseClick(window, Qt.LeftButton, pos=QPoint(100, 80))
    assert cap.phase == 'preview' and cap.selection.bounds == (100, 80, 300, 280)


@pytest.mark.parametrize('first_corner', [False, True])
def test_mouse_hold_drag_and_release_do_not_select_a_corner(screenshot, first_corner):
    _, cap, _, _ = screenshot
    start(cap)
    window = cap.windows[0]
    if first_corner:
        QTest.mouseClick(window, Qt.LeftButton, pos=QPoint(40, 50))
    phase = cap.phase
    QTest.mousePress(window, Qt.LeftButton, pos=QPoint(80, 90))
    assert cap.phase == phase
    QTest.mouseMove(window, QPoint(200, 160))
    QTest.mouseRelease(window, Qt.LeftButton, pos=QPoint(200, 160))
    assert cap.phase == phase and not window.confirm.isEnabled()
    if first_corner:
        assert cap.selection.points[0] == (40, 50)
    else:
        assert cap.selection is None


def test_tiny_second_corner_preserves_first_and_toolbar_does_not_select(screenshot):
    _, cap, _, _ = screenshot
    start(cap)
    window = cap.windows[0]
    QTest.mouseClick(window, Qt.LeftButton, pos=QPoint(40, 50))
    QTest.mouseClick(window, Qt.LeftButton, pos=QPoint(42, 51))
    assert cap.phase == 'selecting' and '太小' in cap.notice
    assert cap.selection.points[0] == (40, 50)
    QTest.mouseClick(window.hint, Qt.LeftButton)
    assert cap.phase == 'selecting' and cap.selection.points[0] == (40, 50)
    QTest.mouseClick(window.confirm, Qt.LeftButton)
    assert cap.phase == 'selecting'
    QTest.mouseClick(window, Qt.LeftButton, pos=QPoint(200, 160))
    assert cap.phase == 'preview' and not cap.notice


def test_directory_is_persisted_and_save_never_overwrites(tmp_path, screenshot):
    owner, cap, _, _ = screenshot
    destination = tmp_path / '用户截图'
    assert cap.setDirectory(str(destination))
    assert owner._settings.value(DIRECTORY_KEY) == str(destination)
    image = QImage(12, 14, QImage.Format_RGB32); image.fill(Qt.blue)
    first, second = save_png(image, destination), save_png(image, destination)
    assert first != second and QImage(str(first)) == image and QImage(str(second)) == image
    assert not cap.setDirectory('relative/path')
    assert not cap.setDirectory(str(first))
    assert cap.directory == str(destination)
    with pytest.raises(OSError): save_png(image, first)


def test_global_screenshot_can_be_assigned_to_every_gesture(route, monkeypatch):
    owner, _, _, backend, sent, *_ = route
    outside(backend)
    for gesture in GESTURE_LABELS:
        assert 'screen_capture' in {row['id'] for row in owner.ringGestures.globalActionOptions(gesture)}
        bindings = GlobalGestureBindings().with_action(gesture, 'screen_capture')
        assert GlobalGestureBindings.from_json(bindings.to_json()).action_for(gesture) == 'screen_capture'
    started = []
    monkeypatch.setattr(owner.ringGestures.screenshot, 'start', lambda: started.append(True))
    assert owner.ringGestures.setGlobalGestureAction('snap', 'screen_capture')
    request(owner, 'snap')
    assert started == [True] and not sent


def test_screenshot_owns_gestures_before_global_and_voice_actions(screenshot):
    owner, cap, _, _ = screenshot
    previous = owner.ringGestures.envelope(SimpleNamespace(name='snap'))
    start(cap)
    assert owner.touchpad.blocksVoice
    assert not owner._recognition_event.is_set()
    assert not owner.ringGestures.accepts(previous)
    for name in ('tap', 'double-tap', 'swipe-up', 'snap'):
        assert not owner.ringGestures.filter(SimpleNamespace(name=name), False, owner._disconnect_event)
    QTest.qWait(20)
    assert cap.active and cap.phase == 'locating'
    assert not owner.ringGestures.filter(SimpleNamespace(name='swipe-left'), False, owner._disconnect_event)
    QTest.qWait(20)
    assert not cap.active
    assert not owner.touchpad.blocksVoice


def test_two_click_hints_follow_selection_not_touchpad_dwell(screenshot):
    owner, cap, _, _ = screenshot
    owner.touchpad.dwellMs = 530
    start(cap)
    window = cap.windows[0]
    assert window.title.text() == '点击第一个角'
    assert '两次点击' in window.hint.text()
    owner.touchpad.dwellMs = 350
    assert '停留' not in window.hint.text() and 'ms' not in window.hint.text()
    QTest.mouseClick(window, Qt.LeftButton, pos=QPoint(40, 50))
    assert window.title.text() == '点击另一个角' and '移动光标预览' in window.hint.text()
    assert not window.confirm.isEnabled()
    QTest.mouseClick(window, Qt.LeftButton, pos=QPoint(200, 160))
    assert '点击画面重新选区' in window.hint.text() and '左滑退出' in window.back.text()
    assert 'tap / 右滑保存并复制后退出' in window.hint.text()
    assert window.confirm.toolTip() == cap.confirm_hint
    assert window.confirm.isEnabled()


@pytest.mark.parametrize('phase', ['selecting', 'preview'])
def test_left_swipe_discards_selection_and_exits(screenshot, phase):
    owner, cap, _, root = screenshot
    start(cap)
    if phase == 'preview':
        select_region(cap)
    else:
        cap.select_point(0, 80, 90)
        cap.move(200, 180)
    cap.consume(SimpleNamespace(name='swipe-left'), owner._disconnect_event)
    QTest.qWait(20)
    assert not cap.active and not cap.windows and cap.selection is None
    assert not root.exists() and not owner.touchpad.blocksVoice


@pytest.mark.parametrize('gesture', GESTURE_LABELS)
def test_bound_gesture_exits_except_builtin_save(screenshot, gesture):
    owner, cap, _, root = screenshot
    assert owner.ringGestures.setGlobalGestureAction(gesture, 'screen_capture')
    start(cap)
    expected = ('左滑退出' if gesture in ('tap', 'swipe-left', 'swipe-right') else
                f'左滑 / {GESTURE_LABELS[gesture]}退出')
    assert cap.exit_hint == expected and expected in cap.windows[0].back.text()
    select_region(cap)
    assert not owner.ringGestures.filter(SimpleNamespace(name=gesture), False, owner._disconnect_event)
    QTest.qWait(30)
    assert not cap.active
    assert bool(list(root.glob('*.png'))) == (gesture in ('tap', 'swipe-right'))


def test_exit_survives_selection_phase_change_but_not_new_capture(screenshot):
    owner, cap, _, root = screenshot
    assert owner.ringGestures.setGlobalGestureAction('index-pinch', 'screen_capture')
    start(cap)
    previous = cap.exit_context
    cap.consume(SimpleNamespace(name='index-pinch'), owner._disconnect_event)
    cap.select_point(0, 40, 50)
    cap.select_point(0, 200, 160)
    QTest.qWait(20)
    assert not cap.active and not root.exists()
    start(cap)
    cap._handle_input(previous, 'exit-capture', owner._disconnect_event, time.monotonic())
    assert cap.active


@pytest.mark.parametrize('paste_gesture', ['', 'middle-pinch'])
def test_saved_image_uses_corner_notice_with_paste_hint_not_gesture_hud(screenshot, inline_ui, paste_gesture):
    from PySide6.QtGui import QWindow
    owner, cap, _, _ = screenshot
    if paste_gesture:
        assert owner.ringGestures.setGlobalGestureAction(paste_gesture, 'clipboard_paste')
    hud = []
    owner.ringGestures.showRequested.connect(lambda *args: hud.append(args))
    start(cap); select_region(cap); cap.confirm()
    QTest.qWait(30)
    assert not cap.active and not hud
    message = owner.appGestures.notice
    assert '已保存并复制' in message and '粘贴图片' in message
    assert ('中指捏合' in message) == bool(paste_gesture)
    notice = inline_ui[3].findChild(QWindow, 'appGestureNotice')
    assert notice.isVisible() and notice.flags() & Qt.WindowDoesNotAcceptFocus
    area = notice.screen().geometry()
    assert 16 <= area.x() + area.width() - notice.x() - notice.width() <= 32
    assert area.y() <= notice.y() <= area.y() + 80
    if directory := os.environ.get('MYTHLINK_SCREENSHOT_DIR'):
        directory = Path(directory); directory.mkdir(parents=True, exist_ok=True)
        assert notice.grabWindow().save(str(directory / ('paste-notice-bound.png' if paste_gesture else 'paste-notice.png')))
