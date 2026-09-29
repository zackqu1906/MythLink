"""Replay the logged ACK/focus-check ordering; no native app, BLE or audio IO."""
import pytest
import sys
import threading
from types import SimpleNamespace
from test_inline_state import component, receive
from test_input_source_activation import FakeSource

@pytest.mark.parametrize('outer_callback', ['timer', 'state', 'source'])
def test_successful_begin_ack_during_workspace_refresh_is_not_cancelled(component, monkeypatch, outer_callback):
    c, diagnostics = component
    origin = ('test.editor',123)
    monkeypatch.setattr(c, '_foreground_identity', lambda: origin)
    c._source_switch_factory = FakeSource
    assert c.begin(auto_select=True)
    source = c._source_activation
    source.event.emit({'event':'selected','already_selected':True})
    receive(c, ready=True, phase='idle', utterance_id='', application=origin[0])
    assert c._reply_stage == 'begin'
    utterance = c._utterance_id
    acknowledgments = []
    c.began.connect(lambda: acknowledgments.append(c._utterance_id))
    delivered = False
    def workspace_refresh():
        nonlocal delivered
        if not delivered:
            delivered = True
            # mac_workspace.frontmost_application drains NSRunLoop. A pending
            # native BEGIN response can arrive before the outer read returns.
            receive(c, ready=True, phase='listening', application=origin[0])
        return origin  # User never switched application.
    monkeypatch.setattr(c, '_foreground_identity', workspace_refresh)
    if outer_callback == 'timer': c._refresh_activation()
    elif outer_callback == 'state':
        receive(c, ready=True, phase='listening', application=origin[0])
    else: source.event.emit({'event':'selected','already_selected':True})
    assert delivered
    assert c._view['phase'] == 'listening', [(e['type'],e.get('phase'),e.get('error')) for e in diagnostics]
    assert c._utterance_id == utterance
    assert not any(m['type']=='reset' for m in c._bridge.messages)
    assert acknowledgments == [utterance]
    # A normal endpoint must allow the next sentence, not retain an orphan
    # native listening transaction like the one in the recorded failure.
    receive(c, ready=True, phase='dictated', application=origin[0])
    assert c.begin(auto_select=True)
    next_utterance = c._utterance_id
    assert next_utterance != utterance
    c._source_activation.event.emit({'event': 'selected', 'already_selected': True})
    receive(c, ready=True, phase='dictated', utterance_id=utterance, application=origin[0])
    receive(c, ready=True, phase='listening', application=origin[0])
    assert acknowledgments == [utterance, next_utterance]
    assert c._view['phase'] == 'listening' and not c.error


@pytest.mark.parametrize('outer_callback', ['timer', 'state', 'source'])
@pytest.mark.parametrize('transition', ['cancel', 'restart', 'disconnect'])
def test_superseded_focus_check_cannot_change_newer_state(component, monkeypatch,
                                                        outer_callback, transition):
    c, diagnostics = component
    origin = ('test.editor', 123)
    monkeypatch.setattr(c, '_foreground_identity', lambda: origin)
    c._source_switch_factory = FakeSource
    assert c.begin(auto_select=True)
    old_source = c._source_activation
    old_source.event.emit({'event': 'selected', 'already_selected': True})
    receive(c, ready=True, phase='idle', utterance_id='', application=origin[0])
    before = c._utterance_id
    expected = {}
    def lookup():
        monkeypatch.setattr(c, '_foreground_identity', lambda: origin)
        if transition == 'disconnect':
            receive(c, 'disconnected')
        else:
            c.cancel()
            if transition == 'restart':
                assert c.begin(auto_select=True)
                assert c._utterance_id != before
        expected.update(context=c._startup_context(), view=dict(c._view),
                        commands=list(c._bridge.messages))
        return ('unrelated.app', 456)  # Result belongs to the retired check.
    monkeypatch.setattr(c, '_foreground_identity', lookup)
    if outer_callback == 'timer': c._refresh_activation()
    elif outer_callback == 'state':
        receive(c, ready=True, phase='listening', application=origin[0])
    else: old_source.event.emit({'event': 'selected'})
    assert c._startup_context() == expected['context']
    assert c._view == expected['view']
    assert c._bridge.messages == expected['commands']
    assert not any(e.get('type') == 'startup_focus_changed' for e in diagnostics)


def test_qt_workspace_lookup_cannot_deliver_pending_begin_ack(component, monkeypatch):
    from proximic_ring import mac_workspace
    c, _ = component
    origin = ('test.editor', 123)
    monkeypatch.setattr(c, '_foreground_identity', lambda: origin)
    c._source_switch_factory = FakeSource
    assert c.begin(auto_select=True)
    source = c._source_activation
    source.event.emit({'event': 'selected', 'already_selected': True})
    receive(c, ready=True, phase='idle', utterance_id='', application=origin[0])
    workspace = SimpleNamespace(frontmostApplication=lambda: 'current-app')
    monkeypatch.setitem(sys.modules, 'AppKit', SimpleNamespace(
        NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: workspace)))
    def nested_run_loop(_):
        pytest.fail('Workspace lookup nested the GUI event loop')
    monkeypatch.setitem(sys.modules, 'Foundation', SimpleNamespace(
        NSRunLoop=SimpleNamespace(currentRunLoop=lambda: SimpleNamespace(runUntilDate_=nested_run_loop)),
        NSDate=SimpleNamespace(dateWithTimeIntervalSinceNow_=lambda interval: interval)))
    assert mac_workspace.frontmost_application() == 'current-app'
    assert c._view['phase'] == 'starting'
    receive(c, ready=True, phase='listening', application=origin[0])
    assert c._view['phase'] == 'listening' and not c.error


def test_background_workspace_refresh_still_services_notifications(component, monkeypatch):
    from proximic_ring import mac_workspace
    state = {'app': 'old', 'drains': 0}
    workspace = SimpleNamespace(frontmostApplication=lambda: state['app'])
    def drain(_):
        state.update(app='new', drains=state['drains'] + 1)
    monkeypatch.setitem(sys.modules, 'AppKit', SimpleNamespace(
        NSWorkspace=SimpleNamespace(sharedWorkspace=lambda: workspace)))
    monkeypatch.setitem(sys.modules, 'Foundation', SimpleNamespace(
        NSRunLoop=SimpleNamespace(currentRunLoop=lambda: SimpleNamespace(runUntilDate_=drain)),
        NSDate=SimpleNamespace(dateWithTimeIntervalSinceNow_=lambda interval: interval)))
    result = []
    worker = threading.Thread(target=lambda: result.append(mac_workspace.frontmost_application()))
    worker.start()
    worker.join(2)
    assert not worker.is_alive()
    assert result == ['new'] and state['drains'] == 1
