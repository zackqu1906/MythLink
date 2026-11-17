import json
import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, QMetaObject, QPointF
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from proximic_ring.text_processing import LLMSettings, OpenAICompatibleTextProcessor
from proximic_ring.text_processing.handwriting_correction import validate_handwriting_match
from proximic_ring.touchpad.stroke.stroke_menu import two_finger_selection
from test_inline_ui import inline_ui
from test_stroke_session import stroke, wake, until


@pytest.mark.parametrize('response', [dict(original_text=None), dict(original_text='不存在'),
    dict(original_text='清华'), dict(original_text='青华', modified_text='其他'), [], 'bad json'])
def test_invalid_matches_never_become_rewrites(response):
    with pytest.raises(ValueError):
        validate_handwriting_match('明天去青华大学开会。', '清华', response)


def test_repeated_fragment_is_not_globally_replaced():
    with pytest.raises(ValueError):
        validate_handwriting_match('青华，青华。', '清华', {'original_text': '青华'})
    with pytest.raises(ValueError):
        validate_handwriting_match('哈哈哈', '呵呵', {'original_text': '哈哈'})
    with pytest.raises(ValueError):
        validate_handwriting_match('甲' * 65, '清华', {'original_text': '甲' * 65})
    assert validate_handwriting_match('明天去清大。', '清华大学', {'original_text': '清大'}) == '清大'


def test_model_receives_preceding_text_and_authoritative_handwriting(monkeypatch):
    processor = OpenAICompatibleTextProcessor()
    calls = []
    def request(settings, **kwargs):
        calls.append((settings, kwargs))
        return '{"original_text":"青华"}'
    monkeypatch.setattr(processor, '_request_chat', request)
    assert processor.locate_handwriting_correction('明天去青华大学开会。', '清华',
        LLMSettings(model='test', api_key_env='', timeout_s=45)) == '青华'
    settings, request = calls[0]
    assert settings.enabled and settings.timeout_s == 20
    assert json.loads(request['user_content']) == {'original': '明天去青华大学开会。', 'handwriting': '清华'}
    assert request['require_complete'] and request['max_tokens'] == 512


def test_identical_preceding_fragment_can_remove_duplicate_handwriting():
    assert validate_handwriting_match('明天去清华大学。', '清华', {'original_text': '清华'}) == '清华'


def test_right_pull_is_opt_in_and_matches_left_progress():
    for enabled in (False, True):
        for x in (6, 18, 24, 30, 36):
            right = two_finger_selection([(0, 0), (x, 0)], correction_enabled=enabled, finished=True)
            left = two_finger_selection([(0, 0), (-x, 0)], finished=True)
            assert right['action'] == ('correct-text' if enabled and x >= 30 else 'select')
            assert right['correction_progress'] == (left['clear_progress'] if enabled else 0)
    assert two_finger_selection([(0, 0), (35, 0), (0, 0)], correction_enabled=True,
                                finished=True)['action'] == 'cancel-selection'
    assert two_finger_selection([(0, 0), (30, 30)], correction_enabled=True,
                                finished=True)['action'] == 'select'


def native(c, session, kind, **fields):
    inline = c._inline_input
    inline._accept(dict(type=kind, epoch=inline._epoch, client_id=inline.stroke_target[1],
                        stroke_session=session._token, **fields))


def test_setting_routes_long_pull_without_committing_a_candidate(stroke):
    c, bridge, tp, _, session = stroke
    assert not tp.handwritingCorrectionEnabled
    tp.handwritingCorrectionEnabled = True
    assert c._settings.value('touchpad/handwritingCorrection', type=bool)
    wake(stroke)
    assert next(m for m in bridge.messages if m['type'] == 'stroke_begin')['correction_enabled']
    native(c, session, 'stroke_correction_state', text='清华', available=True)
    run = tp._run
    decision = two_finger_selection([(0, 0), (30, 0)], correction_enabled=True, finished=True)
    tp._receive(run, 'selection', (run.output, dict(decision=decision)))
    assert any(m['type'] == 'stroke_correction_request' for m in bridge.messages)
    assert not any(m['type'] == 'stroke_commit' for m in bridge.messages)
    tp.addStroke('h')
    count = len(bridge.messages)
    tp._receive(run, 'selection', (run.output, dict(decision=decision)))
    assert not any(m['type'] == 'stroke_correction_request' for m in bridge.messages[count:])
    assert '先确认' in tp.commitMessage and tp._code == 'h'
    tp.handwritingCorrectionEnabled = False
    assert not tp.handwritingCorrectionText
    assert bridge.messages[-1]['type'] == 'stroke_correction_configure' and not bridge.messages[-1]['enabled']


def test_async_match_delivers_once_to_current_session(stroke, monkeypatch):
    c, bridge, tp, _, session = stroke
    tp.handwritingCorrectionEnabled = True
    wake(stroke)
    processor = c._text_processing_worker._processor
    monkeypatch.setattr(processor, 'locate_handwriting_correction', lambda *_: '青华', raising=False)
    native(c, session, 'stroke_correction_state', text='清华', busy=True)
    native(c, session, 'stroke_correction_requested', request_id='edit-one', original='去青华。', handwriting='清华')
    until(lambda: any(m['type'] == 'stroke_correction_result' for m in bridge.messages))
    result = next(m for m in bridge.messages if m['type'] == 'stroke_correction_result')
    assert result['original_text'] == '青华' and not result['error'] and result['stroke_session'] == session._token
    tp.addStroke('h')
    assert not tp._code  # Native correction is still busy until writeback completes.


def test_firmware_left_swipe_undoes_correction_and_preserves_candidate_navigation(stroke, monkeypatch):
    c, bridge, tp, _, session = stroke
    tp.handwritingCorrectionEnabled = True
    wake(stroke)
    moves = []
    monkeypatch.setattr(tp, '_move_candidate', moves.append)
    monkeypatch.setattr(type(c._global_gesture_bindings), 'action_for',
                        lambda *_: pytest.fail('correction undo leaked to global shortcuts'))
    native(c, session, 'stroke_correction_state', can_undo=True, busy=False)
    assert not c._ring_gestures.filter(SimpleNamespace(name='swipe-left'), False, c._disconnect_event)
    until(lambda: any(m['type'] == 'stroke_correction_undo' for m in bridge.messages))
    assert not moves and not any(m['type'] == 'undo' for m in bridge.messages)

    native(c, session, 'stroke_correction_state', can_undo=False, busy=False)
    assert tp.consume_gesture(SimpleNamespace(name='swipe-left'), c._disconnect_event)
    until(lambda: moves == [-1])
    native(c, session, 'stroke_correction_state', can_undo=True, busy=False)
    tp.addStroke('h')
    assert not tp.handwritingCorrectionCanUndo
    assert tp.consume_gesture(SimpleNamespace(name='swipe-left'), c._disconnect_event)
    until(lambda: moves == [-1, -1])
    assert len([m for m in bridge.messages if m['type'] == 'stroke_correction_undo']) == 1


def test_correction_undo_gesture_drops_after_editor_focus_changes(stroke, monkeypatch):
    c, bridge, tp, _, session = stroke
    tp.handwritingCorrectionEnabled = True
    wake(stroke)
    native(c, session, 'stroke_correction_state', can_undo=True, busy=False)
    monkeypatch.setattr(c._inline_input, '_foreground_identity', lambda: ('other.editor', 456))
    assert tp.consume_gesture(SimpleNamespace(name='swipe-left'), c._disconnect_event)
    until(lambda: session.phase == 'idle')
    assert not any(m['type'] == 'stroke_correction_undo' for m in bridge.messages)


def test_match_returned_after_exit_is_discarded(stroke, monkeypatch):
    c, bridge, tp, _, session = stroke
    tp.handwritingCorrectionEnabled = True
    wake(stroke)
    gate = threading.Event()
    def match(*_):
        gate.wait(2)
        return '青华'
    monkeypatch.setattr(c._text_processing_worker._processor, 'locate_handwriting_correction', match, raising=False)
    native(c, session, 'stroke_correction_requested', request_id='late', original='去青华。', handwriting='清华')
    session.cancel()
    gate.set()
    until(lambda: not tp._handwriting_correction.thread.is_alive())
    tp._handwriting_correction._poll()
    assert not any(m['type'] == 'stroke_correction_result' for m in bridge.messages)


def test_correction_key_pins_process_and_rejects_duplicate(stroke, monkeypatch):
    c, bridge, tp, _, session = stroke
    tp.handwritingCorrectionEnabled = True
    wake(stroke)
    native(c, session, 'stroke_correction_state', text='清华', busy=True)
    calls = []
    monkeypatch.setattr('proximic_ring.platforms.macos.wechat_native_keys.send_input_method_key',
                        lambda *args, **kw: calls.append((args, kw)))
    for _ in range(2):
        native(c, session, 'stroke_correction_key', request_id='key', application='test.editor',
               command='select_to_start', count=0, event_tag=42)
    assert calls == [(('test.editor', 'select_to_start', 0, 42), {'expected_pid': 123})]
    monkeypatch.setattr(c._inline_input, '_foreground_identity', lambda: ('other.app', 456))
    native(c, session, 'stroke_correction_key', request_id='changed', application='test.editor',
           command='select_to_start', count=0, event_tag=43)
    assert len(calls) == 1


def test_right_pull_scope_is_complete_and_switch_is_on_stroke_page(inline_ui):
    c, _, _, root, _ = inline_ui
    tp = c.touchpad
    root.setProperty('currentPage', 3)
    page = root.findChild(QObject, 'touchpadPage')
    if page is not None:
        page.setProperty('section', 'stroke')
    # The section is selected by the existing page button.
    button = root.findChild(QObject, 'strokeModeButton')
    QMetaObject.invokeMethod(button, 'click')
    tp.handwritingCorrectionEnabled = True
    word = '清华大学人机交互研究实验室'
    tp._handwriting_correction.state = dict(text=word)
    tp._selection_state = two_finger_selection([(0, 0), (24, 0)], correction_enabled=True)
    tp.changed.emit(); tp.selectionChanged.emit(); root.show(); QTest.qWait(100)
    scope = root.findChild(QQuickItem, 'strokeCorrectionScope')
    menu = root.findChild(QQuickItem, 'strokeSelectionMenu')
    switch = root.findChild(QObject, 'handwritingCorrectionSwitch')
    assert switch.property('checked') and switch.property('visible')
    assert scope.isVisible() and menu.property('correctionProgress') == .5
    assert scope.mapToScene(QPointF(0, scope.height())).y() <= menu.mapToScene(QPointF(0, 0)).y()
    labels = [child.property('text') for child in scope.findChildren(QObject) if child.property('text')]
    assert '修正用词「' + word + '」' in labels
    assert not scope.property('highlighted')
    tp._selection_state = two_finger_selection([(0, 0), (30, 0)], correction_enabled=True)
    tp.changed.emit(); tp.selectionChanged.emit(); QTest.qWait(30)
    assert scope.property('highlighted') and scope.property('color').name() == '#234cdb'
    assert root.grabWindow().save('/private/tmp/mythlink-handwriting-correction-settings.png')
    tp._selection_state = two_finger_selection([(0, 0), (24, 0)], correction_enabled=True)
    tp.changed.emit(); tp.selectionChanged.emit(); QTest.qWait(30)
    assert not scope.property('highlighted') and scope.property('color').name() == '#e9efff'
    message = '青华 → 清华 · 左滑撤销'
    tp._selection_state = {}
    tp._handwriting_correction.state = dict(text='', message=message, can_undo=True)
    tp._commit_message = message
    tp.changed.emit(); tp.selectionChanged.emit(); QTest.qWait(30)
    assert scope.isVisible() and not root.findChild(QQuickItem, 'strokeCommitMessage').isVisible()
