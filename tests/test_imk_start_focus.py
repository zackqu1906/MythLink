"""First-gesture focus proof, source activation, and race/timeout boundaries."""
from dataclasses import replace
from types import SimpleNamespace
import time

import pytest

from proximic_ring.voice.ime.focus_probe import probe_input_focus, InputFocus
from test_app_gestures import route
from test_application_menus import configure, ACTION
from test_ring_gestures import request
from test_inline_state import component


BUNDLE = 'com.tencent.xinWeChat'
FRONT = (BUNDLE, 42)


def state(**extra):
    return dict(epoch='e', client_id='c', utterance_id='', application=BUNDLE,
                ready=True, selection=[3, 0], **extra)


def setup_probe(reply=None):
    data = {'front': FRONT, 'reply': state() if reply is None else reply, 'selected': []}
    bridge = SimpleNamespace(epoch='e', connected=True,
                             focus_state=lambda **_: data['reply'])
    def select(pid, **options):
        data['selected'].append((pid, options))
        data['reply'] = state()
    return bridge, data, select


def test_idle_live_focus_needs_neither_sentence_nor_source_switch():
    bridge, data, select = setup_probe()
    proof = probe_input_focus(bridge, lambda: data['front'], select)
    assert proof.status == 'text' and proof.target == ('e', 'c', '', BUNDLE, 42, (3, 0))
    assert not data['selected']


def test_unselected_source_is_selected_before_fresh_focus_query():
    bridge, data, select = setup_probe({'ready': False, 'client_id': '', 'application': ''})
    proof = probe_input_focus(bridge, lambda: data['front'], select)
    assert proof.status == 'text'
    assert data['selected'][0][0] == 42 and 0 < data['selected'][0][1]['timeout'] <= .8


def test_source_recovery_cannot_retarget_a_user_app_switch():
    bridge, data, _ = setup_probe({'ready': False, 'client_id': '', 'application': ''})
    def select(*_, **__):
        data['front'] = ('other.editor', 43)
        data['reply'] = state()
    proof = probe_input_focus(bridge, lambda: data['front'], select)
    assert proof.reason == 'foreground_or_connection_changed' and proof.target is None


def test_retained_client_with_invalid_selection_is_not_focus():
    bridge, data, select = setup_probe({**state(), 'ready': False, 'selection': [-1, -1]})
    assert probe_input_focus(bridge, lambda: FRONT, select).status == 'nontext'
    assert not data['selected']


@pytest.mark.parametrize('change', ['app', 'pid', 'epoch', 'late'])
def test_probe_rejects_changes_or_reply_past_deadline(change):
    bridge, data, select = setup_probe()
    def reply(**_):
        if change == 'app': data['front'] = ('other', 42)
        if change == 'pid': data['front'] = (BUNDLE, 43)
        if change == 'epoch': bridge.epoch = 'new'
        if change == 'late': time.sleep(.025)
        return state()
    bridge.focus_state = reply
    proof = probe_input_focus(bridge, lambda: data['front'], select, timeout=.02)
    assert proof.status == 'unavailable'
    assert not data['selected']


def test_timeout_cannot_use_cached_readiness_or_select_another_source():
    bridge, data, select = setup_probe()
    bridge.focus_state = lambda **_: None
    assert probe_input_focus(bridge, lambda: FRONT, select).reason == 'input_method_timeout'
    assert not data['selected']


def test_activation_callback_can_finish_after_first_unanswered_ping():
    bridge, data, select = setup_probe({'ready': False, 'client_id': '', 'application': ''})
    replies = iter([data['reply'], None, state()])
    bridge.focus_state = lambda **_: next(replies)
    assert probe_input_focus(bridge, lambda: FRONT, select).status == 'text'
    assert len(data['selected']) == 1


@pytest.mark.parametrize('reason', ['input_method_source_shortcut_unavailable',
                                  'input_method_source_event_permission', 'input_method_source_modifiers_held'])
def test_source_configuration_failure_has_actionable_reason(reason):
    from proximic_ring.voice.ime.system_input_source import InputSourceUnavailable
    bridge, _, _ = setup_probe({'ready': False, 'client_id': '', 'application': ''})
    def select(*_, **__):
        raise InputSourceUnavailable(reason)
    assert probe_input_focus(bridge, lambda: FRONT, select).reason == reason


@pytest.mark.parametrize('bundle', [BUNDLE, 'com.openai.codex'])
def test_first_start_with_current_app_conflict_uses_imk_without_ax(route, monkeypatch, bundle):
    c, service, inline, backend, sent, messages, _ = route
    assert c.setGestureBinding('confirm', 0, 'tap')
    inline._utterance_id = ''
    inline._view.update(phase='idle', ready=False)
    backend.target = replace(backend.target, bundle=bundle, input_context='unknown', focus=None)
    assert configure(service, monkeypatch, bundle).setBinding(bundle, 'tap', ACTION['id'])
    proof = ('e', 'idle-client', '', bundle, backend.target.pid, (0, 0))
    monkeypatch.setattr(inline, 'capture_start_focus', lambda: InputFocus('text', proof))
    monkeypatch.setattr(backend, 'capture', lambda **_: pytest.fail('first IMK start queried AX'))
    assert request(c, 'tap')
    assert inline._pending_gesture_focus[0] == proof
    assert not sent and not messages


@pytest.mark.parametrize('reason,notice', [
    ('input_method_timeout', '输入焦点确认超时，请重新点入文本框后重试'),
    ('input_method_not_ready', '输入法尚未就绪，请重新点入文本框后重试'),
    ('foreground_or_connection_changed', '前台应用或输入法连接已变化，本次操作已取消'),
])
def test_start_timeout_never_falls_back_to_ax_text(route, monkeypatch, reason, notice):
    c, service, inline, backend, sent, messages, _ = route
    assert c.setGestureBinding('confirm', 0, 'tap')
    assert configure(service, monkeypatch, backend.target.bundle).setBinding(backend.target.bundle, 'tap', ACTION['id'])
    monkeypatch.setattr(inline, 'capture_start_focus', lambda: InputFocus('unavailable', reason=reason))
    monkeypatch.setattr(backend, 'capture', lambda **_: pytest.fail('timeout fell back to AX'))
    notices = []
    monkeypatch.setattr(service, 'notify', notices.append)
    assert not request(c, 'tap')
    assert not sent and not messages
    assert notices == [notice]


def test_imk_nontext_cannot_be_overridden_by_ax_text(route, monkeypatch):
    c, service, inline, backend, sent, messages, _ = route
    assert c.setGestureBinding('confirm', 0, 'tap')
    assert configure(service, monkeypatch, backend.target.bundle).setBinding(backend.target.bundle, 'tap', ACTION['id'])
    monkeypatch.setattr(inline, 'capture_start_focus', lambda: InputFocus('nontext', reason='input_method_no_focus'))
    backend.target = replace(backend.target, input_context='text')
    assert not request(c, 'tap')
    assert not sent and not messages


@pytest.mark.parametrize('status', ['text', 'nontext', 'unavailable'])
def test_unconflicted_first_start_also_uses_live_imk_without_ax(route, monkeypatch, status):
    c, service, inline, backend, sent, messages, _ = route
    monkeypatch.setattr(service, 'foreground_has_binding', lambda _: False)
    proof = ('e', 'fresh', '', BUNDLE, 42, (0, 0))
    notices = []
    monkeypatch.setattr(service, 'notify', notices.append)
    monkeypatch.setattr(inline, 'capture_start_focus', lambda: InputFocus(status, proof if status == 'text' else None,
                      'input_method_timeout' if status == 'unavailable' else 'input_method_no_focus'))
    monkeypatch.setattr(backend, 'capture', lambda **_: pytest.fail('unconflicted start queried AX'))
    assert request(c, c._gesture_bindings.confirm[0]) == (status == 'text')
    assert not sent and not messages
    assert bool(notices) == (status != 'text')
    if status == 'text':
        assert inline._pending_gesture_focus[0] == proof


@pytest.mark.parametrize('change', ['client', 'selection', 'epoch', 'not_ready'])
def test_begin_ack_cannot_follow_changed_focus_proof(component, change):
    c, _ = component
    c._startup_focus_target = ('e', 'c', '', BUNDLE, 42, (3, 0))
    c._begin_origin = FRONT
    message = state()
    message[{'client': 'client_id', 'selection': 'selection', 'epoch': 'epoch', 'not_ready': 'ready'}[change]] = (
        [0, 0] if change == 'selection' else False if change == 'not_ready' else 'changed')
    assert not c._input_client_ready(message)
    assert '已变化' in c.error


def test_matching_begin_focus_does_not_require_prior_sentence(component):
    c, _ = component
    c._startup_focus_target = ('e', 'c', '', BUNDLE, 42, (3, 0))
    c._begin_origin = FRONT
    assert c._input_client_ready(state())


@pytest.mark.parametrize('reported_utterance', ['previous', 'unrelated'])
def test_pinned_start_after_settled_sentence_needs_no_source_helper(component, monkeypatch, reported_utterance):
    from test_inline_state import receive
    c, _ = component
    monkeypatch.setattr(c, '_foreground_identity', lambda: FRONT)
    c._source_switch_factory = lambda *_: pytest.fail('warm start invoked focus recovery')
    c._utterance_id = 'previous'
    c._view.update(phase='interrupted', application=BUNDLE, selection=[3, 0])
    c.pin_start_focus((c._epoch, c._client_id, 'previous', BUNDLE, 42, (3, 0)), time.monotonic())
    assert c.begin(auto_select=True)
    receive(c, ready=True, phase='interrupted', utterance_id=reported_utterance,
            application=BUNDLE, selection=[3, 0], request_id=c._startup_focus_request)
    assert any(m['type'] == 'begin' for m in c._bridge.messages) == (reported_utterance == 'previous')
    if reported_utterance == 'previous':
        began = []
        c.began.connect(lambda: began.append(True))
        receive(c, ready=True, phase='listening', application=BUNDLE, selection=[3, 0])
        assert began == [True]


@pytest.mark.parametrize('change', ['app', 'pid', 'expired'])
def test_queued_start_proof_is_rejected_before_begin(component, monkeypatch, change):
    c, _ = component
    c.pin_start_focus(('e', 'c', '', BUNDLE, 42, (3, 0)), time.monotonic() - (2 if change == 'expired' else 0))
    front = ('other', 42) if change == 'app' else (BUNDLE, 43) if change == 'pid' else FRONT
    monkeypatch.setattr(c, '_foreground_identity', lambda: front)
    assert not c.begin(auto_select=True)
    assert not any(message['type'] == 'begin' for message in c._bridge.messages)


@pytest.mark.parametrize('ack_selection', [[3, 0], [0, 0], [-1, -1], None])
def test_native_begin_intermediate_idle_waits_for_validated_ack(component, monkeypatch, ack_selection):
    from test_inline_state import receive
    c, _ = component
    monkeypatch.setattr(c, '_foreground_identity', lambda: FRONT)
    c._source_switch_factory = lambda *_: pytest.fail('pinned focus invoked legacy focus recovery')
    c.pin_start_focus((c._epoch, c._client_id, '', BUNDLE, 42, (3, 0)), time.monotonic())
    began = []
    c.began.connect(lambda: began.append(True))
    assert c.begin(auto_select=True)
    assert c._source_activation is None and c._bridge.messages[-1]['type'] == 'ping'
    receive(c, ready=True, phase='idle', utterance_id='', application=BUNDLE, selection=[3, 0],
            request_id=c._startup_focus_request)
    assert c._reply_stage == 'begin'
    # Captured WeChat sequence: invalidate() sends this snapshot-free state
    # before constructing the new CompositionSession and its full BEGIN ack.
    receive(c, ready=True, phase='idle', utterance_id='', application=BUNDLE)
    assert c._reply_stage == 'begin' and not c.error and not began
    receive(c, ready=True, phase='listening', application=BUNDLE,
            **({'selection': ack_selection} if ack_selection is not None else {}))
    assert bool(began) == (ack_selection == [3, 0])
    if not began:
        assert '已变化' in c.error
        assert c._bridge.messages[-1]['type'] == 'reset'


@pytest.mark.parametrize('live_selection', [[5, 0], [8, 0], [-1, -1], None])
def test_next_sentence_uses_live_caret_instead_of_previous_original_snapshot(component, monkeypatch, live_selection):
    from test_inline_state import receive
    c, _ = component
    monkeypatch.setattr(c, '_foreground_identity', lambda: FRONT)
    c._utterance_id = 'finished-sentence'
    c._view.update(phase='dictated', application=BUNDLE, selection=[0, 0], raw='first')
    c.pin_start_focus((c._epoch, c._client_id, 'finished-sentence', BUNDLE, 42, (5, 0)), time.monotonic())
    began = []
    c.began.connect(lambda: began.append(True))
    assert c.begin(auto_select=True)
    ping = c._bridge.messages[-1]
    assert ping['request_id'].startswith('start:')
    # Native ordinary notifications retain the old sentence's original caret.
    receive(c, ready=True, phase='dictated', utterance_id='finished-sentence',
            application=BUNDLE, selection=[0, 0])
    receive(c, ready=True, phase='dictated', utterance_id='finished-sentence',
            application=BUNDLE, selection=[5, 0], request_id='start:obsolete')
    assert not c.error and not any(m['type'] == 'begin' for m in c._bridge.messages)
    receive(c, ready=True, phase='dictated', utterance_id='finished-sentence',
            application=BUNDLE, selection=live_selection, request_id=ping['request_id'])
    if live_selection != [5, 0]:
        assert '已变化' in c.error and not began
        assert not any(m['type'] == 'begin' for m in c._bridge.messages)
        return
    assert c._reply_stage == 'begin'
    # A retried focus reply may arrive after BEGIN; it cannot roll startup back.
    receive(c, ready=True, phase='dictated', utterance_id='finished-sentence',
            application=BUNDLE, selection=[0, 0], request_id=ping['request_id'])
    assert not c.error
    receive(c, ready=True, phase='listening', application=BUNDLE, selection=[5, 0])
    assert began == [True]
    before = dict(c._view)
    receive(c, ready=True, phase='dictated', utterance_id='finished-sentence',
            application=BUNDLE, selection=[0, 0], request_id=ping['request_id'])
    assert c._view == before
