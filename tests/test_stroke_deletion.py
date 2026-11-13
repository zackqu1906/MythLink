"""Backspace and long-left deletion respect pending ink and the target editor."""
import pytest

from test_inline_ui import inline_ui
from test_stroke_session import stroke, wake
from proximic_ring.touchpad.stroke.stroke_menu import two_finger_selection


def live_focus(inline):
    return dict(epoch=inline.stroke_target[0], client_id=inline.stroke_target[1],
                application='test.editor', ready=True, selection=[2, 0])


def long_left(tp):
    run = tp._run
    tp._receive(run, 'selection', (run.output, dict(decision=two_finger_selection(
        [(0., 0.), (-30., 0.)], finished=True))))


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


@pytest.mark.parametrize('before', [(2048, 0), (3, 2), (0, 3), (0, 0)])
def test_empty_long_pull_selects_and_verifies_prefix_before_backspace(stroke, monkeypatch, before):
    c, bridge, tp, _, session = stroke
    wake(stroke, context='你好')  # Predictions are not pending ink.
    keys, reads = [], []
    expected = (0, sum(before))
    def focus(**_):
        # One stale range after posting: the OS has not delivered the selection yet.
        selected = expected if keys and len(reads) >= 2 else before
        reads.append(selected)
        return {**live_focus(c._inline_input), 'selection': selected}
    monkeypatch.setattr(bridge, 'focus_state', focus, raising=False)
    monkeypatch.setattr('proximic_ring.platforms.macos.wechat_native_keys.send_stroke_select_to_start',
                        lambda *target: keys.append(('select', target)))
    def delete(*target):
        assert reads[-1] == expected
        keys.append(('delete', target))
    monkeypatch.setattr('proximic_ring.platforms.macos.wechat_native_keys.send_stroke_backspace', delete)
    long_left(tp)
    assert keys == ([('select', ('test.editor', 123))] if before != expected else []) + (
        [('delete', ('test.editor', 123))] if sum(before) else [])
    assert not tp._stroke_context and not tp.strokeCandidates
    assert session.phase == 'running'


@pytest.mark.parametrize('after', [None, dict(client_id='other-field'), dict(epoch='stale'),
    dict(ready=False), dict(selection=[0, 1]), dict(selection=[2, 0]), dict(application='other.app')])
def test_long_pull_does_not_delete_if_selection_is_unconfirmed(stroke, monkeypatch, after):
    c, bridge, tp, _, _ = stroke
    wake(stroke)
    selected = []
    def focus(**_):
        state = live_focus(c._inline_input)
        return state if not selected else (None if after is None else {**state, **after})
    monkeypatch.setattr(bridge, 'focus_state', focus, raising=False)
    monkeypatch.setattr('proximic_ring.platforms.macos.wechat_native_keys.send_stroke_select_to_start',
                        lambda *_: selected.append(True))
    monkeypatch.setattr('proximic_ring.platforms.macos.wechat_native_keys.send_stroke_backspace',
                        lambda *_: pytest.fail('unverified prefix must not be deleted'))
    long_left(tp)
    assert selected and '未执行删除' in tp.commitMessage


def test_long_pull_rechecks_foreground_after_selecting(stroke, monkeypatch):
    c, bridge, tp, _, _ = stroke
    wake(stroke)
    monkeypatch.setattr(bridge, 'focus_state', lambda **_: live_focus(c._inline_input), raising=False)
    def select(*_):
        monkeypatch.setattr(c._inline_input, '_foreground_identity', lambda: ('other.app', 456))
    monkeypatch.setattr('proximic_ring.platforms.macos.wechat_native_keys.send_stroke_select_to_start', select)
    monkeypatch.setattr('proximic_ring.platforms.macos.wechat_native_keys.send_stroke_backspace',
                        lambda *_: pytest.fail('must not delete after foreground changed'))
    long_left(tp)
    assert '未执行删除' in tp.commitMessage
