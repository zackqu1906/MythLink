"""Shared gestures use IMK; AX missing focus must neither block nor grant input."""
from dataclasses import replace
from types import SimpleNamespace
import time

import pytest
from PySide6.QtCore import QCoreApplication

from proximic_ring.voice.ime.focus_probe import InputFocus, probe_input_focus, same_input_focus
from test_app_gestures import route
from test_application_menus import configure
from test_ring_gestures import request
from test_presentation_portability import native_backend


@pytest.fixture
def imk_route(route, monkeypatch):
    c, service, inline, backend, sent, messages, emit = route
    backend.target = replace(backend.target, bundle='com.apple.Safari', profile='safari',
                             focus=None, role='', input_context='unknown')
    inline._view['application'] = backend.target.bundle
    live = dict(epoch='e', client_id='c', utterance_id='u', application=backend.target.bundle,
                ready=False, selection=[-1, -1])
    data = dict(state=live, front=(backend.target.bundle, backend.target.pid))
    bridge = SimpleNamespace(epoch='e', connected=True, focus_state=lambda **_: data['state'], close=lambda: None)
    monkeypatch.setattr(inline, '_bridge', bridge)
    monkeypatch.setattr(inline, '_foreground_identity', lambda: data['front'])
    for name in ('capture_start_focus', 'capture_gesture_target', 'same_input_focus', 'same_gesture_target'):
        monkeypatch.setattr(inline, name, getattr(type(inline), name).__get__(inline))
    catalog = configure(service, monkeypatch, backend.target.bundle)
    assert catalog.setCustomBinding(backend.target.bundle, 'swipe-left', 'Previous tab', 'Ctrl+Shift+Tab')
    assert catalog.setCustomBinding(backend.target.bundle, 'swipe-right', 'Next tab', 'Ctrl+Tab')
    return (*route, data)


@pytest.mark.parametrize('ax_context', ['unknown', 'text', 'nontext'])
@pytest.mark.parametrize('gesture,shortcut', [('swipe-left', 'Ctrl+Shift+Tab'), ('swipe-right', 'Ctrl+Tab')])
def test_pdf_shared_gesture_uses_explicit_imk_nontext_even_without_ax(imk_route, ax_context, gesture, shortcut):
    c, _, _, backend, sent, messages, _, _ = imk_route
    backend.target = replace(backend.target, input_context=ax_context)
    request(c, gesture)
    assert sent == [('safari', shortcut)] and not messages


@pytest.mark.parametrize('gesture', ['swipe-left', 'swipe-right', 'swipe-down'])
def test_live_text_never_queries_ax_or_dispatches_browser_action(imk_route, monkeypatch, gesture):
    c, service, inline, backend, sent, messages, _, data = imk_route
    data['state'].update(ready=True, selection=[3, 0])
    monkeypatch.setattr(backend, 'capture', lambda **_: pytest.fail('IMK text route queried AX'))
    request(c, gesture)
    assert not sent
    assert not service.notice or gesture == 'swipe-down'


@pytest.mark.parametrize('change', ['selection', 'client', 'utterance', 'epoch', 'foreground', 'timeout', 'window', 'dialog', 'expired'])
def test_queued_outside_gesture_is_cancelled_when_focus_or_target_changes(imk_route, monkeypatch, change):
    c, _, inline, backend, sent, messages, _, data = imk_route
    request(c, 'swipe-left', deliver=False)
    if change == 'selection': data['state'].update(ready=True, selection=[0, 0])
    if change == 'client': data['state']['client_id'] = 'second-editor'
    if change == 'utterance': data['state']['utterance_id'] = 'new-utterance'
    if change == 'epoch': inline._bridge.epoch = 'new-connection'
    if change == 'foreground': data['front'] = ('another.app', 99)
    if change == 'timeout': data['state'] = None
    if change == 'window': backend.target = replace(backend.target, window='another-window')
    if change == 'dialog': backend.target = replace(backend.target, blocked=True)
    if change == 'expired':
        clock = time.monotonic
        monkeypatch.setattr(time, 'monotonic', lambda: clock() + 2)
    QCoreApplication.processEvents()
    assert not sent and not messages


@pytest.mark.parametrize('selection', [None, [], [0], ['-1', '-1'], [False, False]])
def test_missing_or_malformed_selection_is_not_nontext(imk_route, selection):
    _, _, inline, _, _, _, _, data = imk_route
    data['state']['selection'] = selection
    proof = inline.capture_start_focus()
    assert proof.status == 'unavailable'


@pytest.mark.parametrize('case', ['missing', 'menu', 'sheet', 'dialog', 'foreign', 'modal'])
def test_window_guard_remains_independent_of_ax_text_focus(monkeypatch, case):
    window = dict(AXRole='AXWindow', AXSubrole='AXStandardWindow')
    focus = dict(AXRole='AXGroup', AXParent=window, AXWindow=window)
    if case == 'missing': focus = None
    if case == 'menu': focus['AXParent'] = dict(AXRole='AXMenu', AXParent=window)
    if case == 'sheet': window['AXSheets'] = [dict(AXRole='AXSheet')]
    if case == 'dialog': focus['AXParent'] = dict(AXRole='AXGroup', AXSubrole='AXDialog', AXParent=window)
    if case == 'foreign': focus['AXParent'] = dict(AXRole='AXWindow')
    if case == 'modal': window['AXModal'] = True
    backend, _, _ = native_backend(monkeypatch, window, focus, bundle='com.apple.Safari')
    target = backend.capture(menu_action=True, focus_sensitive=False)
    assert target is not None
    assert target.blocked is (case != 'missing')
    assert not target.focus_sensitive


@pytest.mark.parametrize('changed_again', [False, True])
def test_activation_notification_can_match_only_the_proved_client(imk_route, monkeypatch, changed_again):
    c, _, inline, _, sent, messages, _, data = imk_route
    original = inline.capture_start_focus
    def activate():
        data['state'].update(client_id='new-activation', utterance_id='')
        return original()
    monkeypatch.setattr(inline, 'capture_start_focus', activate)
    request(c, 'swipe-left', deliver=False)
    # Activation notification reaches Qt before the queued gesture.
    inline._client_id = 'unrelated-client' if changed_again else 'new-activation'
    inline._utterance_id = ''
    QCoreApplication.processEvents()
    assert sent == ([] if changed_again else [('safari', 'Ctrl+Shift+Tab')])
    assert not messages


def test_own_window_keeps_qt_available_during_imk_revalidation(imk_route, monkeypatch):
    import os
    import threading
    from PySide6.QtCore import QObject, Signal, Qt, QThread
    c, service, inline, backend, sent, _, _, data = imk_route
    backend.target = replace(backend.target, pid=os.getpid())
    data['front'] = (backend.target.bundle, backend.target.pid)
    gui = QCoreApplication.instance().thread()
    class Responder(QObject):
        requested = Signal(object)
        def __init__(self):
            super().__init__()
            self.requested.connect(lambda done: done.set(), Qt.QueuedConnection)
    responder = Responder()
    original = inline.same_input_focus
    calls = []
    def verify(proof):
        calls.append(QThread.currentThread())
        done = threading.Event()
        responder.requested.emit(done)
        return done.wait(.5) and original(proof)
    monkeypatch.setattr(inline, 'same_input_focus', verify)
    request(c, 'swipe-left')
    deadline = time.monotonic() + 1
    while not sent and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(.001)
    assert sent == [('safari', 'Ctrl+Shift+Tab')]
    assert calls and all(thread != gui for thread in calls)
    QCoreApplication.processEvents()
