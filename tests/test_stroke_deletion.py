"""Backspace crosses the composition boundary; outward clear never deletes text."""
import pytest

from test_inline_ui import inline_ui
from test_stroke_session import stroke, wake
from proximic_ring.touchpad.stroke.stroke_menu import two_finger_selection


def live_focus(inline):
    return dict(epoch=inline.stroke_target[0], client_id=inline.stroke_target[1],
                application='test.editor', ready=True, selection=[2, 0])


def test_backspace_removes_ink_then_can_repeat_beyond_our_commits(stroke, monkeypatch):
    c, bridge, tp, _, session = stroke
    wake(stroke, context='旧字')
    monkeypatch.setattr(bridge, 'focus_state', lambda **_: live_focus(c._inline_input), raising=False)
    keys = []
    monkeypatch.setattr('proximic_ring.platforms.macos.wechat_native_keys.send_stroke_backspace',
                        lambda *target: keys.append(target))
    tp.addStroke('h'); tp.addStroke('s')
    tp.deleteOne()
    assert tp._code == 'h' and not keys
    tp.deleteOne()
    assert not tp._code and not keys
    for _ in range(4):
        tp.deleteOne()
    assert keys == [('test.editor', 123)] * 4
    assert not tp._stroke_context and not tp.strokeCandidates
    assert session.phase == 'running'


@pytest.mark.parametrize('change', [dict(epoch='stale'), dict(client_id='different-field'),
    dict(application='different.app'), dict(ready=False), dict(selection=None)])
def test_backspace_requires_fresh_focus_before_posting(stroke, monkeypatch, change):
    c, bridge, tp, _, _ = stroke
    wake(stroke)
    monkeypatch.setattr(bridge, 'focus_state', lambda **_: {**live_focus(c._inline_input), **change}, raising=False)
    monkeypatch.setattr('proximic_ring.platforms.macos.wechat_native_keys.send_stroke_backspace',
                        lambda *_: pytest.fail('sent Backspace to unproven target'))
    tp.deleteOne()
    assert '未执行退格' in tp.commitMessage


def test_long_left_clear_preserves_committed_text_and_predictions(stroke, monkeypatch):
    c, bridge, tp, _, session = stroke
    wake(stroke, context='你好')
    monkeypatch.setattr('proximic_ring.platforms.macos.wechat_native_keys.send_stroke_backspace',
                        lambda *_: pytest.fail('clear must not send Backspace'))
    tp.addStroke('h'); tp.addStroke('s')
    run = tp._run
    points = [(0., 0.), (-18., 0.), (-30., 0.)]
    tp._receive(run, 'selection_state', (run.output, two_finger_selection(points)))
    message = next(m for m in reversed(bridge.messages) if m['type'] == 'stroke_selection')
    assert message['clear_armed'] and message['clear_distance'] == 30 and message['clear_progress'] == 1
    assert tp._code == 'hs'
    tp._receive(run, 'selection', (run.output, dict(decision=two_finger_selection(points, finished=True))))
    assert not tp._code and tp._stroke_context == '你好' and tp.predictionMode
    assert session.phase == 'running'
    assert not any(m['type'] == 'stroke_commit' for m in bridge.messages)
