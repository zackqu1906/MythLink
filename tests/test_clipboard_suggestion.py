from dataclasses import replace
from types import SimpleNamespace
import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication

from proximic_ring.platforms.macos.clipboard_suggestion import ClipboardField
from proximic_ring.gestures.gesture_settings import GestureBindings
from test_app_gestures import route
from test_ring_gestures import request


def wait_for(predicate):
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(.001)
    assert predicate()


@pytest.fixture
def suggestion(route, monkeypatch):
    owner, _, inline, *_ = route
    controller = owner.ringGestures._clipboard_suggestion
    controller._enabled = True
    revision = [7]
    messages, pastes, voices = [], [], []
    native = SimpleNamespace(connected=True, epoch='e', close=lambda: None,
                             send=lambda message: messages.append(message) or True)
    monkeypatch.setattr(inline, '_bridge', native)
    class Backend:
        focused = ClipboardField('editor-a', SimpleNamespace(pid=42, bundle='app.a'), (30, 40, 100, 20))
        def capture(self):
            return self.focused
        def paste(self, field, rev, cancelled):
            if cancelled.is_set() or revision[0] != rev or self.focused != field:
                return False
            pastes.append((field.key, rev))
            return True
    backend = Backend()
    controller._backend = backend
    controller._revision = lambda: revision[0]
    controller._observed_revision = 6
    controller._candidate = None
    owner._recognition_event.set()
    owner._utterance_commands = SimpleNamespace(connection=owner._disconnect_event,
        request=lambda kind, prepare, done: voices.append((kind, prepare, done)) or True)
    yield controller, owner, backend, revision, messages, pastes, voices
    controller.close()


def show(suggestion):
    c, owner, *_ = suggestion
    c.poll()
    wait_for(lambda: c._offer is not None)
    owner._inline_input._accept(dict(type='clipboard_offer_state', epoch='e',
                                    token=c._offer.token, visible=True))
    assert c._offer.shown
    return c._offer


def settled_poll(c):
    c.poll()
    wait_for(lambda: not c._polling)


@pytest.fixture
def clipboard_clock(suggestion):
    now = [time.monotonic()]
    suggestion[0]._clock = lambda: now[0]
    return now


def test_recommends_once_per_field_and_clipboard_revision(suggestion):
    c, _, backend, revision, messages, *_ = suggestion
    first = show(suggestion)
    assert messages[0] == dict(type='clipboard_offer', token=first.token, revision=7,
                              foreground_pid=42, rect=[30, 40, 100, 20])
    c.dismiss()
    settled_poll(c)
    assert c._offer is None
    backend.focused = None
    settled_poll(c)
    backend.focused = first.field
    settled_poll(c)
    assert c._offer is None
    backend.focused = replace(first.field, key='editor-b')
    second = show(suggestion)
    assert second.field.key == 'editor-b'
    revision[0] += 1
    third = show(suggestion)
    assert third.revision == 8 and third.token != second.token


@pytest.mark.parametrize('reason', ['typing', 'timeout', 'blur', 'voice', 'disconnect', 'clipboard_empty'])
def test_recommendation_dismissal(suggestion, reason):
    c, owner, backend, revision, *_ = suggestion
    offer = show(suggestion)
    if reason == 'typing': c._user_input()
    elif reason == 'timeout': offer.expires = time.monotonic() - 1
    elif reason == 'blur': backend.focused = None
    elif reason == 'voice':
        owner._inline_input._view['phase'] = 'listening'
        owner._inline_input.changed.emit()
    elif reason == 'disconnect': owner._disconnect_event.set()
    elif reason == 'clipboard_empty': revision[0] = None
    settled_poll(c)
    assert c._offer is None and offer.cancelled.is_set()


def test_native_palette_ack_is_required_before_consuming_gestures(suggestion):
    c, owner, *_ = suggestion
    c.poll(); wait_for(lambda: c._offer is not None)
    assert not c.consume(SimpleNamespace(name='tap'), owner._disconnect_event)
    c._native_event(dict(epoch='old', token=c._offer.token, visible=True))
    assert not c._offer.shown
    c._native_event(dict(epoch='e', token='old', visible=True))
    assert not c._offer.shown
    c._native_event(dict(epoch='e', token=c._offer.token, visible=False))
    assert c._offer is None
    # A rejected display request must not use up this field's recommendation.
    show(suggestion)


def test_right_swipe_accepts_once_and_never_uses_configured_edit_or_app_action(suggestion):
    c, owner, _, _, _, pastes, voices = suggestion
    offer = show(suggestion)
    assert not request(owner, 'swipe-right', deliver=False)
    assert not request(owner, 'swipe-right', deliver=False)
    # Native acknowledgement of our hide must not cancel the pending paste.
    c._native_event(dict(epoch='e', token=offer.token, visible=False))
    wait_for(lambda: c._offer is None)
    assert pastes == [('editor-a', 7)] and not voices


@pytest.mark.parametrize('gesture', ['tap', 'index-pinch'])
def test_tap_preempts_queued_right_swipe_even_with_custom_voice_binding(suggestion, gesture):
    c, owner, _, _, _, pastes, voices = suggestion
    if gesture == 'index-pinch':
        owner._device_family = 'R1'
    owner._gesture_bindings = GestureBindings(confirm=('snap', ''), end=('double-tap', ''))
    offer = show(suggestion)
    assert not request(owner, 'swipe-right', deliver=False)
    assert not request(owner, gesture, deliver=False)
    assert offer.cancelled.is_set()
    wait_for(lambda: len(voices) == 1)
    assert voices[0][0] == 'start' and not pastes
    assert c._offer is None


def test_tap_cancels_during_slow_paste_focus_validation(suggestion, monkeypatch):
    c, owner, backend, _, _, pastes, voices = suggestion
    entered, release = threading.Event(), threading.Event()
    paste = backend.paste
    def delayed(*args):
        entered.set()
        release.wait(2)
        return paste(*args)
    monkeypatch.setattr(backend, 'paste', delayed)
    show(suggestion)
    request(owner, 'swipe-right')
    wait_for(entered.is_set)
    request(owner, 'tap')
    release.set()
    wait_for(lambda: bool(voices))
    wait_for(lambda: c._queue._active is None)
    assert not pastes


@pytest.mark.parametrize('changed', ['focus', 'clipboard', 'connection'])
def test_accept_never_pastes_after_target_or_content_changed(suggestion, changed):
    c, owner, backend, revision, _, pastes, *_ = suggestion
    show(suggestion)
    request(owner, 'swipe-right', deliver=False)
    if changed == 'focus': backend.focused = replace(backend.focused, key='another-editor')
    elif changed == 'clipboard': revision[0] += 1
    else: owner._disconnect_event.set()
    QCoreApplication.processEvents()
    wait_for(lambda: c._queue._active is None)
    assert not pastes


def test_old_native_hide_cannot_dismiss_new_offer(suggestion):
    c, _, _, revision, *_ = suggestion
    old = show(suggestion)
    revision[0] += 1
    new = show(suggestion)
    c._native_event(dict(epoch='e', token=old.token, visible=False))
    assert c._offer is new and new.shown


def test_queued_focus_read_cannot_reopen_after_typing(suggestion, monkeypatch):
    c, _, backend, *_ = suggestion
    entered, release = threading.Event(), threading.Event()
    def capture():
        entered.set(); release.wait(2); return backend.focused
    monkeypatch.setattr(backend, 'capture', capture)
    c.poll(); wait_for(entered.is_set)
    c._user_input(); release.set()
    wait_for(lambda: not c._polling)
    assert c._offer is None
    c._quiet_until = 0
    settled_poll(c)
    assert c._offer is None


def test_tap_guard_suppresses_sdk_click_during_voice_handoff(suggestion):
    c, owner, *_ = suggestion
    show(suggestion)
    assert c.pointer_context[1] == 'clipboard'
    request(owner, 'tap')
    assert c._offer is None and c.pointer_context[1] == 'clipboard'
    c._pointer_guard = (c._pointer_guard[0], time.monotonic() - 1)
    assert c.pointer_context is None


def test_acceptance_is_shared_by_app_but_other_apps_can_accept(suggestion):
    c, owner, backend, revision, _, pastes, _ = suggestion
    first = show(suggestion)
    request(owner, 'swipe-right')
    wait_for(lambda: c._offer is None)
    # A different editor/window and even a new process still belong to app A.
    backend.focused = replace(first.field, key='new-window', target=SimpleNamespace(pid=99, bundle='app.a'))
    settled_poll(c)
    assert c._offer is None
    backend.focused = replace(first.field, key='app-b-editor', target=SimpleNamespace(pid=43, bundle='app.b'))
    show(suggestion)
    request(owner, 'swipe-right')
    wait_for(lambda: c._offer is None)
    assert pastes == [('editor-a', 7), ('app-b-editor', 7)]
    backend.focused = first.field
    settled_poll(c)
    assert c._offer is None
    # Re-copying (including the same content) creates a new recommendation.
    revision[0] += 1
    assert show(suggestion).field == first.field


@pytest.mark.parametrize('outcome', ['ignored', 'cancelled', 'failed', 'error'])
def test_only_successful_acceptance_suppresses_the_app(suggestion, monkeypatch, outcome):
    c, owner, backend, _, _, pastes, _ = suggestion
    first = show(suggestion)
    if outcome == 'ignored':
        c.dismiss()
    else:
        if outcome == 'failed': monkeypatch.setattr(backend, 'paste', lambda *args: False)
        elif outcome == 'error':
            def fail(*args): raise RuntimeError('paste failed')
            monkeypatch.setattr(backend, 'paste', fail)
        request(owner, 'swipe-right', deliver=False)
        if outcome == 'cancelled': request(owner, 'tap', deliver=False)
        wait_for(lambda: c._offer is None)
    assert not pastes
    backend.focused = replace(first.field, key='another-editor-in-same-app')
    assert show(suggestion).field == backend.focused


def test_clipboard_expires_at_one_minute_across_apps(suggestion, clipboard_clock):
    c, _, backend, revision, *_ = suggestion
    first = show(suggestion)
    c.dismiss()
    clipboard_clock[0] += 59.9
    backend.focused = replace(first.field, key='app-b', target=SimpleNamespace(pid=43, bundle='app.b'))
    last = show(suggestion)
    assert last.expires == pytest.approx(c._candidate.expires)
    clipboard_clock[0] += .1
    settled_poll(c)
    assert c._offer is None and c.pointer_context is None
    backend.focused = replace(first.field, key='app-c', target=SimpleNamespace(pid=44, bundle='app.c'))
    settled_poll(c)
    assert c._offer is None
    revision[0] += 1
    assert show(suggestion).revision == 8


@pytest.mark.parametrize('unavailable', ['disconnected', 'voice'])
def test_copy_age_is_tracked_even_when_recommendations_are_unavailable(suggestion, clipboard_clock, unavailable):
    c, owner, _, revision, *_ = suggestion
    if unavailable == 'disconnected': owner._connected = False
    else: owner._inline_input._view['phase'] = 'listening'
    settled_poll(c)
    assert c._offer is None
    clipboard_clock[0] += 60
    owner._connected = True
    owner._inline_input._view['phase'] = 'idle'
    settled_poll(c)
    assert c._offer is None
    revision[0] += 1
    show(suggestion)


def test_unknown_age_clipboard_at_launch_is_not_recommended(suggestion, clipboard_clock):
    from proximic_ring.ui.shared.clipboard_suggestion import ClipboardSuggestionController
    original, owner, backend, revision, messages, *_ = suggestion
    c = ClipboardSuggestionController(owner, original.router, backend=backend,
        revision=lambda: revision[0], enabled=True, clock=lambda: clipboard_clock[0])
    c._timer.stop()
    try:
        settled_poll(c)
        assert c._offer is None and not messages
        revision[0] += 1
        settled_poll(c)
        assert c._offer is not None and c._offer.revision == 8
    finally:
        c.close()


def test_slow_focus_read_cannot_recommend_expired_clipboard(suggestion, clipboard_clock, monkeypatch):
    c, _, backend, *_ = suggestion
    entered, release = threading.Event(), threading.Event()
    def capture():
        entered.set(); release.wait(2); return backend.focused
    monkeypatch.setattr(backend, 'capture', capture)
    c.poll(); wait_for(entered.is_set)
    clipboard_clock[0] += 60
    release.set()
    wait_for(lambda: not c._polling)
    assert c._offer is None


def test_expired_clipboard_cannot_be_accepted_before_next_poll(suggestion, clipboard_clock):
    c, owner, backend, *_ = suggestion
    first = show(suggestion)
    c.dismiss()
    clipboard_clock[0] += 59.9
    backend.focused = replace(first.field, key='editor-b')
    show(suggestion)
    clipboard_clock[0] += .1
    assert not c.consume(SimpleNamespace(name='swipe-right'), owner._disconnect_event)
