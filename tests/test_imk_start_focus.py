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


@pytest.mark.parametrize('refreshed_selection', [[1, 0], [-1, -1]])
def test_voice_refreshes_unknown_client_before_pinning_target(refreshed_selection):
    stale = {**state(), 'selection': [-1, -1], 'context_key_supported': True}
    bridge, data, select = setup_probe(stale)
    refreshed = []
    def refresh(origin, *, timeout, state):
        refreshed.append(origin)
        data['reply'] = {**stale, 'client_id': 'body-after-key', 'selection': refreshed_selection}
    proof = probe_input_focus(bridge, lambda: data['front'], select, require_selection=False,
                              refresh_context=refresh)
    assert refreshed == [FRONT]
    assert proof.status == 'text' and proof.target[1] == 'body-after-key'
    assert proof.target[-1] == tuple(refreshed_selection)
    assert not data['selected']


def test_context_refresh_cannot_pin_another_app():
    bridge, data, select = setup_probe({**state(), 'selection': [-1, -1], 'context_key_supported': True})
    def refresh(origin, *, timeout, state):
        data['front'] = ('other.app', 43)
    proof = probe_input_focus(bridge, lambda: data['front'], select, require_selection=False,
                              refresh_context=refresh)
    assert proof.status == 'unavailable' and proof.target is None


@pytest.mark.parametrize('selection', [[0, 0], [83, 0], [3, 4]])
def test_voice_uses_reported_selection_without_refreshing(selection):
    bridge, data, select = setup_probe({**state(), 'selection': selection, 'context_key_supported': True})
    def refresh(*args, **kwargs):
        pytest.fail('known selection triggered F20')
    proof = probe_input_focus(bridge, lambda: FRONT, select, require_selection=False,
                              refresh_context=refresh)
    assert proof.status == 'text' and proof.target[-1] == tuple(selection)
    assert not data['selected']


def test_optional_context_refresh_timeout_preserves_unknown_range_dictation(focus_clock):
    unknown = {**state(), 'selection': [-1, -1], 'context_key_supported': True}
    bridge, data, select = setup_probe(unknown)
    calls = []
    def refresh(origin, *, timeout, state):
        calls.append(timeout)
        focus_clock.sleep(timeout)
        raise TimeoutError
    proof = probe_input_focus(bridge, lambda: FRONT, select, require_selection=False,
                              refresh_context=refresh)
    assert proof.status == 'text' and proof.target[-1] == (-1, -1)
    assert len(calls) == 1 and proof.elapsed_ms < 800


@pytest.fixture
def focus_clock(monkeypatch):
    from proximic_ring.voice.ime import focus_probe
    clock = SimpleNamespace(now=10.0)
    clock.monotonic = lambda: clock.now
    clock.sleep = lambda seconds: setattr(clock, 'now', clock.now + seconds)
    monkeypatch.setattr(focus_probe, 'time', clock)
    return clock


@pytest.mark.parametrize('selection_delay', [.78, 1.15])
def test_cold_activation_leaves_time_for_fresh_focus(focus_clock, selection_delay):
    # Recorded startup: connection at ~780 ms, prepared client at ~815 ms.
    # The old shared 800 ms deadline reports an error before that valid reply.
    bridge = SimpleNamespace(epoch='', connected=False)
    selected, queries = [], []
    started = focus_clock.now
    def select(pid, *, timeout):
        selected.append(pid)
        focus_clock.sleep(min(selection_delay, timeout))
        if selection_delay >= timeout:
            raise TimeoutError
        bridge.connected, bridge.epoch = True, 'e'
    def reply(*, timeout):
        queries.append(timeout)
        if focus_clock.now < started + selection_delay + .035:
            return {'ready': False, 'client_id': '', 'application': ''}
        return state()
    bridge.focus_state = reply
    proof = probe_input_focus(bridge, lambda: FRONT, select)
    assert proof.status == 'text', proof
    assert proof.target == ('e', 'c', '', BUNDLE, 42, (3, 0))
    assert proof.elapsed_ms >= (selection_delay + .035) * 1000
    assert selected == [42] and all(0 < wait <= .2 for wait in queries)


@pytest.mark.parametrize('response', ['disconnected', 'inactive', 'silent', 'late_ready'])
def test_activation_wait_has_a_fixed_total_deadline(focus_clock, response):
    bridge, _, _ = setup_probe()
    selected, queries = [], []
    def select(pid, *, timeout):
        selected.append(pid)
        assert timeout == pytest.approx(1.5)
        focus_clock.sleep(1.45)
        bridge.connected = response != 'disconnected'
        bridge.epoch = 'e' if bridge.connected else ''
    def reply(*, timeout):
        queries.append(timeout)
        focus_clock.sleep(timeout)
        if not selected or response == 'inactive':
            return {'ready': False, 'client_id': '', 'application': ''}
        return state() if response == 'late_ready' and focus_clock.now >= 12.3 else None
    bridge.focus_state = reply
    proof = probe_input_focus(bridge, lambda: FRONT, select)
    if response == 'disconnected':
        assert proof.reason == 'foreground_or_connection_changed'
    else:
        assert proof.reason == 'input_method_timeout'
        assert proof.elapsed_ms == pytest.approx(2300)
    assert proof.target is None and selected == [42]
    assert all(0 < wait <= .2 for wait in queries)


def test_source_selection_receipt_does_not_mean_process_or_client_is_ready(focus_clock):
    class ColdBridge:
        @property
        def connected(self):
            return focus_clock.now >= 11.05

        @property
        def epoch(self):
            return 'e' if self.connected else ''

        def focus_state(self, **_):
            return state() if focus_clock.now >= 11.1 else None
    selected = []
    def select(pid, **_):
        selected.append(pid)
        focus_clock.sleep(.02)
    proof = probe_input_focus(ColdBridge(), lambda: FRONT, select)
    assert proof.status == 'text', proof
    assert 1100 <= proof.elapsed_ms < 1200 and selected == [42]


def test_new_source_that_never_connects_stops_at_total_deadline(focus_clock):
    bridge = SimpleNamespace(epoch='', connected=False)
    selected = []
    def select(pid, **_):
        selected.append(pid)
        focus_clock.sleep(.3)
    proof = probe_input_focus(bridge, lambda: FRONT, select)
    assert proof.reason == 'input_method_timeout' and proof.elapsed_ms == pytest.approx(2300)
    assert selected == [42]


def test_source_selection_cannot_overrun_its_budget_with_a_ready_reply(focus_clock):
    bridge, _, _ = setup_probe({'ready': False, 'client_id': '', 'application': ''})
    def select(*_, timeout):
        focus_clock.sleep(timeout + .01)
        bridge.focus_state = lambda **_: pytest.fail('queried after activation expired')
    assert probe_input_focus(bridge, lambda: FRONT, select).reason == 'input_method_timeout'


@pytest.mark.parametrize('change', ['app', 'pid', 'epoch'])
def test_target_change_during_extended_activation_wait_cancels(focus_clock, change):
    bridge, data, _ = setup_probe({'ready': False, 'client_id': '', 'application': ''})
    def select(*_, **__):
        focus_clock.sleep(1.1)
        if change == 'app': data['front'] = ('other', 42)
        if change == 'pid': data['front'] = (BUNDLE, 43)
        if change == 'epoch': bridge.epoch = 'new'
        bridge.focus_state = lambda **_: pytest.fail('queried changed target')
    proof = probe_input_focus(bridge, lambda: data['front'], select)
    assert proof.reason == 'foreground_or_connection_changed' and proof.target is None


@pytest.mark.parametrize('conflicting_binding', [False, True])
def test_slow_focus_confirmation_starts_proof_age_when_ready(route, monkeypatch, focus_clock,
                                                           conflicting_binding):
    from proximic_ring.ui.gestures import ring_gesture_controller, app_gesture_controller
    c, service, inline, backend, sent, messages, _ = route
    assert c.setGestureBinding('confirm', 0, 'tap')
    if conflicting_binding:
        assert configure(service, monkeypatch, backend.target.bundle).setBinding(
            backend.target.bundle, 'tap', ACTION['id'])
    monkeypatch.setattr(ring_gesture_controller, 'time', focus_clock)
    monkeypatch.setattr(app_gesture_controller, 'time', focus_clock)
    target = ('e', 'c', '', backend.target.bundle, backend.target.pid, (3, 0))
    def capture(**_):
        focus_clock.sleep(1.2)
        return InputFocus('text', target, elapsed_ms=1200)
    monkeypatch.setattr(inline, 'capture_start_focus', capture)
    assert request(c, 'tap', deliver=False)
    assert inline._pending_gesture_focus == (target, focus_clock.now)
    assert not sent and not messages


@pytest.mark.parametrize('change', ['none', 'queue_delay', 'settings'])
def test_slow_outside_focus_keeps_dispatch_expiry_and_generation(route, monkeypatch, focus_clock, change):
    from proximic_ring.ui.gestures import app_gesture_controller
    c, service, inline, backend, _, _, _ = route
    assert c.setGestureBinding('confirm', 0, 'tap')
    assert configure(service, monkeypatch, backend.target.bundle).setBinding(
        backend.target.bundle, 'tap', ACTION['id'])
    monkeypatch.setattr(app_gesture_controller, 'time', focus_clock)
    capture = inline.capture_start_focus
    backend.target = replace(backend.target, input_context='nontext')
    def slow_capture(**options):
        focus_clock.sleep(1.2)
        if change == 'settings':
            service._generation += 1
        return capture(**options)
    monkeypatch.setattr(inline, 'capture_start_focus', slow_capture)
    event = service.envelope(SimpleNamespace(name='tap'))
    assert event.route == 'outside'
    if change == 'queue_delay':
        focus_clock.sleep(1.01)
    assert service._override_stop_reason(event) == {
        'none': '', 'queue_delay': 'event_expired', 'settings': 'settings_changed'}[change]


def test_idle_live_focus_needs_neither_sentence_nor_source_switch():
    bridge, data, select = setup_probe()
    proof = probe_input_focus(bridge, lambda: data['front'], select)
    assert proof.status == 'text' and proof.target == ('e', 'c', '', BUNDLE, 42, (3, 0))
    assert not data['selected']


def test_unselected_source_is_selected_before_fresh_focus_query():
    bridge, data, select = setup_probe({'ready': False, 'client_id': '', 'application': ''})
    proof = probe_input_focus(bridge, lambda: data['front'], select)
    assert proof.status == 'text'
    assert data['selected'][0][0] == 42 and 0 < data['selected'][0][1]['timeout'] <= 1.5


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
    def capture(*, require_editable):
        assert require_editable
        return InputFocus('text', proof)
    monkeypatch.setattr(inline, 'capture_start_focus', capture)
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
    monkeypatch.setattr(inline, 'capture_start_focus', lambda **_: InputFocus('unavailable', reason=reason))
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
    monkeypatch.setattr(inline, 'capture_start_focus', lambda **_: InputFocus('nontext', reason='input_method_no_focus'))
    backend.target = replace(backend.target, input_context='text')
    assert not request(c, 'tap')
    assert not sent and not messages


@pytest.mark.parametrize('status,reason', [('text', ''), ('nontext', 'input_method_no_focus'),
    ('unavailable', 'unknown_focus'), ('unavailable', 'input_method_timeout')])
def test_unconflicted_first_start_needs_proof_and_unknown_focus_stays_quiet(route, monkeypatch, status, reason):
    c, service, inline, backend, sent, messages, _ = route
    monkeypatch.setattr(service, 'foreground_has_binding', lambda _: False)
    proof = ('e', 'fresh', '', BUNDLE, 42, (0, 0))
    notices = []
    monkeypatch.setattr(service, 'notify', notices.append)
    def capture(*, require_editable):
        assert require_editable
        return InputFocus(status, proof if status == 'text' else None, reason)
    monkeypatch.setattr(inline, 'capture_start_focus', capture)
    monkeypatch.setattr(backend, 'capture', lambda **_: pytest.fail('unconflicted start queried AX'))
    assert request(c, c._gesture_bindings.confirm[0]) == (status == 'text')
    assert not sent and not messages
    assert bool(notices) == (reason == 'input_method_timeout')
    if reason == 'unknown_focus':
        assert any(row["stage"] == "outcome" and row["reason"] == reason for row in service._diagnostics.recent())
    if status == 'text':
        assert inline._pending_gesture_focus[0] == proof


@pytest.mark.parametrize('change', ['client', 'epoch', 'not_ready'])
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


def test_voice_start_accepts_unreadable_selection_but_keeps_client_proof(component, monkeypatch):
    from proximic_ring.voice.ime.focus_probe import same_input_focus
    from test_inline_state import receive
    c, _ = component
    monkeypatch.setattr(c, '_foreground_identity', lambda: FRONT)
    reply = {**state(), 'epoch': c._epoch, 'client_id': c._client_id, 'selection': [-1, -1]}
    c._bridge.epoch = c._epoch
    c._bridge.connected = True
    c._bridge.focus_state = lambda **_: reply
    proof = c.capture_start_focus(require_editable=True)
    assert proof.status == 'text'
    assert same_input_focus(c._bridge, c._foreground_identity, proof)
    c.pin_start_focus(proof.target, time.monotonic())
    began = []
    c.began.connect(lambda: began.append(True))
    assert c.begin(auto_select=True)
    receive(c, **{k: v for k, v in reply.items() if k not in {'epoch', 'client_id'}},
            phase='idle', request_id=c._startup_focus_request)
    receive(c, ready=True, phase='listening', application=BUNDLE, selection=[-1, -1])
    assert began == [True] and not c.error
    reply['client_id'] = 'another-editor'
    assert not same_input_focus(c._bridge, c._foreground_identity, proof)


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
    assert began == [True] and not c.error


@pytest.mark.parametrize('live_selection', [[5, 0], [8, 0], [-1, -1], None])
def test_next_sentence_requires_correlated_client_reply_not_document_selection(component, monkeypatch, live_selection):
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
